"""破門：隊長選的戰術選項（不是固定流程）。

隊長給參數：
- room   目標房間 id（None＝AI 選：有已知敵人的房間優先，其次最近）
- prep   flash／frag／smoke／none（None＝依手上投擲物與隨機選）
- entry  one（同一扇門）／multi（兩扇門同時，需 ≥2 門、≥3 人）
- tempo  stack（到齊再進）／quick（到兩人就進）

每 tick 依當下狀況決定每人動作（不是逐段照表走）：
- 還沒到門外 → 用避開已知威脅的路線往待命格移動（待命格從房內越看不到越優先，選擇帶小幅隨機）
- 在門外、還沒投擲 → 依 prep 投擲（看得進房內的人丟；丟向已知／聽到的敵人，沒有就盲投房間深處）
- 投擲後（或 prep=none 且人到齊）→ 突入：第一人往門內對角、第二人往另一側、其餘守門口
- 房內看到敵人 → 射擊／相鄰近戰（沿用一般規則）
- 房內連續 CLEAR_TICKS tick 沒有已知敵人 → 回報「房間已清空，請求新指示」，原地固守

守方預判：門外集結會發出聲音（現有聽覺規則），stack 等越久越容易被聽到。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.tactical.sim import Plan

CLEAR_TICKS = 2
STACK_WAIT_MAX = 3      # stack：最多等 N tick 就進
QUICK_MIN = 2           # quick：到 N 人就進


@dataclass
class BreachState:
    room: int | None = None
    doors: list = field(default_factory=list)
    assign: dict = field(default_factory=dict)     # uid -> door
    stack: dict = field(default_factory=dict)      # uid -> 待命格
    thrown: dict = field(default_factory=dict)     # door -> tick（投擲完成）
    entered: bool = False
    enter_tick: int | None = None
    wait: int = 0
    clear: int = 0
    done: bool = False
    prep: str | None = None


def choose_room(ai, units, want: int | None):
    m = ai.b.map
    if want is not None:
        for r in m.rooms:
            if r["id"] == want and r["doors"]:
                return r
    lk = ai.b.intel[ai.side].last_known
    cx = sum(u.pos[0] for u in units) / len(units)
    cy = sum(u.pos[1] for u in units) / len(units)
    best = None
    for r in m.rooms:
        if not r["doors"]:
            continue
        if any(m.room_at(u.pos) is r for u in units):
            continue
        known = sum(1 for uid, (node, _) in lk.items() if ai.b.units[uid].alive and m.room_at(node) is r)
        x0, y0, x1, y1 = r["rect"]
        d = abs((x0 + x1) / 2 - cx) + abs((y0 + y1) / 2 - cy)
        key = (-known, d + ai.b.r("breach_room", ai.side, r["id"]) * 3)
        if best is None or key < best[0]:
            best = (key, r)
    return best[1] if best else None


def choose_prep(ai, units, prep: str | None) -> str:
    if prep in ("flash", "frag", "smoke", "none"):
        return prep
    has = {"flash": sum(u.flashes for u in units), "frag": sum(u.grenades for u in units)}
    roll = ai.b.r("breach_prep", ai.side, ai.b.tick)
    if has["flash"] and has["frag"]:
        return "flash" if roll < 0.6 else "frag"
    if has["flash"]:
        return "flash" if roll < 0.85 else "none"
    if has["frag"]:
        return "frag" if roll < 0.85 else "none"
    return "none"


ITEM_FOR = {"flash": "flashes", "frag": "grenades", "smoke": "smokes"}
MODE_FOR = {"flash": "flash", "frag": "grenade", "smoke": "smoke"}


def plan(ai, intent, units, occupied, threats) -> dict[str, Plan]:
    """回傳每人計畫。ai.breach 保存狀態（換意圖時重置）。"""
    b, m = ai.b, ai.b.map
    st: BreachState = ai.breach
    plans: dict[str, Plan] = {}
    if not units:
        return plans
    if st.room is None:
        r = choose_room(ai, units, intent.room)
        if r is None:
            ai.reports.append(f"T{b.tick} 附近沒有可破門的建築，請求新指示")
            st.done = True
            return {u.id: ai.fire_plan(u) or ai.idle_plan(u) for u in units}
        st.room = r["id"]
        st.prep = choose_prep(ai, units, intent.prep)
        doors = sorted(r["doors"], key=lambda d: (
            min(abs(d[0] - u.pos[0]) + abs(d[1] - u.pos[1]) for u in units)
            + b.r("breach_door", ai.side, d[0], d[1]) * 4))
        multi = intent.entry == "multi" and len(doors) >= 2 and len(units) >= 3
        st.doors = doors[:2] if multi else doors[:1]
        for i, u in enumerate(sorted(units, key=lambda x: x.id)):
            st.assign[u.id] = st.doors[i % len(st.doors)]
        ai.reports.append(f"T{b.tick} 破門：目標建築 {r['rect']}，{len(st.doors)} 個入口，"
                          f"準備 {st.prep}，{'到齊再進' if intent.tempo != 'quick' else '快速突入'}")
    room = next(r for r in m.rooms if r["id"] == st.room)
    if st.done:
        return {u.id: ai.fire_plan(u) or ai.idle_plan(u) for u in units}

    lk = b.intel[ai.side].last_known
    inside_known = [uid for uid, (node, _) in lk.items() if b.units[uid].alive and m.room_at(node) is room]

    if st.entered:
        if inside_known:
            st.clear = 0
        else:
            st.clear += 1
            if st.clear >= CLEAR_TICKS:
                st.done = True
                ai.reports.append(f"T{b.tick} 房間已清空，請求新指示")
                return {u.id: ai.fire_plan(u) or ai.idle_plan(u) for u in units}

    # 已在門外的人
    for u in units:
        if u.id not in st.assign:
            st.assign[u.id] = st.doors[0]
    ready = {}
    for door in st.doors:
        mine = [u for u in units if st.assign[u.id] == door]
        stacks = m.door_stack_cells(room, door)
        ready[door] = [u for u in mine if (u.pos[0], u.pos[1], 0) in stacks[:4] or u.pos[:2] == door
                       or m.room_at(u.pos) is room
                       or (abs(u.pos[0] - door[0]) <= 1 and abs(u.pos[1] - door[1]) <= 1)]
    total_ready = sum(len(v) for v in ready.values())
    need = len(units) if intent.tempo != "quick" else min(QUICK_MIN, len(units))
    go = st.entered or total_ready >= need or st.wait >= STACK_WAIT_MAX
    if total_ready >= 1 and not st.entered:
        st.wait += 1

    used: set = set()
    item = ITEM_FOR.get(st.prep)
    for door in list(st.doors):
        mine = [u for u in units if st.assign[u.id] == door]
        if not mine and len(st.doors) > 1:
            st.doors.remove(door)
    for u in units:
        if st.assign.get(u.id) not in st.doors:
            st.assign[u.id] = st.doors[0]
    for door in st.doors:
        mine = sorted([u for u in units if st.assign[u.id] == door], key=lambda x: x.id)
        stacks = m.door_stack_cells(room, door)
        entry = m.door_entry(room, door)
        thrown = door in st.thrown
        need_throw = st.prep != "none" and not thrown and go
        if thrown and not st.entered and b.tick - st.thrown[door] > 2 and st.prep != "none":
            need_throw = any(x.can_throw(item) for x in mine)
            if need_throw:
                st.thrown.pop(door, None)
        thrower = None
        if need_throw:
            cands = [x for x in mine if x in ready[door] and x.can_throw(item) and not x.pinned]
            if not cands:
                cands = [x for x in mine if x.can_throw(item) and not x.pinned]
            if cands:
                thrower = min(cands, key=lambda x: (abs(x.pos[0] - door[0]) + abs(x.pos[1] - door[1]), x.id))
            elif not any(getattr(x, item, 0) for x in units):
                st.thrown[door] = b.tick - 1
        front = _front(m, room, door)
        if not st.entered and go and (st.prep == "none" or (door in st.thrown and st.thrown[door] < b.tick)):
            st.entered = True
            st.enter_tick = b.tick
        # 突入順序：離門近的先進（每 tick 依位置重排，避免固定）
        mine = sorted(mine, key=lambda x: (abs(x.pos[0] - door[0]) + abs(x.pos[1] - door[1])
                                           - 2 * x.trait("aggr"), x.id))
        for i, u in enumerate(mine):
            if u.pinned:
                plans[u.id] = ai._pinned(u, occupied | used, threats)
                continue
            fire = ai.fire_plan(u)
            if m.room_at(u.pos) is room:
                p = ai.melee_plan(u) or fire or _clear_move(ai, u, room, door, occupied | used, i)
                plans[u.id] = p
                if p.path:
                    used.add(p.path[-1])
                continue
            if st.entered:
                p = None
                dazed = any(b.units[x].dazzled for x in inside_known)
                if fire and ai.exposure(u.pos, threats) > 0 and not dazed:
                    p = fire
                path = _enter_path(ai, u, room, door, entry, occupied | used, i) if p is None else None
                if path:
                    p = Plan(mode="move", path=path)
                    used.add(path[-1])
                elif p is None and u not in ready[door]:
                    p = _approach(ai, u, stacks, occupied | used, threats, i)
                    if p and p.path:
                        used.add(p.path[-1])
                plans[u.id] = p or fire or ai.idle_plan(u)
                continue
            at_door = u in ready[door]
            if u is thrower:
                tp = _throw_plan(ai, u, room, door, st.prep, inside_known)
                if tp is not None:
                    plans[u.id] = tp
                    st.thrown[door] = b.tick
                    continue
                spot = throw_spot(ai, u, room, door, occupied | used)
                if spot is not None:
                    path = _path_to(ai, u, spot, occupied | used, threats)
                    if path:
                        plans[u.id] = Plan(mode="move", path=path)
                        used.add(path[-1])
                        continue
            if not at_door:
                if fire and ai.exposure(u.pos, threats):
                    plans[u.id] = fire
                    continue
                p = _approach(ai, u, stacks, occupied | used, threats, i)
                plans[u.id] = p or fire or ai.idle_plan(u)
                if p and p.path:
                    used.add(p.path[-1])
                continue
            can_enter = go and (st.prep == "none" or (door in st.thrown and st.thrown[door] < b.tick))
            if not can_enter and u.pos == front:
                back = _approach(ai, u, [s for s in stacks if s != front], occupied | used, threats, i)
                if back:
                    plans[u.id] = back
                    used.add(back.path[-1])
                    continue
            if can_enter:
                path = _enter_path(ai, u, room, door, entry, occupied | used, i)
                if path:
                    plans[u.id] = Plan(mode="move", path=path)
                    used.add(path[-1])
                    st.entered = True
                    st.enter_tick = st.enter_tick or b.tick
                    continue
            plans[u.id] = fire or Plan(mode="overwatch", watch=_door_vec(m, room, door, u))
    return plans


def _door_vec(m, room, door, u):
    return (door[0] - u.pos[0], door[1] - u.pos[1]) if (door[0], door[1]) != u.pos[:2] else None


def _approach(ai, u, stacks, occupied, threats, i):
    b = ai.b
    table = ai.paths(u.pos, threats, occupied)
    opts = [s for s in stacks[:6] if s in table and s not in occupied]
    if not opts:
        return None
    pick = sorted(opts, key=lambda s: (table[s][1], table[s][0] + b.r("stack", u.id, s[0], s[1]) * 2))[0]
    step = ai.ap_len(b, u.pos, table[pick][2])
    return Plan(mode="move", path=step) if step else None


def _targets(ai, u, room, door, inside_known):
    """候選落點（依優先序）：已知／聽到的敵人位置 → 房內離門 2～4 格、丟得進去的格子（盲投，帶隨機）。"""
    b, m = ai.b, ai.b.map
    lk = b.intel[ai.side].last_known
    out = []
    for uid in inside_known:
        out.append(lk[uid][0])
    cells = sorted(room["cells"], key=lambda c: (-min(4, abs(c[0] - door[0]) + abs(c[1] - door[1]))
                                                 + b.r("blind", u.id, b.tick, c[0], c[1]) * 1.5, c))
    out += [(c[0], c[1], 0) for c in cells if abs(c[0] - door[0]) + abs(c[1] - door[1]) >= 2]
    return out


def _throw_plan(ai, u, room, door, prep, inside_known):
    m = ai.b.map
    item = ITEM_FOR.get(prep)
    if not item or not u.can_throw(item):
        return None
    if not (abs(u.pos[0] - door[0]) <= 2 and abs(u.pos[1] - door[1]) <= 2):
        return None
    eye = u.eye(m)
    for node in _targets(ai, u, room, door, inside_known):
        c = m.center(node)
        point = (c[0], c[1], c[2] + 0.1)
        if ai.b.arc_clear(eye, point):
            mode = MODE_FOR[prep]
            return Plan(mode=mode, point=point)
    return None


def _front(m, room, door):
    dx, dy = m.door_outside(room, door)
    return (door[0] + dx, door[1] + dy, 0)


def throw_spot(ai, u, room, door, occupied):
    """待命格丟不進去時：門邊 1 格內（房外）找一個丟得進去的位置，越隱蔽越好；最後才用門口正前方。"""
    m = ai.b.map
    entry = m.door_entry(room, door)
    dx, dy = m.door_outside(room, door)
    deep = (entry[0] - dx, entry[1] - dy, 0)
    tgt = deep if m.room_at(deep) is room else entry
    fc = m.center(tgt)
    target = (fc[0], fc[1], 0.2)
    x0, y0, x1, y1 = room["rect"]
    best = None
    downed = {x.pos for x in ai.b.units.values() if x.down and not x.dead}
    for cx in range(door[0] - 2, door[0] + 3):
        for cy in range(door[1] - 2, door[1] + 3):
            c = (cx, cy, 0)
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                continue
            if (c in occupied and c != u.pos) or c in downed:
                continue
            if not m.in_bounds(cx, cy) or not m.standable(c):
                continue
            cc = m.center(c)
            eye = (cc[0], cc[1], 1.0)
            if not ai.b.arc_clear(eye, target):
                continue
            key = (m.stack_exposure(room, c), abs(cx - u.pos[0]) + abs(cy - u.pos[1]))
            if best is None or key < best[0]:
                best = (key, c)
    if best is None or best[1] == u.pos:
        return None
    return best[1]


def _path_to(ai, u, goal, occupied, threats):
    table = ai.paths(u.pos, threats, occupied)
    if goal not in table:
        return None
    return ai.ap_len(ai.b, u.pos, table[goal][2]) or None


def _enter_path(ai, u, room, door, entry, occupied, i):
    """突入：一律經由指定的門。還在門外 → 走到門口／門內第一格；已在房內 → 第一人往對角、第二人往另一側、其餘守門內。"""
    b, m = ai.b, ai.b.map
    dpos = (door[0], door[1], 0)
    if m.room_at(u.pos) is not room:
        other = {(d[0], d[1], 0) for d in room["doors"] if tuple(d) != tuple(door)}
        block = occupied | other
        if u.pos[:2] != tuple(door) and max(abs(u.pos[0] - door[0]), abs(u.pos[1] - door[1])) > 2:
            return None
        table = ai.paths(u.pos, [], block)
        goals = [entry, dpos] + [(c[0], c[1], 0) for c in room["cells"]]
        for goal in goals:
            if goal in block or goal not in table:
                continue
            path = table[goal][2]
            if dpos in path or goal == dpos:
                step = ai.ap_len(b, u.pos, path)
                if step:
                    return step
        return None
    dx, dy = m.door_outside(room, door)
    cells = [(c[0], c[1], 0) for c in room["cells"] if m.standable((c[0], c[1], 0))]
    if not cells:
        return None
    first = 1 if b.r("corner", room["id"], door[0], door[1]) < 0.5 else -1
    side = first if i == 0 else (-first if i == 1 else 0)
    def score(c):
        depth = abs(c[0] - door[0]) * abs(dy) + abs(c[1] - door[1]) * abs(dx)
        lateral = (c[0] - door[0]) * abs(dy) + (c[1] - door[1]) * abs(dx)
        return (-side * lateral, -depth)
    goals = sorted(cells, key=score) if side else [entry] + sorted(cells, key=lambda c: abs(c[0] - entry[0]) + abs(c[1] - entry[1]))
    table = ai.paths(u.pos, [], occupied)
    for g in goals:
        if g in table and g not in occupied:
            step = ai.ap_len(b, u.pos, table[g][2])
            if step:
                return step
    return None


def _clear_move(ai, u, room, door, occupied, i):
    """房內清掃：沒看到人時往房內還沒去過的角落移動；到了就架槍朝門內最深處。"""
    b, m = ai.b, ai.b.map
    cells = [(c[0], c[1], 0) for c in room["cells"] if m.standable((c[0], c[1], 0)) and (c[0], c[1], 0) not in occupied]
    if not cells:
        return ai.idle_plan(u)
    far = max(cells, key=lambda c: (abs(c[0] - door[0]) + abs(c[1] - door[1]) + b.r("sweep", u.id, i, c[0], c[1]), c))
    if far == u.pos:
        return ai.idle_plan(u)
    table = ai.paths(u.pos, [], occupied)
    if far in table:
        step = ai.ap_len(b, u.pos, table[far][2])
        if step:
            return Plan(mode="move", path=step)
    return ai.idle_plan(u)
