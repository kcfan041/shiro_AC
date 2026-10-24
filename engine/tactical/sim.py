"""戰術層 tick 狀態機（docs/archive/combat.md v0.2 §6）。結果與單位處理順序無關。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from engine.tactical import rng
from engine.tactical.geometry import Vec, add, closest_point_on_segment, length, mul, norm, perpendiculars, ray_box, sub
from engine.tactical.tmap import Node, TacMap
from engine.tactical.units import PINNED, SUPPRESSED, WEAPONS, Unit, prof_ammo, prof_melee, prof_sigma, prof_supp

MAX_RANGE = 80.0
MARCH_AP = 2                  # 未接敵行軍：每 2.5 秒的行動點（約 1.5 m/s，與接敵相同；衝刺 +2）
SPRINT_AP = 2
DETECT_NEAR = 15.0
DETECT_FAR = 100.0
DETECT_CUTOFF = 90.0          # 超過這個距離（切比雪夫）不做偵測計算（連續地圖效能）
CONTACT_R = 60.0              # 連續地圖：這個距離內有已知敵人才算接敵（戰鬥速度）
FIRE_REVEAL_R = 70.0          # 開火暴露：這個距離內的敵對陣營可能察覺射手大概位置
FIRE_REVEAL_P = 0.75          # 近距離察覺機率（隨距離線性下降，連發較高；保底 25%）
DETECT_FAR_P = 0.05
SUPP_HIT = 35.0
SUPP_NEAR = 20.0
SUPP_NEAR_R = 1.5
SUPP_NEAR_PER_M = 0.025       # 壓制判定半徑隨距離放大（40 m 外約 2.5 m）
SUPP_FAR_NEAR = 20.0          # 射手步槍：20 m 內用一般壓制係數
SUPP_FAR_AT = 40.0            # 40 m 以上用遠距離壓制係數
SUPP_COVER = 15.0
SUPP_COVER_R = 2.0
TICK_S = 2.5                  # 規則原本以「一步 2.5 秒」校準；以下比率、機率都以此換算到任意步長
DECAY_SEEN = 20.0             # 每 2.5 秒消退（有敵人看得到）
DECAY_UNSEEN = 30.0           # 每 2.5 秒消退（沒人看得到）
DOWN_SECONDS = 30.0           # 倒地沒被穩定多久會離線（原 12 步）
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

SMOKE_RANGE = 35.0
SMOKE_RADIUS = 3.0
SMOKE_HEIGHT = 3.5
SMOKE_FULL = 2.0              # 穿越 2 m 煙霧 → 一般光學完全遮蔽
SMOKE_THERMAL = 0.3           # 熱感測穿透：煙霧最多讓可見程度降到原本的 30%
SMOKE_DELAY_S = 2.5           # 煙霧出手後多久生效（原：下一步）
SMOKE_LIFE_S = 15.0           # 持續（原 6 步）
SMOKE_FADE_S = 5.0            # 最後這段逐漸變淡（原 2 步）

MORALE_START = 70.0
MORALE_FLOOR_LINK = 10.0
MORALE_SHAKEN = 45.0
MORALE_ROUT = 20.0
MORALE_SURRENDER = 5.0
SURRENDER_P = 0.02            # 每 2.5 秒
MORALE_RANGE = 90.0          # 士氣的兵力比、「未受威脅」只看這個距離內（與偵測上限相同）


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
    sprint: bool = False          # 衝刺：本 tick 多 SPRINT_AP 行動點（射擊散布照移動計）
    why: str | None = None        # 行動理由（規則產生，給玩家看；不影響結算）
    snap: bool = False            # move：走完這步順手對看得到的敵人開一槍（行進間射擊，散布照移動計）
    face: Node | None = None      # move：邊走邊面向這一點（倒退、側移比較慢、不能衝刺）；None＝面向移動方向


@dataclass
class SideIntel:
    spotted: dict = field(default_factory=dict)   # unit id -> tick
    last_known: dict = field(default_factory=dict)  # unit id -> (node, tick)
    believed_seen: dict = field(default_factory=dict)  # 本方個體 id -> (tick, 原因)：本方推算「這人已被敵方看到」


BELIEF_TTL_S = 10.0         # 沒有新線索這麼久後回到「應該還沒被發現」（原 4 步）
BELIEF_SPREAD = 4           # 中彈／掠過：附近 N 格內的隊友也視為暴露
WATCHED_RANGE = 20.0        # (c) 被盯上：看得到的敵人在 20 m 內
WATCHED_ANGLE = 30.0        #     且面朝我 ±30°
REASON_RANK = {"hit": 4, "near_miss": 3, "fired": 2, "watched": 1}


def _supp_level(v: float) -> str:
    """壓制強度級別（給畫面；約略對應 Unit.suppressed／pinned 的基準門檻，不含性格修正）。"""
    if v >= PINNED:
        return "pinned"
    if v >= SUPPRESSED:
        return "suppressed"
    return "light" if v > 0 else "none"

FLASH_NEAR = 2.0            # 閃光：2 m 內全強度
FLASH_FAR = 8.0             # 8 m 外無光學效果
FLASH_FACE = (1.0, 0.6, 0.3)  # 正面 ±60°／側面 ±120°／背對
FLASH_STRONG = 0.6          # 強度 ≥ → 過載 5 秒
FLASH_WEAK = 0.25           # 強度 ≥ → 過載 2.5 秒
FLASH_S = 2.5               # 過載一級的秒數（架槍中被閃 +1 級）
FLASH_SOUND_R = 6.0         # 聲響（可穿牆，同層）
FLASH_SUPP = 15.0
DAZZLE_DETECT = 0.3         # 光學過載時發現機率 ×0.3（熱感測保底，不會完全失明）；
#                             瞄準／連發只有 30% 機會照常，否則只能朝大概方向盲射（壓制模式）

BRACE_ARC = 45.0            # 架槍扇區 ±45°
BRACE_REST = 0.6            # 有依託時散布倍率
BRACE_FREE = 0.8            # 無依託
BRACE_BREAK_SUPP = 50       # 壓制達此值 → 架槍中斷
BRACE_SECONDS = 2.5         # 架槍要持續這麼久才生效（中途移動／轉向／被壓制就中斷）
SUPP_HOLD_S = 2.0           # 被壓制、釘死看最近這麼久內的最高壓制值（不是只看當下）
MOVE_SHAKE_S = 1.0          # 停下後這麼久內開火仍算移動中（散布 ×move_mult）
OW_MIN_VIS = 0.3            # 警戒射擊：目標可見程度低於此值（例如被煙擋住）不觸發


class Battle:
    def __init__(self, tmap: TacMap, units: list[Unit], seed: int = 0, linked: dict | None = None,
                 morale: dict | None = None):
        self.map = tmap
        self.units: dict[str, Unit] = {u.id: u for u in units}
        self.seed = seed
        self.tick = 0
        self.dt = TICK_S          # 一步幾秒
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
        self.factions: dict[str, str] = {"blue": "ally", "green": "ally", "red": "enemy"}
        self.paths: dict[str, list[Node]] = {}
        self.reveal: dict[str, set[str]] = {}   # side -> 被無人機揭露的敵方個體（指揮部支援）
        self._contact_cache: dict = {}
        self.allow_exit = True        # 走到地圖邊緣可「撤出戰區」；連續地圖（世界邊緣）關閉
        # 結構化事件（Phase E）：只記錄，不影響結算、不用亂數
        self.ev: list[dict] = []
        self._seq = 0
        self._phase = "pre"
        self._sub = 0
        self._aid_n = 0
        self._fire_meta: dict[str, dict] = {}
        self._why: dict[str, str] = {}
        self.reasons: dict[str, str] = {}
        self._ctx: tuple | None = None
        # 時間線（第 8 步）：每人進行中的動作、事件佇列（見 timeline.py）
        self.acts: dict[str, dict] = {}
        self.queue: list[tuple] = []
        self._qseq = 0
        self._act_n = 0
        self._clock: float | None = None      # 正在結算的事件時間（秒）；不在事件中為 None
        self._move_batch = 0
        self.ow_next: dict[str, float] = {}    # 警戒者下次可觸發的時間
        self.ow_hit: dict[str, float] = {}     # 移動目標上次被警戒觸發的時間
        self.last_move_t: dict[str, float] = {}  # 每人最後一次移動的時間（移動中射擊的散布）
        self._hf_hist: list[dict] = []         # 前幾步的 heavy_fire（湊滿 2.5 秒視窗）
        self.last_drags: dict[str, str] = {}
        self.path_sub: dict[str, list[int]] = {}
        self.path_t: dict[str, list[float]] = {}   # 本步每走一格的抵達時間（秒，前端依此內插）
        self.path_st: dict[str, list[float]] = {}  # 本步每走一格的起步時間
        # 感知節奏（第 8 步）：完整偵測、士氣、自我暴露推算每 PERCEPT_S 秒一次，期間的量以實際經過秒數換算
        self._span: float | None = None        # 正在以多長的時間換算機率／持續量（None＝一步）
        self._percept_t: float | None = None   # 上次完整感知的戰鬥時間
        self._percept_tick = 0
        self._moved_since: set[str] = set()     # 上次完整感知以來移動過／開過火的人
        self._fired_since: set[str] = set()
        self._seen: dict[str, bool] = {}        # 上次感知時：有沒有敵人看得到他（壓制消退快慢）

    # ------------------------------------------------------------------ events

    @property
    def now(self) -> float:
        """戰鬥時間（秒）。"""
        return self.tick * self.dt

    def per_step(self, per_tick: float) -> float:
        """每 2.5 秒的量 → 這一步（或這段感知間隔）的量。"""
        return per_tick * (self._span or self.dt) / TICK_S

    def p_step(self, p_tick: float, span: float | None = None) -> float:
        """每 2.5 秒的機率 → 這一步（或 span 秒）的機率（期望不變）。"""
        s = span or self._span or self.dt
        if s == TICK_S:
            return p_tick
        return 1.0 - (1.0 - p_tick) ** (s / TICK_S)

    def emit(self, kind: str, actor: str | None = None, target: str | None = None, aid: str | None = None,
             grp: str | None = None, **data) -> dict:
        self._seq += 1
        e = {"seq": self._seq, "t": self.tick, "ts": round(self._clock if self._clock is not None else self.now, 3),
             "phase": self._phase, "sub": self._sub, "type": kind, "actor": actor, "target": target}
        if aid:
            e["aid"] = aid
        if grp:
            e["grp"] = grp
        if data:
            e["data"] = data
        self.ev.append(e)
        return e

    def new_aid(self, actor: str) -> str:
        """因果 ID：不含個體 id（避免把看不到的敵人 id 帶給玩家）。"""
        _ = actor
        self._aid_n += 1
        return f"a{self.tick}.{self._aid_n}"

    def gxy(self, v) -> list[float]:
        return [round(v[0] / self.map.cell, 2), round(v[1] / self.map.cell, 2)]

    def why(self, uid: str, text: str):
        """本步這個人的行動理由（後寫的覆蓋 AI 規劃時給的理由；結算阻擋比規劃意圖更直接）。"""
        self._why[uid] = text

    # ------------------------------------------------------------------ helpers

    def ordered(self, units=None) -> list[Unit]:
        seq = sorted((units if units is not None else self.units.values()), key=lambda u: u.id)
        return self.order_hook(seq) if self.order_hook else seq

    def alive(self, side: str | None = None) -> list[Unit]:
        return [u for u in self.units.values() if u.alive and (side is None or u.side == side)]

    def faction_of(self, side: str) -> str:
        """side 可寫成 "blue|<小隊 id>"：一支小隊一個 side，陣營看 | 前面（blue）。"""
        base = side.split("|", 1)[0]
        return self.factions.get(side, self.factions.get(base, base))

    def hostile(self, a: str, b: str) -> bool:
        """陣營敵對：同陣營（例如我方各小隊 blue|* 與失聯友軍 green 都是 ally）不敵對。未登記的陣營視為各自獨立。"""
        if a == b:
            return False
        return self.faction_of(a) != self.faction_of(b)

    def intel_groups(self) -> list[tuple[list[str], "SideIntel"]]:
        """共用同一份情報物件的 side 分成一組（同格我方小隊共享情報）。"""
        groups: dict[int, tuple[list[str], SideIntel]] = {}
        for side in sorted(self.intel):
            it = self.intel[side]
            groups.setdefault(id(it), ([], it))[0].append(side)
        return list(groups.values())

    def enemies_of(self, side: str) -> list[Unit]:
        return [u for u in self.units.values() if u.alive and self.hostile(u.side, side)]

    def occupied(self) -> dict[Node, str]:
        return {u.pos: u.id for u in self.units.values() if u.alive}

    def r(self, *parts) -> float:
        if self._clock is not None:          # 事件中：同一步不同時刻的事件各自獨立
            return rng.uniform(self.seed, self.tick, int(round(self._clock * 1000)), *parts)
        return rng.uniform(self.seed, self.tick, *parts)

    def g(self, *parts) -> float:
        if self._clock is not None:
            return rng.gauss(self.seed, self.tick, int(round(self._clock * 1000)), *parts)
        return rng.gauss(self.seed, self.tick, *parts)

    def recent_heavy_fire(self, side: str) -> dict[str, float]:
        """最近 2.5 秒本方火力落在誰身邊（累計壓制值）。"""
        out = dict(self.heavy_fire.get(side, {}))
        for h in self._hf_hist:
            for uid, v in h.get(side, {}).items():
                out[uid] = out.get(uid, 0.0) + v
        return out

    # ------------------------------------------------------------------ smoke

    def active_smokes(self) -> list[dict]:
        now = self.now
        return [s for s in self.smokes if s["born"] + SMOKE_DELAY_S <= now <= s["expires"] + 1e-9]

    def smoke_density(self, s: dict) -> float:
        """最後 SMOKE_FADE_S 秒逐漸變淡（以 2.5 秒為一級，與原本逐步變淡相同）。"""
        left = (s["expires"] - self.now) / TICK_S
        fade = SMOKE_FADE_S / TICK_S
        return 1.0 if left >= fade else max(0.0, (left + 1) / (fade + 1))

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
        return self.sight_from_base(a, b, self.map.sight(a, b))

    def sight_from_base(self, a: Vec, b: Vec, base: float) -> float:
        """已知地圖視線 base（靜態，可跨步快取）→ 套上煙霧。"""
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
                 "born": self.now, "expires": self.now + SMOKE_LIFE_S, "by": thrower.id}
        self.smokes.append(cloud)
        self.log.append(f"T{self.tick} {thrower.id} 投擲煙霧彈")
        self.emit("throw", actor=thrower.id, item="smokes", frm=self.gxy(eye), at=self.gxy(land),
                  aimed=self.gxy(point))
        return cloud

    def recent_incoming(self, side: str) -> list[dict]:
        """上一 tick 射向本方（命中或擦過）的子彈：受害者感受得到來彈方向，不知道射手確切位置。"""
        out = []
        for s in self.last_shots:
            shooter = self.units.get(s["shooter"])
            if shooter is None or not self.hostile(shooter.side, side):
                continue
            if s["kind"] == "body":
                hit = self.units.get(s["ref"][0])
                if hit is not None and hit.side == side:
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

    def detect(self, phase: str, movers: set[str] = frozenset(), only: set[str] | None = None,
               span: float | None = None):
        """only：只偵測這些目標（移動事件中只看剛移動的人）。span：機率以多少秒換算（預設一步）。"""
        cut = DETECT_CUTOFF / self.map.cell
        bucket = max(1, int(cut))
        for sides, _ in self.intel_groups():
            side = sides[0]
            all_obs = [u for s in sides for u in self.alive(s)]
            # 空間分桶（一桶 90 m）：每個目標只看附近 3×3 桶的觀察者；保持原本順序（機率連乘的順序不變）
            buckets: dict[tuple[int, int], list[int]] = {}
            for i, o in enumerate(all_obs):
                buckets.setdefault((o.pos[0] // bucket, o.pos[1] // bucket), []).append(i)
            revealed = set().union(*(self.reveal.get(s, set()) for s in sides)) if self.reveal else set()
            for t in sorted(self.enemies_of(side), key=lambda u: u.id):
                if only is not None and t.id not in only:
                    continue
                if t.id in revealed:
                    intel = self.intel[side]
                    if t.id not in intel.spotted:
                        self.log.append(f"T{self.tick} {side} 發現 {t.id}（無人機）")
                        self.emit("spot", target=t.id, side=side, how="drone", at=list(t.pos[:2]))
                    intel.spotted[t.id] = self.tick
                    intel.last_known[t.id] = (t.pos, self.tick)
                    continue
                bx, by = t.pos[0] // bucket, t.pos[1] // bucket
                observers = [all_obs[i] for i in sorted(i for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                                                        for i in buckets.get((bx + dx, by + dy), ()))]
                intel = self.intel[side]
                if t.id in intel.spotted:
                    # 已發現：只要有一個觀察者看得夠清楚（> 0.15）就維持，找到就停（結果與全部算完相同）
                    held = False
                    for o in observers:
                        if max(abs(o.pos[0] - t.pos[0]), abs(o.pos[1] - t.pos[1])) > cut:
                            continue
                        v = self.visibility(o, t)
                        if o.dazzled:
                            v *= DAZZLE_DETECT
                        if v > 0.15:
                            held = True
                            break
                    if held:
                        intel.spotted[t.id] = self.tick
                        intel.last_known[t.id] = (t.pos, self.tick)
                    else:
                        del intel.spotted[t.id]
                        lk = intel.last_known.get(t.id)
                        self.emit("lost", target=t.id, side=side, at=list(lk[0][:2]) if lk else None)
                    continue
                vis_any = 0.0
                miss = 1.0
                hear_miss = 1.0
                moving = t.id in movers or t.moved
                for o in observers:
                    if max(abs(o.pos[0] - t.pos[0]), abs(o.pos[1] - t.pos[1])) * self.map.cell > DETECT_CUTOFF:
                        continue
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
                if vis_any > 0 and self.r("detect", side, t.id) < self.p_step(1 - miss, span):
                    intel.spotted[t.id] = self.tick
                    intel.last_known[t.id] = (t.pos, self.tick)
                    self.log.append(f"T{self.tick} {side} 發現 {t.id}")
                    self.emit("spot", target=t.id, side=side, how="sight", at=list(t.pos[:2]))
                    continue
                if hear_miss < 1.0 and self.r("hear", side, t.id) < self.p_step(1 - hear_miss, span):
                    known = intel.last_known.get(t.id)
                    if not known or known[0] != t.pos or known[1] < self.tick:
                        intel.last_known[t.id] = (t.pos, self.tick)
                        if not known or known[0] != t.pos:
                            self.log.append(f"T{self.tick} {side} 察覺 {t.id}（聲音）")
                            self.emit("hear", target=t.id, side=side, how="sound", at=list(t.pos[:2]))

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

    def friendly_clear(self, u: Unit, aim: Vec, spread: float = 1.0) -> bool:
        """開火前確認射線上沒有隊友（射擊當下的位置；連發／壓制散布大，檢查範圍跟著放寬）。"""
        eye = u.eye(self.map)
        d = length(sub(aim, eye))
        if d < 1e-6:
            return True
        for f in self.units.values():
            if f.id == u.id or f.exited or not (f.alive or f.down) or self.hostile(f.side, u.side):
                continue
            t = f.torso(self.map)
            pt, k = closest_point_on_segment(eye, aim, t)
            if 0.02 < k < 0.98 and length(sub(pt, t)) < 0.9 + WEAPONS[u.weapon]["sigma"] * 2 * spread * d * k:
                return False
        return True

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

    def moved_recently(self, u: Unit) -> bool:
        """射擊時算「移動中」：本步移動過，或最後一次移動在 MOVE_SHAKE_S 秒內。"""
        if u.moved:
            return True
        t = self.last_move_t.get(u.id)
        now = self._clock if self._clock is not None else self.now
        return t is not None and now - t < MOVE_SHAKE_S - 1e-9

    def sigma(self, shooter: Unit, dist: float, overwatch: bool = False, aim: Vec | None = None) -> float:
        w = WEAPONS[shooter.weapon]
        s = w["sigma"] * prof_sigma(shooter.proficiency())
        moved = self.moved_recently(shooter)
        braced = aim is not None and not moved and self.in_brace_arc(shooter, aim)
        if braced:
            s *= BRACE_REST if shooter.brace_rest else BRACE_FREE
            s *= 1 + shooter.suppression / 100.0
        if shooter.dazzled:
            s *= 2.0
        if dist > w["range"]:
            s *= 1 + (dist - w["range"]) / w["range"]
        if moved:
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

    def fire(self, shooter: Unit, plan: Plan, action: str, overwatch: bool = False,
             rounds: int | None = None) -> list[dict]:
        """產生射擊結果（不套用）。rounds：壓制分波時這一波打幾發。"""
        n_override = rounds
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
            rounds = w["supp_rounds"] if n_override is None else n_override
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
        if not self.friendly_clear(shooter, aim, spread=3.0 if mode != "aimed" else 1.0):
            self.why(shooter.id, "射線上有隊友，暫停射擊")
            self.emit("hold_fire", actor=shooter.id, target=plan.target, why="friendly_in_line")
            return []
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
        for r in results:
            r["mode"] = mode
            r["ow"] = overwatch
            r["aim_at"] = plan.target if mode != "suppress" or target is not None else None
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
            if not ray_box(eye, direction, unit.bounds(self.map), best_t):
                continue            # 連整個人的外框都沒碰到 → 不可能打中任何部位（結果不變，只是少算）
            for name, box in unit.parts(self.map).items():
                hit = ray_box(eye, direction, box, best_t)
                if hit and hit[0] < best_t:
                    best_t, best_kind, best_ref = hit[0], "body", (unit.id, name)
        end = add(eye, mul(direction, best_t))
        return {"shooter": shooter.id, "from": eye, "to": end, "kind": best_kind, "ref": best_ref,
                "dir": direction, "len": best_t}

    @staticmethod
    def supp_k(weapon: str, dist: float) -> float:
        """壓制係數：射手步槍遠距離精準壓制（20 m 內 supp_k，40 m 以上 supp_far，中間線性）。"""
        w = WEAPONS[weapon]
        far = w.get("supp_far")
        if far is None:
            return w["supp_k"]
        t = max(0.0, min(1.0, (dist - SUPP_FAR_NEAR) / (SUPP_FAR_AT - SUPP_FAR_NEAR)))
        return w["supp_k"] + (far - w["supp_k"]) * t

    def suppression_from(self, shot: dict, shooter: Unit) -> dict[str, float]:
        out: dict[str, float] = {}
        rng_len = length(sub(shot["to"], shot["from"]))
        k = self.supp_k(shooter.weapon, rng_len) * prof_supp(shooter.proficiency())
        if shot.get("pellet"):
            k /= 4
        for unit in self.units.values():
            if not unit.alive or not self.hostile(unit.side, shooter.side):
                continue
            value = 0.0
            if shot["kind"] == "body" and shot["ref"][0] == unit.id:
                value = SUPP_HIT
            else:
                torso = unit.torso(self.map)
                pt, frac = closest_point_on_segment(shot["from"], shot["to"], torso)
                dist = length(sub(pt, torso))
                near_r = SUPP_NEAR_R + SUPP_NEAR_PER_M * frac * rng_len
                if dist < near_r and not self.map.blocked(pt, torso):
                    value = SUPP_NEAR * (1 - dist / near_r)
                if shot["kind"] == "cover":
                    gap = length(sub(shot["to"], torso))
                    shooter_side = length(sub(shot["from"], shot["to"])) < length(sub(shot["from"], torso))
                    if gap < SUPP_COVER_R + SUPP_NEAR_PER_M * rng_len and shooter_side:
                        value = max(value, SUPP_COVER)
            if value > 0:
                out[unit.id] = value * k
        return out

    def apply(self, results: list[tuple[Unit, list[dict]]], grp: str | None = None):
        """同時套用一批射擊的損傷與壓制。grp：同時結算群組（事件用）。"""
        supp: dict[str, float] = {}
        supp_src: dict[str, dict[str, float]] = {}
        hits: list[tuple[str, str, str, int]] = []
        aids: dict[str, str] = {}
        grp = grp or f"t{self.tick}-{self._phase}{self._sub}"
        for shooter, shots in results:
            if shots:
                aid = aids[shooter.id] = self.new_aid(shooter.id)
                first = shots[0]
                tr0 = first["group"][0] if "group" in first else first
                self.emit("shot", actor=shooter.id, target=first.get("aim_at"), aid=aid, grp=grp,
                          mode=first.get("mode", "aimed"), ow=bool(first.get("ow")), rounds=len(shots),
                          weapon=shooter.weapon, frm=self.gxy(tr0["from"]),
                          to=[self.gxy((t["group"][0] if "group" in t else t)["to"]) for t in shots],
                          impact=[(t["group"][0] if "group" in t else t)["kind"] for t in shots])
            for i, s in enumerate(shots):
                shooter.stats["shots"] += 1
                self.used.setdefault(shooter.id, set()).add(shooter.weapon)
                traces = s["group"] if "group" in s else [s]
                hit_any = False
                for j, tr in enumerate(traces):
                    tr["aid"] = aids[shooter.id]
                    tr["tick"] = self.tick
                    tr["ts"] = round(self._clock if self._clock is not None else self.now, 3)
                    if i:
                        tr["r"] = i                     # 畫面用：這次射擊的第幾發（同一發霰彈的彈丸同號），規則上同時結算
                    self.shots.append(tr)
                    for uid, val in self.suppression_from(tr, shooter).items():
                        supp[uid] = supp.get(uid, 0.0) + val
                        src = supp_src.setdefault(uid, {})
                        src[shooter.id] = src.get(shooter.id, 0.0) + val
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
                self.reveal_fire(shooter, len(shots))
        flanked_sides = set()
        for shooter_id, uid, part, i in hits:
            target = self.units[uid]
            shooter = self.units[shooter_id]
            self._ctx = (aids.get(shooter_id), grp)
            if self.hostile(target.side, shooter.side):
                shooter.stats["hits"] += 1
                if self._flanked(target, shooter) and target.side not in flanked_sides and (
                        target.side not in self.flanked
                        or (self.tick - self.flanked[target.side]) * self.dt >= TICK_S - 1e-9):
                    flanked_sides.add(target.side)
                    self.flanked[target.side] = self.tick
                    self._morale_event(target.side, -10, "遭側翼攻擊")
                    self.emit("flanked", actor=shooter_id, target=uid, aid=aids.get(shooter_id), grp=grp,
                              side=target.side)
            if target.dead:
                continue
            if target.down:
                self._kill(target, shooter, "倒地後再被擊中")
                continue
            self._damage(target, part, shooter, i)
        self._ctx = None
        for uid, val in supp.items():
            before = self.units[uid].suppression
            self.add_suppression(self.units[uid], val)
            src = max(supp_src[uid].items(), key=lambda kv: (kv[1], kv[0]))[0]
            self.emit("suppress", actor=src, target=uid, grp=grp, aid=aids.get(src), amount=round(val, 1),
                      level=_supp_level(self.units[uid].suppression), was=_supp_level(before),
                      sources=sorted(supp_src[uid]))

    def add_suppression(self, u: Unit, val: float):
        """加壓制（上限 100），記下這一刻的值當作峰值。"""
        u.suppression = min(100.0, u.suppression + val)
        t = self._clock if self._clock is not None else self.now
        u.supp_peaks.append([t, u.suppression])
        u.supp_peak = max(u.supp_peak, u.suppression)

    def refresh_supp_peaks(self):
        """步末：丟掉 SUPP_HOLD_S 秒以前的峰值，重算最近的最高值。"""
        cut = self.now - SUPP_HOLD_S
        for u in self.units.values():
            if u.supp_peaks:
                u.supp_peaks = [p for p in u.supp_peaks if p[0] > cut + 1e-9]
                u.supp_peak = max((p[1] for p in u.supp_peaks), default=0.0)
            elif u.supp_peak:
                u.supp_peak = 0.0

    def reveal_fire(self, shooter: Unit, rounds: int):
        """槍口火光、槍聲：開火者附近 FIRE_REVEAL_R 內的敵對陣營，得知射手的大概位置（寫進 last_known，不是 spotted）。
        誤差隨距離增加（每 10 m 約 1 格），多發／連發比較好定位；同一陣營已經看得到他就不用。
        被打到附近（壓制、命中）的人一定聽得到；其他人依距離有機率。"""
        sp = shooter.pos
        for sides, intel in self.intel_groups():
            side = sides[0]
            if not self.hostile(side, shooter.side) or shooter.id in intel.spotted:
                continue
            listeners = [u for s in sides for u in self.alive(s)]
            if not listeners:
                continue
            best = min(max(abs(u.pos[0] - sp[0]), abs(u.pos[1] - sp[1])) for u in listeners) * self.map.cell
            if best > FIRE_REVEAL_R:
                continue
            p = min(1.0, FIRE_REVEAL_P * (1.0 + 0.25 * (rounds - 1)) * (1.0 - best / FIRE_REVEAL_R) + 0.25)
            if self.r("fire_reveal", side, shooter.id) >= p:
                continue
            err = max(0.0, best / 10.0 / max(1.0, rounds ** 0.5))
            ex = int(round(self.g("fire_reveal_x", side, shooter.id) * err))
            ey = int(round(self.g("fire_reveal_y", side, shooter.id) * err))
            node = (min(self.map.w - 1, max(0, sp[0] + ex)), min(self.map.h - 1, max(0, sp[1] + ey)), sp[2])
            old = intel.last_known.get(shooter.id)
            if old is not None and old[1] == self.tick:
                continue
            intel.last_known[shooter.id] = (node, self.tick)
            self.emit("hear", target=shooter.id, side=side, how="gunfire", at=list(node[:2]),
                      err=round(err * self.map.cell, 1))
            if old is None:
                self.log.append(f"T{self.tick} {side} 察覺 {shooter.id}（槍聲）")

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
        aid = self.new_aid(thrower.id)
        self.emit("throw", actor=thrower.id, aid=aid, item=item, frm=self.gxy(eye), at=self.gxy(land),
                  aimed=self.gxy(point))
        return {"thrower": thrower.id, "at": land, "item": item, "aid": aid}

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
            expected = thrower is not None and not self.hostile(u.side, thrower.side)
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
                out[u.id] = n * FLASH_S
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
        ctx = getattr(self, "_ctx", None) or (None, None)
        self.emit("kia", actor=by.id if by else None, target=target.id, aid=ctx[0], grp=ctx[1], why=why)
        self._morale_event(target.side, -15, f"{target.id} 陣亡")

    def _damage(self, target: Unit, part: str, shooter: Unit, i: int):
        ctx = getattr(self, "_ctx", None) or (None, None)
        self.emit("hit", actor=shooter.id, target=target.id, aid=ctx[0], grp=ctx[1], part=part)
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
        ctx = getattr(self, "_ctx", None) or (None, None)
        self.emit("down", actor=shooter.id, target=target.id, aid=ctx[0], grp=ctx[1])
        if self.hostile(shooter.side, target.side):
            shooter.stats["kills"] += 1
            self._morale_event(shooter.side, +8, f"擊倒 {target.id}")
        self.log.append(f"T{self.tick} {target.id} 倒地（{shooter.id}）")
        self._morale_event(target.side, -25 if target.leader else -15,
                           f"{'隊長 ' if target.leader else ''}{target.id} 倒地")

    # ------------------------------------------------------------------ morale

    def _morale_event(self, side: str, delta: float, why: str, per_tick: bool = False):
        """per_tick：每 2.5 秒的持續量（依步長換算）；否則是一次性事件（倒地、擊倒、側翼）。"""
        if side not in self.morale:
            return
        if per_tick:
            delta = self.per_step(delta)
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
            r = MORALE_RANGE / self.map.cell

            def near(node):
                return any(max(abs(node[0] - u.pos[0]), abs(node[1] - u.pos[1])) <= r for u in own)
            supp = sum(u.suppression for u in own) / len(own)
            if supp > 50:
                self._morale_event(side, -5, "持續被壓制", per_tick=True)
            # 兵力比：只算己方情報知道、最後位置在己方任何人 90 m 內的敵人（不偷看真相）；友軍也只算 90 m 內的
            lk = self.intel[side].last_known if side in self.intel else {}
            known = [uid for uid, (n, _) in lk.items() if self.units.get(uid) is not None
                     and self.units[uid].alive and near(n)]
            friends = [u for u in self.units.values() if u.alive and not self.hostile(u.side, side) and near(u.pos)]
            if known and len(friends) * 2 < len(known):
                self._morale_event(side, -5, "兵力劣勢", per_tick=True)
            foes = [e for e in self.enemies_of(side) if near(e.pos)]
            if not any(self.sight(e.eye(self.map), u.torso(self.map)) > 0 for e in foes for u in own
                       if max(abs(e.pos[0] - u.pos[0]), abs(e.pos[1] - u.pos[1])) <= r) and supp < 20:
                self._morale_event(side, +2, "掩蔽中、未受威脅", per_tick=True)
            if self.morale_state(side) == "surrender":
                for u in sorted(own, key=lambda x: x.id):
                    if self.r("surrender", u.id) < self.p_step(SURRENDER_P):
                        u.surrendered = True
                        self.log.append(f"T{self.tick} {u.id} 投降")
                        self.emit("surrender", actor=u.id)

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
        """舊介面：每個人照計畫開始新動作（沒有計畫的人待命），再推進一步。"""
        from engine.tactical import timeline
        timeline.assign(self, plans, idle_others=True)
        self.advance()

    def assign(self, plans: dict[str, Plan]):
        """只換掉這些人的動作（其他人繼續做手上的事）。"""
        from engine.tactical import timeline
        timeline.assign(self, plans)

    def advance(self):
        from engine.tactical import timeline
        timeline.advance(self)

    def _finish_reasons(self, plans: dict[str, Plan]):
        """本步每個人的行動理由：結算阻擋（self._why）優先，其次 AI 規劃時給的理由，再其次由狀態推。"""
        out: dict[str, str] = {}
        for uid, u in self.units.items():
            if not u.alive:
                continue
            text = self._why.get(uid)
            p = plans.get(uid)
            if text is None and p is not None and p.why:
                text = p.why
            if text is None:
                if u.bracing:
                    text = "架槍中（下一步就緒）"
                elif u.braced and p is not None and p.mode in ("overwatch", "idle", "brace"):
                    text = "架槍警戒" + ("（有依託）" if u.brace_rest else "")
            if text:
                out[uid] = text
        self.reasons = out

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
                self.emit("brace", actor=uid, state="broken", why=why)
        for uid, u in self.units.items():
            if u.bracing and uid not in bracing:
                u.bracing = None
        for uid, p in bracing.items():
            u = self.units[uid]
            if not u.alive or u.moved or u.fired or u.dazzled or u.suppression >= BRACE_BREAK_SUPP:
                if u.bracing and u.alive:
                    self.emit("brace", actor=uid, state="broken",
                              why="被壓制" if u.suppression >= BRACE_BREAK_SUPP else "中斷")
                    if u.suppression >= BRACE_BREAK_SUPP:
                        self.why(uid, "被壓制，架槍中斷")
                u.bracing = None
                continue
            d = p.watch or u.watch or u.facing
            n = math.hypot(d[0], d[1])
            if n < 1e-6:
                continue
            want = (d[0] / n, d[1] / n)
            if u.bracing is None or u.bracing[0] * want[0] + u.bracing[1] * want[1] < 0.9:
                u.bracing = want
                u.brace_since = self.now
                self.emit("brace", actor=uid, state="start", dir=[round(want[0], 2), round(want[1], 2)])
                continue
            if self.now - (u.brace_since if u.brace_since is not None else self.now) < BRACE_SECONDS - 1e-9:
                continue
            u.bracing = None
            u.braced = want
            u.brace_rest = self.has_rest(u, u.braced)
            u.facing = self.map.facing_to(u.pos, (u.pos[0] + round(u.braced[0] * 3),
                                                 u.pos[1] + round(u.braced[1] * 3), u.pos[2]))
            self.log.append(f"T{self.tick} {uid} 架槍{'（有依託）' if u.brace_rest else ''}")
            self.emit("brace", actor=uid, state="ready", rest=u.brace_rest,
                      dir=[round(want[0], 2), round(want[1], 2)])

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
            if not t.alive or self.hostile(t.side, u.side):
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
            self.emit("transfer", actor=u.id, target=t.id, mode=p.mode, items=got)
        return moved

    # ------------------------------------------------------------------ 自我暴露推算

    def shots_since(self, tick: int | None) -> list[dict]:
        """tick 之後（不含）的子彈；None＝本步。"""
        if tick is None:
            return self.shots[self._shot_mark:]
        out = []
        for s in reversed(self.shots):
            if s.get("tick", 0) <= tick:
                break
            out.append(s)
        return out[::-1]

    def _incoming_now(self, side: str, since: int | None = None) -> list[tuple[str, str]]:
        """本步（或 since 之後）打向本方的子彈：回傳 [(受害者 id, hit|near_miss)]。受害者感受得到，不需要知道射手。"""
        out = []
        for s in self.shots_since(since):
            shooter = self.units.get(s["shooter"])
            if shooter is None or not self.hostile(shooter.side, side):
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

    def update_beliefs(self, since: int | None = None, fired: set[str] | None = None):
        """每方只用自己知道的資訊推算「哪些隊員已被敵方看到」。since／fired：上次推算以來的子彈與開火者（預設本步）。"""
        fired = fired if fired is not None else {u.id for u in self.units.values() if u.fired}
        for side, intel in self.intel.items():
            bs = intel.believed_seen
            fresh: dict[str, str] = {}

            def mark(uid, why):
                if REASON_RANK[why] > REASON_RANK.get(fresh.get(uid, ""), 0):
                    fresh[uid] = why

            for uid, why in self._incoming_now(side, since):
                mark(uid, why)
                victim = self.units[uid]
                for mate in self.alive(side):
                    if mate.id != uid and max(abs(mate.pos[0] - victim.pos[0]),
                                              abs(mate.pos[1] - victim.pos[1])) <= BELIEF_SPREAD:
                        mark(mate.id, "near_miss")
            for u in self.alive(side):
                if u.id in fired and self.exposed_to(u.pos, side, u.posture):
                    mark(u.id, "fired")
                if self._watched_by(u):
                    mark(u.id, "watched")
            for uid, why in fresh.items():
                bs[uid] = (self.tick, why)
            for uid in list(bs):
                u = self.units.get(uid)
                if u is None or not u.alive or (self.tick - bs[uid][0]) * self.dt >= BELIEF_TTL_S - 1e-9:
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

    def unit_in_contact(self, u: Unit) -> bool:
        """大地圖（連續地圖）：只看這個人附近 CONTACT_R 內有沒有已知的活敵人、或自己正被壓制。
        小地圖（單格）維持整個 side 一起算。"""
        if self.map.w <= 40:
            return self.in_contact(u.side)
        if u.suppression > 0:
            return True
        intel = self.intel.get(u.side)
        if not intel:
            return False
        r = CONTACT_R / self.map.cell
        for uid, (n, _) in intel.last_known.items():
            t = self.units.get(uid)
            if t is not None and t.alive and max(abs(n[0] - u.pos[0]), abs(n[1] - u.pos[1])) <= r:
                return True
        return False

    def move_budget(self, u: Unit) -> int:
        """行動點：戰鬥狀態 2；未接敵時以行軍速度移動（MARCH_AP）。"""
        if u is None:
            return 2
        c = self._contact_cache
        key = u.side if self.map.w <= 40 else u.id
        if c.get("t") != self.tick or not c.get("on"):
            return 2 if self.unit_in_contact(u) else MARCH_AP
        if key not in c:
            c[key] = self.unit_in_contact(u)
        return 2 if c[key] else MARCH_AP

    def freeze_contact(self, on: bool):
        """規劃期間（狀態不變）快取「是否接敵」；規劃完關掉。"""
        self._contact_cache.clear()
        self._contact_cache.update({"t": self.tick, "on": on})

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

    def _snap_target(self, u: Unit) -> Unit | None:
        """行進間射擊：本方看得到、這個人也看得到部位、在他面前（面向 ±90° 內）的最近敵人。"""
        best, bd = None, None
        e = u.eye(self.map)
        fx, fy = u.facing
        for t in self.enemies_of(u.side):
            if not self.is_spotted(u.side, t) or not self.visible_parts(u, t):
                continue
            if (t.pos[0] - u.pos[0]) * fx + (t.pos[1] - u.pos[1]) * fy < 0:
                continue                         # 在背後：邊走邊打不能往後打
            d = length(sub(t.torso(self.map), e))
            if d > WEAPONS[u.weapon]["range"] * 1.5:
                continue
            if bd is None or (d, t.id) < (bd, best.id):
                best, bd = t, d
        return best

    def _in_cover(self, u: Unit) -> bool:
        return any(self.visibility(e, u) < 0.5 for e in self.enemies_of(u.side)) or not self.enemies_of(u.side)

    # ------------------------------------------------------------------ state

    def state(self) -> dict:
        from engine.tactical import timeline
        return {
            "tick": self.tick,
            "seed": self.seed,
            "timeline": timeline.state(self),
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
        from engine.tactical import timeline
        timeline.load(self, st.get("timeline", {}))

    def over(self) -> str | None:
        """只剩一個陣營（例如藍＋綠都是 ally）→ 結束，回傳存活的 side（多個同陣營 side 時回傳任一）。"""
        sides = {u.side for u in self.alive()}
        if not any(self.hostile(a, b) for a in sides for b in sides):
            return min(sides) if sides else "none"
        return None
