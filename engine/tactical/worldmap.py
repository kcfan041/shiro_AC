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
