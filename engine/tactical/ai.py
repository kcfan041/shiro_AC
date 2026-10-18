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


@dataclass
class Intent:
    kind: str = "hold"            # hold / advance / supp_advance / flank / ambush / assault / retreat
    goal: Node | None = None
    side: str | None = None       # flank: left / right
    ambush_range: float = 12.0
    toward: tuple[int, int] | None = None   # retreat 方向


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
        self.ambush_state: dict = {"sprung": False}

    def new_intent(self):
        """隊長下達新意圖時重置耐心與固守狀態。"""
        self.patience = 0
        self.holding = False

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
        threats = self.threats() if threats is None else threats
        m = self.b.map
        n = 0
        for _, enode, epost in threats:
            f = m.facing_to(node, enode)
            torso = body.torso_center(m.hug_base(node, f), posture, f)
            eye = body.eye(m.hug_base(enode, m.facing_to(enode, node)), epost)
            if m.sight(eye, torso) > 0.3:
                n += 1
        return n

    # ------------------------------------------------------------------ paths

    def paths(self, start: Node, threats, occupied: set[Node]) -> dict[Node, tuple[float, int, list[Node]]]:
        """Dijkstra：成本 = AP + RISK_W × 暴露。回傳 node -> (成本, 累積風險, 路徑)。"""
        dist = {start: (0.0, 0, [])}
        heap = [(0.0, 0, start)]
        seen = set()
        while heap:
            cost, risk, node = heapq.heappop(heap)
            if node in seen:
                continue
            seen.add(node)
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
        out, ap, cur = [], 2, start
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
            if self.b.map.sight(u.eye(self.b.map), pt) > 0 or self._near_los(u, node):
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
            if self.b.map.sight(u.eye(self.b.map), (c[0], c[1], c[2] + dz)) > 0:
                return True
        return False

    def idle_plan(self, u: Unit) -> Plan:
        return Plan(mode="overwatch") if u.can_shoot() else Plan()

    # ------------------------------------------------------------------ movement choice

    def best_move(self, u: Unit, goal: Node | None, occupied: set[Node], threats, mode: str = "advance",
                  flank_target: Node | None = None, risk_cap: int = 0) -> tuple[list[Node] | None, int]:
        table = self.paths(u.pos, threats, occupied)
        here = self._score(u.pos, goal, threats, mode, flank_target)
        best, best_score, best_risk = None, here, None
        min_risk = None
        for node, (_, risk, path) in table.items():
            if node == u.pos or not path:
                continue
            step = self.ap_len(self.b, u.pos, path)
            if not step:
                continue
            dest = step[-1]
            seg_risk = sum(self.exposure(n, threats) for n in step)
            score = self._score(dest, goal, threats, mode, flank_target)
            progress = self._progress(u.pos, dest, goal, mode, flank_target, threats)
            if progress > 0:
                min_risk = seg_risk if min_risk is None else min(min_risk, seg_risk)
            if seg_risk > risk_cap or progress <= 0:
                continue
            if score > best_score:
                best, best_score, best_risk = step, score, seg_risk
        return best, (min_risk if min_risk is not None else 0)

    def _progress(self, a: Node, b: Node, goal, mode, flank_target, threats) -> float:
        if mode == "flank" and flank_target:
            return self._flank_value(b, flank_target) - self._flank_value(a, flank_target) + (
                0.01 if self._d(b, flank_target) < self._d(a, flank_target) else 0)
        if goal is None:
            return 0
        return self._d(a, goal) - self._d(b, goal)

    def _score(self, node: Node, goal, threats, mode, flank_target) -> float:
        s = -3.0 * self.exposure(node, threats) + 1.5 * node[2]
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
        return sum(self.b.map.sight(eye, p) for p in pts.values()) / len(pts)

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
        watchers = [u for u in units if plans.get(u.id) and plans[u.id].mode in ("overwatch", "idle", "posture")]
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
            if threat_nodes:
                t = threat_nodes[i % len(threat_nodes)]
                vec = (t[0] - u.pos[0], t[1] - u.pos[1])
                plans[u.id].watch = vec if vec != (0, 0) else None
            else:
                a = base + (math.pi * ((i + 0.5) / n - 0.5) if n > 1 else 0.0)
                plans[u.id].watch = (math.cos(a), math.sin(a))

    # ------------------------------------------------------------------ C、F：伏擊

    def ambush_plans(self, units: list[Unit], intent: Intent, occupied: set[Node]) -> dict[str, Plan]:
        plans: dict[str, Plan] = {}
        st = self.ambush_state
        fired_now = any(u.fired for u in units)
        if fired_now:
            st["sprung"] = True
            st["last_fire"] = self.b.tick - 1
        trigger = st["sprung"] or any(
            length(sub(e.torso(self.b.map), u.eye(self.b.map))) <= intent.ambush_range
            and self.b.visible_parts(u, e)
            for u in units for e in self.spotted())
        quiet = self.b.tick - st.get("last_fire", self.b.tick) if st["sprung"] else 0
        threats = self.threats()
        for u in units:
            hit_recently = u.suppression > 0 or u.damage
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

    def grenade_plan(self, u: Unit, need_hidden: bool = True, taken: set[str] | None = None) -> Plan | None:
        """對看不到（或只露頭）的近距已知敵人投擲手榴彈；不投到己方 5 m 內；同一目標每 tick 只投一顆。"""
        if not u.can_throw():
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
            vis = self.b.visible_parts(u, e) if e.id in self.b.intel[self.side].spotted else []
            if need_hidden and len(vis) > 1:
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
        return Plan(mode="grenade", point=best[1])

    # ------------------------------------------------------------------ B：救援、撤退

    def aid_plan(self, u: Unit, occupied: set[Node], threats) -> Plan | None:
        downed = [d for d in self.b.units.values() if d.side == self.side and d.down and not d.dead
                  and not d.exited]
        if not downed:
            return None
        d = min(downed, key=lambda x: (self._d(x.pos, u.pos), x.id))
        if d.pos == u.pos or self.b.adjacent(d.pos, u.pos):
            if not d.stabilized:
                return Plan(mode="aid", target=d.id)
            back = self.retreat_step(u, occupied, threats)
            if back:
                return Plan(mode="drag", target=d.id, path=back[:1])
            return None
        if self._d(d.pos, u.pos) <= 3 and self.exposure(d.pos, threats) == 0:
            table = self.paths(u.pos, threats, occupied - {d.pos})
            best = None
            for n in [x for x, _ in self.b.map.neighbors(d.pos)] + [d.pos]:
                if n in table and n not in occupied and table[n][1] == 0:
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

    def retreat_plans(self, units: list[Unit], occupied: set[Node], threats,
                      toward: tuple[int, int] | None = None) -> dict[str, Plan]:
        """交替撤退：兩組輪流，一組掩護射擊、一組後撤；到邊緣就撤出。"""
        plans: dict[str, Plan] = {}
        group = {u.id: i % 2 for i, u in enumerate(units)}
        for u in units:
            if self.b.on_edge(u.pos) and self._edge_is_rear(u, toward):
                plans[u.id] = Plan(mode="move", path=[u.pos], target="__exit__")
                continue
            if group[u.id] == self.b.tick % 2 or len(units) == 1:
                step = self.retreat_step(u, occupied, threats, toward)
                if step:
                    exit_now = self.b.on_edge(step[-1]) and self._edge_is_rear_node(step[-1], u, toward)
                    plans[u.id] = Plan(mode="move", path=step, target="__exit__" if exit_now else None)
                    occupied.add(step[-1])
                    occupied.discard(u.pos)
                    continue
            plans[u.id] = self.suppress_plan(u) or self.fire_plan(u) or self.idle_plan(u)
        return plans

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
                                                       if u.down and not u.exited}
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
        for u in units:
            if u.pinned:
                continue
            p = self.melee_plan(u) if intent.kind == "assault" or self.b.morale_state(self.side) == "normal" else None
            if p is None and intent.kind != "ambush":
                p = self.grenade_plan(u, need_hidden=True, taken=taken)
            if p is None and intent.kind != "ambush" and not self.b.intel[self.side].spotted:
                p = self.aid_plan(u, occupied | used, threats)
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
