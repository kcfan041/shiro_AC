"""戰術層 tick 狀態機（combat.md v0.2 §6）。結果與單位處理順序無關。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from engine.tactical import rng
from engine.tactical.geometry import Vec, add, closest_point_on_segment, length, mul, norm, perpendiculars, ray_box, sub
from engine.tactical.tmap import Node, TacMap
from engine.tactical.units import PINNED, WEAPONS, Unit, prof_ammo, prof_melee, prof_sigma, prof_supp

MAX_RANGE = 80.0
MARCH_AP = 6
DETECT_NEAR = 15.0
DETECT_FAR = 100.0
DETECT_FAR_P = 0.05
SUPP_HIT = 35.0
SUPP_NEAR = 20.0
SUPP_NEAR_R = 1.5
SUPP_COVER = 15.0
SUPP_COVER_R = 2.0
DECAY_SEEN = 20.0
DECAY_UNSEEN = 30.0
DOWN_TIMER = 12
SUPPRESS_SPREAD = 3.0
FOV_FRONT = 60.0
FOV_SIDE = 120.0
FOV_K = (1.0, 0.5, 0.2)
HEAR_RANGE = 4.0
HEAR_P = 0.3
HEAR_MOVING = 2.0

GRENADE_RANGE = 20.0
GRENADE_SIGMA = 0.12          # 落點散布（相對距離）
GRENADE_KILL_R = 1.0
GRENADE_WOUND_R = 4.0
GRENADE_WOUND_P = 0.6         # 殺傷半徑邊緣以內、未被遮擋時受傷的最高機率
GRENADE_SUPP = 60.0
GRENADE_SUPP_R = 6.0

SMOKE_RANGE = 25.0
SMOKE_RADIUS = 3.0
SMOKE_HEIGHT = 3.5
SMOKE_FULL = 2.0              # 穿越 2 m 煙霧 → 一般光學完全遮蔽
SMOKE_THERMAL = 0.3           # 熱感測穿透：煙霧最多讓可見程度降到原本的 30%
SMOKE_LIFE = 6
SMOKE_FADE = 2

MORALE_START = 70.0
MORALE_FLOOR_LINK = 10.0
MORALE_SHAKEN = 45.0
MORALE_ROUT = 20.0
MORALE_SURRENDER = 5.0
SURRENDER_P = 0.02


@dataclass
class Plan:
    mode: str = "idle"            # idle / move / shoot / overwatch / posture / melee / grenade / aid / drag
    #                               / take（撿取倒地／陣亡者或敵兵的投擲物）/ give（交給相鄰隊友）
    #                               / brace（架槍）/ flash（閃光彈）
    item: str | None = None       # take／give：grenades／smokes／flashes；None＝缺什麼拿什麼
    path: list[Node] = field(default_factory=list)
    target: str | None = None     # 單位 id
    point: Vec | None = None      # 壓制目標點
    fire: str = "aimed"           # aimed / burst / suppress
    aim_part: str | None = None
    posture: str | None = None
    watch: tuple[float, float] | None = None   # 負責警戒的方向（扇區中心）


@dataclass
class SideIntel:
    spotted: dict = field(default_factory=dict)   # unit id -> tick
    last_known: dict = field(default_factory=dict)  # unit id -> (node, tick)
    believed_seen: dict = field(default_factory=dict)  # 本方個體 id -> (tick, 原因)：本方推算「這人已被敵方看到」


BELIEF_TTL = 4              # 沒有新線索 N tick 後回到「應該還沒被發現」
BELIEF_SPREAD = 4           # 中彈／掠過：附近 N 格內的隊友也視為暴露
WATCHED_RANGE = 20.0        # (c) 被盯上：看得到的敵人在 20 m 內
WATCHED_ANGLE = 30.0        #     且面朝我 ±30°
REASON_RANK = {"hit": 4, "near_miss": 3, "fired": 2, "watched": 1}

FLASH_NEAR = 2.0            # 閃光：2 m 內全強度
FLASH_FAR = 8.0             # 8 m 外無光學效果
FLASH_FACE = (1.0, 0.6, 0.3)  # 正面 ±60°／側面 ±120°／背對
FLASH_STRONG = 0.6          # 強度 ≥ → 過載 2 tick
FLASH_WEAK = 0.25           # 強度 ≥ → 過載 1 tick
FLASH_SOUND_R = 6.0         # 聲響（可穿牆，同層）
FLASH_SUPP = 15.0
DAZZLE_DETECT = 0.3         # 光學過載時發現機率 ×0.3（熱感測保底，不會完全失明）；
#                             瞄準／連發只有 30% 機會照常，否則只能朝大概方向盲射（壓制模式）

BRACE_ARC = 45.0            # 架槍扇區 ±45°
BRACE_REST = 0.6            # 有依託時散布倍率
BRACE_FREE = 0.8            # 無依託
BRACE_BREAK_SUPP = 50       # 壓制達此值 → 架槍中斷


class Battle:
    def __init__(self, tmap: TacMap, units: list[Unit], seed: int = 0, linked: dict | None = None,
                 morale: dict | None = None):
        self.map = tmap
        self.units: dict[str, Unit] = {u.id: u for u in units}
        self.seed = seed
        self.tick = 0
        sides = {u.side for u in units}
        self.intel: dict[str, SideIntel] = {s: SideIntel() for s in sides}
        self.linked: dict[str, bool] = {s: True for s in sides} | (linked or {})
        self.morale: dict[str, float] = {s: MORALE_START for s in sides} | (morale or {})
        self.morale_log: list[tuple[int, str, float, str]] = []
        self.used: dict[str, set[str]] = {}
        self.blasts: list[dict] = []
        self.smokes: list[dict] = []
        self._sight_cache: dict = {}
        self.flanked: dict[str, int] = {}
        self.last_shots: list[dict] = []
        self._shot_mark = 0
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

    # ------------------------------------------------------------------ smoke

    def active_smokes(self) -> list[dict]:
        return [s for s in self.smokes if s["born"] < self.tick <= s["expires"]]

    def smoke_density(self, s: dict) -> float:
        left = s["expires"] - self.tick
        return 1.0 if left >= SMOKE_FADE else max(0.0, (left + 1) / (SMOKE_FADE + 1))

    def smoke_thickness(self, a: Vec, b: Vec) -> float:
        """線段穿越煙霧的等效厚度（m），用水平圓柱近似。"""
        total = 0.0
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        for s in self.active_smokes():
            if min(a[2], b[2]) > s["top"]:
                continue
            cx, cy, r = s["x"], s["y"], s["r"]
            if seg2 < 1e-9:
                inside = (ax - cx) ** 2 + (ay - cy) ** 2 <= r * r
                total += (r if inside else 0.0) * self.smoke_density(s)
                continue
            fx, fy = ax - cx, ay - cy
            qa = seg2
            qb = 2 * (fx * dx + fy * dy)
            qc = fx * fx + fy * fy - r * r
            disc = qb * qb - 4 * qa * qc
            if disc <= 0:
                continue
            sq = math.sqrt(disc)
            t0 = max(0.0, (-qb - sq) / (2 * qa))
            t1 = min(1.0, (-qb + sq) / (2 * qa))
            if t1 > t0:
                total += (t1 - t0) * math.sqrt(seg2) * self.smoke_density(s)
        return total

    def sight(self, a: Vec, b: Vec) -> float:
        """地圖視線 × 煙霧（熱感測保留最多 30%）。"""
        base = self.map.sight(a, b)
        if base <= 0 or not self.smokes:
            return base
        key = (self.tick, tuple(round(v, 3) for v in a), tuple(round(v, 3) for v in b))
        cached = self._sight_cache.get(key)
        if cached is not None:
            return cached
        thick = self.smoke_thickness(a, b)
        if thick <= 0:
            value = base
        else:
            optical = max(0.0, 1.0 - thick / SMOKE_FULL)
            value = base * max(optical, SMOKE_THERMAL)
        if len(self._sight_cache) > 200000:
            self._sight_cache.clear()
        self._sight_cache[key] = value
        return value

    def throw_smoke(self, thrower: Unit, point: Vec, action: str) -> dict | None:
        if not thrower.alive or thrower.smokes <= 0:
            return None
        eye = thrower.eye(self.map)
        dist = length(sub(point, eye))
        if dist > SMOKE_RANGE:
            return None
        sig = GRENADE_SIGMA * (1.5 if thrower.suppressed else 1.0)
        dx = self.g("smoke", thrower.id, action, "x") * sig * dist
        dy = self.g("smoke", thrower.id, action, "y") * sig * dist
        land = (point[0] + dx, point[1] + dy)
        thrower.smokes -= 1
        cloud = {"x": land[0], "y": land[1], "r": SMOKE_RADIUS, "top": SMOKE_HEIGHT,
                 "born": self.tick, "expires": self.tick + SMOKE_LIFE, "by": thrower.id}
        self.smokes.append(cloud)
        self.log.append(f"T{self.tick} {thrower.id} 投擲煙霧彈")
        return cloud

    def recent_incoming(self, side: str) -> list[dict]:
        """上一 tick 射向本方（命中或擦過）的子彈：受害者感受得到來彈方向，不知道射手確切位置。"""
        out = []
        for s in self.last_shots:
            shooter = self.units.get(s["shooter"])
            if shooter is None or shooter.side == side:
                continue
            if s["kind"] == "body" and self.units[s["ref"][0]].side == side:
                out.append(s)
                continue
            for u in self.alive(side):
                pt, _ = closest_point_on_segment(s["from"], s["to"], u.torso(self.map))
                if length(sub(pt, u.torso(self.map))) < SUPP_NEAR_R:
                    out.append(s)
                    break
        return out

    def is_targeted(self, u: Unit) -> bool:
        """上一 tick 被命中，或壓制值 ≥ 30（自己感受得到正被射擊）。"""
        if u.suppression >= 30:
            return True
        return any(s["kind"] == "body" and s["ref"][0] == u.id for s in self.last_shots)

    def visibility(self, observer: Unit, target: Unit) -> float:
        e = observer.eye(self.map)
        pts = target.samples(self.map)
        return sum(self.sight(e, p) for p in pts.values()) / len(pts)

    def visible_parts(self, observer: Unit, target: Unit) -> list[str]:
        """可瞄準的部位。光學過載時只剩熱影像：只有清楚暴露（可見程度 > 0.6）的部位可瞄準。"""
        e = observer.eye(self.map)
        need = 0.6 if observer.dazzled else 0.3
        return [k for k, p in target.samples(self.map).items() if self.sight(e, p) > need]

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
            if self.sight(eye, torso) > 0.3:
                out.append(eid)
        return out

    # ------------------------------------------------------------------ detection

    def fov_factor(self, observer: Unit, target: Unit) -> float:
        """視野方向：負責扇區（watch）或面向 ±60° 內 1.0、±120° 內 0.5、背後 0.2。"""
        d = observer.watch if observer.watch is not None else observer.facing
        tx = target.pos[0] - observer.pos[0]
        ty = target.pos[1] - observer.pos[1]
        if (tx == 0 and ty == 0) or (d[0] == 0 and d[1] == 0):
            return FOV_K[0]
        cos = (d[0] * tx + d[1] * ty) / (math.hypot(*d) * math.hypot(tx, ty))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if ang <= FOV_FRONT:
            return FOV_K[0]
        if ang <= FOV_SIDE:
            return FOV_K[1]
        return FOV_K[2]

    @staticmethod
    def detect_range_factor(dist: float) -> float:
        """15 m 內 1.0；之後線性下降到 100 m 時 0.05；更遠維持 0.05。"""
        if dist <= DETECT_NEAR:
            return 1.0
        if dist >= DETECT_FAR:
            return DETECT_FAR_P
        return 1.0 - (1.0 - DETECT_FAR_P) * (dist - DETECT_NEAR) / (DETECT_FAR - DETECT_NEAR)

    def detect(self, phase: str, movers: set[str] = frozenset()):
        for side in sorted(self.intel):
            observers = [u for u in self.alive(side)]
            for t in sorted(self.enemies_of(side), key=lambda u: u.id):
                vis_any = 0.0
                miss = 1.0
                hear_miss = 1.0
                moving = t.id in movers or t.moved
                for o in observers:
                    gap = math.hypot(*(a - b for a, b in zip(o.base(self.map)[:2], t.base(self.map)[:2])))
                    if gap <= HEAR_RANGE and abs(o.pos[2] - t.pos[2]) <= 1:
                        hear_miss *= 1 - min(0.9, HEAR_P * (HEAR_MOVING if moving else 1.0))
                    v = self.visibility(o, t)
                    if v <= 0:
                        continue
                    if o.dazzled:
                        v *= DAZZLE_DETECT
                    vis_any = max(vis_any, v)
                    dist = length(sub(o.eye(self.map), t.torso(self.map)))
                    k = self.detect_range_factor(dist)
                    p = v * k * (1.5 if moving else 1.0) * self.fov_factor(o, t)
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
                if vis_any > 0 and self.r("detect", side, t.id) < 1 - miss:
                    intel.spotted[t.id] = self.tick
                    intel.last_known[t.id] = (t.pos, self.tick)
                    self.log.append(f"T{self.tick} {side} 發現 {t.id}")
                    continue
                if hear_miss < 1.0 and self.r("hear", side, t.id) < 1 - hear_miss:
                    known = intel.last_known.get(t.id)
                    if not known or known[0] != t.pos or known[1] < self.tick:
                        intel.last_known[t.id] = (t.pos, self.tick)
                        if not known or known[0] != t.pos:
                            self.log.append(f"T{self.tick} {side} 察覺 {t.id}（聲音）")

    # ------------------------------------------------------------------ shooting

    def in_brace_arc(self, u: Unit, point: Vec) -> bool:
        if not u.braced:
            return False
        e = u.eye(self.map)
        dx, dy = point[0] - e[0], point[1] - e[1]
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return True
        bx, by = u.braced
        cos = (bx * dx + by * dy) / (math.hypot(bx, by) * math.hypot(dx, dy))
        return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= BRACE_ARC

    def has_rest(self, u: Unit, direction: tuple[float, float]) -> bool:
        """依託：臥姿，或面向方向那一側的格內有矮牆、窗台、瓦礫。"""
        if u.posture == "prone":
            return True
        x0, y0 = self.map.cell_lo(u.pos[0], u.pos[1])
        x1, y1 = x0 + self.map.cell, y0 + self.map.cell
        z = u.pos[2] * 3.0
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for b in self.map._boxes_in_cell(u.pos[0], u.pos[1]):
            if b.tag not in ("lowwall", "window", "rubble") or b.hi[2] <= z or b.lo[2] >= z + 1.6:
                continue
            bx, by = (b.lo[0] + b.hi[0]) / 2 - cx, (b.lo[1] + b.hi[1]) / 2 - cy
            if b.tag == "rubble" or bx * direction[0] + by * direction[1] > 0:
                return True
        return False

    def sigma(self, shooter: Unit, dist: float, overwatch: bool = False, aim: Vec | None = None) -> float:
        w = WEAPONS[shooter.weapon]
        s = w["sigma"] * prof_sigma(shooter.proficiency())
        braced = aim is not None and not shooter.moved and self.in_brace_arc(shooter, aim)
        if braced:
            s *= BRACE_REST if shooter.brace_rest else BRACE_FREE
        if shooter.dazzled:
            s *= 2.0
        if dist > w["range"]:
            s *= 1 + (dist - w["range"]) / w["range"]
        if shooter.moved:
            s *= w["move_mult"]
        s *= {"stand": 1.0, "crouch": 0.85, "prone": 0.7}[shooter.posture]
        if shooter.pinned:
            s *= 4.0
        elif shooter.suppressed:
            s *= 1.5
        for arm in ("larm", "rarm"):
            if shooter.damage.get(arm):
                s *= 1.6
        if overwatch and not braced:
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
        base_sigma = self.sigma(shooter, dist, overwatch, aim=aim)
        if shooter.braced and not self.in_brace_arc(shooter, aim):
            shooter.braced = None
        pellets = w.get("pellets", 1)
        results = []
        for i in range(rounds):
            if mode == "aimed":
                s = base_sigma
            elif mode == "burst":
                s = base_sigma * (2 + i)
            else:
                s = base_sigma * SUPPRESS_SPREAD
            if pellets > 1:
                group = []
                center = self.trace(shooter, eye, aim, s * 0.5, action, i)
                for j in range(pellets):
                    shot = self.trace(shooter, eye, center["to"], w["sigma"], action, i * 100 + j + 1)
                    shot["pellet"] = True
                    group.append(shot)
                results.append({"group": group, "round": i})
            else:
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
        k = WEAPONS[shooter.weapon]["supp_k"] * prof_supp(shooter.proficiency())
        if shot.get("pellet"):
            k /= 4
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
                self.used.setdefault(shooter.id, set()).add(shooter.weapon)
                traces = s["group"] if "group" in s else [s]
                hit_any = False
                for j, tr in enumerate(traces):
                    self.shots.append(tr)
                    for uid, val in self.suppression_from(tr, shooter).items():
                        supp[uid] = supp.get(uid, 0.0) + val
                        seen = self.heavy_fire.setdefault(shooter.side, {})
                        seen[uid] = seen.get(uid, 0.0) + val
                    if tr["kind"] == "body":
                        hits.append((shooter.id, tr["ref"][0], tr["ref"][1], i * 100 + j))
                        hit_any = True
                s["hit_any"] = hit_any
            if shots:
                cost = len(shots) * prof_ammo(shooter.proficiency())
                frac = cost - int(cost)
                spent = int(cost) + (1 if self.r("ammo", shooter.id, len(self.shots)) < frac else 0)
                shooter.ammo = max(0, shooter.ammo - max(1, spent))
                shooter.fired = True
        flanked_sides = set()
        for shooter_id, uid, part, i in hits:
            target = self.units[uid]
            shooter = self.units[shooter_id]
            if target.side != shooter.side:
                shooter.stats["hits"] += 1
                if self._flanked(target, shooter) and target.side not in flanked_sides:
                    flanked_sides.add(target.side)
                    self.flanked[target.side] = self.tick
                    self._morale_event(target.side, -10, "遭側翼攻擊")
            if target.dead:
                continue
            if target.down:
                self._kill(target, shooter, "倒地後再被擊中")
                continue
            self._damage(target, part, shooter, i)
        for uid, val in supp.items():
            self.units[uid].suppression = min(100.0, self.units[uid].suppression + val)

    # ------------------------------------------------------------------ grenade / melee

    def arc_clear(self, eye: Vec, land: Vec) -> bool:
        """擲物拋物線（與 throw 相同的三段近似）到落點是否不被擋。"""
        dist = length(sub(land, eye))
        pre = (land[0] + (eye[0] - land[0]) * 0.08, land[1] + (eye[1] - land[1]) * 0.08, land[2] + 2.5)
        apex = ((eye[0] + land[0]) / 2, (eye[1] + land[1]) / 2, max(eye[2], land[2]) + max(2.5, dist * 0.45))
        for a, b in ((eye, apex), (apex, pre), (pre, land)):
            d = sub(b, a)
            seg = length(d)
            if seg > 1e-6 and self.map.raycast(a, norm(d), seg):
                return False
        return True

    def throw(self, thrower: Unit, point: Vec, action: str, item: str = "grenades") -> dict | None:
        """手榴彈／閃光彈：拋物線近似為「到落點的直線不被牆擋」；落點有散布；碰到牆就彈回牆前。"""
        if not thrower.can_throw(item):
            return None
        eye = thrower.eye(self.map)
        dist = length(sub(point, eye))
        if dist > GRENADE_RANGE:
            return None
        sig = GRENADE_SIGMA * (1.5 if thrower.suppressed else 1.0) * (1.6 if thrower.damage.get("rarm") else 1.0)
        dx = self.g("gren", thrower.id, action, "x") * sig * dist
        dy = self.g("gren", thrower.id, action, "y") * sig * dist
        land = (point[0] + dx, point[1] + dy, point[2])
        pre = (land[0] + (eye[0] - land[0]) * 0.08, land[1] + (eye[1] - land[1]) * 0.08, land[2] + 2.5)
        apex = ((eye[0] + land[0]) / 2, (eye[1] + land[1]) / 2, max(eye[2], land[2]) + max(2.5, dist * 0.45))
        for a, b in ((eye, apex), (apex, pre), (pre, land)):
            d = sub(b, a)
            seg = length(d)
            hit = self.map.raycast(a, norm(d), seg) if seg > 1e-6 else None
            if hit:
                land = add(a, mul(norm(d), max(0.0, hit[0] - 0.3)))
                land = (land[0], land[1], max(land[2], self.map.floor_z((int(land[0] // self.map.cell),
                                                                         int(land[1] // self.map.cell), 0))))
                break
        land = (land[0], land[1], max(0.0, land[2]) + 0.1)
        setattr(thrower, item, getattr(thrower, item) - 1)
        thrower.fired = True
        return {"thrower": thrower.id, "at": land, "item": item}

    def flash(self, blast: dict) -> tuple[dict[str, int], dict[str, float]]:
        """閃光彈：光學過載（不能穿牆；距離、遮蔽、面向遞減）＋聲響壓制（同層 6 m 內，可穿牆）。
        強度 = 距離（2 m 內 1.0 → 8 m 為 0）× 爆點到眼睛的可見程度（牆 0、矮牆／窗框部分、煙霧與植被遞減）× 面向。
        投擲方的隊友預期爆閃（會轉頭）→ 面向一律以「背對」計，但仍受幾何影響（太近、正在門口仍可能被閃）。
        回傳 ({uid: 過載 tick}, {uid: 壓制})。架槍中被閃 → 多 1 tick。"""
        at = blast["at"]
        out: dict[str, int] = {}
        supp: dict[str, float] = {}
        thrower = self.units.get(blast.get("thrower", ""))
        for u in sorted(self.units.values(), key=lambda x: x.id):
            if not u.alive:
                continue
            expected = thrower is not None and u.side == thrower.side
            eye = u.eye(self.map)
            d = length(sub(eye, at))
            if d <= FLASH_SOUND_R and abs(eye[2] - at[2]) < 2.5:
                supp[u.id] = FLASH_SUPP * (1 - d / FLASH_SOUND_R)
            if d >= FLASH_FAR:
                continue
            dist_k = 1.0 if d <= FLASH_NEAR else 1.0 - (d - FLASH_NEAR) / (FLASH_FAR - FLASH_NEAR)
            los = self.sight(at, eye)
            if los <= 0:
                continue
            f = u.watch if u.watch is not None else (u.braced or u.facing)
            dx, dy = at[0] - eye[0], at[1] - eye[1]
            face = FLASH_FACE[0]
            if math.hypot(dx, dy) > 0.3 and math.hypot(*f) > 1e-6:
                cos = (f[0] * dx + f[1] * dy) / (math.hypot(*f) * math.hypot(dx, dy))
                ang = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                face = FLASH_FACE[0] if ang <= 60 else (FLASH_FACE[1] if ang <= 120 else FLASH_FACE[2])
            if expected:
                face = min(face, FLASH_FACE[2])
            k = dist_k * los * face
            n = 2 if k >= FLASH_STRONG else (1 if k >= FLASH_WEAK else 0)
            if n and u.braced:
                n += 1
            if n:
                out[u.id] = n
        return out, supp

    def explode(self, blast: dict) -> tuple[list[tuple[str, str]], dict[str, float]]:
        at = blast["at"]
        hurt: list[tuple[str, str]] = []
        supp: dict[str, float] = {}
        for u in sorted(self.units.values(), key=lambda x: x.id):
            if u.dead or u.exited:
                continue
            torso = u.torso(self.map)
            d = length(sub(torso, at))
            if d > GRENADE_SUPP_R:
                continue
            if self.map.blocked(at, torso):
                continue
            if d <= GRENADE_KILL_R:
                hurt.append((u.id, "torso2"))
            elif d <= GRENADE_WOUND_R:
                p = GRENADE_WOUND_P * (1 - (d - GRENADE_KILL_R) / (GRENADE_WOUND_R - GRENADE_KILL_R))
                if u.posture == "prone":
                    p *= 0.5
                if self.r("frag", blast["thrower"], u.id, round(at[0], 2), round(at[1], 2)) < p:
                    parts = ("torso", "lleg", "rleg", "larm", "rarm", "head")
                    pick = parts[int(self.r("fragpart", blast["thrower"], u.id) * len(parts))]
                    hurt.append((u.id, pick))
            supp[u.id] = GRENADE_SUPP * (1 - d / GRENADE_SUPP_R)
        return hurt, supp

    def melee(self, a: Unit, b: Unit, action: str) -> tuple[str, str] | None:
        """近戰：相鄰格，雙方擲骰比較；輸方倒地。回傳 (勝者 id, 敗者 id) 或 None（平手）。"""
        def score(u: Unit, attacker: bool, other: Unit) -> float:
            s = WEAPONS[u.weapon].get("melee", 1.0) * prof_melee(u.proficiency())
            if u.pinned:
                s *= 0.2
            elif u.suppressed:
                s *= 0.5
            if u.damage.get("lleg") or u.damage.get("rleg"):
                s *= 0.7
            if attacker and (not self.is_spotted(other.side, u) or self._flanked(other, u)):
                s *= 1.5
            return s * (0.5 + self.r("melee", u.id, other.id, action))
        sa, sb = score(a, True, b), score(b, False, a)
        if abs(sa - sb) < 0.05:
            return None
        return (a.id, b.id) if sa > sb else (b.id, a.id)

    def _flanked(self, target: Unit, attacker: Unit) -> bool:
        fx, fy = target.facing
        ax, ay = attacker.pos[0] - target.pos[0], attacker.pos[1] - target.pos[1]
        if (fx, fy) == (0, 0) or (ax, ay) == (0, 0):
            return False
        cos = (fx * ax + fy * ay) / (math.hypot(fx, fy) * math.hypot(ax, ay))
        return cos < 0.0

    @staticmethod
    def adjacent(a: Node, b: Node) -> bool:
        return a[2] == b[2] and max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1

    # ------------------------------------------------------------------ damage / down

    def _kill(self, target: Unit, by: Unit | None, why: str):
        if target.dead:
            return
        target.dead = True
        target.down = True
        self.log.append(f"T{self.tick} {target.id} 離線（{why}）")
        self._morale_event(target.side, -15, f"{target.id} 陣亡")

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
        target.down_tick = self.tick
        if shooter.side != target.side:
            shooter.stats["kills"] += 1
            self._morale_event(shooter.side, +8, f"擊倒 {target.id}")
        self.log.append(f"T{self.tick} {target.id} 倒地（{shooter.id}）")
        self._morale_event(target.side, -25 if target.leader else -15,
                           f"{'隊長 ' if target.leader else ''}{target.id} 倒地")

    # ------------------------------------------------------------------ morale

    def _morale_event(self, side: str, delta: float, why: str):
        if side not in self.morale:
            return
        self.morale[side] = max(0.0, min(100.0, self.morale[side] + delta))
        self.morale_log.append((self.tick, side, delta, why))

    def effective_morale(self, side: str) -> float:
        return self.morale.get(side, MORALE_START) + (MORALE_FLOOR_LINK if self.linked.get(side, True) else 0.0)

    def morale_state(self, side: str) -> str:
        m = self.effective_morale(side)
        if m <= MORALE_SURRENDER:
            return "surrender"
        if m < MORALE_ROUT:
            return "rout"
        if m < MORALE_SHAKEN:
            return "shaken"
        return "normal"

    def _morale_tick(self):
        for side in sorted(self.morale):
            own = self.alive(side)
            if not own:
                continue
            foes = self.enemies_of(side)
            supp = sum(u.suppression for u in own) / len(own)
            if supp > 50:
                self._morale_event(side, -5, "持續被壓制")
            if foes and len(own) * 2 < len(foes):
                self._morale_event(side, -5, "兵力劣勢")
            if not any(self.sight(e.eye(self.map), u.torso(self.map)) > 0 for e in foes for u in own) \
                    and supp < 20:
                self._morale_event(side, +2, "掩蔽中、未受威脅")
            if self.morale_state(side) == "surrender":
                for u in sorted(own, key=lambda x: x.id):
                    if self.r("surrender", u.id) < SURRENDER_P:
                        u.surrendered = True
                        self.log.append(f"T{self.tick} {u.id} 投降")

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
        self.last_shots = self.shots[self._shot_mark:]
        self._shot_mark = len(self.shots)
        for u in self.units.values():
            u.moved = False
            u.fired = False
        self.face_threats([u for u in self.units.values()
                           if not (plans.get(u.id) and plans[u.id].mode == "move")])
        snap1 = {uid: u.pos for uid, u in self.units.items()}
        pre_state = {uid: (u.posture, sum(1 for v in u.damage.values() if v), u.torso_hits)
                     for uid, u in self.units.items()}
        for uid, p in plans.items():
            u = self.units[uid]
            if u.alive and p.mode == "posture" and p.posture:
                u.posture = p.posture
        for u in self.units.values():
            p = plans.get(u.id)
            u.watch = p.watch if p is not None and p.mode in ("overwatch", "idle", "posture", "brace") else None
            if u.watch is None and u.braced and (p is None or p.mode in ("overwatch", "idle", "shoot")):
                u.watch = u.braced
        bracing = {uid: p for uid, p in plans.items()
                   if p.mode == "brace" and self.units[uid].alive and not self.units[uid].pinned}

        movers = {uid: list(p.path) for uid, p in plans.items()
                  if p.mode in ("move", "drag", "take") and p.path and self.units[uid].alive}
        dragging = {uid: p.target for uid, p in plans.items()
                    if p.mode == "drag" and p.target and uid in movers
                    and self.units[p.target].down and not self.units[p.target].dead
                    and (self.units[p.target].pos == self.units[uid].pos
                         or self.adjacent(self.units[p.target].pos, self.units[uid].pos))}
        self.last_drags = dict(dragging)
        exits = {uid for uid, p in plans.items()
                 if (p.mode == "move" and p.target == "__exit__") or (p.mode == "drag" and p.posture == "__exit__")}
        for uid, p in plans.items():
            u = self.units[uid]
            if uid in exits and u.alive and p.path == [u.pos] and self.on_edge(u.pos):
                u.exited = True
                if uid in dragging:
                    self.units[dragging[uid]].exited = True
                self.log.append(f"T{self.tick} {uid} 撤出戰區")
        movers = {uid: path for uid, path in movers.items() if not self.units[uid].exited}
        overwatch = {uid for uid, p in plans.items()
                     if p.mode in ("overwatch", "idle") and self.units[uid].alive and not self.units[uid].pinned
                     and (p.mode == "overwatch" or self.units[uid].braced)}
        overwatch -= {uid for uid in overwatch if self.units[uid].dazzled}
        ow_used: set[str] = set()
        budget = {uid: self.move_budget(self.units[uid]) for uid in movers}
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
                cost = self._move_cost(u, nxt, uid in dragging)
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
                cost = self._move_cost(u, node, winner in dragging)
                dx, dy = node[0] - u.pos[0], node[1] - u.pos[1]
                if dx or dy:
                    u.facing = (dx, dy)
                prev = u.pos
                u.pos = node
                u.moved = True
                budget[winner] -= cost
                movers[winner].pop(0)
                moved_now.append(winner)
                if winner in dragging:
                    self.units[dragging[winner]].pos = prev
                if winner in exits and not movers[winner] and self.on_edge(node):
                    u.exited = True
                    if winner in dragging:
                        self.units[dragging[winner]].exited = True
                    self.log.append(f"T{self.tick} {winner} 撤出戰區")
            for uid in list(movers):
                if uid not in moved_now or not movers[uid] or budget[uid] <= 0:
                    movers.pop(uid, None)
            if not moved_now:
                break
            self.detect(f"move{step_no}", set(moved_now))
            # 已架槍的警戒者先開火（先套用），未架槍的再以更新後的狀態開火
            for braced_pass in (True, False):
                batch = []
                for wid in sorted(overwatch - ow_used):
                    watcher = self.units[wid]
                    if bool(watcher.braced) != braced_pass:
                        continue
                    if not watcher.alive or watcher.pinned or not watcher.can_shoot():
                        continue
                    cands = [self.units[m] for m in moved_now
                             if self.units[m].side != watcher.side and self.units[m].alive
                             and self.is_spotted(watcher.side, self.units[m])
                             and self.visible_parts(watcher, self.units[m])]
                    if not cands:
                        continue
                    target = min(cands, key=lambda t: (not self.in_brace_arc(watcher, t.torso(self.map)),
                                                       length(sub(t.torso(self.map), watcher.eye(self.map))), t.id))
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

        # 快照 2：射擊、投擲、近戰、急救都以此刻狀態計算，再一起套用
        results = []
        blasts = []
        bouts = []
        aids = []
        smoke_throws = []
        transfers = []
        flashes = []
        for uid in sorted(plans):
            p = plans[uid]
            u = self.units[uid]
            if not u.alive:
                continue
            if p.mode == "smoke" and p.point is not None:
                smoke_throws.append((u, p.point))
                continue
            if p.mode == "grenade" and p.point is not None and not u.pinned:
                g = self.throw(u, p.point, "gren")
                if g:
                    blasts.append(g)
                continue
            if p.mode == "flash" and p.point is not None and not u.pinned:
                g = self.throw(u, p.point, "flash", item="flashes")
                if g:
                    flashes.append(g)
                continue
            if p.mode == "melee" and p.target:
                t = self.units.get(p.target)
                if t and t.alive and self.adjacent(u.pos, t.pos):
                    bouts.append((u, t))
                continue
            if p.mode == "aid" and p.target:
                t = self.units.get(p.target)
                if t and t.down and not t.dead and t.side == u.side and \
                        (t.pos == u.pos or self.adjacent(u.pos, t.pos)):
                    aids.append(t)
                continue
            if p.mode in ("take", "give") and p.target:
                transfers.append((u, p))
                continue
            if p.mode != "shoot":
                continue
            if u.pinned and p.fire != "suppress" and not self._in_cover(u):
                continue
            target = self.units.get(p.target) if p.target else None
            if target is not None and p.fire != "suppress" and not self.is_spotted(u.side, target):
                continue
            if u.dazzled and p.fire != "suppress" and self.r("dazzle_fire", u.id, self.tick) >= DAZZLE_DETECT:
                p = Plan(mode="shoot", fire="suppress", point=target.torso(self.map) if target else p.point)
                if p.point is None:
                    continue
            shots = self.fire(u, p, "shot")
            if shots:
                results.append((u, shots))
        melee_out = []
        for a, b in bouts:
            res = self.melee(a, b, "melee")
            melee_out.append((a, b, res))
        self.apply(results)
        for a, b, res in melee_out:
            self.log.append(f"T{self.tick} {a.id} 突擊 {b.id}")
            self.used.setdefault(a.id, set()).add(a.weapon)
            if res is None:
                for x, y in ((a, b), (b, a)):
                    if not x.dead:
                        self._damage(x, "torso", y, 9001)
            else:
                winner, loser = self.units[res[0]], self.units[res[1]]
                if not loser.down:
                    self._down(loser, winner)
            a.fired = True
        g_supp: dict[str, float] = {}
        g_hurt: list[tuple[str, str, str]] = []
        for bl in blasts:
            self.blasts.append(bl)
            self.log.append(f"T{self.tick} {bl['thrower']} 投擲手榴彈")
            hurt, supp = self.explode(bl)
            for uid, part in hurt:
                g_hurt.append((bl["thrower"], uid, part))
            for uid, val in supp.items():
                g_supp[uid] = g_supp.get(uid, 0.0) + val
        for thrower_id, uid, part in g_hurt:
            target, thrower = self.units[uid], self.units[thrower_id]
            if target.dead:
                continue
            if target.down:
                if part == "torso2":
                    self._kill(target, thrower, "手榴彈")
            elif part == "torso2":
                self._damage(target, "torso", thrower, 9100)
                if not target.down:
                    self._damage(target, "torso", thrower, 9101)
            else:
                self._damage(target, part, thrower, 9102)
        for uid, val in g_supp.items():
            u = self.units[uid]
            if u.alive:
                u.suppression = min(100.0, u.suppression + val)
        for u in self.units.values():
            if u.dazzled:
                u.dazzled -= 1
        for fl in flashes:
            self.blasts.append(fl)
            self.log.append(f"T{self.tick} {fl['thrower']} 投擲閃光彈")
            dazed, f_supp = self.flash(fl)
            for uid, n in dazed.items():
                u = self.units[uid]
                if u.alive:
                    u.dazzled = max(u.dazzled, n)
                    self.log.append(f"T{self.tick} {uid} 光學過載（{n}）")
            for uid, val in f_supp.items():
                u = self.units[uid]
                if u.alive:
                    u.suppression = min(100.0, u.suppression + val)
        for u, point in smoke_throws:
            self.throw_smoke(u, point, "smoke")
        self.smokes = [s for s in self.smokes if s["expires"] >= self.tick]
        for t in aids:
            if not t.dead and not t.stabilized:
                t.stabilized = True
                self.log.append(f"T{self.tick} {t.id} 已被穩定")
        for u, p in transfers:
            self.transfer_items(u, p)
        for uid, tid in dragging.items():
            u, t = self.units[uid], self.units[tid]
            if u.alive and t.throwables() and u.room():
                self.transfer_items(u, Plan(mode="take", target=tid))
        self._update_braces(bracing, pre_state)

        for u in self.units.values():
            if u.down and not u.dead and not u.stabilized and u.down_tick is not None \
                    and self.tick - u.down_tick >= DOWN_TIMER:
                self._kill(u, None, "倒地過久")
        self._morale_tick()

        for u in self.units.values():
            if not u.alive:
                continue
            seen = any(self.sight(e.eye(self.map), u.torso(self.map)) > 0
                       for e in self.enemies_of(u.side))
            u.suppression = max(0.0, u.suppression - (DECAY_SEEN if seen else DECAY_UNSEEN))
        for u in self.units.values():
            if u.fired and u.alive:
                for side in self.intel:
                    if side != u.side and any(self.sight(e.eye(self.map), u.torso(self.map)) > 0
                                              for e in self.alive(side)):
                        self.intel[side].spotted[u.id] = self.tick
                        self.intel[side].last_known[u.id] = (u.pos, self.tick)
        for side, intel in self.intel.items():
            for uid in list(intel.last_known):
                if not self.units[uid].alive:
                    intel.last_known.pop(uid, None)
                    intel.spotted.pop(uid, None)
        self.update_beliefs()
        _ = snap1

    # ------------------------------------------------------------------ 架槍

    def _update_braces(self, bracing: dict, pre_state: dict):
        """tick 結束：中斷（移動、換姿勢、壓制 ≥50、中彈、光學過載、倒地），再完成本 tick 的架槍動作。"""
        for uid, u in self.units.items():
            if not u.braced:
                continue
            posture, dmg, torso = pre_state.get(uid, (u.posture, 0, 0))
            hit = sum(1 for v in u.damage.values() if v) > dmg or u.torso_hits > torso
            why = None
            if not u.alive:
                why = "倒地"
            elif u.moved:
                why = "移動"
            elif u.posture != posture:
                why = "換姿勢"
            elif u.suppression >= BRACE_BREAK_SUPP:
                why = "被壓制"
            elif hit:
                why = "中彈"
            elif u.dazzled:
                why = "光學過載"
            if why:
                u.braced = None
                u.brace_rest = False
                self.log.append(f"T{self.tick} {uid} 架槍中斷（{why}）")
        for uid, p in bracing.items():
            u = self.units[uid]
            if not u.alive or u.moved or u.fired or u.dazzled or u.suppression >= BRACE_BREAK_SUPP:
                continue
            d = p.watch or u.watch or u.facing
            n = math.hypot(d[0], d[1])
            if n < 1e-6:
                continue
            u.braced = (d[0] / n, d[1] / n)
            u.brace_rest = self.has_rest(u, u.braced)
            u.facing = self.map.facing_to(u.pos, (u.pos[0] + round(u.braced[0] * 3),
                                                 u.pos[1] + round(u.braced[1] * 3), u.pos[2]))
            self.log.append(f"T{self.tick} {uid} 架槍{'（有依託）' if u.brace_rest else ''}")

    # ------------------------------------------------------------------ 投擲物撿取／傳遞

    def transfer_items(self, u: Unit, p: Plan) -> int:
        """take：從相鄰（或同格）的倒地／陣亡者（敵我皆可）拿投擲物；give：交給相鄰活著的隊友。
        接收者合計不超過 THROW_CAP。回傳移交數量。"""
        from engine.tactical.units import ITEMS, ITEM_LABELS
        t = self.units.get(p.target)
        if t is None or not u.alive or t.exited:
            return 0
        if not (t.pos == u.pos or self.adjacent(u.pos, t.pos)):
            return 0
        if p.mode == "take":
            if t.alive:
                return 0
            giver, taker = t, u
        else:
            if not t.alive or t.side != u.side:
                return 0
            giver, taker = u, t
        items = [p.item] if p.item else list(ITEMS)
        moved = 0
        got = []
        for it in items:
            n = min(getattr(giver, it), taker.room())
            if p.mode == "give" and p.item is None:
                n = min(n, 1)
            if n <= 0:
                continue
            setattr(giver, it, getattr(giver, it) - n)
            setattr(taker, it, getattr(taker, it) + n)
            moved += n
            got.append(f"{ITEM_LABELS[it]}{n}")
        if moved:
            verb = "撿起" if p.mode == "take" else "交給"
            other = t.id
            self.log.append(f"T{self.tick} {u.id} {verb} {other} {'、'.join(got)}")
        return moved

    # ------------------------------------------------------------------ 自我暴露推算

    def _incoming_now(self, side: str) -> list[tuple[str, str]]:
        """本 tick 打向本方的子彈：回傳 [(受害者 id, hit|near_miss)]。受害者感受得到，不需要知道射手。"""
        out = []
        for s in self.shots[self._shot_mark:]:
            shooter = self.units.get(s["shooter"])
            if shooter is None or shooter.side == side:
                continue
            if s["kind"] == "body" and self.units.get(s["ref"][0]) and self.units[s["ref"][0]].side == side:
                out.append((s["ref"][0], "hit"))
                continue
            for u in self.alive(side):
                pt, _ = closest_point_on_segment(s["from"], s["to"], u.torso(self.map))
                if length(sub(pt, u.torso(self.map))) < SUPP_NEAR_R:
                    out.append((u.id, "near_miss"))
        return out

    def _watched_by(self, u: Unit) -> bool:
        """(c) 本方看得到的敵人（spotted）在 20 m 內、面朝我 ±30°、且其視線能看到我的軀幹。"""
        for eid in self.intel[u.side].spotted:
            e = self.units.get(eid)
            if not e or not e.alive:
                continue
            dx, dy = u.pos[0] - e.pos[0], u.pos[1] - e.pos[1]
            dist = math.hypot(dx, dy) * self.map.cell
            if dist > WATCHED_RANGE or dist == 0:
                continue
            f = e.facing
            if f[0] == 0 and f[1] == 0:
                continue
            cos = (f[0] * dx + f[1] * dy) / (math.hypot(*f) * math.hypot(dx, dy))
            if math.degrees(math.acos(max(-1.0, min(1.0, cos)))) > WATCHED_ANGLE:
                continue
            if self.sight(e.eye(self.map), u.torso(self.map)) > 0.3:
                return True
        return False

    def update_beliefs(self):
        """每方只用自己知道的資訊推算「哪些隊員已被敵方看到」。"""
        for side, intel in self.intel.items():
            bs = intel.believed_seen
            fresh: dict[str, str] = {}

            def mark(uid, why):
                if REASON_RANK[why] > REASON_RANK.get(fresh.get(uid, ""), 0):
                    fresh[uid] = why

            for uid, why in self._incoming_now(side):
                mark(uid, why)
                victim = self.units[uid]
                for mate in self.alive(side):
                    if mate.id != uid and max(abs(mate.pos[0] - victim.pos[0]),
                                              abs(mate.pos[1] - victim.pos[1])) <= BELIEF_SPREAD:
                        mark(mate.id, "near_miss")
            for u in self.alive(side):
                if u.fired and self.exposed_to(u.pos, side, u.posture):
                    mark(u.id, "fired")
                if self._watched_by(u):
                    mark(u.id, "watched")
            for uid, why in fresh.items():
                bs[uid] = (self.tick, why)
            for uid in list(bs):
                u = self.units.get(uid)
                if u is None or not u.alive or self.tick - bs[uid][0] >= BELIEF_TTL:
                    del bs[uid]

    def believes_seen(self, u: Unit) -> str | None:
        """本方推算 u 已被看到時回傳原因（hit／near_miss／fired／watched），否則 None。"""
        v = self.intel[u.side].believed_seen.get(u.id)
        return v[1] if v else None

    def believes_any_seen(self, units: list[Unit]) -> bool:
        return any(self.believes_seen(u) for u in units)

    def in_contact(self, side: str) -> bool:
        """本方知道任何活著的敵人（已發現或有最後已知位置），或正在被射擊 → 戰鬥狀態。"""
        intel = self.intel.get(side)
        if intel and any(self.units[uid].alive for uid in intel.last_known):
            return True
        return any(u.suppression > 0 for u in self.alive(side))

    def move_budget(self, u: Unit) -> int:
        """行動點：戰鬥狀態 2；未接敵時以行軍速度移動（MARCH_AP）。"""
        if u is None:
            return 2
        return 2 if self.in_contact(u.side) else MARCH_AP

    def _move_cost(self, u: Unit, nxt: Node, drag: bool = False) -> int:
        cost = self.map.step_cost(u.pos, nxt) or 99
        if u.damage.get("lleg") or u.damage.get("rleg"):
            cost *= 2
        if u.posture == "prone":
            cost *= 2
        if drag:
            cost *= 2
        return cost

    def on_edge(self, node: Node) -> bool:
        return node[0] in (0, self.map.w - 1) or node[1] in (0, self.map.h - 1)

    def proficiency_update(self, engaged: set[str] | None = None):
        """交戰結算：射擊 +0.5、命中 +1、擊倒 +3；有參戰但沒用到的武器，100 以上部分 −1。"""
        engaged = engaged if engaged is not None else {u.id for u in self.units.values()}
        for u in self.units.values():
            if u.id not in engaged:
                continue
            w = u.weapon
            p = u.proficiency(w)
            gain = 0.5 * u.stats["shots"] + 1.0 * u.stats["hits"] + 3.0 * u.stats["kills"]
            if gain:
                k = max(0.0, 1 - p / 150) if p < 100 else max(0.0, 1 - 100 / 150) * 0.3
                u.prof[w] = min(200.0, p + gain * k)
            for other, val in list(u.prof.items()):
                if other not in self.used.get(u.id, set()) and other != w and val > 100:
                    u.prof[other] = max(100.0, val - 1)

    def _in_cover(self, u: Unit) -> bool:
        return any(self.visibility(e, u) < 0.5 for e in self.enemies_of(u.side)) or not self.enemies_of(u.side)

    # ------------------------------------------------------------------ state

    def state(self) -> dict:
        return {
            "tick": self.tick,
            "seed": self.seed,
            "morale": dict(sorted(self.morale.items())),
            "linked": dict(sorted(self.linked.items())),
            "smokes": [dict(s) for s in self.smokes],
            "units": {uid: u.to_dict() for uid, u in sorted(self.units.items())},
            "intel": {s: {"spotted": dict(i.spotted),
                          "last_known": {k: [list(v[0]), v[1]] for k, v in i.last_known.items()}}
                      for s, i in sorted(self.intel.items())},
        }

    def load_state(self, st: dict):
        self.tick = st["tick"]
        self.seed = st["seed"]
        self.morale = dict(st.get("morale", {}))
        self.linked = dict(st.get("linked", {}))
        self.smokes = [dict(s) for s in st.get("smokes", [])]
        self._sight_cache = {}
        self.units = {uid: Unit.from_dict(d) for uid, d in st["units"].items()}
        self.intel = {s: SideIntel(spotted=dict(i["spotted"]),
                                   last_known={k: (tuple(v[0]), v[1]) for k, v in i["last_known"].items()})
                      for s, i in st["intel"].items()}

    def over(self) -> str | None:
        sides = {u.side for u in self.alive()}
        if len(sides) <= 1:
            return next(iter(sides), "none")
        return None
