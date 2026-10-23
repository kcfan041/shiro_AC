"""規則式戰術 AI：隊長意圖 → 每人計畫。只讀己方情報（docs/archive/combat.md v0.2 §13）。"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from engine.tactical import body
from engine.tactical.geometry import closest_point_on_segment, length, sub
from engine.tactical.sim import TICK_S, Battle, Plan
from engine.tactical.tmap import Node
from engine.tactical.units import WEAPONS, Unit

RISK_W = 6
INFEASIBLE_RISK = 1
CLOSE_BURST = 12.0
HEAVY_FIRE = 50.0
RISK_CAP = {"advance": 1, "supp_advance": 0, "flank": 0}
PATIENCE_STEP = 4
PATIENCE_MAX = 3
RECON_MIN_AMMO = 40
RECON_MAX_AGE_S = 30.0     # 最後已知位置多舊以內才做火力偵察（原 12 步）
AMBUSH_RELOCATE_S = 15.0   # 伏擊開火後多久沒再開火就轉移（原 6 步）
GRENADE_THROW = 12.0
GRENADE_MIN = 4.0
GRENADE_SAFE = 5.0
SMOKE_REUSE_R = 4.0
SMOKE_RADIUS = 3.0
WAIT_MAX_S = 7.5           # 撤退路線暴露時最多躲著等多久（原 3 步）
SMOKE_CLUSTER = 2
GRENADE_COOLDOWN_S = 7.5   # 個人投擲冷卻（原 3 步）
MAX_THREAT_RANGE = 80.0
PATH_HORIZON = 80.0
GRENADE_LAST_RANGE = 8.0
GRENADE_PER_TICK = {"assault": 2}   # 每方每 2.5 秒最多幾顆
SPRINT_EXTRA = 2
MARCH_GAP = 2
BOUND_RANGE = 20.0       # 敵人在這個距離外 → 交替躍進
LR_SMOKE_RANGE = 15.0    # 長距離推進：敵人在這個距離外、路線暴露 → 先放煙再衝
LOCAL_THREAT_R = 70.0
CARRY_RANGE = 20         # 行軍時，倒地隊友在小隊任一人 20 格內 → 帶著走
REPLAN_MAX_AGE_S = 10.0   # 「沒事件就沿用不動」最多沿用多久，之後一定重新評估
PAIR_CACHE_MAX = 600000  # 跨步視線快取（掛在地圖上）的上限；超過就清空重來
ESCORT_LEAD = 4          # 拖運傷員時，其他人最多超前拖運者幾格


def dist_weight(d: float) -> float:
    """暴露的距離權重：≤10 m → 1，30 m → 0.5，≥50 m → 0.25（中間線性）。"""
    if d <= 10:
        return 1.0
    if d <= 30:
        return 1.0 - 0.5 * (d - 10) / 20
    if d <= 50:
        return 0.5 - 0.25 * (d - 30) / 20
    return 0.25


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
    focus: Node | None = None     # 指揮官標定的集火點：優先射擊、壓制離這裡最近的敵人
    sprint: bool = False          # march：衝刺（撤退時）


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
        self.focus: Node | None = None
        self.mpaths: dict[str, tuple[Node, list[Node]]] = {}   # march：每人的目標格與快取路徑
        self._last_plan_t: float | None = None
        self.plan_dt = TICK_S                   # 距上次規劃幾秒（計數器以秒累計，與規劃頻率無關）
        self._gren_times: list[float] = []      # 本方最近投擲的時間（每 2.5 秒上限）
        self._stay: dict[tuple, tuple] = {}     # 沿用「不動」：(用途, 個體) -> (局勢簽章, 評估的步數, 結果)

    def new_intent(self):
        """隊長下達新意圖時重置耐心與固守狀態。"""
        from engine.tactical.breach import BreachState
        self.patience = 0
        self.holding = False
        self.breach = BreachState()
        self._stay = {}

    # ------------------------------------------------------------------ knowledge

    def known(self) -> list[Unit]:
        intel = self.b.intel[self.side]
        return [self.b.units[uid] for uid in sorted(intel.last_known) if self.b.units[uid].alive]

    def spotted(self) -> list[Unit]:
        intel = self.b.intel[self.side]
        return [self.b.units[uid] for uid in sorted(intel.spotted) if self.b.units[uid].alive]

    def suppressed_guess(self) -> set[str]:
        """上個 tick 我方火力明顯落在其身邊（看得到彈著）的敵人，推測為被壓制。"""
        seen = self.b.recent_heavy_fire(self.side)
        return {uid for uid, val in seen.items() if val >= HEAVY_FIRE}

    def threats(self) -> list[tuple[str, Node, str]]:
        ov = getattr(self, "_threats_override", None)
        if ov is not None:
            return ov
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
            if self._exposed_to(node, enode, posture, epost):
                n += 1
        if len(self._exp_cache) > 200000:
            self._exp_cache.clear()
        self._exp_cache[key] = n
        return n

    def wexposure(self, node: Node, threats=None, posture: str = "crouch") -> float:
        """依距離加權的暴露：10 m 內每個威脅算 1，30 m 算 0.5，50 m 以上算 0.25（遠距離推進時不會因偶爾被看見就整條否決）。"""
        threats = self.threats() if threats is None else threats
        key = ("w", self.b.tick, len(self.b.smokes), node, posture, tuple(threats))
        cached = self._exp_cache.get(key)
        if cached is not None:
            return cached
        m = self.b.map
        total = 0.0
        for _, enode, epost in threats:
            d = max(abs(node[0] - enode[0]), abs(node[1] - enode[1])) * m.cell
            if d > MAX_THREAT_RANGE:
                continue
            if self._exposed_to(node, enode, posture, epost):
                total += dist_weight(d)
        self._exp_cache[key] = total
        return total

    def _exposed_to(self, node: Node, enode: Node, posture: str, epost: str) -> bool:
        """站在 node（posture）時，enode 上的威脅（epost）看不看得到我的軀幹（可見度 > 0.3）。
        地圖視線是靜態的：同一組（格、威脅格、姿勢）的結果掛在地圖上跨步、跨戰鬥沿用；煙霧只會讓視線變差，
        所以地圖視線 ≤ 0.3 直接回 False，> 0.3 且有煙時才另外算煙霧。結果與每次重算完全相同。"""
        m = self.b.map
        cache = m.__dict__.setdefault("_pair_sight", {})
        key = (node, enode, posture, epost)
        hit = cache.get(key)
        if hit is None:
            f = m.facing_to(node, enode)
            torso = body.torso_center(m.hug_base(node, f), posture, f)
            eye = body.eye(m.hug_base(enode, m.facing_to(enode, node)), epost)
            hit = (m.sight(eye, torso), eye, torso)
            if len(cache) > PAIR_CACHE_MAX:
                cache.clear()
            cache[key] = hit
        base, eye, torso = hit
        if base <= 0.3:
            return False
        return self.b.sight_from_base(eye, torso, base) > 0.3

    def _sees(self, node: Node, enode: Node) -> bool:
        """蹲在 node 看得到 enode 上方 1 m（打得到那裡的人；可見度 > 0.2）。地圖視線跨步快取，同 _exposed_to。"""
        m = self.b.map
        cache = m.__dict__.setdefault("_pair_sees", {})
        key = (node, enode)
        hit = cache.get(key)
        if hit is None:
            f = m.facing_to(node, enode)
            eye = body.eye(m.hug_base(node, f), "crouch")
            tc = m.center(enode)
            pt = (tc[0], tc[1], tc[2] + 1.0)
            hit = (m.sight(eye, pt), eye, pt)
            if len(cache) > PAIR_CACHE_MAX:
                cache.clear()
            cache[key] = hit
        base, eye, pt = hit
        if base <= 0.2:
            return False
        return self.b.sight_from_base(eye, pt, base) > 0.2

    # ------------------------------------------------------------------ paths

    def paths(self, start: Node, threats, occupied: set[Node],
              max_cost: float = PATH_HORIZON, ap_cap: int | None = None) -> dict[Node, tuple[float, int, list[Node]]]:
        """Dijkstra：成本 = AP + RISK_W × 暴露。回傳 node -> (成本, 累積風險, 路徑)。成本超過 max_cost 的不展開。
        ap_cap：只走本步行動點走得到的格（選本步落點用；不必為了繞路把半徑十幾格內每一格都評估暴露）。"""
        dist = {start: (0.0, 0, [])}
        aps = {start: 0}
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
                ap = aps[node] + step
                if ap_cap is not None and ap > ap_cap:
                    continue
                exp = self.wexposure(nxt, threats)
                c = cost + step + RISK_W * exp
                if nxt not in dist or c < dist[nxt][0]:
                    dist[nxt] = (c, risk + exp, dist[node][2] + [nxt])
                    aps[nxt] = ap
                    heapq.heappush(heap, (c, risk + exp, nxt))
        return dist

    def route(self, start: Node, goal: Node, occupied: set[Node], limit: int = 6000) -> list[Node] | None:
        """A*（只看行動點、不看風險）：沒有已知威脅時的長距離移動。走不到 goal 就走到最接近的點。"""
        m = self.b.map

        def h(n):
            return max(abs(n[0] - goal[0]), abs(n[1] - goal[1]))
        came = {start: None}
        g = {start: 0}
        heap = [(h(start), 0, start)]
        best = start
        n_exp = 0
        while heap and n_exp < limit:
            _, cost, node = heapq.heappop(heap)
            if cost > g[node]:
                continue
            n_exp += 1
            if h(node) < h(best):
                best = node
            if node[:2] == goal[:2]:
                best = node
                break
            for nxt, step in m.neighbors(node):
                if nxt in occupied and nxt[:2] != goal[:2]:
                    continue
                c = cost + step
                if nxt not in g or c < g[nxt]:
                    g[nxt] = c
                    came[nxt] = node
                    heapq.heappush(heap, (c + h(nxt), c, nxt))
        if best == start:
            return None
        path = []
        cur = best
        while cur is not None and cur != start:
            path.append(cur)
            cur = came[cur]
        return path[::-1]

    def reachable(self, start: Node, ap: int, occupied: set[Node]) -> dict[Node, list[Node]]:
        """本 tick 用 ap 行動點走得到的所有格（不看風險，只看行動點）：node -> 最短路徑。"""
        best = {start: (0, [])}
        frontier = [(0, start)]
        while frontier:
            cost, node = heapq.heappop(frontier)
            if cost > best[node][0]:
                continue
            for nxt, step in self.b.map.neighbors(node):
                c = cost + step
                if c > ap or nxt in occupied:
                    continue
                if nxt not in best or c < best[nxt][0]:
                    best[nxt] = (c, best[node][1] + [nxt])
                    heapq.heappush(frontier, (c, nxt))
        return {n: p for n, (_, p) in best.items()}

    @staticmethod
    def ap_len(battle: Battle, start: Node, path: list[Node], extra: int = 0) -> list[Node]:
        mover = next((u for u in battle.units.values() if u.alive and u.pos == start), None)
        ap = (battle.move_budget(mover) if mover else 2) + extra
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

    def lane_clear(self, u: Unit, aim, spread: float = 0.0, pos: Node | None = None,
                   ignore: set | None = None) -> bool:
        """射線淨空：射手到目標點之間，沒有我方（同陣營）個體的軀幹在射線附近（1 m＋散布）。
        近距離（2 m 內）就在身旁的隊友也算擋住。pos：假設射手站在這格（不改真實位置）；ignore：不算這些人。"""
        eye = u.eye(self.b.map) if pos is None else body.eye(self.b.map.hug_base(pos, u.facing), u.posture)
        d = length(sub(aim, eye))
        if d < 1e-6:
            return True
        for f in self.b.units.values():
            if f.id == u.id or not (f.alive or f.down) or f.exited or self.b.hostile(f.side, u.side):
                continue
            if ignore and f.id in ignore:
                continue
            t = f.torso(self.b.map)
            pt, k = closest_point_on_segment(eye, aim, t)
            if k <= 0.02 or k >= 0.98:
                continue
            if length(sub(pt, t)) < 0.9 + spread * d * k:
                return False
        return True

    def fire_plan(self, u: Unit) -> Plan | None:
        if not u.can_shoot():
            return None
        targets = [e for e in self.spotted() if self.b.visible_parts(u, e)
                   and self.lane_clear(u, e.torso(self.b.map), WEAPONS[u.weapon]["sigma"] * 2)]
        if not targets:
            return None
        if self.focus is not None:
            e = min(targets, key=lambda t: (self._d(t.pos, self.focus) > 3, self._d(t.pos, self.focus), t.id))
        else:
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
        order = self.focus_order(self.focus or anchor)
        if not order:
            return None
        covered = self.suppressed_guess()
        pending = [t for t in order if t.id not in covered] or order
        e = None
        for t in pending:
            node, _ = self.b.intel[self.side].last_known[t.id]
            f = self.b.map.facing_to(node, u.pos)
            pt = body.torso_center(self.b.map.hug_base(node, f), t.posture, f)
            if (self.b.sight(u.eye(self.b.map), pt) > 0 or self._near_los(u, node)) and \
                    self.lane_clear(u, pt, WEAPONS[u.weapon]["sigma"] * 6):
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
        """本 tick 能走的步數上限對應的搜尋成本（含風險加權的餘裕）。
        大地圖：風險餘裕上限 RISK_W×4（行軍時 6 行動點 → 原本可搜到 120，幾乎半張圖，太慢；只用得到本步走得到的終點）。"""
        steps = self.b.move_budget(u)
        if self.b.map.w > 40:
            return steps * 2 + RISK_W * min(steps * 3, 4)
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
        threats = self.threats()
        if threats and self.exposure(u.pos, threats) > 0 and not self.b.has_rest(u, tuple(u.facing)):
            return Plan(mode="overwatch")
        return Plan(mode="brace")

    # ------------------------------------------------------------------ movement choice

    def best_move(self, u: Unit, goal: Node | None, occupied: set[Node], threats, mode: str = "advance",
                  flank_target: Node | None = None, risk_cap: int = 0) -> tuple[list[Node] | None, int]:
        if not threats:
            return self._best_move(u, goal, occupied, threats, mode, flank_target, risk_cap)
        return self._reuse_stay("move", u, threats, (goal, mode, flank_target, risk_cap),
                                lambda: self._best_move(u, goal, occupied, threats, mode, flank_target, risk_cap),
                                lambda r: not r[0])

    def _best_move(self, u: Unit, goal: Node | None, occupied: set[Node], threats, mode: str = "advance",
                   flank_target: Node | None = None, risk_cap: int = 0) -> tuple[list[Node] | None, int]:
        if not threats and goal is not None and mode != "flank":
            path = self.route(u.pos, goal, occupied)
            step = self.ap_len(self.b, u.pos, path) if path else []
            return (step or None), 0
        table = self.paths(u.pos, threats, occupied, max_cost=self.move_horizon(u), ap_cap=self.b.move_budget(u))
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
            seg_risk = sum(self.wexposure(n, threats) for n in step)
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
            st["last_fire"] = self.b.tick - 1         # 戳記：上一步開過火
        compromised = self.b.believes_any_seen(units)
        trigger = st["sprung"] or any(
            length(sub(e.torso(self.b.map), u.eye(self.b.map))) <= intent.ambush_range * (1 + 0.4 * u.trait("aggr"))
            and self.b.visible_parts(u, e)
            for u in units for e in self.spotted()) or (compromised and any(
                self.b.visible_parts(u, e) for u in units for e in self.spotted()))
        if compromised and not st["sprung"] and trigger:
            self.reports.append(f"T{self.b.tick} 伏擊位置可能已暴露，提早開火")
        quiet = self.ago(st.get("last_fire", self.b.tick)) if st["sprung"] else 0.0
        threats = self.threats()
        for u in units:
            hit_recently = u.suppression > 0 or u.damage or self.b.believes_seen(u) in ("hit", "near_miss", "watched")
            retry = st.setdefault("relocate_after", {})
            if st["sprung"] and (hit_recently or quiet >= AMBUSH_RELOCATE_S - 1e-9) and not u.pinned \
                    and self.b.now >= retry.get(u.id, -1.0) - 1e-9:
                dest = self.relocate(u, occupied, threats)
                if dest:
                    plans[u.id] = Plan(mode="move", path=dest)
                    occupied.add(dest[-1])
                    occupied.discard(u.pos)
                    if quiet >= AMBUSH_RELOCATE_S - 1e-9:
                        st["last_fire"] = self.b.tick
                    continue
                retry[u.id] = self.b.now + 3 * TICK_S   # 附近找不到更好的隱蔽點：過 7.5 秒再找
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
        if self.b.map.w > 40:
            table = {n: (0, sum(self.exposure(x, threats, posture="prone") for x in p[:-1]), p)
                     for n, p in self.reachable(u.pos, self.b.move_budget(u), occupied).items()}
        else:
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
                 and self.ago(lk[e.id][1]) <= RECON_MAX_AGE_S + 1e-9]
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
        if u.id in self.last_throw and self.ago(self.last_throw[u.id]) < GRENADE_COOLDOWN_S - 1e-9:
            return None
        lk = self.b.intel[self.side].last_known
        m = self.b.map
        friends = [f for f in self.b.alive(self.side)]
        best = None
        for e in self.known():
            if taken is not None and e.id in taken:
                continue
            if self.ago(lk[e.id][1]) > 3 * TICK_S + 1e-9:
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

    AID_COMMIT_S = 7.5        # 決定去救之後至少堅持這麼久（不因敵人時有時無來回切換）
    DRAG_MAX_S = 10.0         # 穩定後最多拖這麼久到掩護

    def assign_rescuers(self, units: list[Unit], threats) -> dict[str, str]:
        """每個倒地的友方（沒陣亡、沒撤出）只派一個救援者：已派的人還能救就沿用；否則挑最近、沒被壓制、
        符合救援條件的人。回傳 {救援者 id: 倒地者 id}。"""
        st = self.__dict__.setdefault("aid_assign", {})
        free = {u.id: u for u in units if u.alive and not u.pinned}
        downed = sorted((d for d in self.b.units.values() if not self.b.hostile(d.side, self.side) and d.down
                         and not d.dead and not d.exited), key=lambda d: (d.side != self.side, d.id))
        out: dict[str, str] = {}
        taken: set[str] = set()
        for d in downed:
            cur = st.get(d.id)
            if cur and cur["by"] in free and cur["by"] not in taken:
                out[cur["by"]] = d.id
                taken.add(cur["by"])
                continue
            cands = [u for u in free.values() if u.id not in taken and self._d(u.pos, d.pos) <= self._reach(u, d, threats)]
            if not cands:
                st.pop(d.id, None)
                continue
            u = min(cands, key=lambda x: (self._d(x.pos, d.pos), x.id))
            st[d.id] = {"by": u.id, "since": self.b.tick, "drag": 0}
            out[u.id] = d.id
            taken.add(u.id)
        for k in list(st):
            if k not in {d.id for d in downed}:
                st.pop(k, None)
        return out

    def _reach(self, u: Unit, d: Unit, threats) -> int:
        """願意跑多遠去救（格）：羈絆越高越遠；看得到敵人時只有羈絆夠高的人去（煙霧掩護另計）。
        附近沒有威脅（交戰外）：同隊一定有人回頭去救（CARRY_RANGE 內）。"""
        bond = u.trait("bond")
        if not threats and d.side == self.side:
            return CARRY_RANGE
        screened = self.smoke_live_near(self.b.map.center(d.pos)[:2], SMOKE_RADIUS + 2)
        if self.b.intel[self.side].spotted and bond < 0.3 and not screened:
            return -1
        tolerate = max(0, int(round(2 * bond)))
        if self.exposure(d.pos, threats) > tolerate and not screened and self._d(u.pos, d.pos) > 1:
            return -1
        return 3 + int(round(2 * bond))

    def aid_plan(self, u: Unit, occupied: set[Node], threats, target: str | None = None) -> Plan | None:
        """急救／拖到掩護（只處理指派給這個人的倒地者；同格友軍也救、自己隊優先）。
        穩定後：已不暴露就不拖；暴露時最多拖 DRAG_MAX 步到不暴露的位置；附近沒有已知敵人不拖。"""
        st = self.__dict__.setdefault("aid_assign", {})
        d = self.b.units.get(target) if target else None
        if d is None or not d.down or d.dead or d.exited:
            return None
        rec = st.get(d.id) or {"by": u.id, "since": self.b.tick, "drag": 0}
        if d.pos == u.pos or self.b.adjacent(d.pos, u.pos):
            if not d.stabilized:
                return Plan(mode="aid", target=d.id, why=f"為 {d.id.split(':')[-1]} 止血")
            if not threats or self.exposure(d.pos, threats) == 0 or rec["drag"] >= self.DRAG_MAX_S - 1e-9:
                return None
            back = self.retreat_step(u, occupied, threats)
            if back:
                rec["drag"] += self.plan_dt
                st[d.id] = rec
                return Plan(mode="drag", target=d.id, path=back[:1], why=f"把 {d.id.split(':')[-1]} 拖到掩護")
            return None
        if d.stabilized:
            return None
        committed = self.ago(rec["since"]) < self.AID_COMMIT_S - 1e-9
        if not committed and self._d(d.pos, u.pos) > self._reach(u, d, threats):
            return None
        if not threats:
            path = self.route(u.pos, d.pos, occupied - {d.pos})
            step = self.ap_len(self.b, u.pos, path or [])
            if step and step[-1] == d.pos:
                step = step[:-1]
            if step:
                return Plan(mode="move", path=step, why=f"去救倒地的 {d.id.split(':')[-1]}")
            return None
        screened = self.smoke_live_near(self.b.map.center(d.pos)[:2], SMOKE_RADIUS + 2)
        tolerate = max(0, int(round(2 * u.trait("bond")))) + (2 if committed else 0)
        horizon = (self._d(d.pos, u.pos) + 2) * 2 + RISK_W * (tolerate + 1)
        table = self.paths(u.pos, threats, occupied - {d.pos}, max_cost=min(PATH_HORIZON, horizon))
        best = None
        for n in [x for x, _ in self.b.map.neighbors(d.pos)] + [d.pos]:
            if n in table and n not in occupied and (table[n][1] <= tolerate or screened):
                if best is None or table[n][0] < table[best][0]:
                    best = n
        if best:
            step = self.ap_len(self.b, u.pos, table[best][2])
            if step:
                return Plan(mode="move", path=step, sprint=bool(threats),
                            why=f"去救倒地的 {d.id.split(':')[-1]}")
        return None

    def retreat_step(self, u: Unit, occupied: set[Node], threats, toward: tuple[int, int] | None = None,
                     extra: int = 0) -> list[Node] | None:
        """往後方（預設：遠離已知敵人的平均位置）移動。分數＝後撤距離 − 暴露（終點暴露加重）。
        不會停下來等：只要有往後的路就走。extra＝衝刺多出的行動點。"""
        lk = self.b.intel[self.side].last_known
        if toward is None:
            if lk:
                ex = sum(n[0] for n, _ in lk.values()) / len(lk)
                ey = sum(n[1] for n, _ in lk.values()) / len(lk)
                toward = (u.pos[0] - ex, u.pos[1] - ey)
            else:
                toward = (0, -1)
        n = math.hypot(*toward) or 1.0
        tx, ty = toward[0] / n, toward[1] / n
        if self.b.map.w > 40:
            # 大地圖：只看本步走得到的格子（行動點），再用暴露評分；不做帶風險的大範圍搜尋
            table = {n: (0, 0, p) for n, p in self.reachable(u.pos, self.b.move_budget(u) + extra, occupied).items()}
        else:
            table = self.paths(u.pos, threats, occupied, max_cost=self.move_horizon(u) + extra * 4)
        best, best_score = None, None
        for node, (_, risk, path) in table.items():
            if node == u.pos:
                continue
            step = self.ap_len(self.b, u.pos, path, extra)
            if not step:
                continue
            dest = step[-1]
            gain = (dest[0] - u.pos[0]) * tx + (dest[1] - u.pos[1]) * ty
            if gain <= 0.3:
                continue
            score = gain - 1.5 * sum(self.exposure(x, threats) for x in step[:-1]) \
                - 3 * self.exposure(dest, threats)
            if best_score is None or score > best_score:
                best, best_score = step, score
        return best

    # ------------------------------------------------------------------ 煙霧

    def smoke_live_near(self, point: tuple[float, float], radius: float = SMOKE_REUSE_R) -> bool:
        """己方已知（看得到）的煙霧：仍在或即將生效的煙霧離此點夠近 → 不重複投擲。"""
        for s in self.b.smokes:
            if s["expires"] - self.b.now < 2 * TICK_S - 1e-9:      # 5 秒內就散 → 不算
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
        """撤退：路線暴露先放煙；全員一起往後撤（暴露的人衝刺），最多一人在掩體後壓制掩護；
        拖走倒地隊友；到後方邊緣撤出。不停下來等。"""
        plans: dict[str, Plan] = {}
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
        # 全員一起撤（不再兩組輪流）；只留一名「壓制掩護」者：本身在掩體後、看得到敵人、最後一個走。
        cover_man = None
        if len(units) >= 3 and tnodes:
            cands = [u for u in units if u.id not in plans and u.id not in draggers and u.can_shoot()
                     and self.exposure(u.pos, threats) == 0 and self._period() % 3 != 2]
            if cands:
                cover_man = min(cands, key=lambda u: (-self._rear_score(u, toward), u.id)).id
        for u in units:
            if u.id in plans:
                continue
            dragging = u.id in draggers
            if self.b.allow_exit and self.b.on_edge(u.pos) and self._edge_is_rear(u, toward):
                if dragging:
                    plans[u.id] = Plan(mode="drag", target=draggers[u.id], path=[u.pos], posture="__exit__")
                else:
                    plans[u.id] = Plan(mode="move", path=[u.pos], target="__exit__")
                continue
            if u.id == cover_man:
                p = self.suppress_plan(u) or self.fire_plan(u)
                if p is not None:
                    plans[u.id] = p
                    continue
            exposed_now = self.exposure(u.pos, threats) > 0 or self.b.is_targeted(u)
            sprint = exposed_now and not dragging and u.posture != "prone"
            step = self.retreat_step(u, occupied, threats, toward, extra=SPRINT_EXTRA if sprint else 0)
            if step is None and exposed_now:
                step = self.cover_step(u, occupied, threats, toward=self._rear_vec(u, toward))
            if step:
                exit_now = self.b.allow_exit and self.b.on_edge(step[-1]) and \
                    self._edge_is_rear_node(step[-1], u, toward)
                if dragging:
                    d = self.b.units[draggers[u.id]]
                    plans[u.id] = Plan(mode="drag", target=d.id, path=step[:1])
                    occupied.add(step[0])
                else:
                    plans[u.id] = Plan(mode="move", path=step, target="__exit__" if exit_now else None,
                                       sprint=sprint, posture="crouch" if u.posture == "prone" else None)
                    occupied.add(step[-1])
                occupied.discard(u.pos)
                continue
            plans[u.id] = (self.suppress_plan(u) or self.fire_plan(u) or self.recon_fire(u)
                           or self.idle_plan(u))
        return plans

    def _rear_score(self, u: Unit, toward) -> float:
        """越靠近敵人（越前面）分數越高：掩護者選最前面那個，讓其他人先走。"""
        v = self._rear_vec(u, toward)
        return -(u.pos[0] * v[0] + u.pos[1] * v[1])

    def wait_ok(self, u: Unit) -> bool:
        """躲在掩體後、撤退路線暴露時：等煙霧或壓制，最多等 WAIT_MAX_S 秒，之後硬撤。"""
        n = self.waits.get(u.id, 0.0) + self.plan_dt
        self.waits[u.id] = n
        return n <= WAIT_MAX_S + 1e-9

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
            far = self._nearest_threat_m(units, threats)
            if not want and far is not None and far >= LR_SMOKE_RANGE:
                want = sum(1 for u in units if self.exposure(u.pos, threats) > 0) >= max(1, len(units) // 2)
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
        if self.side in self.b.flanked and self.ago(self.b.flanked[self.side]) <= 3 * TICK_S + 1e-9:
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

    def _period(self) -> int:
        """第幾個 2.5 秒（輪替用）。"""
        return int(round(self.b.now / TICK_S, 6))

    def ago(self, stamp) -> float:
        """某個戰術步戳記距今幾秒。"""
        return (self.b.tick - stamp) * self.b.dt

    @property
    def pu(self) -> float:
        """這次規劃代表多少個「2.5 秒」（耐心等以此累計）。"""
        return self.plan_dt / TICK_S

    def plan(self, intent: Intent, units: list[Unit] | None = None) -> dict[str, Plan]:
        now = self.b.now
        self.plan_dt = TICK_S if self._last_plan_t is None else max(self.b.dt, now - self._last_plan_t)
        self._last_plan_t = now
        self.b.freeze_contact(True)
        try:
            plans = self._plan(intent, units)
            self._unblock_braces(plans)
            self._annotate(intent, plans)
            return plans
        finally:
            self.b.freeze_contact(False)
            self._threats_override = None

    BRACE_LANE = 15.0         # 檢查架槍方向前方這麼遠（m）內有沒有隊友擋線

    def _lane_point(self, u: Unit, d, pos: Node | None = None) -> tuple | None:
        """架槍／警戒方向上的檢查點：有已知威脅在扇區內 → 威脅位置；否則前方 BRACE_LANE。pos：假設站在這格。"""
        n = math.hypot(d[0], d[1])
        if n < 1e-6:
            return None
        m = self.b.map
        here = u.pos if pos is None else pos
        for _, enode, _ in self.threats():
            v = (enode[0] - here[0], enode[1] - here[1])
            if self._within_arc(d, v):
                c = m.center(enode)
                return (c[0], c[1], c[2] + 1.0)
        e = u.eye(m) if pos is None else body.eye(m.hug_base(pos, u.facing), u.posture)
        k = self.BRACE_LANE / n
        return (e[0] + d[0] * k, e[1] + d[1] * k, e[2])

    def brace_blocked(self, u: Unit, d, ignore: set | None = None, pos: Node | None = None) -> bool:
        """架槍方向被隊友擋住。ignore：假設這些人不在（例如這一步會移開的人）；pos：假設 u 站在這格。
        都只是參數，不暫改真實單位（規劃只讀狀態，才能拿到快照上平行跑）。"""
        pt = self._lane_point(u, d, pos)
        if pt is None:
            return False
        return not self.lane_clear(u, pt, WEAPONS[u.weapon]["sigma"] * 2, pos=pos, ignore=ignore)

    def _unblock_braces(self, plans: dict[str, Plan]):
        """架槍（或已架槍警戒）的方向被隊友擋住：本步走得到、射線淨空的附近位置挪過去再架；
        找不到就換一個沒被擋的警戒方向。結算時被擋就不開火，架了也是白架。"""
        occupied = {x.pos for x in self.b.alive()} | {p.path[-1] for p in plans.values() if p.mode == "move" and p.path}
        movers = {uid for uid, p in plans.items() if p.mode in ("move", "drag") and p.path}
        streak = self.__dict__.setdefault("_lane_block", {})
        for uid in sorted(plans):
            p = plans[uid]
            u = self.b.units.get(uid)
            if u is None or not u.alive or u.pinned:
                continue
            if p.mode == "brace":
                d = p.watch or u.watch or u.facing
            elif p.mode == "overwatch" and u.braced:
                d = p.watch or u.braced
            else:
                streak.pop(uid, None)
                continue
            # 擋線的隊友這一步正在移動 → 多半會讓開，不急著動
            if not d or not self.brace_blocked(u, d, ignore=None) or not self.brace_blocked(u, d, ignore=movers):
                streak.pop(uid, None)
                continue
            n = streak[uid] = streak.get(uid, 0.0) + self.plan_dt
            if u.braced and n < 2 * TICK_S - 1e-9:
                continue
            moved = self._side_step(u, d, occupied)
            if moved is not None:
                plans[uid] = Plan(mode="move", path=moved, posture="crouch" if u.posture == "prone" else None,
                                  why="架槍射線被隊友擋住，往旁邊挪")
                occupied.add(moved[-1])
                continue
            alt = self._clear_dir(u, d)
            if alt is not None:
                plans[uid] = Plan(mode="brace", watch=alt, why="架槍射線被隊友擋住，改架別的方向")
            else:
                plans[uid] = Plan(mode="overwatch", watch=d, why="射線被隊友擋住，等隊友讓開")

    def _side_step(self, u: Unit, d, occupied: set) -> list[Node] | None:
        """本步走得到（不衝刺）、往側邊或後方、射線淨空且不比現在更暴露的位置。"""
        threats = self.threats()
        here = self.exposure(u.pos, threats)
        reach = self.reachable(u.pos, self.b.move_budget(u), occupied)
        best, best_v = None, None
        for node, path in reach.items():
            if node == u.pos or not path:
                continue
            fwd = (node[0] - u.pos[0]) * d[0] + (node[1] - u.pos[1]) * d[1]
            if fwd > 0.5 * math.hypot(*d):
                continue
            exp = self.exposure(node, threats)
            if exp > here or (threats and exp > 0):
                continue
            if self.brace_blocked(u, d, pos=node):
                continue
            v = -len(path) - 2 * exp + (1.0 if self.b.has_rest(u, tuple(d)) else 0.0)
            if best_v is None or v > best_v:
                best, best_v = path, v
        return best

    def _clear_dir(self, u: Unit, d) -> tuple | None:
        """原方向 ±45°、±90° 裡第一個沒被擋的方向（優先有已知威脅的）。"""
        a0 = math.atan2(d[1], d[0])
        tvecs = [(n[0] - u.pos[0], n[1] - u.pos[1]) for _, n, _ in self.threats()]
        for off in (45, -45, 90, -90):
            a = a0 + math.radians(off)
            v = (math.cos(a), math.sin(a))
            if tvecs and not any(self._within_arc(v, t) for t in tvecs):
                continue
            if not self.brace_blocked(u, v):
                return v
        return None

    INTENT_MOVE = {"advance": "向目標推進", "supp_advance": "掩護推進中", "flank": "包抄移動中",
                   "assault": "突擊衝鋒", "march": "行軍中", "retreat": "撤退中", "breach": "破門行動",
                   "ambush": "伏擊就位", "hold": "調整位置"}

    def _annotate(self, intent: Intent, plans: dict[str, Plan]):
        """沒有具體理由的計畫，依模式與意圖補一個（只給玩家看）。"""
        for uid, p in plans.items():
            if p.why:
                continue
            u = self.b.units.get(uid)
            if p.mode == "move":
                p.why = ("衝刺" if p.sprint and intent.kind != "march" else "") + self.INTENT_MOVE.get(intent.kind, "移動中")
            elif p.mode == "shoot":
                if p.fire == "suppress":
                    p.why = "壓制射擊" + ("（看不到目標，朝最後位置）" if p.target is None else "")
                else:
                    p.why = "射擊看得到的敵人"
            elif p.mode == "overwatch":
                if u is not None and u.braced:
                    p.why = "架槍警戒"
                elif u is not None and u.dazzled:
                    p.why = "光學過載，只能警戒"
                elif u is not None and u.suppressed:
                    p.why = "被壓制，只能警戒"
                else:
                    p.why = "警戒"
            elif p.mode == "brace":
                p.why = "架槍中"
            elif p.mode == "posture" and p.posture == "prone":
                p.why = "被壓制，臥倒"
            elif p.mode == "grenade":
                p.why = "向看不到的敵人投擲手榴彈"
            elif p.mode == "flash":
                p.why = "投擲閃光彈"
            elif p.mode == "smoke":
                p.why = "投擲煙霧掩護"
            elif p.mode == "aid":
                p.why = "急救倒地隊員"
            elif p.mode == "drag":
                p.why = "拖倒地隊員到掩護"
            elif p.mode == "melee":
                p.why = "近身突擊"
            elif p.mode in ("take", "give"):
                p.why = "撿取投擲物" if p.mode == "take" else "傳遞投擲物"
            elif p.mode == "idle" and u is not None and not u.can_shoot():
                p.why = "無法射擊（彈藥耗盡或雙臂受傷）"

    def local_threats(self, units: list[Unit]) -> list:
        """大地圖：只把這群人附近（LOCAL_THREAT_R）內的已知敵人當威脅；單格地圖不過濾。"""
        th = self.threats()
        if self.b.map.w <= 40 or not units:
            return th
        r = LOCAL_THREAT_R / self.b.map.cell
        return [t for t in th if any(max(abs(t[1][0] - u.pos[0]), abs(t[1][1] - u.pos[1])) <= r for u in units)]

    def march_plans(self, units: list[Unit], intent: Intent, occupied: set[Node], threats) -> dict[str, Plan]:
        """連續地圖行軍：排成縱隊沿路線走向 intent.goal（每人目標＝目標點往回排開，間隔 MARCH_GAP 格）。
        附近有已知敵人：邊走邊打（走完順手開一槍）；只有被壓制、或正在挨打又站在暴露位置的人才停下找掩體／還擊。"""
        plans: dict[str, Plan] = {}
        goal = intent.goal
        if goal is None:
            return {u.id: self.idle_plan(u) for u in units}
        carry = self.carry_plans(units, goal, occupied, threats)
        if carry:
            plans.update(carry)
            units = [u for u in units if u.id not in carry]
            if not units:
                return plans
            carriers = [self.b.units[uid] for uid, p in carry.items() if p.mode in ("drag", "move", "aid")]
            if carriers and not threats:
                # 護送：其他人不超前拖運者太多（全隊一起放慢）
                slow = min(carriers, key=lambda c: (-self._d(c.pos, goal), c.id))
                for u in units:
                    if self._d(u.pos, goal) + ESCORT_LEAD < self._d(slow.pos, goal) or u.pinned:
                        plans[u.id] = Plan(mode="overwatch", why="等拖運傷員的隊友跟上")
                units = [u for u in units if u.id not in plans]
                if not units:
                    return plans
        lead = min(units, key=lambda u: (self._d(u.pos, goal), u.id))
        order = [lead] + sorted([u for u in units if u is not lead], key=lambda u: (self._d(u.pos, lead.pos), u.id))
        prev = None
        for i, u in enumerate(order):
            if u.pinned:
                plans[u.id] = self._pinned(u, occupied, threats)
                prev = prev or u
                continue
            if threats and u.suppressed and self.exposure(u.pos, threats) > 0:
                cov = self.take_cover_plan(u, occupied, threats)
                if cov is not None:
                    cov.why = "行軍中被壓制，先找掩體"
                    plans[u.id] = cov
                    occupied.add(cov.path[-1])
                    occupied.discard(u.pos)
                    prev = prev or u
                    continue
                shot = self.fire_plan(u)
                if shot is not None:
                    shot.why = "行軍中被壓制，就地還擊"
                    plans[u.id] = shot
                    prev = prev or u
                    continue
            target = goal if prev is None else prev.pos
            if prev is not None and self._d(u.pos, target) <= MARCH_GAP:
                plans[u.id] = (self.fire_plan(u) or Plan(mode="overwatch")) if threats else Plan()
                if threats and plans[u.id].mode == "shoot":
                    plans[u.id].why = "等前面的人拉開距離，先射擊"
                prev = u
                continue
            leading = target is goal
            path = self._march_path(u, target, occupied)
            if path:
                hot = bool(threats)
                step = self.ap_len(self.b, u.pos, path, SPRINT_EXTRA if (intent.sprint or hot) else 0)
                if not leading:
                    while len(step) > 1 and self._d(step[-1], target) < MARCH_GAP:
                        step = step[:-1]
                if step:
                    plans[u.id] = Plan(mode="move", path=step, sprint=intent.sprint or hot,
                                       posture="crouch" if u.posture == "prone" else None,
                                       snap=hot and not intent.sprint,
                                       why="依命令行軍，邊走邊打" if hot else None)
                    occupied.add(step[-1])
                    occupied.discard(u.pos)
                    prev = u
                    continue
            plans[u.id] = (self.fire_plan(u) or Plan(mode="overwatch")) if threats else Plan()
            prev = u
        return plans

    def carry_plans(self, units: list[Unit], goal: Node, occupied: set[Node], threats) -> dict[str, Plan]:
        """行軍時帶著倒地的隊友：每個倒地者派一人（assign_rescuers）。還沒穩定 → 先止血；已穩定、附近沒有
        已知敵人 → 拖著往目標走（拖人行動點加倍，自然變慢）；有敵人 → 放下（交給一般戰鬥規則，最多拖到掩護）。"""
        mine = {u.id for u in units}
        downed = [d for d in self.b.units.values() if d.side == self.side and d.down and not d.dead
                  and not d.exited and any(self._d(d.pos, u.pos) <= CARRY_RANGE for u in units)]
        if not downed:
            return {}
        rescuers = self.assign_rescuers(units, threats if threats else [])
        out: dict[str, Plan] = {}
        for uid, did in rescuers.items():
            if uid not in mine:
                continue
            u, d = self.b.units[uid], self.b.units[did]
            if not d.stabilized or threats:
                p = self.aid_plan(u, occupied, threats, target=did)
                if p is not None:
                    out[uid] = p
                    if p.path:
                        occupied.add(p.path[-1])
                continue
            if not (d.pos == u.pos or self.b.adjacent(d.pos, u.pos)):
                path = self.route(u.pos, d.pos, occupied - {d.pos})
                if path:
                    step = self.ap_len(self.b, u.pos, path)
                    if step and step[-1] == d.pos:
                        step = step[:-1]
                    if step:
                        out[uid] = Plan(mode="move", path=step, why=f"回頭去扛 {did.split(':')[-1]}")
                        occupied.add(step[-1])
                        continue
                out[uid] = Plan(mode="overwatch", why=f"守著 {did.split(':')[-1]}")
                continue
            path = self._march_path(u, goal, occupied - {d.pos})
            if path:
                out[uid] = Plan(mode="drag", target=did, path=path[:3],
                                why=f"拖運傷員 {did.split(':')[-1]}（行軍放慢）")
                occupied.add(path[0])
            else:
                out[uid] = Plan(mode="overwatch", why=f"守著 {did.split(':')[-1]}")
        return out

    def _march_path(self, u: Unit, target: Node, occupied: set[Node]) -> list[Node] | None:
        """快取路徑：目標沒變且路徑第一步仍可走 → 沿用；否則重算（A*）。"""
        cached = self.mpaths.get(u.id)
        if cached and cached[0] == target:
            path = cached[1]
            if u.pos in path:              # 已經走到路徑中段（上次計畫只走了一部分）→ 從目前位置接著走
                path = path[path.index(u.pos) + 1:]
            if path and self.b.map.step_cost(u.pos, path[0]) is not None and path[0] not in occupied:
                self.mpaths[u.id] = (target, path)
                return path
        path = self.route(u.pos, target, occupied - {target})
        if path:
            self.mpaths[u.id] = (target, path)
        return path

    def _plan(self, intent: Intent, units: list[Unit] | None = None) -> dict[str, Plan]:
        self.focus = intent.focus
        units = [u for u in (units or self.b.alive(self.side))]
        units = sorted(units, key=lambda u: u.id)
        occupied = {u.pos for u in self.b.alive()} | {u.pos for u in self.b.units.values()
                                                       if u.down and not u.exited and not u.dead}
        threats = self.local_threats(units)
        self._threats_override = threats
        state = self.b.morale_state(self.side)
        if intent.kind == "march" and (state not in ("rout", "surrender") or not self.b.allow_exit):
            if state in ("rout", "surrender") and not self.routed:
                self.routed = True
                self.reports.append(f"T{self.b.tick} 士氣崩潰，撤往後方")
            return self.march_plans(units, intent, occupied, threats)
        if (state in ("rout", "surrender") and self.b.allow_exit) or intent.kind == "retreat":
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
        now = self.b.now
        self._gren_times = [t for t in self._gren_times if t > now - TICK_S + 1e-9]
        gren_left = GRENADE_PER_TICK.get(intent.kind, 1) - len(self._gren_times)
        rescuers = self.assign_rescuers(units, threats) if intent.kind not in ("ambush", "breach") else {}
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
                    self._gren_times.append(now)
            if p is None and u.id in rescuers:
                p = self.aid_plan(u, occupied | used, threats, target=rescuers[u.id])
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

        cap = RISK_CAP.get(k, 0) + int(self.patience // PATIENCE_STEP)
        cap_maxed = self.patience >= PATIENCE_STEP * PATIENCE_MAX

        if k in ("advance", "supp_advance", "flank") and not self.holding and len(units) >= 2:
            far = self._nearest_threat_m(units, threats)
            if far is not None and far >= BOUND_RANGE:
                return self.bounding_plans(intent, units, occupied, threats, cap)

        for u in units:
            if u.pinned:
                plans[u.id] = self._pinned(u, occupied | reserved, threats)
                continue
            if u.posture == "prone":
                plans[u.id] = Plan(mode="posture", posture="crouch")
                continue
            if k == "hold" or self.holding:
                mv = self.take_cover_plan(u, occupied | reserved, threats)
                if mv is None and threats and k == "hold" and not self.holding and self.fire_plan(u) is None \
                        and self.b.recent_incoming(self.side):
                    mv = self.firing_position_plan(u, occupied | reserved, threats)
                if mv is not None:
                    plans[u.id] = mv
                    reserved.add(mv.path[-1])
                    occupied.discard(u.pos)
                    continue
                plans[u.id] = self.fallback(u, occupied, reserved, threats)
                continue

            moving_turn = (half[u.id] == self._period() % 2) if k == "advance" else (half[u.id] == 1)
            if (k in ("supp_advance", "flank") and half[u.id] == 0) or (k == "advance" and not moving_turn):
                shot = (self.suppress_plan(u, intent.goal) if k != "advance" else None) or self.fire_plan(u)
                if shot is None:
                    mv = self.take_cover_plan(u, occupied | reserved, threats)
                    if mv is not None:
                        plans[u.id] = mv
                        reserved.add(mv.path[-1])
                        occupied.discard(u.pos)
                        continue
                plans[u.id] = shot or self.fallback(u, occupied, reserved, threats)
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
                p = fire or self.fallback(u, occupied, reserved, threats)
                if not p.why and threats and min_risk > cap:
                    p.why = "前進路線都暴露在敵人射界，暫停推進"
                plans[u.id] = p

        if k in ("advance", "supp_advance", "flank") and not self.holding:
            progress = moved_any or any(u.fired for u in units)
            arrived = intent.goal is not None and any(self._d(u.pos, intent.goal) <= 1 for u in units)
            if arrived and self.known() and not moved_any:
                self.holding = True
                self.reports.append(f"T{self.b.tick} 已抵達目標，仍有已知敵人在附近，就地固守，請求新指示")
            elif not moved_any and not stalled and not arrived and self.known():
                stalled = True
            if progress and not stalled:
                self.patience = max(0, self.patience - self.pu)
            elif stalled or not moved_any:
                before = self.patience
                self.patience += self.pu
                if stalled and int(self.patience // PATIENCE_STEP) > int(before // PATIENCE_STEP) and not cap_maxed:
                    self.reports.append(f"T{self.b.tick} 路線暴露於已知敵人射界，提高可接受風險（{cap + 1}）")
            if cap_maxed and stalled:
                self.holding = True
                self.reports.append(f"T{self.b.tick} 無法推進：所有路線都暴露於已知敵人射界，"
                                    f"已就地固守，請求新指示")
        self.assign_watch(plans, units, intent.goal)
        return plans

    def _nearest_threat_m(self, units, threats) -> float | None:
        if not threats or not units:
            return None
        return min(max(abs(u.pos[0] - t[1][0]), abs(u.pos[1] - t[1][1])) for u in units for t in threats) * self.b.map.cell

    def bounding_plans(self, intent: Intent, units: list[Unit], occupied: set[Node], threats, cap) -> dict[str, Plan]:
        """交替躍進（敵人在 BOUND_RANGE 外）：支援組（射手步槍、機槍優先）壓制已知敵人；推進組衝刺到下一個
        有掩體的位置（往目標更近、暴露少）；推進組全部到位（或兩步）就換手。包抄時推進組往側翼點走。"""
        b = self.b
        st = self.__dict__.setdefault("_bound", {"group": None, "since": 0})
        ids = sorted(u.id for u in units)
        if st["group"] is None or set(st["group"]) - set(ids) or len(st["group"]) == 0:
            pri = {"dmr": 0, "lmg": 1, "rifle": 2, "shotgun": 3}
            ordered = sorted(units, key=lambda u: (pri.get(u.weapon, 2), u.id))
            n_sup = max(1, len(units) // 2)
            st["group"] = sorted(u.id for u in ordered[n_sup:])
            st["since"] = b.tick
        movers = set(st["group"]) & set(ids)
        plans: dict[str, Plan] = {}
        goal = intent.goal
        if intent.kind == "flank" and threats:
            goal = self._flank_point(units, threats, intent.side or "left") or goal
        reserved: set[Node] = set()
        moved = 0
        arrived = 0
        for u in sorted(units, key=lambda x: x.id):
            if u.pinned:
                plans[u.id] = self._pinned(u, occupied | reserved, threats)
                continue
            if u.id not in movers:
                p = self.suppress_plan(u, goal) or self.fire_plan(u) or self.recon_fire(u)
                if p is not None:
                    self._not_blind(u)
                if p is None:
                    p = self.take_cover_plan(u, occupied | reserved, threats) or \
                        self.blind_reposition(u, occupied | reserved, threats) or Plan(mode="overwatch")
                    if p.mode == "move":
                        reserved.add(p.path[-1])
                        occupied.discard(u.pos)
                    elif not p.why:
                        p.why = "掩護組：看不到敵人，警戒"
                elif not p.why:
                    p.why = "掩護組：壓制敵人，掩護隊友躍進" if p.fire == "suppress" else "掩護組：射擊掩護隊友躍進"
                plans[u.id] = p
                continue
            step = self._bound_step(u, goal, occupied | reserved, threats)
            if step:
                plans[u.id] = Plan(mode="move", path=step, sprint=True,
                                   posture="crouch" if u.posture == "prone" else None,
                                   why="躍進到下一個掩體（側翼）" if intent.kind == "flank" else "躍進到下一個掩體")
                reserved.add(step[-1])
                occupied.discard(u.pos)
                moved += 1
            else:
                arrived += 1
                p = self.fire_plan(u) or self.blind_reposition(u, occupied | reserved, threats) or Plan(mode="overwatch")
                if p.mode == "move":
                    reserved.add(p.path[-1])
                    occupied.discard(u.pos)
                if not p.why and p.mode == "overwatch":
                    p.why = "躍進組：找不到更前面的掩體，就地警戒"
                plans[u.id] = p
        if moved == 0 or self.ago(st["since"]) >= 2 * TICK_S - 1e-9:
            st["group"] = sorted(set(ids) - movers) or st["group"]
            st["since"] = b.tick
        if moved:
            self.patience = max(0, self.patience - self.pu)
        else:
            self.patience += self.pu
        _ = arrived
        return plans

    def _bound_step(self, u: Unit, goal, occupied, threats) -> list[Node] | None:
        """衝刺一段：本步走得到、比現在更接近目標（至少 2 格）的格子裡，挑暴露少、有掩體的。"""
        if goal is None:
            return None
        ap = self.b.move_budget(u) + SPRINT_EXTRA
        reach = self.reachable(u.pos, ap, occupied)
        route = self._march_path(u, goal, occupied)
        on_route = {n: i for i, n in enumerate(route or [])}

        def dist(n):
            return math.hypot(n[0] - goal[0], n[1] - goal[1])
        here = dist(u.pos)
        best, best_v = None, None
        for node, path in reach.items():
            if node == u.pos:
                continue
            gain = here - dist(node)
            if node in on_route:
                gain = max(gain, (on_route[node] + 1) * 0.9)
            if gain < 1.0:
                continue
            v = 1.2 * gain + self.cover_value(node, threats) - 2.0 * self.wexposure(node, threats) \
                - 0.6 * sum(self.wexposure(n, threats) for n in path[:-1])
            if best_v is None or v > best_v:
                best, best_v = path, v
        return best

    def _flank_point(self, units, threats, side: str):
        """側翼點：敵人重心往我方→敵人方向的左／右側偏 15～20 m（7～10 格），找可站的位置。"""
        m = self.b.map
        cx = sum(u.pos[0] for u in units) / len(units)
        cy = sum(u.pos[1] for u in units) / len(units)
        ex = sum(t[1][0] for t in threats) / len(threats)
        ey = sum(t[1][1] for t in threats) / len(threats)
        dx, dy = ex - cx, ey - cy
        n = math.hypot(dx, dy) or 1.0
        px, py = (-dy / n, dx / n) if side == "left" else (dy / n, -dx / n)
        for off in (9, 8, 10, 7):
            x, y = int(round(ex + px * off - dx / n * 2)), int(round(ey + py * off - dy / n * 2))
            for r in range(0, 4):
                for ddx in range(-r, r + 1):
                    for ddy in range(-r, r + 1):
                        nd = (x + ddx, y + ddy, 0)
                        if m.in_bounds(nd[0], nd[1]) and m.standable(nd):
                            return nd
        return None

    def cover_value(self, node: Node, threats) -> float:
        """站在 node：對已知威脅的暴露（越少越好）＋是否有矮牆／窗／瓦礫可依託＋能否看到（射到）威脅。"""
        m = self.b.map
        exp = self.exposure(node, threats)
        exp_prone = self.exposure(node, threats, posture="prone")
        rest = 0.0
        for bx in m._boxes_in_cell(node[0], node[1]):
            if bx.tag in ("lowwall", "window", "rubble"):
                rest = 1.0
                break
        sees = sum(1 for _, enode, _ in threats if self._sees(node, enode))
        return -3.0 * exp - 1.0 * exp_prone + 1.0 * rest + (1.2 if sees else 0.0) + 0.3 * min(sees, 2)

    def firing_position_plan(self, u: Unit, occupied: set[Node], threats) -> Plan | None:
        """有已知敵人、但從這裡看不到（打不到）任何一個 → 本步走到附近一個「看得到敵人、又有掩體」的位置。
        只在附近找（本步走得到），不會一口氣衝出去。"""
        if u.pinned or not threats:
            return None
        ap = self.b.move_budget(u)
        reach = self.reachable(u.pos, ap, occupied)
        best, best_v = None, None
        m = self.b.map
        for node, path in reach.items():
            if node == u.pos:
                continue
            if not any(self._sees(node, enode) for _, enode, _ in threats):
                continue
            v = self.cover_value(node, threats) - 0.1 * len(path)
            if best_v is None or v > best_v:
                best, best_v = path, v
        if best is None:
            return None
        return Plan(mode="move", path=best, posture="crouch" if u.posture == "prone" else None,
                    why="看不到開火的敵人，移到看得到的掩體")

    BLIND_S = 5.0             # 有已知敵人卻看不到、閒著架槍這麼久 → 換到看得到的射擊位置

    def blind_reposition(self, u: Unit, occupied: set[Node], threats) -> Plan | None:
        """看不到任何已知敵人（打不到也壓制不到）而閒著：累計步數，到了就移到附近看得到敵人、有掩體的位置。"""
        cnt = self.__dict__.setdefault("_blind", {})
        if not threats or u.pinned or not u.can_shoot():
            cnt.pop(u.id, None)
            return None
        n = cnt[u.id] = cnt.get(u.id, 0.0) + self.plan_dt
        if n < self.BLIND_S - 1e-9:
            return None
        mv = self.firing_position_plan(u, occupied, threats)
        if mv is not None:
            cnt[u.id] = 0
            mv.why = "看不到敵人，換到看得到的射擊位置"
        return mv

    def _not_blind(self, u: Unit):
        self.__dict__.setdefault("_blind", {}).pop(u.id, None)

    def fallback(self, u: Unit, occupied: set[Node], reserved: set[Node], threats) -> Plan:
        """沒有更好的事：射擊 → 火力偵察 → （看不到敵人太久就換位置）→ 架槍警戒。"""
        p = self.fire_plan(u) or self.recon_fire(u)
        if p is not None:
            self._not_blind(u)
            return p
        mv = self.blind_reposition(u, occupied | reserved, threats)
        if mv is not None:
            reserved.add(mv.path[-1])
            occupied.discard(u.pos)
            return mv
        return self.idle_plan(u)

    # ------------------------------------------------------------------ 沒事件就沿用

    def _situation(self, u: Unit, threats, extra=()) -> tuple:
        """決定「要不要換位置」用到的局勢：自己的位置、姿勢、壓制等級、腿傷、已知威脅（誰、在哪、姿勢）、
        煙霧、我方還能作戰的人數。這些都沒變 → 上次評估的「不動」照樣成立。"""
        b = self.b
        supp = 2 if u.pinned else 1 if u.suppressed else 0
        legs = (bool(u.damage.get("lleg")), bool(u.damage.get("rleg")))
        smokes = tuple((round(sm["x"], 1), round(sm["y"], 1), sm.get("expires")) for sm in b.smokes)
        return (u.pos, u.posture, supp, legs, tuple(threats), smokes, len(b.alive(self.side)), extra)

    def _reuse_stay(self, what: str, u: Unit, threats, extra, compute, is_stay):
        """上次評估是「不動」、局勢沒變、而且不超過 REPLAN_MAX_AGE_S 秒 → 直接沿用，不重算昂貴的站位搜尋。
        有事件（新命令會清空、新發現、威脅移動、壓制跨門檻、受傷、傷亡、煙霧變化）或到期就重算。"""
        key = (what, u.id)
        sig = self._situation(u, threats, extra)
        memo = self._stay.get(key)
        if memo is not None and memo[0] == sig and self.ago(memo[1]) < REPLAN_MAX_AGE_S - 1e-9:
            return memo[2]
        res = compute()
        if is_stay(res):
            self._stay[key] = (sig, self.b.tick, res)
        else:
            self._stay.pop(key, None)
        return res

    def take_cover_plan(self, u: Unit, occupied: set[Node], threats) -> Plan | None:
        return self._reuse_stay("cover", u, threats, (), lambda: self._take_cover_plan(u, occupied, threats),
                                lambda r: r is None)

    def _take_cover_plan(self, u: Unit, occupied: set[Node], threats) -> Plan | None:
        """固守／接敵：人在空地上暴露（或看不到任何已知敵人）→ 本 tick 走到附近更好的掩體位置（可衝刺）。
        已在好位置（不暴露、或有依託且看得到敵人）→ None。"""
        if not threats or u.pinned or u.damage.get("lleg") and u.damage.get("rleg"):
            return None
        here = self.cover_value(u.pos, threats)
        exposed = self.exposure(u.pos, threats) > 0
        if not exposed and here >= 0.5:
            return None
        sprint = exposed
        ap = self.b.move_budget(u) + (SPRINT_EXTRA if sprint else 0)
        reach = self.reachable(u.pos, ap, occupied)
        best, best_v = None, here + 0.8
        for node, path in reach.items():
            if node == u.pos:
                continue
            v = self.cover_value(node, threats) - 0.15 * len(path)
            if v > best_v:
                best, best_v = path, v
        if best is None:
            return None
        return Plan(mode="move", path=best, sprint=sprint, posture="crouch" if u.posture == "prone" else None,
                    why="暴露在敵人射界，移到掩體" if exposed else "找更好的掩體")

    def _pinned(self, u: Unit, occupied: set[Node], threats) -> Plan:
        if self.exposure(u.pos, threats) > 0:
            for nxt, cost in sorted(self.b.map.neighbors(u.pos)):
                if nxt not in occupied and cost <= 2 and self.exposure(nxt, threats) == 0:
                    return Plan(mode="move", path=[nxt], why="被壓制，爬到旁邊的掩護")
            if u.posture != "prone":
                return Plan(mode="posture", posture="prone", why="被壓制，臥倒")
            return Plan(why="被壓制到抬不起頭")
        p = self.suppress_plan(u)
        if p is not None:
            p.why = "被壓制，躲在掩體後盲射還擊"
            return p
        return Plan(why="被壓制，躲在掩體後")
