"""戰術層 tick 狀態機（combat.md v0.2 §6）。結果與單位處理順序無關。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from engine.tactical import rng
from engine.tactical.geometry import Vec, add, closest_point_on_segment, length, mul, norm, perpendiculars, ray_box, sub
from engine.tactical.tmap import Node, TacMap
from engine.tactical.units import PINNED, WEAPONS, Unit

MAX_RANGE = 60.0
SUPP_HIT = 35.0
SUPP_NEAR = 20.0
SUPP_NEAR_R = 1.5
SUPP_COVER = 15.0
SUPP_COVER_R = 2.0
DECAY_SEEN = 20.0
DECAY_UNSEEN = 30.0
DOWN_TIMER = 12
SUPPRESS_SPREAD = 3.0


@dataclass
class Plan:
    mode: str = "idle"            # idle / move / shoot / overwatch / posture
    path: list[Node] = field(default_factory=list)
    target: str | None = None     # 單位 id
    point: Vec | None = None      # 壓制目標點
    fire: str = "aimed"           # aimed / burst / suppress
    aim_part: str | None = None
    posture: str | None = None


@dataclass
class SideIntel:
    spotted: dict = field(default_factory=dict)   # unit id -> tick
    last_known: dict = field(default_factory=dict)  # unit id -> (node, tick)


class Battle:
    def __init__(self, tmap: TacMap, units: list[Unit], seed: int = 0):
        self.map = tmap
        self.units: dict[str, Unit] = {u.id: u for u in units}
        self.seed = seed
        self.tick = 0
        self.intel: dict[str, SideIntel] = {s: SideIntel() for s in {u.side for u in units}}
        self.log: list[str] = []
        self.shots: list[dict] = []
        self.order_hook = None
        self.heavy_fire: dict[str, dict[str, float]] = {}
        self.last_heavy_fire: dict[str, dict[str, float]] = {}

    # ------------------------------------------------------------------ helpers

    def ordered(self, units=None) -> list[Unit]:
        seq = sorted((units if units is not None else self.units.values()), key=lambda u: u.id)
        return self.order_hook(seq) if self.order_hook else seq

    def alive(self, side: str | None = None) -> list[Unit]:
        return [u for u in self.units.values() if u.alive and (side is None or u.side == side)]

    def enemies_of(self, side: str) -> list[Unit]:
        return [u for u in self.units.values() if u.alive and u.side != side]

    def occupied(self) -> dict[Node, str]:
        return {u.pos: u.id for u in self.units.values() if u.alive}

    def r(self, *parts) -> float:
        return rng.uniform(self.seed, self.tick, *parts)

    def g(self, *parts) -> float:
        return rng.gauss(self.seed, self.tick, *parts)

    def visibility(self, observer: Unit, target: Unit) -> float:
        e = observer.eye(self.map)
        pts = target.samples(self.map)
        return sum(self.map.sight(e, p) for p in pts.values()) / len(pts)

    def visible_parts(self, observer: Unit, target: Unit) -> list[str]:
        e = observer.eye(self.map)
        return [k for k, p in target.samples(self.map).items() if self.map.sight(e, p) > 0.3]

    def is_spotted(self, by_side: str, target: Unit) -> bool:
        return target.id in self.intel[by_side].spotted

    def exposed_to(self, node: Node, side: str, posture: str = "crouch") -> list[str]:
        """以 side 陣營情報判斷：站在 node 時，有哪些已知敵人的最後位置對軀幹有視線。"""
        from engine.tactical import body
        base = self.map.center(node)
        torso = body.torso_center(base, posture, (0, 1))
        out = []
        for eid, (enode, _) in self.intel[side].last_known.items():
            e = self.units.get(eid)
            if not e or not e.alive:
                continue
            eye = body.eye(self.map.center(enode), e.posture)
            if self.map.sight(eye, torso) > 0.3:
                out.append(eid)
        return out

    # ------------------------------------------------------------------ detection

    def detect(self, phase: str, movers: set[str] = frozenset()):
        for side in sorted(self.intel):
            observers = [u for u in self.alive(side)]
            for t in sorted(self.enemies_of(side), key=lambda u: u.id):
                vis_any = 0.0
                miss = 1.0
                for o in observers:
                    v = self.visibility(o, t)
                    if v <= 0:
                        continue
                    vis_any = max(vis_any, v)
                    dist = length(sub(o.eye(self.map), t.torso(self.map)))
                    k = 1.0 if dist <= 8 else max(0.25, 1.0 - (dist - 8) / 40)
                    p = v * k * (1.5 if t.id in movers or t.moved else 1.0)
                    if o.damage.get("head"):
                        p *= 0.5
                    miss *= 1 - min(0.95, p)
                intel = self.intel[side]
                if t.id in intel.spotted:
                    if vis_any > 0.15:
                        intel.spotted[t.id] = self.tick
                        intel.last_known[t.id] = (t.pos, self.tick)
                    else:
                        del intel.spotted[t.id]
                    continue
                if vis_any <= 0:
                    continue
                if self.r("detect", side, t.id) < 1 - miss:
                    intel.spotted[t.id] = self.tick
                    intel.last_known[t.id] = (t.pos, self.tick)
                    self.log.append(f"T{self.tick} {side} 發現 {t.id}")

    # ------------------------------------------------------------------ shooting

    def sigma(self, shooter: Unit, dist: float, overwatch: bool = False) -> float:
        w = WEAPONS[shooter.weapon]
        s = w["sigma"]
        if dist > w["range"]:
            s *= 1 + (dist - w["range"]) / w["range"]
        if shooter.moved:
            s *= w["move_mult"]
        s *= {"stand": 1.0, "crouch": 0.85, "prone": 0.7}[shooter.posture]
        if shooter.suppression >= PINNED:
            s *= 4.0
        elif shooter.suppressed:
            s *= 1.5
        for arm in ("larm", "rarm"):
            if shooter.damage.get(arm):
                s *= 1.6
        if overwatch:
            s *= 1.3
        return s

    def fire(self, shooter: Unit, plan: Plan, action: str, overwatch: bool = False) -> list[dict]:
        """產生射擊結果（不套用）。"""
        if not shooter.can_shoot():
            return []
        w = WEAPONS[shooter.weapon]
        mode = plan.fire
        if shooter.pinned:
            mode = "suppress"
        target = self.units.get(plan.target) if plan.target else None
        eye = shooter.eye(self.map)
        if mode == "suppress":
            if target is not None:
                aim = target.torso(self.map)
            elif plan.point is not None:
                aim = plan.point
            else:
                return []
            rounds = w["supp_rounds"]
        else:
            if target is None or not target.alive:
                return []
            vis = self.visible_parts(shooter, target)
            if not vis:
                return []
            part = plan.aim_part if (mode == "aimed" and plan.aim_part in vis) else (
                "torso" if "torso" in vis else ("head" if "head" in vis else vis[0]))
            aim = target.samples(self.map)[part]
            rounds = 1 if mode == "aimed" else (w["burst"] or 1)
        rounds = min(rounds, shooter.ammo)
        dist = length(sub(aim, eye))
        base_sigma = self.sigma(shooter, dist, overwatch)
        results = []
        for i in range(rounds):
            if mode == "aimed":
                s = base_sigma
            elif mode == "burst":
                s = base_sigma * (2 + i)
            else:
                s = base_sigma * SUPPRESS_SPREAD
            results.append(self.trace(shooter, eye, aim, s, action, i))
        return results

    def trace(self, shooter: Unit, eye: Vec, aim: Vec, sigma: float, action: str, i: int) -> dict:
        d = norm(sub(aim, eye))
        u, v = perpendiculars(d)
        du = self.g("spread", shooter.id, action, i, "u") * sigma
        dv = self.g("spread", shooter.id, action, i, "v") * sigma
        direction = norm(add(d, add(mul(u, math.tan(du)), mul(v, math.tan(dv)))))
        best_t, best_kind, best_ref = MAX_RANGE, "none", None
        wall = self.map.raycast(eye, direction, MAX_RANGE)
        if wall:
            best_t, best_kind, best_ref = wall[0], "cover", wall[1]
        if direction[2] < -1e-6:
            tg = -eye[2] / direction[2]
            if 0 < tg < best_t:
                best_t, best_kind, best_ref = tg, "ground", None
        for unit in sorted(self.units.values(), key=lambda x: x.id):
            if unit.id == shooter.id or not unit.alive:
                continue
            for name, box in unit.parts(self.map).items():
                hit = ray_box(eye, direction, box, best_t)
                if hit and hit[0] < best_t:
                    best_t, best_kind, best_ref = hit[0], "body", (unit.id, name)
        end = add(eye, mul(direction, best_t))
        return {"shooter": shooter.id, "from": eye, "to": end, "kind": best_kind, "ref": best_ref,
                "dir": direction, "len": best_t}

    def suppression_from(self, shot: dict, shooter: Unit) -> dict[str, float]:
        out: dict[str, float] = {}
        k = WEAPONS[shooter.weapon]["supp_k"]
        for unit in self.units.values():
            if not unit.alive or unit.side == shooter.side:
                continue
            value = 0.0
            if shot["kind"] == "body" and shot["ref"][0] == unit.id:
                value = SUPP_HIT
            else:
                torso = unit.torso(self.map)
                pt, _ = closest_point_on_segment(shot["from"], shot["to"], torso)
                dist = length(sub(pt, torso))
                if dist < SUPP_NEAR_R and not self.map.blocked(pt, torso):
                    value = SUPP_NEAR * (1 - dist / SUPP_NEAR_R)
                if shot["kind"] == "cover":
                    gap = length(sub(shot["to"], torso))
                    shooter_side = length(sub(shot["from"], shot["to"])) < length(sub(shot["from"], torso))
                    if gap < SUPP_COVER_R and shooter_side:
                        value = max(value, SUPP_COVER)
            if value > 0:
                out[unit.id] = value * k
        return out

    def apply(self, results: list[tuple[Unit, list[dict]]]):
        """同時套用一批射擊的損傷與壓制。"""
        supp: dict[str, float] = {}
        hits: list[tuple[str, str, str, int]] = []
        for shooter, shots in results:
            for i, s in enumerate(shots):
                shooter.stats["shots"] += 1
                self.shots.append(s)
                for uid, val in self.suppression_from(s, shooter).items():
                    supp[uid] = supp.get(uid, 0.0) + val
                    seen = self.heavy_fire.setdefault(shooter.side, {})
                    seen[uid] = seen.get(uid, 0.0) + val
                if s["kind"] == "body":
                    hits.append((shooter.id, s["ref"][0], s["ref"][1], i))
            if shots:
                shooter.ammo -= len(shots)
                shooter.fired = True
        for shooter_id, uid, part, i in hits:
            target = self.units[uid]
            shooter = self.units[shooter_id]
            if target.side != shooter.side:
                shooter.stats["hits"] += 1
            if target.down:
                continue
            self._damage(target, part, shooter, i)
        for uid, val in supp.items():
            self.units[uid].suppression = min(100.0, self.units[uid].suppression + val)

    def _damage(self, target: Unit, part: str, shooter: Unit, i: int):
        if part == "torso":
            target.torso_hits += 1
            target.damage["torso"] = target.torso_hits
            if target.torso_hits >= 2:
                self._down(target, shooter)
        elif part == "head":
            target.damage["head"] = True
            if self.r("headshot", target.id, shooter.id, i) < 0.2:
                self._down(target, shooter)
        else:
            target.damage[part] = True
        self.log.append(f"T{self.tick} {shooter.id} 命中 {target.id} {part}")

    def _down(self, target: Unit, shooter: Unit):
        if target.down:
            return
        target.down = True
        if shooter.side != target.side:
            shooter.stats["kills"] += 1
        self.log.append(f"T{self.tick} {target.id} 倒地（{shooter.id}）")

    # ------------------------------------------------------------------ tick

    def face_threats(self, units=None):
        """沒有移動的單位面向最近的已知敵人（只用己方情報）。"""
        for u in units if units is not None else self.units.values():
            if not u.alive:
                continue
            known = self.intel[u.side].last_known
            if not known:
                continue
            nodes = [n for eid, (n, _) in known.items() if self.units[eid].alive]
            if not nodes:
                continue
            target = min(nodes, key=lambda n: (abs(n[0] - u.pos[0]) + abs(n[1] - u.pos[1]), n))
            u.facing = self.map.facing_to(u.pos, target)

    def step(self, plans: dict[str, Plan]):
        self.tick += 1
        self.last_heavy_fire, self.heavy_fire = self.heavy_fire, {}
        for u in self.units.values():
            u.moved = False
            u.fired = False
        self.face_threats([u for u in self.units.values()
                           if not (plans.get(u.id) and plans[u.id].mode == "move")])
        snap1 = {uid: u.pos for uid, u in self.units.items()}
        for uid, p in plans.items():
            u = self.units[uid]
            if u.alive and p.mode == "posture" and p.posture:
                u.posture = p.posture

        movers = {uid: list(p.path) for uid, p in plans.items()
                  if p.mode == "move" and p.path and self.units[uid].alive}
        overwatch = {uid for uid, p in plans.items()
                     if p.mode == "overwatch" and self.units[uid].alive and not self.units[uid].pinned}
        ow_used: set[str] = set()
        budget = {uid: 2 for uid in movers}
        step_no = 0
        while movers:
            step_no += 1
            start = self.occupied()
            proposals: dict[str, Node] = {}
            for uid in sorted(movers):
                u = self.units[uid]
                if not u.alive or u.pinned or not movers[uid]:
                    continue
                nxt = movers[uid][0]
                cost = self.map.step_cost(u.pos, nxt) or 99
                if u.damage.get("lleg") or u.damage.get("rleg"):
                    cost *= 2
                if u.posture == "prone":
                    cost *= 2
                if cost > budget[uid]:
                    continue
                proposals[uid] = nxt
            if not proposals:
                break
            claims: dict[Node, list[str]] = {}
            for uid, node in proposals.items():
                if node in start and start[node] != uid:
                    continue
                claims.setdefault(node, []).append(uid)
            moved_now = []
            for node, uids in sorted(claims.items()):
                winner = min(uids, key=lambda x: self.r("claim", node, x, step_no))
                u = self.units[winner]
                cost = self.map.step_cost(u.pos, node) or 1
                if u.damage.get("lleg") or u.damage.get("rleg"):
                    cost *= 2
                if u.posture == "prone":
                    cost *= 2
                dx, dy = node[0] - u.pos[0], node[1] - u.pos[1]
                if dx or dy:
                    u.facing = (dx, dy)
                u.pos = node
                u.moved = True
                budget[winner] -= cost
                movers[winner].pop(0)
                moved_now.append(winner)
            for uid in list(movers):
                if uid not in moved_now or not movers[uid] or budget[uid] <= 0:
                    movers.pop(uid, None)
            if not moved_now:
                break
            self.detect(f"move{step_no}", set(moved_now))
            batch = []
            for wid in sorted(overwatch - ow_used):
                watcher = self.units[wid]
                if not watcher.alive or watcher.pinned or not watcher.can_shoot():
                    continue
                cands = [self.units[m] for m in moved_now
                         if self.units[m].side != watcher.side and self.units[m].alive
                         and self.is_spotted(watcher.side, self.units[m])
                         and self.visible_parts(watcher, self.units[m])]
                if not cands:
                    continue
                target = min(cands, key=lambda t: (length(sub(t.torso(self.map), watcher.eye(self.map))), t.id))
                shots = self.fire(watcher, Plan(mode="shoot", target=target.id, fire="aimed"),
                                  f"ow{step_no}", overwatch=True)
                if shots:
                    batch.append((watcher, shots))
                    ow_used.add(wid)
            if batch:
                self.apply(batch)
                for m in moved_now:
                    if self.units[m].down or self.units[m].pinned:
                        movers.pop(m, None)

        self.face_threats([u for u in self.units.values() if u.moved])
        self.detect("post_move")
        results = []
        for uid in sorted(plans):
            p = plans[uid]
            u = self.units[uid]
            if p.mode != "shoot" or not u.alive:
                continue
            if u.pinned and p.fire != "suppress" and not self._in_cover(u):
                continue
            target = self.units.get(p.target) if p.target else None
            if target is not None and p.fire != "suppress" and not self.is_spotted(u.side, target):
                continue
            shots = self.fire(u, p, "shot")
            if shots:
                results.append((u, shots))
        self.apply(results)

        for u in self.units.values():
            if not u.alive:
                continue
            seen = any(self.map.sight(e.eye(self.map), u.torso(self.map)) > 0
                       for e in self.enemies_of(u.side))
            u.suppression = max(0.0, u.suppression - (DECAY_SEEN if seen else DECAY_UNSEEN))
        for u in self.units.values():
            if u.fired and u.alive:
                for side in self.intel:
                    if side != u.side and any(self.map.sight(e.eye(self.map), u.torso(self.map)) > 0
                                              for e in self.alive(side)):
                        self.intel[side].spotted[u.id] = self.tick
                        self.intel[side].last_known[u.id] = (u.pos, self.tick)
        for side, intel in self.intel.items():
            for uid in list(intel.last_known):
                if self.units[uid].down:
                    intel.last_known.pop(uid, None)
                    intel.spotted.pop(uid, None)
        _ = snap1

    def _in_cover(self, u: Unit) -> bool:
        return any(self.visibility(e, u) < 0.5 for e in self.enemies_of(u.side)) or not self.enemies_of(u.side)

    # ------------------------------------------------------------------ state

    def state(self) -> dict:
        return {
            "tick": self.tick,
            "seed": self.seed,
            "units": {uid: u.to_dict() for uid, u in sorted(self.units.items())},
            "intel": {s: {"spotted": dict(i.spotted),
                          "last_known": {k: [list(v[0]), v[1]] for k, v in i.last_known.items()}}
                      for s, i in sorted(self.intel.items())},
        }

    def load_state(self, st: dict):
        self.tick = st["tick"]
        self.seed = st["seed"]
        self.units = {uid: Unit.from_dict(d) for uid, d in st["units"].items()}
        self.intel = {s: SideIntel(spotted=dict(i["spotted"]),
                                   last_known={k: (tuple(v[0]), v[1]) for k, v in i["last_known"].items()})
                      for s, i in st["intel"].items()}

    def over(self) -> str | None:
        sides = {u.side for u in self.alive()}
        if len(sides) <= 1:
            return next(iter(sides), "none")
        return None
