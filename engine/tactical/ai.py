"""規則式戰術 AI：隊長意圖 → 每人計畫。只讀己方情報（combat.md v0.2 §13）。"""
from __future__ import annotations

import heapq
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


@dataclass
class Intent:
    kind: str = "hold"            # hold / advance / supp_advance / flank / ambush
    goal: Node | None = None
    side: str | None = None       # flank: left / right
    ambush_range: float = 12.0


@dataclass
class GroupState:
    infeasible: list[str] = field(default_factory=list)


class TacticalAI:
    def __init__(self, battle: Battle, side: str):
        self.b = battle
        self.side = side
        self.reports: list[str] = []
        self.shot_at: dict[str, int] = {}

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

    def plan(self, intent: Intent, units: list[Unit] | None = None) -> dict[str, Plan]:
        units = [u for u in (units or self.b.alive(self.side))]
        units = sorted(units, key=lambda u: u.id)
        plans: dict[str, Plan] = {}
        occupied = {u.pos for u in self.b.alive()}
        threats = self.threats()
        reserved: set[Node] = set()
        half = {u.id: i % 2 for i, u in enumerate(units)}
        k = intent.kind

        for u in units:
            if u.pinned:
                plans[u.id] = self._pinned(u, occupied | reserved, threats)
                continue
            if k == "ambush":
                if u.posture != "prone":
                    plans[u.id] = Plan(mode="posture", posture="prone")
                    continue
                close = [e for e in self.spotted() if self.b.visible_parts(u, e)
                         and length(sub(e.torso(self.b.map), u.eye(self.b.map))) <= intent.ambush_range]
                plans[u.id] = (self.fire_plan(u) or Plan()) if close else Plan()
                continue
            if u.posture == "prone":
                plans[u.id] = Plan(mode="posture", posture="crouch")
                continue
            if k == "hold":
                plans[u.id] = self.fire_plan(u) or self.idle_plan(u)
                continue

            moving_turn = (half[u.id] == self.b.tick % 2) if k == "advance" else (half[u.id] == 1)
            if k in ("supp_advance", "flank"):
                if half[u.id] == 0:
                    plans[u.id] = self.suppress_plan(u, intent.goal) or self.fire_plan(u) or self.idle_plan(u)
                    continue
            elif not moving_turn:
                plans[u.id] = self.fire_plan(u) or self.idle_plan(u)
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
                                            flank_target=flank_target, risk_cap=RISK_CAP.get(k, 0))
            if path:
                plans[u.id] = Plan(mode="move", path=path)
                reserved.add(path[-1])
                occupied.discard(u.pos)
            else:
                if min_risk >= INFEASIBLE_RISK:
                    self.reports.append(f"T{self.b.tick} {u.id}：目前無法前進（路線暴露於已知敵人射界）")
                plans[u.id] = fire or self.idle_plan(u)
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
