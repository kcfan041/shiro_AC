"""作戰層 ↔ 戰術層：每格永久戰術地圖、進場邊、單位在格內的位置。"""
from __future__ import annotations

from engine import scenario
from engine.tactical import mapgen
from engine.tactical.tmap import Node, TacMap

_MAPS: dict[str, TacMap] = {}
_TEMPLATES: dict[str, str] = {}


def tmap(cell: str) -> TacMap:
    m = _MAPS.get(cell)
    if m is None:
        m, tpl = mapgen.generate(scenario.SCENARIO_ID, cell, scenario.CELLS[cell], scenario.BASE,
                                 scenario.ENEMY_BASES)
        _MAPS[cell] = m
        _TEMPLATES[cell] = tpl
    return m


def template(cell: str) -> str:
    tmap(cell)
    return _TEMPLATES[cell]


def clear_cache():
    _MAPS.clear()
    _TEMPLATES.clear()
    _GLOBAL.clear()


# ---------------------------------------------------------------------- 全圖（連續戰術地圖）

SIZE = mapgen.SIZE
_GLOBAL: dict = {}


def cell_origin(cell: str) -> tuple[int, int]:
    """格子在全圖的左下角（小格座標）。列 A→E 南→北＝y 增加；欄 1→5 西→東＝x 增加。"""
    return scenario.COLS.index(cell[1:]) * SIZE, scenario.ROWS.index(cell[0]) * SIZE


def cell_of(x: float, y: float) -> str | None:
    c, r = int(x) // SIZE, int(y) // SIZE
    if 0 <= r < len(scenario.ROWS) and 0 <= c < len(scenario.COLS):
        return scenario.ROWS[r] + scenario.COLS[c]
    return None


def to_global(cell: str, node) -> tuple:
    ox, oy = cell_origin(cell)
    return (node[0] + ox, node[1] + oy, node[2] if len(node) > 2 else 0)


def to_local(node) -> tuple[str | None, tuple]:
    cell = cell_of(node[0], node[1])
    if cell is None:
        return None, tuple(node)
    ox, oy = cell_origin(cell)
    return cell, (node[0] - ox, node[1] - oy, node[2] if len(node) > 2 else 0)


def global_map() -> TacMap:
    """5×5 格拼成一張 125×125 的地圖（每格原本的方塊平移過去；房間 id 依序重編）。
    格子邊界：兩側都在邊緣第一排放了牆時，打通成門洞（避免整條邊界封死）；最後再保證全圖連通。"""
    g = _GLOBAL.get("map")
    if g is not None:
        return g
    from engine.tactical.geometry import Box
    W, H = len(scenario.COLS) * SIZE, len(scenario.ROWS) * SIZE
    g = TacMap(w=W, h=H)
    room_cell = {}
    for cell in scenario.CELLS:
        m = tmap(cell)
        ox, oy = cell_origin(cell)
        dx, dy = ox * m.cell, oy * m.cell
        for b in m.boxes:
            g.boxes.append(Box((b.lo[0] + dx, b.lo[1] + dy, b.lo[2]), (b.hi[0] + dx, b.hi[1] + dy, b.hi[2]),
                               b.material, b.tag))
        for sx, sy in m.stairs:
            g.stairs.add((sx + ox, sy + oy))
        for r in m.rooms:
            x0, y0, x1, y1 = r["rect"]
            room = g.add_room(x0 + ox, y0 + oy, x1 + ox, y1 + oy, [(d[0] + ox, d[1] + oy) for d in r["doors"]])
            room_cell[room["id"]] = (cell, r["id"])
    g._cache.clear()
    mapgen.ensure_connected(g)
    g.warm()
    _GLOBAL["map"] = g
    _GLOBAL["room_cell"] = room_cell
    return g


def global_room(cell: str, local_room_id: int) -> int | None:
    global_map()
    for gid, (c, lid) in _GLOBAL["room_cell"].items():
        if c == cell and lid == local_room_id:
            return gid
    return None


def zone_cells(center: str, radius: int = 1) -> set[str]:
    r0, c0 = scenario.ROWS.index(center[0]), scenario.COLS.index(center[1:])
    out = set()
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            r, c = r0 + dr, c0 + dc
            if 0 <= r < len(scenario.ROWS) and 0 <= c < len(scenario.COLS):
                out.add(scenario.ROWS[r] + scenario.COLS[c])
    return out


def entry_side(frm: str | None, to: str) -> str | None:
    """從 frm 進入 to 時的進場邊：N/S/E/W。frm 在 to 的南邊 → 從南邊進場。"""
    if not frm or frm == to:
        return None
    dr = scenario.ROWS.index(frm[0]) - scenario.ROWS.index(to[0])
    dc = scenario.COLS.index(frm[1]) - scenario.COLS.index(to[1])
    if abs(dr) >= abs(dc):
        return "S" if dr < 0 else "N"
    return "W" if dc < 0 else "E"


def edge_nodes(m: TacMap, side: str | None, depth: int = 2) -> list[Node]:
    w, h = m.w, m.h
    if side is None:
        cx, cy = w // 2, h // 2
        cells = sorted(((x, y) for x in range(w) for y in range(h)),
                       key=lambda c: abs(c[0] - cx) + abs(c[1] - cy))
    elif side == "S":
        cells = [(x, y) for y in range(depth) for x in sorted(range(w), key=lambda v: abs(v - w // 2))]
    elif side == "N":
        cells = [(x, h - 1 - y) for y in range(depth) for x in sorted(range(w), key=lambda v: abs(v - w // 2))]
    elif side == "W":
        cells = [(x, y) for x in range(depth) for y in sorted(range(h), key=lambda v: abs(v - h // 2))]
    else:
        cells = [(w - 1 - x, y) for x in range(depth) for y in sorted(range(h), key=lambda v: abs(v - h // 2))]
    return [(x, y, 0) for x, y in cells if m.standable((x, y, 0))]


def occupied(world, cell: str, exclude: set[int] | None = None) -> set[Node]:
    out = set()
    exclude = exclude or set()
    for s in world.squads.values():
        if s.status != "active" or s.pos != cell:
            continue
        for mem in s.members:
            if not mem.lost and mem.tpos and id(mem) not in exclude:
                out.add(tuple(mem.tpos))
    for e in world.enemies.values():
        if not e.alive or e.pos != cell:
            continue
        for sd in e.soldiers:
            if sd.get("tpos") and id(sd) not in exclude:
                out.add(tuple(sd["tpos"]))
    for r in getattr(world, "remnants", {}).values():
        if r.status != "holding" or r.pos != cell:
            continue
        for mem in r.members:
            if not mem.lost and mem.tpos and id(mem) not in exclude:
                out.add(tuple(mem.tpos))
    return out


def best_room(m: TacMap) -> dict | None:
    """殘部躲藏用：門最多、房間最大的建築（只看地面層可站的格子）。"""
    rooms = [r for r in m.rooms if r["doors"]]
    if not rooms:
        return None
    return max(rooms, key=lambda r: (len(r["doors"]),
                                     sum(1 for c in r["cells"] if m.standable((c[0], c[1], 0))), -r["id"]))


def place_in_room(world, cell: str, items: list, room: dict):
    """放進房間內：窗邊、面向門口的位置優先，彼此至少隔 1 格（不同格）。"""
    m = tmap(cell)
    taken = occupied(world, cell, exclude={id(i) for i in items})
    cells = [(c[0], c[1], 0) for c in room["cells"] if m.standable((c[0], c[1], 0)) and (c[0], c[1], 0) not in taken]
    doors = room["doors"]

    def score(n):
        s = 0.0
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            if m.in_bounds(n[0] + dx, n[1] + dy) and any(b.tag == "window"
                                                       for b in m._boxes_in_cell(n[0] + dx, n[1] + dy)):
                s += 2
        if doors:
            d = min(max(abs(n[0] - x), abs(n[1] - y)) for x, y in doors)
            s += 1.5 if 2 <= d <= 4 else (0.5 if d > 4 else -1)
        return s
    ranked = sorted(cells, key=lambda n: (-score(n), n))
    chosen: list = []
    for n in ranked:
        if all(n != o for o in chosen):
            chosen.append(n)
        if len(chosen) >= len(items):
            break
    for item, spot in zip(items, chosen):
        d = min(doors, key=lambda dd: max(abs(spot[0] - dd[0]), abs(spot[1] - dd[1]))) if doors else (12, 12)
        f = [int((d[0] > spot[0]) - (d[0] < spot[0])), int((d[1] > spot[1]) - (d[1] < spot[1]))]
        if f == [0, 0]:
            f = [0, -1]
        if isinstance(item, dict):
            item["tpos"], item["facing"] = list(spot), f
        else:
            item.tpos, item.facing = list(spot), f
    rest = items[len(chosen):]
    if rest:
        place_defenders(world, cell, rest)


def place_besiegers(world, cell: str, items: list, room: dict):
    """圍攻方：在建築外 6～10 格、可站立且不在房內的位置，彼此至少隔 2 格。"""
    m = tmap(cell)
    taken = occupied(world, cell, exclude={id(i) for i in items})
    x0, y0, x1, y1 = room["rect"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def gap(x, y):
        dx = max(x0 - x, 0, x - x1)
        dy = max(y0 - y, 0, y - y1)
        return max(dx, dy)
    cands = [(x, y, 0) for x in range(m.w) for y in range(m.h)
             if 6 <= gap(x, y) <= 10 and m.standable((x, y, 0)) and (x, y, 0) not in taken
             and m.room_at((x, y, 0)) is None]
    if not cands:
        cands = [(x, y, 0) for x in range(m.w) for y in range(m.h)
                 if gap(x, y) >= 3 and m.standable((x, y, 0)) and (x, y, 0) not in taken]
    if cands:
        far = max(cands, key=lambda n: (abs(n[0] - cx) + abs(n[1] - cy), n))
        cands.sort(key=lambda n: (abs(n[0] - far[0]) + abs(n[1] - far[1]), n))
    chosen: list = []
    for n in cands:
        if all(max(abs(n[0] - o[0]), abs(n[1] - o[1])) >= 2 for o in chosen):
            chosen.append(n)
        if len(chosen) >= len(items):
            break
    for item, spot in zip(items, chosen):
        f = [int((cx > spot[0]) - (cx < spot[0])), int((cy > spot[1]) - (cy < spot[1]))]
        if isinstance(item, dict):
            item["tpos"], item["facing"] = list(spot), f
        else:
            item.tpos, item.facing = list(spot), f


def place_defenders(world, cell: str, items: list, face: list | None = None):
    """守備布陣（殘部、守軍）：窗後、矮牆後、二樓，彼此至少隔 2 格。"""
    m = tmap(cell)
    taken = occupied(world, cell, exclude={id(i) for i in items})
    for item, spot in zip(items, defensive_spots(m, taken)):
        if isinstance(item, dict):
            item["tpos"], item["facing"] = list(spot), list(face or [0, -1])
        else:
            item.tpos, item.facing = list(spot), list(face or [0, -1])


def place(world, cell: str, items: list, frm: str | None = None, facing: list | None = None):
    """把一組個體（Member 或敵兵 dict）放到 cell 的戰術地圖：依進場邊，互不重疊。"""
    m = tmap(cell)
    side = entry_side(frm, cell)
    taken = occupied(world, cell, exclude={id(i) for i in items})
    spots = [n for n in edge_nodes(m, side, depth=3 if side else 25) if n not in taken]
    if facing is None:
        facing = {"S": [0, 1], "N": [0, -1], "W": [1, 0], "E": [-1, 0]}.get(side, [0, 1])
    for item, spot in zip(items, spots):
        if isinstance(item, dict):
            item["tpos"] = list(spot)
            item["facing"] = list(facing)
        else:
            item.tpos = list(spot)
            item.facing = list(facing)


ENTRY_DEPTH = 4
ENTRY_GAP = 2


ENTRY_FOE_GAP = 6     # 進場位置離格內敵兵至少 N 格


def _hostile(world, cell: str, m: TacMap) -> list:
    """格內敵兵：(位置, 眼點)。進場時避開他們附近與看得到的位置。"""
    from engine.tactical import body
    out = []
    for e in world.enemies.values():
        if not e.alive or e.pos != cell:
            continue
        for sd in e.soldiers:
            if sd.get("tpos") and not sd.get("down"):
                n = tuple(sd["tpos"])
                f = tuple(sd.get("facing") or (0, -1))
                out.append((n, body.eye(m.center(n), sd.get("posture", "crouch"))))
    return out


def _hostile_eyes(world, cell: str, m: TacMap) -> list:
    return [eye for _, eye in _hostile(world, cell, m)]


def entry_spots(world, cell: str, n: int, frm: str | None, taken: set) -> list[Node]:
    """進場隊形：進場邊 ENTRY_DEPTH 排內，彼此至少隔 ENTRY_GAP 格，優先有掩體（矮牆、瓦礫、植被、牆邊）、
    不被格內敵兵看到的位置，並盡量靠近進場邊中段。"""
    m = tmap(cell)
    side = entry_side(frm, cell)
    foes = _hostile(world, cell, m)
    eyes = [eye for _, eye in foes]
    cands = [c for c in edge_nodes(m, side, depth=ENTRY_DEPTH) if c not in taken]
    far = [c for c in cands if all(max(abs(c[0] - n[0]), abs(c[1] - n[1])) >= ENTRY_FOE_GAP for n, _ in foes)]
    if len(far) >= n:
        cands = far
    else:
        deep = [c for c in edge_nodes(m, side, depth=ENTRY_DEPTH + 2) if c not in taken
                and all(max(abs(c[0] - p[0]), abs(c[1] - p[1])) >= ENTRY_FOE_GAP for p, _ in foes)]
        cands = (deep if len(deep) >= n else cands)
    if not cands:
        return []

    def score(c):
        cover = 0.0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for b in m._boxes_in_cell(c[0] + dx, c[1] + dy) if m.in_bounds(c[0] + dx, c[1] + dy) else []:
                    if b.tag in ("lowwall", "rubble", "window"):
                        cover += 1.0 if (dx, dy) == (0, 0) else 0.4
                    elif b.tag in ("wall", "wallside", "veg"):
                        cover += 0.3
        cc = m.center(c)
        seen = sum(1 for e in eyes if m.sight(e, (cc[0], cc[1], cc[2] + 1.0)) > 0.3)
        if side in ("S", "N"):
            mid = abs(c[0] - m.w // 2)
        elif side in ("W", "E"):
            mid = abs(c[1] - m.h // 2)
        else:
            mid = abs(c[0] - m.w // 2) + abs(c[1] - m.h // 2)
        return -4.0 * seen + min(cover, 2.0) - 0.08 * mid

    ranked = sorted(cands, key=lambda c: (-score(c), c))
    out: list[Node] = []
    for c in ranked:
        if all(max(abs(c[0] - o[0]), abs(c[1] - o[1])) >= ENTRY_GAP for o in out):
            out.append(c)
        if len(out) == n:
            return out
    for c in ranked:
        if c not in out:
            out.append(c)
        if len(out) == n:
            break
    return out


def place_squad(world, squad, frm: str | None = None):
    place(world, squad.pos, [m for m in squad.members if not m.lost], frm)


def place_enemy(world, enemy, frm: str | None = None, defend: bool = False):
    if defend:
        m = tmap(enemy.pos)
        taken = occupied(world, enemy.pos, exclude={id(s) for s in enemy.soldiers})
        spots = defensive_spots(m, taken)
        for sd, spot in zip(enemy.soldiers, spots):
            sd["tpos"] = list(spot)
            sd["facing"] = [0, -1]
        rest = [sd for sd in enemy.soldiers if not sd.get("tpos")]
        if rest:
            place(world, enemy.pos, rest, frm)
        return
    place(world, enemy.pos, enemy.soldiers, frm, facing=[0, -1])


def defensive_spots(m: TacMap, taken: set) -> list[Node]:
    """守軍布陣：窗後、矮牆後、二樓平台，越靠近地圖中央越優先；彼此至少隔 2 格。"""
    ranked = _defensive_rank(m, taken)
    out: list[Node] = []
    for n in ranked:
        if all(max(abs(n[0] - o[0]), abs(n[1] - o[1])) >= 2 for o in out):
            out.append(n)
    return out + [n for n in ranked if n not in out]


def _defensive_rank(m: TacMap, taken: set) -> list[Node]:
    cands = []
    for x in range(m.w):
        for y in range(m.h):
            for l in (1, 0):
                n = (x, y, l)
                if n in taken or not m.standable(n):
                    continue
                boxes = m._boxes_in_cell(x, y)
                tags = {b.tag for b in boxes if b.lo[2] < (l + 1) * 3 and b.hi[2] > l * 3}
                score = 0
                if l == 1:
                    score += 3
                if "window" in tags:
                    score += 2
                if "lowwall" in tags:
                    score += 2
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    if m.in_bounds(x + dx, y + dy):
                        nt = {b.tag for b in m._boxes_in_cell(x + dx, y + dy)}
                        if "window" in nt:
                            score += 1
                if score:
                    score -= (abs(x - m.w // 2) + abs(y - m.h // 2)) * 0.05
                    cands.append((score, n))
    cands.sort(key=lambda t: (-t[0], t[1]))
    return [n for _, n in cands]


def render(world, cell: str, side: str | None = None) -> str:
    """GM 用：畫出 cell 的戰術地圖與單位（大寫＝我方小隊首字、小寫 e＝敵兵）。"""
    m = tmap(cell)
    marks = {}
    for s in world.squads.values():
        if s.pos == cell and s.status == "active":
            for mem in s.members:
                if mem.alive and mem.tpos and mem.tpos[2] == 0:
                    marks[(mem.tpos[0], mem.tpos[1])] = s.callsign[0].upper()
    if side is None:
        for e in world.enemies.values():
            if e.pos == cell and e.alive:
                for sd in e.soldiers:
                    if sd.get("tpos") and sd["tpos"][2] == 0:
                        marks[(sd["tpos"][0], sd["tpos"][1])] = "e"
    rows = []
    base = m.render([], level=0).split("\n")
    for line in base[:-1]:
        y = int(line.split()[0])
        cells = line.split()[1:]
        for (x, yy), ch in marks.items():
            if yy == y:
                cells[x] = ch
        rows.append(f"{y:>2} " + "".join(cells))
    rows.append("   " + "".join(str(x % 10) for x in range(m.w)))
    return "\n".join(rows)
