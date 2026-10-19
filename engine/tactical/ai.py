"""規則式戰術 AI：隊長意圖 → 每人計畫。只讀己方情報（combat.md v0.2 §13）。"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from engine.tactical import body
from engine.tactical.geometry import length, sub
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import Node
from engine.tactical.units import Unit

RISK_W = 6
INFEASIBLE_RISK = 1
CLOSE_BURST = 12.0
HEAVY_FIRE = 50.0
RISK_CAP = {"advance": 1, "supp_advance": 0, "flank": 0}
PATIENCE_STEP = 4
PATIENCE_MAX = 3
RECON_MIN_AMMO = 40
RECON_MAX_AGE = 12
AMBUSH_RELOCATE_AFTER = 6
GRENADE_THROW = 12.0
GRENADE_MIN = 4.0
GRENADE_SAFE = 5.0
SMOKE_REUSE_R = 4.0
SMOKE_RADIUS = 3.0
WAIT_MAX = 3
SMOKE_CLUSTER = 2
GRENADE_COOLDOWN = 3
MAX_THREAT_RANGE = 120.0
PATH_HORIZON = 80.0
GRENADE_LAST_RANGE = 8.0
GRENADE_PER_TICK = {"assault": 2}


@dataclass
class Intent:
    kind: str = "hold"            # hold / advance / supp_advance / flank / ambush / assault / retreat / breach
    goal: Node | None = None
    side: str | None = None       # flank: left / right
    ambush_range: float = 12.0
    toward: tuple[int, int] | None = None   # retreat 方向
    room: int | None = None       # breach：目標房間 id（None＝AI 選）
    prep: str | None = None       # breach：flash／frag／smoke／none（None＝AI 選）
    entry: str | None = None      # breach：one／multi
    tempo: str | None = None      # breach：stack／quick


@dataclass
class GroupState:
    infeasible: list[str] = field(default_factory=list)


class TacticalAI:
    def __init__(self, battle: Battle, side: str):
        self.b = battle
        self.side = side
        self.reports: list[str] = []
        self.shot_at: dict[str, int] = {}
        self.patience = 0
        self.holding = False
        self.routed = False
        self.waits: dict[str, int] = {}
        self.last_throw: dict[str, int] = {}
        self._exp_cache: dict = {}
        self.ambush_state: dict = {"sprung": False}
        from engine.tactical.breach import BreachState
        self.breach = BreachState()

    def new_intent(self):
        """隊長下達新意圖時重置耐心與固守狀態。"""
        from engine.tactical.breach import BreachState
        self.patience = 0
        self.holding = False
        self.breach = BreachState()

    # ------------------------------------------------------------------ knowledge

    def known(self) -> list[Unit]:
        intel = self.b.intel[self.side]
        return [self.b.units[uid] for uid in sorted(intel.last_known) if self.b.units[uid].alive]

    def spotted(self) -> list[Unit]:
        intel = self.b.intel[self.side]
        return [self.b.units[uid] for uid in sorted(intel.spotted) if self.b.units[uid].alive]

    def suppressed_guess(self) -> set[str]:
        """上個 tick 我方火力明顯落在其身邊（看得到彈著）的敵人，推測為被壓制。"""
        seen = self.b.heavy_fire.get(self.side, {})
        return {uid for uid, val in seen.items() if val >= HEAVY_FIRE}

    def threats(self) -> list[tuple[str, Node, str]]:
        sup = self.suppressed_guess()
        out = []
        for e in self.known():
            if e.id in sup:
                continue
            node, _ = self.b.intel[self.side].last_known[e.id]
            out.append((e.id, node, e.posture))
        return out

    def exposure(self, node: Node, threats=None, posture: str = "crouch") -> int:
        """站在 node 時，暴露於幾個已知威脅（同一 tick 內快取）。"""
        threats = self.threats() if threats is None else threats
        key = (self.b.tick, len(self.b.smokes), node, posture, tuple(threats))
        cached = self._exp_cache.get(key)
        if cached is not None:
            return cached
        m = self.b.map
        n = 0
        for _, enode, epost in threats:
            if max(abs(node[0] - enode[0]), abs(node[1] - enode[1])) * m.cell > MAX_THREAT_RANGE:
                continue
            f = m.facing_to(node, enode)
            torso = body.torso_center(m.hug_base(node, f), posture, f)
            eye = body.eye(m.hug_base(enode, m.facing_to(enode, node)), epost)
            if self.b.sight(eye, torso) > 0.3:
                n += 1
        if len(self._exp_cache) > 200000:
            self._exp_cache.clear()
        self._exp_cache[key] = n
        return n

    # ------------------------------------------------------------------ paths

    def paths(self, start: Node, threats, occupied: set[Node],
              max_cost: float = PATH_HORIZON) -> dict[Node, tuple[float, int, list[Node]]]:
        """Dijkstra：成本 = AP + RISK_W × 暴露。回傳 node -> (成本, 累積風險, 路徑)。成本超過 max_cost 的不展開。"""
        dist = {start: (0.0, 0, [])}
        heap = [(0.0, 0, start)]
        seen = set()
        while heap:
            cost, risk, node = heapq.heappop(heap)
            if node in seen:
                continue
            seen.add(node)
            if cost > max_cost:
                continue
            for nxt, step in self.b.map.neighbors(node):
                if nxt in occupied:
                    continue
                exp = self.exposure(nxt, threats)
                c = cost + step + RISK_W * exp
                if nxt not in dist or c < dist[nxt][0]:
                    dist[nxt] = (c, risk + exp, dist[node][2] + [nxt])
                    heapq.heappush(heap, (c, risk + exp, nxt))
        return dist

    @staticmethod
    def ap_len(battle: Battle, start: Node, path: list[Node]) -> list[Node]:
        mover = next((u for u in battle.units.values() if u.alive and u.pos == start), None)
        ap = battle.move_budget(mover) if mover else 2
        out, cur = [], start
        for n in path:
            c = battle.map.step_cost(cur, n) or 2
            if c > ap:
                break
            ap -= c
            out.append(n)
            cur = n
        return out

    # ------------------------------------------------------------------ fire control

    def fire_plan(self, u: Unit) -> Plan | None:
        if not u.can_shoot():
            return None
        targets = [e for e in self.spotted() if self.b.visible_parts(u, e)]
        if not targets:
            return None
        e = min(targets, key=lambda t: (length(sub(t.torso(self.b.map), u.eye(self.b.map))), t.id))
        dist = length(sub(e.torso(self.b.map), u.eye(self.b.map)))
        vis = self.b.visible_parts(u, e)
        mode = "aimed"
        if u.weapon == "lmg":
            mode = "burst"
        elif dist < CLOSE_BURST and len(vis) >= 3:
            mode = "burst"
        part = "head" if vis == ["head"] or ("torso" not in vis and "head" in vis) else None
        self.shot_at[e.id] = self.b.tick + 1
        return Plan(mode="shoot", target=e.id, fire=mode, aim_part=part)

    def focus_order(self, anchor: Node | None) -> list[Unit]:
        """壓制目標順序：所有支援者照同一個順序集中火力（離目標點最近的已知敵人優先）。"""
        known = self.known()
        if anchor is None:
            own = self.b.alive(self.side)
            anchor = own[0].pos if own else (0, 0, 0)
        lk = self.b.intel[self.side].last_known
        return sorted(known, key=lambda t: (self._d(lk[t.id][0], anchor), t.id))

    def suppress_plan(self, u: Unit, anchor: Node | None = None) -> Plan | None:
        if not u.can_shoot():
            return None
        order = self.focus_order(anchor)
        if not order:
            return None
        covered = self.suppressed_guess()
        pending = [t for t in order if t.id not in covered] or order
        e = None
        for t in pending:
            node, _ = self.b.intel[self.side].last_known[t.id]
            f = self.b.map.facing_to(node, u.pos)
            pt = body.torso_center(self.b.map.hug_base(node, f), t.posture, f)
            if self.b.sight(u.eye(self.b.map), pt) > 0 or self._near_los(u, node):
                e = t
                break
        if e is None:
            return None
        node, _ = self.b.intel[self.side].last_known[e.id]
        f = self.b.map.facing_to(node, u.pos)
        point = body.torso_center(self.b.map.hug_base(node, f), e.posture, f)
        self.shot_at[e.id] = self.b.tick + 1
        return Plan(mode="shoot", point=point, fire="suppress",
                    target=e.id if e.id in self.b.intel[self.side].spotted else None)

    def _near_los(self, u: Unit, node: Node) -> bool:
        c = self.b.map.center(node)
        for dz in (0.5, 1.0, 1.4):
            if self.b.sight(u.eye(self.b.map), (c[0], c[1], c[2] + dz)) > 0:
                return True
        return False

    def move_horizon(self, u: Unit) -> float:
        """本 tick 能走的步數上限對應的搜尋成本（含風險加權的餘裕）。"""
        steps = self.b.move_budget(u)
        return steps * 2 + RISK_W * steps * 3

    def idle_plan(self, u: Unit) -> Plan:
        """沒事做：已架槍 → 維持（警戒）；未架槍且能射擊 → 架槍（花整個 tick）。
        衝動的人（積極高、謹慎低）有時懶得架槍，只警戒。"""
        if not u.can_shoot():
            return Plan()
        if u.braced:
            return Plan(mode="overwatch", watch=u.braced)
        if u.dazzled or u.suppressed:
            return Plan(mode="overwatch")
        skip = 0.6 * u.trait("aggr") - 0.6 * u.trait("caut")
        if skip > 0 and self.b.r("brace_skip", u.id, self.b.tick) < skip:
            return Plan(mode="overwatch")
        return Plan(mode="brace")

    # ------------------------------------------------------------------ movement choice

    def best_move(self, u: Unit, goal: Node | None, occupied: set[Node], threats, mode: str = "advance",
                  flank_target: Node | None = None, risk_cap: int = 0) -> tuple[list[Node] | None, int]:
        table = self.paths(u.pos, threats, occupied, max_cost=self.move_horizon(u))
        risk_cap = risk_cap + self.risk_shift(u)
        self._scoring = u
        here = self._score(u.pos, goal, threats, mode, flank_target)
        best, best_score, best_risk = None, here, None
        min_risk = None
        seen_dest = set()
        for node, (_, risk, path) in table.items():
            if node == u.pos or not path:
                continue
            step = self.ap_len(self.b, u.pos, path)
            if not step:
                continue
            dest = step[-1]
            if dest in seen_dest:
                continue
            seen_dest.add(dest)
            seg_risk = sum(self.exposure(n, threats) for n in step)
            score = self._score(dest, goal, threats, mode, flank_target)
            progress = self._progress(u.pos, dest, goal, mode, flank_target, threats)
            if progress > 0:
                min_risk = seg_risk if min_risk is None else min(min_risk, seg_risk)
            if seg_risk > risk_cap or progress <= 0:
                continue
            if score > best_score:
                best, best_score, best_risk = step, score, seg_risk
        self._scoring = None
        return best, (min_risk if min_risk is not None else 0)

    def _progress(self, a: Node, b: Node, goal, mode, flank_target, threats) -> float:
        if mode == "flank" and flank_target:
            return self._flank_value(b, flank_target) - self._flank_value(a, flank_target) + (
                0.01 if self._d(b, flank_target) < self._d(a, flank_target) else 0)
        if goal is None:
            return 0
        return self._d(a, goal) - self._d(b, goal)

    @staticmethod
    def risk_shift(u: Unit) -> int:
        """性格對可接受風險的影響：積極 +、謹慎 −（×人格值），四捨五入成格數。"""
        return int(round(1.5 * u.trait("aggr") - 1.0 * u.trait("caut")))

    def _score(self, node: Node, goal, threats, mode, flank_target) -> float:
        u = getattr(self, "_scoring", None)
        w = 3.0 * (1 + 0.5 * u.trait("caut") - 0.3 * u.trait("aggr")) if u is not None else 3.0
        s = -w * self.exposure(node, threats) + 1.5 * node[2]
        if mode == "flank" and flank_target:
            s += 4.0 * self._flank_value(node, flank_target) - 0.3 * self._d(node, flank_target)
        elif goal is not None:
            s += -2.0 * self._d(node, goal)
        return s

    def _flank_value(self, node: Node, target: Node) -> float:
        e = self.b.units[self._target_at(target)] if self._target_at(target) else None
        posture = e.posture if e else "crouch"
        m = self.b.map
        tf = e.facing if e else (0, 1)
        eye = body.eye(m.hug_base(node, m.facing_to(node, target)), "crouch")
        pts = body.sample_points(m.hug_base(target, tf), posture, tf)
        return sum(self.b.sight(eye, p) for p in pts.values()) / len(pts)

    def _target_at(self, node: Node) -> str | None:
        for uid, (n, _) in self.b.intel[self.side].last_known.items():
            if n == node:
                return uid
        return None

    @staticmethod
    def _d(a: Node, b: Node) -> float:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1])) + abs(a[2] - b[2]) * 2

    # ------------------------------------------------------------------ main

    # ------------------------------------------------------------------ A：警戒扇區

    def assign_watch(self, plans: dict[str, Plan], units: list[Unit], goal: Node | None):
        """沒有移動的人分配警戒扇區：朝已知威脅；沒有威脅時依人數把前方 180° 平均分配。"""
        watchers = [u for u in units if plans.get(u.id) and plans[u.id].mode in ("overwatch", "idle", "posture", "brace")]
        if not watchers:
            return
        lk = self.b.intel[self.side].last_known
        threat_nodes = [lk[e.id][0] for e in self.known()]
        if goal is not None:
            ax = (goal[0] - watchers[0].pos[0], goal[1] - watchers[0].pos[1])
        else:
            ax = watchers[0].facing
        if ax == (0, 0):
            ax = (0, 1)
        base = math.atan2(ax[1], ax[0])
        n = len(watchers)
        for i, u in enumerate(watchers):
            door = self.threatened_door(u, threat_nodes)
            if door is not None:
                vec = (door[0] - u.pos[0], door[1] - u.pos[1])
                want = vec if vec != (0, 0) else None
            elif threat_nodes:
                t = threat_nodes[i % len(threat_nodes)]
                vec = (t[0] - u.pos[0], t[1] - u.pos[1])
                want = vec if vec != (0, 0) else None
            else:
                a = base + (math.pi * ((i + 0.5) / n - 0.5) if n > 1 else 0.0)
                want = (math.cos(a), math.sin(a))
            p = plans[u.id]
            if u.braced and p.mode == "overwatch":
                if door is not None and want is not None and not self._within_arc(u.braced, want):
                    plans[u.id] = Plan(mode="brace", watch=want)
                    if not any("架向門口" in r for r in self.reports[-3:]):
                        self.reports.append(f"T{self.b.tick} 門外有動靜，架向門口")
                elif want is not None and threat_nodes and not self._within_arc(u.braced, want) \
                        and not any(self._within_arc(u.braced, (tn[0] - u.pos[0], tn[1] - u.pos[1]))
                                    for tn in threat_nodes):
                    plans[u.id] = Plan(mode="brace", watch=want)
                else:
                    p.watch = u.braced
                continue
            p.watch = want

    def threatened_door(self, u: Unit, threat_nodes) -> tuple[int, int] | None:
        """守方預判：自己在房內、已知（看到或聽到）敵人在某扇門外 3 格內 → 那扇門。"""
        m = self.b.map
        room = m.room_at(u.pos)
        if room is None or not threat_nodes:
            return None
        best = None
        for d in room["doors"]:
            for t in threat_nodes:
                if m.room_at(t) is room:
                    continue
                dist = max(abs(t[0] - d[0]), abs(t[1] - d[1]))
                if dist <= 3 and (best is None or dist < best[0]):
                    best = (dist, d)
        return best[1] if best else None

    @staticmethod
    def _within_arc(a, b, arc: float = 45.0) -> bool:
        na, nb = math.hypot(*a), math.hypot(*b)
        if na < 1e-6 or nb < 1e-6:
            return True
        cos = (a[0] * b[0] + a[1] * b[1]) / (na * nb)
        return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= arc

    # ------------------------------------------------------------------ C、F：伏擊

    def ambush_plans(self, units: list[Unit], intent: Intent, occupied: set[Node]) -> dict[str, Plan]:
        plans: dict[str, Plan] = {}
        st = self.ambush_state
        fired_now = any(u.fired for u in units)
        if fired_now:
            st["sprung"] = True
            st["last_fire"] = self.b.tick - 1
        compromised = self.b.believes_any_seen(units)
        trigger = st["sprung"] or any(
            length(sub(e.torso(self.b.map), u.eye(self.b.map))) <= intent.ambush_range * (1 + 0.4 * u.trait("aggr"))
            and self.b.visible_parts(u, e)
            for u in units for e in self.spotted()) or (compromised and any(
                self.b.visible_parts(u, e) for u in units for e in self.spotted()))
        if compromised and not st["sprung"] and trigger:
            self.reports.append(f"T{self.b.tick} 伏擊位置可能已暴露，提早開火")
        quiet = self.b.tick - st.get("last_fire", self.b.tick) if st["sprung"] else 0
        threats = self.threats()
        for u in units:
            hit_recently = u.suppression > 0 or u.damage or self.b.believes_seen(u) in ("hit", "near_miss", "watched")
            if st["sprung"] and (hit_recently or quiet >= AMBUSH_RELOCATE_AFTER) and not u.pinned:
                dest = self.relocate(u, occupied, threats)
                if dest:
                    plans[u.id] = Plan(mode="move", path=dest)
                    occupied.add(dest[-1])
                    occupied.discard(u.pos)
                    if quiet >= AMBUSH_RELOCATE_AFTER:
                        st["last_fire"] = self.b.tick
                    continue
            if u.posture != "prone" and not trigger:
                plans[u.id] = Plan(mode="posture", posture="prone")
                continue
            if trigger:
                st["sprung"] = True
                plans[u.id] = self.fire_plan(u) or self.recon_fire(u) or Plan()
            else:
                plans[u.id] = Plan()
        return plans

    def relocate(self, u: Unit, occupied: set[Node], threats) -> list[Node] | None:
        """伏擊者轉移：附近不暴露、而且仍有隱蔽（植被或低矮掩體）的格子。"""
        table = self.paths(u.pos, threats, occupied)
        best, best_score = None, None
        for node, (_, risk, path) in table.items():
            if node == u.pos or not path or len(path) > 3:
                continue
            step = self.ap_len(self.b, u.pos, path)
            if not step or step[-1] != node:
                continue
            exp = self.exposure(node, threats, posture="prone")
            conceal = self._concealment(node)
            score = conceal * 3 - exp * 4 - risk
            if best_score is None or score > best_score:
                best, best_score = step, score
        return best if best_score is not None and best_score > 0 else None

    def _concealment(self, node: Node) -> float:
        boxes = self.b.map._boxes_in_cell(node[0], node[1])
        if any(b.material == "veg" for b in boxes):
            return 1.0
        if any(b.tag in ("lowwall", "rubble") for b in boxes):
            return 0.5
        return 0.0

    # ------------------------------------------------------------------ D：火力偵察

    def recon_fire(self, u: Unit) -> Plan | None:
        """看不到敵人、但知道最後位置（含聲音察覺）時，對該位置壓制射擊逼其暴露。"""
        if not u.can_shoot() or u.ammo < RECON_MIN_AMMO:
            return None
        lk = self.b.intel[self.side].last_known
        cands = [e for e in self.known() if e.id not in self.b.intel[self.side].spotted
                 and self.b.tick - lk[e.id][1] <= RECON_MAX_AGE]
        for e in sorted(cands, key=lambda t: (self._d(lk[t.id][0], u.pos), t.id)):
            node = lk[e.id][0]
            if self._near_los(u, node):
                c = self.b.map.center(node)
                return Plan(mode="shoot", fire="suppress", point=(c[0], c[1], c[2] + 0.4))
        return None

    # ------------------------------------------------------------------ A：近戰、手榴彈

    def melee_plan(self, u: Unit) -> Plan | None:
        foes = [e for e in self.b.enemies_of(self.side) if self.b.adjacent(u.pos, e.pos)
                and (e.id in self.b.intel[self.side].last_known)]
        if not foes:
            return None
        e = min(foes, key=lambda t: (t.suppression < 50, t.id))
        return Plan(mode="melee", target=e.id)

    def behind_cover(self, u: Unit, node: Node, posture: str) -> bool:
        """以幾何判斷：從投擲者看過去，目標軀幹被實心掩體擋住（不是因為遠、煙或植被看不清）。"""
        m = self.b.map
        f = m.facing_to(node, u.pos)
        torso = body.torso_center(m.hug_base(node, f), posture, f)
        return m.blocked(u.eye(m), torso)

    def grenade_plan(self, u: Unit, need_hidden: bool = True, taken: set[str] | None = None,
                     mode: str = "") -> Plan | None:
        """對躲在實心掩體後的近距已知敵人投擲手榴彈。
        限制：個人冷卻 3 tick；目標軀幹須被實心物遮擋；最後一顆只在 8 m 內或突擊／撤退時使用；
        不投到己方 5 m 內；同一目標每 tick 只投一顆。"""
        if not u.can_throw():
            return None
        if self.b.tick - self.last_throw.get(u.id, -99) < GRENADE_COOLDOWN:
            return None
        lk = self.b.intel[self.side].last_known
        m = self.b.map
        friends = [f for f in self.b.alive(self.side)]
        best = None
        for e in self.known():
            if taken is not None and e.id in taken:
                continue
            if self.b.tick - lk[e.id][1] > 3:
                continue
            node = lk[e.id][0]
            c = m.hug_base(node, m.facing_to(node, u.pos))
            target = (c[0], c[1], c[2] + 0.1)
            dist = length(sub(target, u.eye(m)))
            if dist > GRENADE_THROW or dist < GRENADE_MIN:
                continue
            if u.grenades <= 1 and dist > GRENADE_LAST_RANGE and mode not in ("assault", "retreat"):
                continue
            vis = self.b.visible_parts(u, e) if e.id in self.b.intel[self.side].spotted else []
            if need_hidden and len(vis) > 1:
                continue
            if need_hidden and not self.behind_cover(u, node, e.posture):
                continue
            if any(length(sub(f.torso(m), target)) < GRENADE_SAFE for f in friends):
                continue
            cluster = sum(1 for o in self.known() if self._d(lk[o.id][0], node) <= 1)
            score = cluster * 10 - dist
            if best is None or score > best[0]:
                best = (score, target, e.id)
        if not best:
            return None
        if taken is not None:
            taken.add(best[2])
        self.last_throw[u.id] = self.b.tick
        return Plan(mode="grenade", point=best[1])

    # ------------------------------------------------------------------ 投擲物撿取／傳遞

    def scavenge_plan(self, u: Unit, occupied: set[Node], threats, need: str | None = None) -> Plan | None:
        """撿投擲物：相鄰（或 1 步內、路線不暴露）的倒地／陣亡者（敵我皆可）身上有東西、自己還有空位。
        need 指定時只找那一種。"""
        if u.room() <= 0 or u.pinned or not u.alive:
            return None
        cands = [t for t in self.b.units.values() if not t.alive and not t.exited and t.throwables() > 0
                 and (need is None or getattr(t, need) > 0)]
        if not cands:
            return None
        for t in sorted(cands, key=lambda x: (self._d(x.pos, u.pos), x.id)):
            if t.pos == u.pos or self.b.adjacent(t.pos, u.pos):
                return Plan(mode="take", target=t.id, item=need)
            if self._d(t.pos, u.pos) > 2:
                continue
            for n, cost in sorted(self.b.map.neighbors(u.pos)):
                if n in occupied or cost > 1 or not (n == t.pos or self.b.adjacent(n, t.pos)):
                    continue
                if self.exposure(n, threats) == 0:
                    return Plan(mode="take", target=t.id, path=[n], item=need)
        return None

    def give_plan(self, giver: Unit, receiver: Unit, item: str) -> Plan | None:
        if not giver.alive or getattr(giver, item) <= 0 or receiver.room() <= 0:
            return None
        if not (giver.pos == receiver.pos or self.b.adjacent(giver.pos, receiver.pos)):
            return None
        return Plan(mode="give", target=receiver.id, item=item)

    # ------------------------------------------------------------------ B：救援、撤退

    def aid_plan(self, u: Unit, occupied: set[Node], threats) -> Plan | None:
        """急救／拖到掩護：同格友軍（別的小隊、殘部）也救；自己隊優先。"""
        downed = [d for d in self.b.units.values() if not self.b.hostile(d.side, self.side) and d.down
                  and not d.dead and not d.exited]
        if not downed:
            return None
        d = min(downed, key=lambda x: (x.side != self.side, self._d(x.pos, u.pos), x.id))
        if d.pos == u.pos or self.b.adjacent(d.pos, u.pos):
            if not d.stabilized:
                return Plan(mode="aid", target=d.id)
            back = self.retreat_step(u, occupied, threats)
            if back:
                return Plan(mode="drag", target=d.id, path=back[:1])
            return None
        screened = self.smoke_live_near(self.b.map.center(d.pos)[:2], SMOKE_RADIUS + 2)
        bond = u.trait("bond")
        reach = 3 + int(round(2 * bond))
        tolerate = max(0, int(round(2 * bond)))
        if self._d(d.pos, u.pos) <= reach and (self.exposure(d.pos, threats) <= tolerate or screened):
            table = self.paths(u.pos, threats, occupied - {d.pos})
            best = None
            for n in [x for x, _ in self.b.map.neighbors(d.pos)] + [d.pos]:
                if n in table and n not in occupied and (table[n][1] <= tolerate or screened):
                    if best is None or table[n][0] < table[best][0]:
                        best = n
            if best:
                step = self.ap_len(self.b, u.pos, table[best][2])
                if step:
                    return Plan(mode="move", path=step)
        return None

    def retreat_step(self, u: Unit, occupied: set[Node], threats, toward: tuple[int, int] | None = None
                     ) -> list[Node] | None:
        """往後方邊緣（預設：遠離已知敵人的平均位置）移動，優先不暴露的格子。"""
        lk = self.b.intel[self.side].last_known
        if toward is None:
            if lk:
                ex = sum(n[0] for n, _ in lk.values()) / len(lk)
                ey = sum(n[1] for n, _ in lk.values()) / len(lk)
                toward = (u.pos[0] - ex, u.pos[1] - ey)
            else:
                toward = (0, -1)
        table = self.paths(u.pos, threats, occupied)
        best, best_score = None, None
        for node, (_, risk, path) in table.items():
            if node == u.pos:
                continue
            step = self.ap_len(self.b, u.pos, path)
            if not step:
                continue
            dest = step[-1]
            gain = (dest[0] - u.pos[0]) * toward[0] + (dest[1] - u.pos[1]) * toward[1]
            if gain <= 0:
                continue
            score = gain - 3 * sum(self.exposure(n, threats) for n in step)
            if best_score is None or score > best_score:
                best, best_score = step, score
        return best

    # ------------------------------------------------------------------ 煙霧

    def smoke_live_near(self, point: tuple[float, float], radius: float = SMOKE_REUSE_R) -> bool:
        """己方已知（看得到）的煙霧：仍在或即將生效的煙霧離此點夠近 → 不重複投擲。"""
        for s in self.b.smokes:
            if s["expires"] - self.b.tick < 2:
                continue
            if math.hypot(s["x"] - point[0], s["y"] - point[1]) < radius:
                return True
        return False

    def smoke_screen_point(self, units: list[Unit], threat_nodes: list[Node]) -> tuple[float, float, float] | None:
        """在小隊與威脅之間放煙：取小隊中心往威脅中心方向 40% 的位置（至少離我方 3 m）。"""
        if not units or not threat_nodes:
            return None
        m = self.b.map
        ux = sum(m.center(u.pos)[0] for u in units) / len(units)
        uy = sum(m.center(u.pos)[1] for u in units) / len(units)
        tx = sum(m.center(n)[0] for n in threat_nodes) / len(threat_nodes)
        ty = sum(m.center(n)[1] for n in threat_nodes) / len(threat_nodes)
        dx, dy = tx - ux, ty - uy
        dist = math.hypot(dx, dy)
        if dist < 2.0:
            return None
        k = max(0.4, min(0.7, 3.0 / dist)) if dist > 3 else 0.5
        return (ux + dx * k, uy + dy * k, 0.0)

    def smoke_plan(self, units: list[Unit], point) -> dict[str, Plan]:
        """選一個有煙霧彈、沒被釘死的人投擲。"""
        if point is None or self.smoke_live_near(point[:2]):
            return {}
        throwers = [u for u in units if u.smokes > 0 and u.alive and not u.pinned]
        if not throwers:
            return {}
        m = self.b.map
        t = min(throwers, key=lambda u: (math.hypot(m.center(u.pos)[0] - point[0], m.center(u.pos)[1] - point[1]),
                                         u.id))
        return {t.id: Plan(mode="smoke", point=point)}

    def smoke_behind_fire(self, units: list[Unit]) -> tuple[float, float, float] | None:
        """不知道射手在哪、但正在被打：把煙放在來彈方向（依上一 tick 打到自己的彈道反推）。"""
        dirs = []
        for s in self.b.recent_incoming(self.side):
            d = s["dir"]
            dirs.append((-d[0], -d[1]))
        if not dirs:
            return None
        dx = sum(d[0] for d in dirs) / len(dirs)
        dy = sum(d[1] for d in dirs) / len(dirs)
        n = math.hypot(dx, dy) or 1.0
        m = self.b.map
        ux = sum(m.center(u.pos)[0] for u in units) / len(units)
        uy = sum(m.center(u.pos)[1] for u in units) / len(units)
        return (ux + dx / n * 4.0, uy + dy / n * 4.0, 0.0)

    @staticmethod
    def _clusters(units: list[Unit], gap: int) -> list[list[Unit]]:
        """依格距（切比雪夫）分群：彼此 gap 格內的歸同一群。"""
        groups: list[list[Unit]] = []
        for u in sorted(units, key=lambda x: x.id):
            for g in groups:
                if any(max(abs(u.pos[0] - v.pos[0]), abs(u.pos[1] - v.pos[1])) <= gap for v in g):
                    g.append(u)
                    break
            else:
                groups.append([u])
        return groups

    def threat_nodes(self) -> list[Node]:
        lk = self.b.intel[self.side].last_known
        return [lk[e.id][0] for e in self.known()]

    # ------------------------------------------------------------------ 撤退

    def retreat_plans(self, units: list[Unit], occupied: set[Node], threats,
                      toward: tuple[int, int] | None = None) -> dict[str, Plan]:
        """交替撤退：先放煙（若路線暴露）；兩組輪流，一組壓制掩護、一組後撤；拖走倒地隊友；
        到後方邊緣撤出；路線全暴露又沒煙時先就近找掩體。"""
        plans: dict[str, Plan] = {}
        group = {u.id: i % 2 for i, u in enumerate(units)}
        tnodes = self.threat_nodes()
        exposed = [u for u in units if self.exposure(u.pos, threats) > 0 or self.b.is_targeted(u)]
        for cluster in self._clusters(exposed, SMOKE_CLUSTER):
            point = self.smoke_screen_point(cluster, tnodes) if tnodes else None
            if point is None:
                point = self.smoke_behind_fire(cluster)
            free = [u for u in units if u.id not in plans]
            plans.update(self.smoke_plan(free, point))
        downed = [d for d in self.b.units.values() if d.side == self.side and d.down and not d.dead
                  and not d.exited]
        draggers: dict[str, str] = {}
        for d in sorted(downed, key=lambda x: x.id):
            near = [u for u in units if u.id not in plans and u.id not in draggers
                    and (u.pos == d.pos or self.b.adjacent(u.pos, d.pos))]
            if near:
                draggers[min(near, key=lambda u: (-u.trait("bond"), u.id)).id] = d.id
        moving_group = self.b.tick % 2
        for u in units:
            if u.id in plans:
                continue
            dragging = u.id in draggers
            if self.b.on_edge(u.pos) and self._edge_is_rear(u, toward):
                if dragging:
                    plans[u.id] = Plan(mode="drag", target=draggers[u.id], path=[u.pos], posture="__exit__")
                else:
                    plans[u.id] = Plan(mode="move", path=[u.pos], target="__exit__")
                continue
            if dragging or group[u.id] == moving_group or len(units) <= 2:
                step = self.retreat_step(u, occupied, threats, toward)
                if step and not dragging:
                    risk = sum(self.exposure(n, threats) for n in step)
                    if risk > 0:
                        cover = self.cover_step(u, occupied, threats, toward=self._rear_vec(u, toward))
                        if cover:
                            step = cover
                        elif self.exposure(u.pos, threats) == 0 and self.wait_ok(u):
                            step = None
                if step:
                    exit_now = self.b.on_edge(step[-1]) and self._edge_is_rear_node(step[-1], u, toward)
                    if dragging:
                        d = self.b.units[draggers[u.id]]
                        plans[u.id] = Plan(mode="drag", target=d.id, path=step[:1])
                        occupied.add(step[0])
                    else:
                        plans[u.id] = Plan(mode="move", path=step, target="__exit__" if exit_now else None)
                        occupied.add(step[-1])
                    occupied.discard(u.pos)
                    continue
            plans[u.id] = (self.suppress_plan(u) or self.fire_plan(u) or self.recon_fire(u)
                           or self.idle_plan(u))
        return plans

    def wait_ok(self, u: Unit) -> bool:
        """躲在掩體後、撤退路線暴露時：等煙霧或壓制，最多等 WAIT_MAX tick，之後硬撤。"""
        n = self.waits.get(u.id, 0) + 1
        self.waits[u.id] = n
        return n <= WAIT_MAX

    def _rear_vec(self, u: Unit, toward) -> tuple[float, float]:
        if toward is not None:
            return toward
        lk = self.b.intel[self.side].last_known
        if not lk:
            return (0, -1)
        ex = sum(n[0] for n, _ in lk.values()) / len(lk)
        ey = sum(n[1] for n, _ in lk.values()) / len(lk)
        return (u.pos[0] - ex, u.pos[1] - ey)

    def cover_step(self, u: Unit, occupied: set[Node], threats, toward=None) -> list[Node] | None:
        """沒有安全撤退路線時：移到本 tick 走得到、對已知敵人不暴露的位置（若現在已不暴露則不動）。
        撤退時不可朝敵人方向移動（toward 給定時，終點不得比現在更靠近敵人）。"""
        if self.exposure(u.pos, threats) == 0:
            return None
        table = self.paths(u.pos, threats, occupied)
        best = None
        for node, (_, risk, path) in table.items():
            step = self.ap_len(self.b, u.pos, path)
            if not step or step[-1] != node:
                continue
            if toward is not None and (node[0] - u.pos[0]) * toward[0] + (node[1] - u.pos[1]) * toward[1] < 0:
                continue
            if self.exposure(node, threats) == 0:
                if best is None or risk < best[0] or (risk == best[0] and len(step) < len(best[1])):
                    best = (risk, step)
        return best[1] if best else None

    def offensive_smoke(self, intent: Intent, units: list[Unit], threats) -> dict[str, Plan]:
        """推進／突擊／救援：路線暴露時先放煙；耐心快到上限時也先試一次煙霧。"""
        tnodes = self.threat_nodes()
        if not tnodes or not units:
            return {}
        k = intent.kind
        want = False
        point = None
        if k == "assault":
            want = any(self.exposure(u.pos, threats) for u in units)
            lk = self.b.intel[self.side].last_known
            near = min(tnodes, key=lambda n: min(self._d(n, u.pos) for u in units))
            c = self.b.map.center(near)
            point = (c[0], c[1], 0.0)
            if want and self._d(near, min(units, key=lambda u: self._d(near, u.pos)).pos) <= 2:
                want = False
        elif k in ("advance", "supp_advance", "flank") and not self.holding:
            near_cap = self.patience >= PATIENCE_STEP * PATIENCE_MAX - 1
            want = near_cap and any(self.exposure(u.pos, threats) == 0 for u in units)
            point = self.smoke_screen_point(units, tnodes)
        downed = [d for d in self.b.units.values() if d.side == self.side and d.down and not d.dead
                  and not d.exited and not d.stabilized]
        if not want and downed and k != "ambush":
            d = downed[0]
            if self.exposure(d.pos, threats) > 0 and any(self._d(u.pos, d.pos) <= 3 for u in units):
                want = True
                point = self.smoke_screen_point([d], tnodes)
        if not want:
            return {}
        return self.smoke_plan(units, point)

    # ------------------------------------------------------------------ 重大事件（給隊長的撤退選項）

    def major_events(self) -> list[str]:
        """回傳目前觸發的重大事件；有任一項時，隊長可選擇撤退。"""
        ev = []
        own = [u for u in self.b.units.values() if u.side == self.side and not u.exited]
        if any(u.down and not u.dead for u in own) or any(u.dead for u in own):
            ev.append("有隊員倒地")
        alive = [u for u in own if u.alive]
        if alive:
            from engine.tactical.units import WEAPONS
            ratio = sum(u.ammo / WEAPONS[u.weapon]["ammo"] for u in alive) / len(alive)
            if ratio < 0.3:
                ev.append("彈藥不足三成")
            foes = len(self.known())
            if foes and len(alive) * 2 <= foes:
                ev.append("兵力劣勢")
        if self.b.tick - self.b.flanked.get(self.side, -99) <= 3:
            ev.append("遭側翼攻擊")
        if self.holding:
            ev.append("請求新指示")
        return ev

    def can_retreat(self) -> bool:
        return bool(self.major_events())

    def _edge_is_rear(self, u: Unit, toward) -> bool:
        return self._edge_is_rear_node(u.pos, u, toward)

    def _edge_is_rear_node(self, node: Node, u: Unit, toward) -> bool:
        lk = self.b.intel[self.side].last_known
        if toward is None:
            if not lk:
                return node[1] == 0
            ex = sum(n[0] for n, _ in lk.values()) / len(lk)
            ey = sum(n[1] for n, _ in lk.values()) / len(lk)
            toward = (node[0] - ex, node[1] - ey)
        w, h = self.b.map.w, self.b.map.h
        return ((node[1] == 0 and toward[1] < 0) or (node[1] == h - 1 and toward[1] > 0)
                or (node[0] == 0 and toward[0] < 0) or (node[0] == w - 1 and toward[0] > 0))

    # ------------------------------------------------------------------ main

    def plan(self, intent: Intent, units: list[Unit] | None = None) -> dict[str, Plan]:
        units = [u for u in (units or self.b.alive(self.side))]
        units = sorted(units, key=lambda u: u.id)
        occupied = {u.pos for u in self.b.alive()} | {u.pos for u in self.b.units.values()
                                                       if u.down and not u.exited and not u.dead}
        threats = self.threats()
        state = self.b.morale_state(self.side)
        if state in ("rout", "surrender") or intent.kind == "retreat":
            if state in ("rout", "surrender") and intent.kind != "retreat" and not self.routed:
                self.routed = True
                self.reports.append(f"T{self.b.tick} 士氣崩潰，交替撤退")
            free = [u for u in units if not u.pinned]
            plans = {u.id: self._pinned(u, set(occupied), threats) for u in units if u.pinned}
            plans.update(self.retreat_plans(free, set(occupied), threats, intent.toward))
            return plans
        pre: dict[str, Plan] = {}
        used: set[Node] = set()
        taken: set[str] = set()
        if intent.kind != "breach":
            pre.update(self.offensive_smoke(intent, units, threats))
        gren_left = GRENADE_PER_TICK.get(intent.kind, 1)
        for u in units:
            if u.pinned or u.id in pre:
                continue
            p = self.melee_plan(u) if intent.kind == "assault" or self.b.morale_state(self.side) == "normal" else None
            if intent.kind == "breach":
                if p is None and not self.b.intel[self.side].spotted and self.breach.prep in ("flash", "frag", "smoke") \
                        and not self.breach.entered:
                    from engine.tactical.breach import ITEM_FOR
                    item = ITEM_FOR[self.breach.prep]
                    if not any(getattr(x, item) for x in units) and getattr(u, item) == 0:
                        p = self.scavenge_plan(u, occupied | used, threats, need=item)
                if p is not None:
                    pre[u.id] = p
                    if p.path:
                        used.add(p.path[-1])
                continue
            if p is None and intent.kind != "ambush" and gren_left > 0:
                p = self.grenade_plan(u, need_hidden=True, taken=taken, mode=intent.kind)
                if p is not None:
                    gren_left -= 1
            if p is None and intent.kind != "ambush" and (not self.b.intel[self.side].spotted
                                                          or u.trait("bond") >= 0.3):
                p = self.aid_plan(u, occupied | used, threats)
            if p is None and not self.b.intel[self.side].spotted:
                p = self.scavenge_plan(u, occupied | used, threats)
            if p is not None:
                pre[u.id] = p
                if p.path:
                    used.add(p.path[-1])
        rest = [u for u in units if u.id not in pre]
        plans = self._plan_core(intent, rest, occupied | used, threats)
        plans.update(pre)
        if state == "shaken":
            for uid, p in plans.items():
                if p.mode == "move" and intent.kind in ("advance", "supp_advance", "flank", "assault"):
                    u = self.b.units[uid]
                    if any(self.exposure(n, threats) for n in p.path):
                        plans[uid] = self.fire_plan(u) or self.idle_plan(u)
        return plans

    def assault_plans(self, units: list[Unit], occupied: set[Node], threats, goal: Node | None) -> dict[str, Plan]:
        """突擊：全員衝向最近的已知敵人相鄰格；第一順位投擲手榴彈，其他人邊衝邊打。"""
        plans: dict[str, Plan] = {}
        lk = self.b.intel[self.side].last_known
        targets = [lk[e.id][0] for e in self.known()] or ([goal] if goal else [])
        if not targets:
            return {u.id: self.idle_plan(u) for u in units}
        for u in units:
            tgt = min(targets, key=lambda n: (self._d(n, u.pos), n))
            table = self.paths(u.pos, [], occupied)
            dests = [n for n, _ in self.b.map.neighbors(tgt) if n not in occupied and n in table] + \
                    ([tgt] if tgt not in occupied and tgt in table else [])
            if not dests:
                plans[u.id] = self.fire_plan(u) or self.idle_plan(u)
                continue
            dest = min(dests, key=lambda n: (table[n][0], n))
            step = self.ap_len(self.b, u.pos, table[dest][2])
            if step:
                plans[u.id] = Plan(mode="move", path=step)
                occupied.add(step[-1])
                occupied.discard(u.pos)
            else:
                plans[u.id] = self.fire_plan(u) or self.idle_plan(u)
        return plans

    def _plan_core(self, intent: Intent, units: list[Unit], occupied: set[Node], threats) -> dict[str, Plan]:
        plans: dict[str, Plan] = {}
        reserved: set[Node] = set()
        half = {u.id: i % 2 for i, u in enumerate(units)}
        k = intent.kind
        moved_any = False
        stalled = False
        if k == "assault":
            free = [u for u in units if not u.pinned]
            plans = {u.id: self._pinned(u, occupied, threats) for u in units if u.pinned}
            plans.update(self.assault_plans(free, set(occupied), threats, intent.goal))
            return plans

        if k == "breach":
            from engine.tactical import breach
            return breach.plan(self, intent, units, set(occupied), threats)

        if k == "ambush":
            free = [u for u in units if not u.pinned]
            for u in units:
                if u.pinned:
                    plans[u.id] = self._pinned(u, occupied | reserved, threats)
            plans.update(self.ambush_plans(free, intent, set(occupied | reserved)))
            return plans

        cap = RISK_CAP.get(k, 0) + self.patience // PATIENCE_STEP
        cap_maxed = self.patience >= PATIENCE_STEP * PATIENCE_MAX

        for u in units:
            if u.pinned:
                plans[u.id] = self._pinned(u, occupied | reserved, threats)
                continue
            if u.posture == "prone":
                plans[u.id] = Plan(mode="posture", posture="crouch")
                continue
            if k == "hold" or self.holding:
                plans[u.id] = self.fire_plan(u) or self.recon_fire(u) or self.idle_plan(u)
                continue

            moving_turn = (half[u.id] == self.b.tick % 2) if k == "advance" else (half[u.id] == 1)
            if k in ("supp_advance", "flank"):
                if half[u.id] == 0:
                    plans[u.id] = (self.suppress_plan(u, intent.goal) or self.fire_plan(u)
                                   or self.recon_fire(u) or self.idle_plan(u))
                    continue
            elif not moving_turn:
                plans[u.id] = self.fire_plan(u) or self.recon_fire(u) or self.idle_plan(u)
                continue

            fire = self.fire_plan(u)
            if fire and k == "advance" and self.exposure(u.pos, threats) > 0:
                plans[u.id] = fire
                continue
            flank_target = None
            if k == "flank":
                known = self.known()
                if known:
                    flank_target = self.b.intel[self.side].last_known[known[0].id][0]
            path, min_risk = self.best_move(u, intent.goal, occupied | reserved, threats,
                                            mode="flank" if k == "flank" else "advance",
                                            flank_target=flank_target, risk_cap=cap)
            if path:
                plans[u.id] = Plan(mode="move", path=path)
                reserved.add(path[-1])
                occupied.discard(u.pos)
                moved_any = True
            else:
                if min_risk >= INFEASIBLE_RISK:
                    stalled = True
                plans[u.id] = fire or self.recon_fire(u) or self.idle_plan(u)

        if k in ("advance", "supp_advance", "flank") and not self.holding:
            progress = moved_any or any(u.fired for u in units)
            arrived = intent.goal is not None and any(self._d(u.pos, intent.goal) <= 1 for u in units)
            if arrived and self.known() and not moved_any:
                self.holding = True
                self.reports.append(f"T{self.b.tick} 已抵達目標，仍有已知敵人在附近，就地固守，請求新指示")
            elif not moved_any and not stalled and not arrived and self.known():
                stalled = True
            if progress and not stalled:
                self.patience = max(0, self.patience - 1)
            elif stalled or not moved_any:
                self.patience += 1
                if stalled and self.patience % PATIENCE_STEP == 0 and not cap_maxed:
                    self.reports.append(f"T{self.b.tick} 路線暴露於已知敵人射界，提高可接受風險（{cap + 1}）")
            if cap_maxed and stalled:
                self.holding = True
                self.reports.append(f"T{self.b.tick} 無法推進：所有路線都暴露於已知敵人射界，"
                                    f"已就地固守，請求新指示")
        self.assign_watch(plans, units, intent.goal)
        return plans

    def _pinned(self, u: Unit, occupied: set[Node], threats) -> Plan:
        if self.exposure(u.pos, threats) > 0:
            for nxt, cost in sorted(self.b.map.neighbors(u.pos)):
                if nxt not in occupied and cost <= 2 and self.exposure(nxt, threats) == 0:
                    return Plan(mode="move", path=[nxt])
            if u.posture != "prone":
                return Plan(mode="posture", posture="prone")
            return Plan()
        return self.suppress_plan(u) or Plan()
