"""戰術層時間線（模擬重構第 8 步）：每個人有一個「進行中的動作」，動作裡的事件各有時間點，
依時間先後結算；同一時刻的射擊、爆炸一起套用（同時性），先被打倒的人後面的事件就作廢。

- start(b, u, plan)：計畫 → 動作（排好事件）。動作在 b.acts、事件在 b.queue（都在 Battle 上，快照、存讀會帶著）。
- advance(b)：推進一個執行步（b.dt 秒），依序處理 (步首, 步末] 之間的事件，步末做偵測、架槍、壓制消退、士氣等。
- 事件時間對齊到 0.05 秒（同一時刻的事件才能湊成一批同時結算）。
- 射擊用真實節奏：瞄準時間看距離、露出多少、熟練度、壓制；點放、連發之後要回穩；壓制分兩波打出。
- 移動：一格的時間＝行動點成本 × 每點秒數（接敵 2 點／2.5 秒、行軍 6 點、衝刺 +2 點，與原本每步格數相同）；
  每走一格，移動的人對各方做一次偵測（原本也是每個微步一次）。
- 警戒射擊：看到已發現的敵人移動，已架槍 0.2 秒、沒架槍 0.45 秒後開火；每名警戒者 1 秒才能再觸發一次，
  每個移動目標 1 秒內最多被觸發一次。
- 投擲：出手 0.8 秒；破片 1.2 秒後爆炸、閃光 0.5 秒後爆閃、煙霧出手時落地（2.5 秒後才生效）。
"""
from __future__ import annotations

import heapq
import math
from dataclasses import asdict

from engine.tactical import facing
from engine.tactical.geometry import length, sub
from engine.tactical.sim import (DAZZLE_DETECT, DECAY_SEEN, DECAY_UNSEEN, DOWN_SECONDS, OW_MIN_VIS, SPRINT_AP,
                                 TICK_S, Plan, _supp_level)
from engine.tactical.units import WEAPONS

# ---------------------------------------------------------------------- 動作時間（秒；第 12 步統一調平衡）

QUANT = 0.05
AIM_BASE = {"aimed": 0.6, "burst": 0.35}
AIM_PER_M = {"aimed": 0.012, "burst": 0.006}
AIM_MIN, AIM_MAX = 0.25, 2.0
RECOVER = {"aimed": 0.4, "burst": 0.5}
SUPP_VOLLEYS = (0.4, 1.4)       # 壓制分兩波打出
SUPP_END = 2.0
OW_REACT_BRACED = 0.2
OW_REACT = 0.45
OW_COOLDOWN = 1.0
OW_TARGET_GAP = 1.0
SNAP_FIRST = 1.25               # 行進間射擊：開始移動後這麼久開第一槍，之後每 2.5 秒
THROW_RELEASE = 0.8
GRENADE_FUSE = 1.2
FLASH_FUSE = 0.5
THROW_END = 1.2
MELEE_HIT = 0.6
MELEE_END = 1.0
AID_S = 2.5
TRANSFER_S = 1.0
POSTURE_S = 0.5
WATCH_S = 2.5                   # 警戒／待命：這麼久後重新評估（局勢變了會提早，見 Engagement._due）
RETRY_S = 0.5                   # 移動被擋、被壓制：這麼久後再試
FWD_DEG, BACK_DEG = 45.0, 135.0  # 面向與移動方向夾角：45° 內算正面、135° 以上算倒退，之間算側移
SIDE_SPEED, BACK_SPEED = 0.75, 0.5   # 側移、倒退的速度倍率（正面 1.0，只有正面能衝刺）
REACT_JITTER = 0.3              # 射擊、投擲、開始移動前每人 0～0.3 秒的反應差（不然同一步規劃的人會在同一瞬間動作）
PERCEPT_S = 1.0                 # 完整偵測、士氣、自我暴露推算、「有沒有敵人看得到我」的間隔

# 同一時刻的處理順序：換姿勢 → 移動 → 射擊 → 近戰 → 出手 → 爆炸 → 救護／移交
RANK = {"posture": 0, "move": 1, "fire": 2, "volley": 2, "ow": 2, "snap": 2, "melee": 3, "release": 4,
        "blast": 5, "bang": 5, "aid": 6, "transfer": 6}
PERSIST = {"blast", "bang"}     # 已經在空中的投擲物：投擲者倒下也照樣爆
REDIRECTABLE = {"move", "overwatch", "idle", "brace", "posture"}   # 可以被新計畫中途換掉的動作


def q(t: float) -> float:
    return round(round(t / QUANT) * QUANT, 6)


# ---------------------------------------------------------------------- 計畫 → 動作

def secs_per_ap(b, u, sprint: bool = False) -> float:
    return TICK_S / max(1, b.move_budget(u) + (SPRINT_AP if sprint else 0))


def face_factor(frm, to, face) -> tuple[float, bool]:
    """邊走邊面向 face：回傳（時間倍率, 是否算正面）。正面 1.0、側移 ÷0.75、倒退 ÷0.5。"""
    if face is None:
        return 1.0, True
    mx, my = to[0] - frm[0], to[1] - frm[1]
    fx, fy = face[0] - frm[0], face[1] - frm[1]
    nm, nf = math.hypot(mx, my), math.hypot(fx, fy)
    if nm < 1e-9 or nf < 1e-9:
        return 1.0, True
    ang = math.degrees(math.acos(max(-1.0, min(1.0, (mx * fx + my * fy) / (nm * nf)))))
    if ang <= FWD_DEG + 1e-9:
        return 1.0, True
    if ang < BACK_DEG - 1e-9:
        return 1.0 / SIDE_SPEED, False
    return 1.0 / BACK_SPEED, False


def tile_secs(b, u, p: Plan, to, drag: bool) -> float:
    """從目前位置走到 to 要幾秒：行動點成本 × 每點秒數 × 面向倍率（只有面向前方能衝刺）。"""
    fac, fwd = face_factor(u.pos, to, p.face)
    return b._move_cost(u, to, drag) * secs_per_ap(b, u, p.sprint and fwd) * fac


def set_move_facing(b, u, p: Plan, to):
    """移動中的面向：有指定 face 就面向它，否則面向這一格的方向。"""
    if p.face is not None:
        u.facing = b.map.facing_to(u.pos, p.face)
        return
    dx, dy = to[0] - u.pos[0], to[1] - u.pos[1]
    if dx or dy:
        u.facing = facing.toward(dx, dy)


def aim_time(b, u, p: Plan) -> float:
    """瞄準時間：距離越遠、目標露出越少、自己被壓得越重越慢；熟練度高越快。"""
    target = b.units.get(p.target) if p.target else None
    aim = target.torso(b.map) if target is not None else p.point
    dist = length(sub(aim, u.eye(b.map))) if aim is not None else 20.0
    mode = p.fire if p.fire in AIM_BASE else "aimed"
    t = AIM_BASE[mode] + AIM_PER_M[mode] * dist
    if mode == "aimed" and target is not None:
        t += 0.4 * (1 - len(b.visible_parts(u, target)) / 6)
    t += 0.5 * min(1.0, u.suppression / 100)
    t -= 0.2 * (u.proficiency() - 100) / 100
    if u.weapon == "dmr" and mode == "aimed":
        t += 0.2
    if u.weapon == "shotgun":
        t -= 0.1
    return max(AIM_MIN, min(AIM_MAX, t))


def push(b, t: float, kind: str, uid: str, act: int | None, data: dict | None = None):
    b._qseq += 1
    heapq.heappush(b.queue, (q(t), RANK[kind], uid, b._qseq, kind, act, data or {}))


def _exiting(b, p: Plan) -> bool:
    return b.allow_exit and ((p.mode == "move" and p.target == "__exit__")
                             or (p.mode == "drag" and p.posture == "__exit__"))


def mid_stride(b, a, now: float) -> bool:
    """已經跨出去、還沒走到下一格（起步了，而且還沒抵達；一格最多 5 秒，防呆）。"""
    return bool(a and a.get("path") and a.get("seg_t") is not None and a.get("next_move") is not None
                and a["seg_t"] < now - 1e-9 and a["next_move"] > now + 1e-9
                and a["next_move"] - a["seg_t"] <= 2 * TICK_S + 1e-9)


def _one_step(a, b) -> bool:
    """一步走得到的距離：平面相鄰（含斜向），或同一格上下樓梯。"""
    return a != b and max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1 and abs(a[2] - b[2]) <= 1


def walkable(b, u, to) -> bool:
    """下一格走得過去：相鄰、同層、兩格之間可以通行（AI 的舊路徑可能已經不對）。"""
    return b.adjacent(u.pos, to) and b.map.step_cost(u.pos, to) is not None


def start(b, u, p: Plan, now: float | None = None):
    """開始一個新動作（取代進行中的動作）。"""
    now = b.now if now is None else now
    prev = b.acts.get(u.id)
    # 已經跨出去的那一步要走完：起步了、還沒走到 → 先走完這一格，到了再接新計畫（同一格繼續走的不算）。
    # 不然走到一半就作廢、人回到原地（白走半格，畫面上像被拉回去）。
    if mid_stride(b, prev, now) and not (p.mode in ("move", "drag", "take") and p.path[:1] == prev["path"][:1]):
        prev["path"] = prev["path"][:1]
        prev["then"] = p
        return
    # 同樣的架槍／警戒／待命繼續做，不重新開始（架槍進度、警戒冷卻都延續）
    if prev is not None and p.mode in ("brace", "overwatch", "idle") and prev["plan"].mode == p.mode \
            and prev["plan"].watch == p.watch:
        prev["plan"] = p
        if p.mode != "brace":
            prev["end"] = q(now + WATCH_S)
        return
    b._act_n += 1
    aid = b._act_n
    act = {"plan": p, "id": aid, "t0": now, "end": None, "next_move": None, "path": []}
    b.acts[u.id] = act
    old = u.posture
    if p.mode == "move" and p.posture in ("crouch", "stand") and (
            u.posture == "prone" or (u.posture == "stand" and p.posture == "crouch")):
        u.posture = p.posture                  # 臥姿起身、站姿蹲下邊走（不另花換姿勢的時間）
    if u.posture != old:
        b.emit("posture", actor=u.id, frm=old, to=u.posture)
    u.watch = p.watch if p.mode in ("overwatch", "idle", "posture", "brace") else None
    if u.watch is None and u.braced and p.mode in ("overwatch", "idle", "shoot"):
        u.watch = u.braced
    m = p.mode
    if m in ("move", "drag", "take") and p.path:
        path = list(p.path)
        if _exiting(b, p) and path == [u.pos] and b.on_edge(u.pos):
            _exit(b, u, act)
            return
        if not walkable(b, u, path[0]):
            b.why(u.id, "路線走不通，重新找路")
            act["end"] = q(now)
            return
        if prev is not None and prev.get("next_move") is not None and prev["path"][:1] == path[:1]:
            first = prev["next_move"]          # 同方向的新計畫：這一格的進度延續
            act["seg_t"] = prev.get("seg_t")
        else:                                  # 新的移動也有反應差（和射擊一致，不然掩護火力總是晚移動者一步）
            go = now + REACT_JITTER * b.r("react", u.id, aid)
            first = go + tile_secs(b, u, p, path[0], m == "drag")
            act["seg_t"] = q(go)               # 這一格什麼時候起步（前端依此內插，動作才連續）
        act["path"] = path
        act["next_move"] = q(first)
        set_move_facing(b, u, p, path[0])
        push(b, first, "move", u.id, aid)
        if p.snap:
            push(b, now + SNAP_FIRST, "snap", u.id, aid)
    elif m in ("take", "give"):
        push(b, now + TRANSFER_S, "transfer", u.id, aid)
        act["end"] = q(now + TRANSFER_S)
    elif m == "shoot":
        t0 = now + REACT_JITTER * b.r("react", u.id, aid)
        if u.pinned or p.fire == "suppress":
            for k, dt in enumerate(SUPP_VOLLEYS):
                push(b, t0 + dt, "volley", u.id, aid, {"k": k})
            act["end"] = q(t0 + SUPP_END)
            _emit_aim(b, u, p, t0, t0 + SUPP_VOLLEYS[0])
        else:
            t = t0 + aim_time(b, u, p)
            push(b, t, "fire", u.id, aid)
            act["end"] = q(t + RECOVER.get(p.fire, 0.4))
            _emit_aim(b, u, p, t0, t)
    elif m in ("grenade", "flash", "smoke") and p.point is not None:
        t0 = now + REACT_JITTER * b.r("react", u.id, aid)
        push(b, t0 + THROW_RELEASE, "release", u.id, aid)
        act["end"] = q(t0 + THROW_END)
    elif m == "melee" and p.target:
        push(b, now + MELEE_HIT, "melee", u.id, aid)
        act["end"] = q(now + MELEE_END)
    elif m == "aid" and p.target:
        push(b, now + AID_S, "aid", u.id, aid)
        act["end"] = q(now + AID_S)
    elif m == "posture" and p.posture:
        push(b, now + POSTURE_S, "posture", u.id, aid)
        act["end"] = q(now + POSTURE_S)
    elif m == "brace":
        act["end"] = None                      # 架好（或被打斷）時結束
    else:
        act["end"] = q(now + WATCH_S)


def _emit_aim(b, u, p: Plan, t0: float, fire_t: float):
    """畫面用：開始舉槍瞄準（t0）與預計開火時間（fire）、瞄準的格子。只記錄已經算好的時間，不影響結果。"""
    target = b.units.get(p.target) if p.target else None
    e = b.emit("aim", actor=u.id, target=p.target, mode=p.fire, fire=q(fire_t),
               at=list(target.pos[:2]) if target is not None else None)
    e["ts"] = q(t0)


def assign(b, plans: dict[str, Plan], idle_others: bool = False):
    """套用一批計畫。idle_others（舊的 step(plans) 用）：沒有計畫的人改成待命。"""
    b._phase, b._sub = "pre", 0               # 開始動作時的事件（換姿勢）算在下一步的開頭
    for uid in sorted(b.units):
        u = b.units[uid]
        if uid in plans:
            p = plans[uid]
        elif idle_others:
            p = Plan()
        else:
            continue
        if u.alive:
            start(b, u, p)
        else:
            b.acts.pop(uid, None)


def busy(b, uid: str) -> bool:
    """正在做不能中途換掉的動作（射擊、投擲、近戰、救護、移交）。"""
    a = b.acts.get(uid)
    if a is None or a["plan"].mode in REDIRECTABLE:
        return False
    if a["plan"].mode in ("drag", "take") and a["path"]:
        return False
    return a["end"] is None or a["end"] > b.now + 1e-9


def idle(b, uid: str) -> bool:
    """動作做完（或沒有動作）→ 需要新計畫。"""
    a = b.acts.get(uid)
    if a is None:
        return True
    if a["path"]:
        return False
    return a["end"] is not None and a["end"] <= b.now + 1e-9


def current_plans(b) -> dict:
    return {uid: a["plan"] for uid, a in b.acts.items()}


# ---------------------------------------------------------------------- 推進一步

def advance(b):
    b.tick += 1
    t_end = b.now
    for uid in [x for x in b.acts if x not in b.units]:     # 被移出戰場（離開區域、合併）的人
        del b.acts[uid]
    if len(b.ev) > 4000:
        del b.ev[:-1000]
    b._why = {}
    b._phase, b._sub = "pre", 0
    b.path_sub = {}
    b.path_t = {}
    b.path_st = {}
    b.path_f = {}                              # 畫面用：路徑每格走的時候的面向（邊走邊面向威脅時和移動方向不同）
    b.paths = {}
    b.last_drags = {}
    # 「最近 2.5 秒」：AI 讀的我方火力落點（heavy_fire）與射向本方的子彈（last_shots）以 2.5 秒為視窗
    k = max(1, int(round(TICK_S / b.dt)))
    b._hf_hist = (b._hf_hist + [b.heavy_fire])[-(k - 1):] if k > 1 else []
    b.last_heavy_fire, b.heavy_fire = b.heavy_fire, {}
    lo = b.tick - k
    recent = []
    for s in reversed(b.shots):
        if s.get("tick", 0) < lo:
            break
        if s.get("tick", 0) < b.tick:
            recent.append(s)
    b.last_shots = recent[::-1]
    b._shot_mark = len(b.shots)
    for u in b.units.values():
        u.moved = False
        u.fired = False
    moving = {uid for uid, a in b.acts.items() if a["path"]}
    b.face_threats([u for u in b.units.values() if u.id not in moving])
    pre_state = {uid: (u.posture, sum(1 for v in u.damage.values() if v), u.torso_hits)
                 for uid, u in b.units.items()}
    b._move_batch = 0
    while b.queue and b.queue[0][0] <= t_end + 1e-9:
        t = b.queue[0][0]
        batch = []
        while b.queue and b.queue[0][0] == t:
            batch.append(heapq.heappop(b.queue))
        b._clock = t
        try:
            _run_batch(b, t, batch)
        finally:
            b._clock = None
    _end_of_step(b, pre_state)


def _valid(b, ev) -> bool:
    _, _, uid, _, kind, act, _ = ev
    if kind in PERSIST:
        return True
    u = b.units.get(uid)
    a = b.acts.get(uid)
    return u is not None and u.alive and a is not None and a["id"] == act


def _run_batch(b, t: float, batch: list):
    ranks: dict[int, list] = {}
    for ev in batch:
        ranks.setdefault(ev[1], []).append(ev)
    for rank in sorted(ranks):
        evs = [ev for ev in ranks[rank] if _valid(b, ev)]     # 前一批可能剛打倒了誰
        if not evs:
            continue
        if rank == RANK["posture"]:
            for ev in evs:
                _do_posture(b, ev)
        elif rank == RANK["move"]:
            _do_moves(b, t, evs)
        elif rank == RANK["fire"]:
            _do_fire(b, t, evs)
        elif rank == RANK["melee"]:
            _do_melee(b, t, evs)
        elif rank == RANK["release"]:
            for ev in evs:
                if _valid(b, ev):
                    _do_release(b, t, ev)
        elif rank == RANK["blast"]:
            _do_blasts(b, t, evs)
        else:
            _do_care(b, evs)


def _do_posture(b, ev):
    uid = ev[2]
    u = b.units[uid]
    old = u.posture
    u.posture = b.acts[uid]["plan"].posture
    if old != u.posture:
        b.emit("posture", actor=uid, frm=old, to=u.posture)


def _exit(b, u, act):
    p = act["plan"]
    drag = p.target if p.mode == "drag" else None
    if drag is not None:
        d = b.units.get(drag)
        if d is None or not d.down or d.dead or d.side != u.side:
            drag = None
    u.exited = True
    if drag:
        b.units[drag].exited = True
    b.log.append(f"T{b.tick} {u.id} 撤出戰區")
    b.emit("exit", actor=u.id, target=drag)
    act["path"] = []
    act["next_move"] = None
    act["end"] = b.now


def _then(b, u, p: Plan, t: float):
    """走完一格後接上的新計畫：移動的路徑從現在的位置接著走（走不通就算了，等下次規劃）。"""
    if p.mode in ("move", "drag", "take") and p.path:
        path = list(p.path)
        if u.pos in path:
            path = path[path.index(u.pos) + 1:]
        if not path or not b.adjacent(u.pos, path[0]):
            b.acts[u.id]["end"] = t
            return
        p = Plan(**{**p.__dict__, "path": path})
    start(b, u, p, now=t)


def _drag_target(b, u, p: Plan):
    if p.mode != "drag" or not p.target:
        return None
    d = b.units.get(p.target)
    if d is None or not d.down or d.dead or b.hostile(d.side, u.side) or not (
            d.pos == u.pos or b.adjacent(d.pos, u.pos)):
        return None
    return d.id


def _wait(b, t: float, uid: str, why: str):
    b.why(uid, why)
    a = b.acts[uid]
    a["next_move"] = q(t + RETRY_S)
    a["seg_t"] = None                          # 停下來等：前端不往前推
    push(b, t + RETRY_S, "move", uid, a["id"])


def _do_moves(b, t: float, evs: list):
    b._move_batch += 1
    step_no = b._move_batch
    b._phase, b._sub = "move", step_no
    occ = b.occupied()
    claims: dict = {}
    for ev in sorted(evs, key=lambda e: e[2]):
        uid = ev[2]
        u, a = b.units[uid], b.acts[uid]
        if not a["path"]:
            continue
        if u.pinned:                           # 被壓制：撲到這一格就停（已經跨出去了），不再往前
            b.why(uid, "被壓制到抬不起頭，撲到最近的位置停下")
            a["path"] = a["path"][:1]
            a.pop("then", None)
        node = a["path"][0]
        if not _one_step(u.pos, node):         # 位置在動作途中被外部改過（同步、搬移）：舊路徑作廢，不一步跨過去
            a["path"] = []
            a["next_move"] = None
            a["seg_t"] = None
            a.pop("then", None)                # 閒下來，下次規劃從新位置重來
            continue
        if node in occ and occ[node] != uid:
            _wait(b, t, uid, "前進位置被占用，原地等待")
            continue
        claims.setdefault(node, []).append(uid)
    moved_now = []
    for node, uids in sorted(claims.items()):
        winner = min(uids, key=lambda x: b.r("claim", node, x))
        for loser in uids:
            if loser != winner:
                _wait(b, t, loser, "前進位置被占用，原地等待")
        u, a = b.units[winner], b.acts[winner]
        p = a["plan"]
        dragging = _drag_target(b, u, p)
        set_move_facing(b, u, p, node)
        b.path_f.setdefault(winner, []).append(list(u.facing))
        prev = u.pos
        u.pos = node
        u.moved = True
        b.last_move_t[winner] = t
        b.paths.setdefault(winner, []).append(node)
        b.path_sub.setdefault(winner, []).append(step_no)
        b.path_t.setdefault(winner, []).append(t)
        st = a.get("seg_t")
        b.path_st.setdefault(winner, []).append(st if st is not None and st < t else q(t - RETRY_S))
        a["path"].pop(0)
        moved_now.append(winner)
        if dragging:
            b.units[dragging].pos = prev
            b.paths.setdefault(dragging, []).append(prev)
            b.path_sub.setdefault(dragging, []).append(step_no)
            b.path_t.setdefault(dragging, []).append(t)
            b.path_st.setdefault(dragging, []).append(b.path_st[winner][-1])
            b.path_f.setdefault(dragging, []).append(list(b.units[dragging].facing))
            if winner not in b.last_drags:
                b.emit("drag", actor=winner, target=dragging)
            b.last_drags[winner] = dragging
        if not a["path"] and _exiting(b, p) and b.on_edge(node):
            _exit(b, u, a)
            continue
        if a["path"] and not walkable(b, u, a["path"][0]):
            a["path"] = []                     # 後面的路走不通：停在這裡，等下次規劃
        if a["path"]:
            a["seg_t"] = t
            set_move_facing(b, u, p, a["path"][0])
            a["next_move"] = q(t + tile_secs(b, u, p, a["path"][0], bool(dragging)))
            push(b, a["next_move"], "move", winner, a["id"])
        else:
            a["next_move"] = None
            if a.get("then") is not None:      # 走完這一格了：接著做走路途中收到的新計畫
                _then(b, u, a.pop("then"), t)
            elif p.mode == "take":
                push(b, t + TRANSFER_S, "transfer", winner, a["id"])
                a["end"] = q(t + TRANSFER_S)
            else:
                a["end"] = t
    if moved_now:
        # 每走一格是一次被發現的機會（與原本每個微步一次相同，機率以 2.5 秒計）
        b.detect(f"move{step_no}", set(moved_now), only=set(moved_now), span=TICK_S)
        _overwatch(b, t, moved_now)


def _overwatch(b, t: float, moved_now: list):
    """移動觸發警戒射擊（反應時間之後才開火，那時目標可能已經走開）。"""
    b._phase = "overwatch"
    for wid in sorted(b.units):
        w = b.units[wid]
        a = b.acts.get(wid)
        if a is None or not w.alive or w.pinned or w.dazzled or not w.can_shoot():
            continue
        mode = a["plan"].mode
        if not (mode == "overwatch" or (mode == "idle" and w.braced)):
            continue
        if b.ow_next.get(wid, -1.0) > t + 1e-9:
            continue
        cands = [b.units[m] for m in moved_now
                 if b.hostile(b.units[m].side, w.side) and b.units[m].alive
                 and b.ow_hit.get(m, -9.0) <= t - OW_TARGET_GAP + 1e-9
                 and b.is_spotted(w.side, b.units[m])
                 and b.visible_parts(w, b.units[m])
                 and b.visibility(w, b.units[m]) >= OW_MIN_VIS
                 and b.friendly_clear(w, b.units[m].torso(b.map))]
        if not cands:
            if any(b.hostile(b.units[m].side, w.side) and b.units[m].alive and b.is_spotted(w.side, b.units[m])
                   and 0 < b.visibility(w, b.units[m]) < OW_MIN_VIS
                   and b.smoke_thickness(w.eye(b.map), b.units[m].torso(b.map)) > 0 for m in moved_now):
                b.why(wid, "目標在煙裡看不清，警戒射擊未觸發")
            continue
        target = min(cands, key=lambda x: (not b.in_brace_arc(w, x.torso(b.map)),
                                           length(sub(x.torso(b.map), w.eye(b.map))), x.id))
        b.ow_next[wid] = t + OW_COOLDOWN
        b.ow_hit[target.id] = t
        push(b, t + (OW_REACT_BRACED if w.braced else OW_REACT), "ow", wid, a["id"], {"target": target.id})


def _do_fire(b, t: float, evs: list):
    """同一時刻的射擊：都以此刻的狀態計算，再一起套用（同時性）。"""
    b._phase = "overwatch" if all(ev[4] == "ow" for ev in evs) else "fire"
    ms = int(round(t * 1000))
    results = []
    for ev in sorted(evs, key=lambda e: (e[2], e[3])):
        _, _, uid, _, kind, _, data = ev
        u, a = b.units[uid], b.acts[uid]
        p = a["plan"]
        label = f"{kind}{ms}"
        shots = []
        if kind == "ow":
            tgt = b.units.get(data["target"])
            if tgt is not None and tgt.alive and not u.pinned and b.is_spotted(u.side, tgt) \
                    and b.visible_parts(u, tgt) and b.visibility(u, tgt) >= OW_MIN_VIS:
                shots = b.fire(u, Plan(mode="shoot", target=tgt.id, fire="aimed"), label, overwatch=True)
        elif kind == "snap":
            if a["path"]:
                push(b, t + TICK_S, "snap", uid, a["id"])
            if not u.pinned and u.can_shoot():
                tgt = b._snap_target(u)
                if tgt is not None:
                    shots = b.fire(u, Plan(mode="shoot", target=tgt.id, fire="aimed"), label)
        else:
            if u.pinned and p.fire != "suppress" and kind == "fire" and not b._in_cover(u):
                b.why(uid, "被壓制在空地上，無法瞄準射擊")
                continue
            target = b.units.get(p.target) if p.target else None
            if target is not None and kind == "fire" and not b.is_spotted(u.side, target):
                b.why(uid, "目標已離開視線")
                continue
            plan = p
            if kind == "fire" and u.dazzled and b.r("dazzle_fire", uid) >= DAZZLE_DETECT:
                plan = Plan(mode="shoot", fire="suppress", point=target.torso(b.map) if target else p.point)
                if plan.point is None:
                    continue
            if kind == "volley":
                total = WEAPONS[u.weapon]["supp_rounds"]
                n = total - total // 2 if data.get("k", 0) == 0 else total // 2
                if n <= 0:
                    continue
                plan = Plan(mode="shoot", target=p.target, point=p.point, fire="suppress")
                shots = b.fire(u, plan, label, rounds=n)
            else:
                shots = b.fire(u, plan, label)
        if shots:
            results.append((u, shots))
    if results:
        b.apply(results, grp=f"t{b.tick}-{'ow' if b._phase == 'overwatch' else 'fire'}{ms}")


def _do_melee(b, t: float, evs: list):
    b._phase = "fire"
    ms = int(round(t * 1000))
    grp = f"t{b.tick}-melee{ms}"
    bouts = []
    for ev in sorted(evs, key=lambda e: e[2]):
        u = b.units[ev[2]]
        tgt = b.units.get(b.acts[ev[2]]["plan"].target)
        if tgt is not None and tgt.alive and b.adjacent(u.pos, tgt.pos):
            bouts.append((u, tgt, b.melee(u, tgt, f"melee{ms}")))
    for x, y, res in bouts:
        b.log.append(f"T{b.tick} {x.id} 突擊 {y.id}")
        b.used.setdefault(x.id, set()).add(x.weapon)
        aid = b.new_aid(x.id)
        b.emit("melee", actor=x.id, target=y.id, aid=aid, grp=grp, winner=res[0] if res else None)
        b._ctx = (aid, grp)
        if res is None:
            for m, n in ((x, y), (y, x)):
                if not m.dead:
                    b._damage(m, "torso", n, 9001)
        else:
            winner, loser = b.units[res[0]], b.units[res[1]]
            if not loser.down:
                b._down(loser, winner)
        b._ctx = None
        x.fired = True


def _do_release(b, t: float, ev):
    uid = ev[2]
    u = b.units[uid]
    p = b.acts[uid]["plan"]
    b._phase = "throw"
    ms = int(round(t * 1000))
    if p.mode == "smoke":
        b.throw_smoke(u, p.point, f"smoke{ms}")
    elif u.pinned:
        b.why(uid, "被壓制，沒能投擲")
    elif p.mode == "grenade":
        g = b.throw(u, p.point, f"gren{ms}")
        if g:
            push(b, t + GRENADE_FUSE, "blast", uid, None, {"blast": g})
    elif p.mode == "flash":
        g = b.throw(u, p.point, f"flash{ms}", item="flashes")
        if g:
            push(b, t + FLASH_FUSE, "bang", uid, None, {"blast": g})


def _do_blasts(b, t: float, evs: list):
    """同一時刻的爆炸一起套用（傷害、壓制累加）。"""
    b._phase = "throw"
    ms = int(round(t * 1000))
    grp = f"t{b.tick}-throw{ms}"
    g_supp: dict[str, float] = {}
    g_hurt = []
    for ev in [e for e in evs if e[4] == "blast" and e[6]["blast"]["thrower"] in b.units]:
        bl = ev[6]["blast"]
        bl["ts"] = t
        b.blasts.append(bl)
        b.log.append(f"T{b.tick} {bl['thrower']} 投擲手榴彈")
        b.emit("blast", actor=bl["thrower"], aid=bl.get("aid"), grp=grp, item="grenades", at=b.gxy(bl["at"]))
        hurt, supp = b.explode(bl)
        for uid, part in hurt:
            g_hurt.append((bl["thrower"], uid, part, bl.get("aid")))
        for uid, val in supp.items():
            g_supp[uid] = g_supp.get(uid, 0.0) + val
    for thrower_id, uid, part, aid in g_hurt:
        target, thrower = b.units.get(uid), b.units.get(thrower_id)
        b._ctx = (aid, grp)
        if target is None or thrower is None or target.dead:
            continue
        if target.down:
            if part == "torso2":
                b._kill(target, thrower, "手榴彈")
        elif part == "torso2":
            b._damage(target, "torso", thrower, 9100)
            if not target.down:
                b._damage(target, "torso", thrower, 9101)
        else:
            b._damage(target, part, thrower, 9102)
    b._ctx = None
    for uid, val in g_supp.items():
        u = b.units[uid]
        if u.alive:
            before = u.suppression
            b.add_suppression(u, val)
            b.emit("suppress", target=uid, grp=grp, amount=round(val, 1),
                   level=_supp_level(u.suppression), was=_supp_level(before), by="blast")
    fgrp = f"t{b.tick}-flash{ms}"
    for ev in [e for e in evs if e[4] == "bang" and e[6]["blast"]["thrower"] in b.units]:
        fl = ev[6]["blast"]
        fl["ts"] = t
        b.blasts.append(fl)
        b.log.append(f"T{b.tick} {fl['thrower']} 投擲閃光彈")
        b.emit("blast", actor=fl["thrower"], aid=fl.get("aid"), grp=fgrp, item="flashes", at=b.gxy(fl["at"]))
        dazed, f_supp = b.flash(fl)
        for uid, n in dazed.items():
            u = b.units[uid]
            if u.alive:
                u.dazzled = max(u.dazzled, n)
                b.log.append(f"T{b.tick} {uid} 光學過載（{n:g} 秒）")
                b.emit("dazzled", actor=fl["thrower"], target=uid, aid=fl.get("aid"), grp=fgrp, seconds=n)
        for uid, val in f_supp.items():
            u = b.units[uid]
            if u.alive:
                b.add_suppression(u, val)


def _do_care(b, evs: list):
    b._phase = "care"
    for ev in sorted(evs, key=lambda e: e[2]):
        uid, kind = ev[2], ev[4]
        u = b.units[uid]
        p = b.acts[uid]["plan"]
        if kind == "aid":
            d = b.units.get(p.target)
            if d is not None and d.down and not d.dead and not d.stabilized and not b.hostile(d.side, u.side) \
                    and (d.pos == u.pos or b.adjacent(u.pos, d.pos)):
                d.stabilized = True
                b.log.append(f"T{b.tick} {d.id} 已被穩定")
                b.emit("aid", actor=uid, target=d.id, state="done")
        else:
            b.transfer_items(u, p)


# ---------------------------------------------------------------------- 步末

def _end_of_step(b, pre_state: dict):
    b._moved_since |= {uid for uid, u in b.units.items() if u.moved}
    b._fired_since |= {uid for uid, u in b.units.items() if u.fired}
    span = b.dt if b._percept_t is None else b.now - b._percept_t
    # 第一次一定做（還沒有上次的時間）；之後每 PERCEPT_S 秒一次
    percept = b._percept_t is None or b.dt >= PERCEPT_S - 1e-9 or span >= PERCEPT_S - 1e-9
    b._phase, b._sub = "post_move", 0
    # 這一步走到、而且停下來的人才轉向威脅；還在走的人維持移動中的面向（不能一邊正面走一邊盯著背後）
    b.face_threats([u for u in b.units.values() if u.moved and not (b.acts.get(u.id) or {}).get("path")])
    if percept:
        b.detect("post_move", set(b._moved_since), span=span)
    b._phase = "care"
    for uid, tid in sorted(b.last_drags.items()):       # 拖人的人順手拿走傷員身上的投擲物
        u, d = b.units[uid], b.units[tid]
        if u.alive and d.throwables() and u.room():
            b.transfer_items(u, Plan(mode="take", target=tid))
    for u in b.units.values():
        if u.dazzled:
            u.dazzled = max(0.0, u.dazzled - b.dt)
    b.smokes = [s for s in b.smokes if s["expires"] >= b.now - 1e-9]
    b._phase = "end"
    bracing = {uid: a["plan"] for uid, a in sorted(b.acts.items())
               if a["plan"].mode == "brace" and b.units[uid].alive and not b.units[uid].pinned}
    b._update_braces(bracing, pre_state)
    for uid, a in b.acts.items():
        u = b.units[uid]
        if a["plan"].mode == "brace" and a["end"] is None and (u.braced or not u.bracing):
            a["end"] = b.now                        # 架好了（或被打斷）：這個動作結束
    for u in b.units.values():
        if u.down and not u.dead and not u.stabilized and u.down_tick is not None \
                and (b.tick - u.down_tick) * b.dt >= DOWN_SECONDS - 1e-9:
            b._kill(u, None, "倒地過久")
    if percept:
        b._span = span
        try:
            b._morale_tick()
        finally:
            b._span = None
        b._seen = {u.id: any(b.sight(e.eye(b.map), u.torso(b.map)) > 0 for e in b.enemies_of(u.side))
                   for u in b.units.values() if u.alive}
    for u in b.units.values():
        if not u.alive:
            continue
        seen = b._seen.get(u.id, True)
        u.suppression = max(0.0, u.suppression - b.per_step(DECAY_SEEN if seen else DECAY_UNSEEN))
    b.refresh_supp_peaks()
    for u in b.units.values():
        if u.fired and u.alive:
            for side in b.intel:
                if b.hostile(side, u.side) and any(b.sight(e.eye(b.map), u.torso(b.map)) > 0
                                                   for e in b.alive(side)):
                    b.intel[side].spotted[u.id] = b.tick
                    b.intel[side].last_known[u.id] = (u.pos, b.tick)
    for side, intel in b.intel.items():
        for uid in list(intel.last_known):
            if not b.units[uid].alive:
                intel.last_known.pop(uid, None)
                intel.spotted.pop(uid, None)
    plans = current_plans(b)
    for uid in [uid for uid in b.acts if not b.units[uid].alive]:
        del b.acts[uid]
    if percept:
        b.update_beliefs(since=b._percept_tick if b._percept_t is not None else None, fired=set(b._fired_since))
        b._percept_t = b.now
        b._percept_tick = b.tick
        b._moved_since = set()
        b._fired_since = set()
    b._finish_reasons(plans)
    b._phase, b._sub = "idle", 0


# ---------------------------------------------------------------------- 存讀

def _plan_from(d: dict) -> Plan:
    d = dict(d)
    d["path"] = [tuple(n) for n in d.get("path") or []]
    for k in ("point", "watch", "face"):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
    return Plan(**d)


def _act_state(a: dict) -> dict:
    out = {k: v for k, v in a.items() if k not in ("plan", "then")}
    out["plan"] = asdict(a["plan"])
    if a.get("then") is not None:
        out["then"] = asdict(a["then"])
    return out


def state(b) -> dict:
    return {"acts": {uid: _act_state(a) for uid, a in sorted(b.acts.items())},
            "queue": [list(ev) for ev in sorted(b.queue)], "qseq": b._qseq, "act_n": b._act_n,
            "ow_next": dict(b.ow_next), "ow_hit": dict(b.ow_hit), "last_move_t": dict(b.last_move_t)}


def load(b, st: dict):
    b.acts = {uid: {**{k: ([tuple(n) for n in v] if k == "path" else v) for k, v in a.items()
                       if k not in ("plan", "then")},
                    "plan": _plan_from(a["plan"]),
                    **({"then": _plan_from(a["then"])} if a.get("then") else {})}
              for uid, a in st.get("acts", {}).items()}
    b.queue = [tuple(ev) for ev in st.get("queue", [])]
    heapq.heapify(b.queue)
    b._qseq = st.get("qseq", 0)
    b._act_n = st.get("act_n", 0)
    b.ow_next = dict(st.get("ow_next", {}))
    b.ow_hit = dict(st.get("ow_hit", {}))
    b.last_move_t = dict(st.get("last_move_t", {}))
