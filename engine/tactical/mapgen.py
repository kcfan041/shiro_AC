"""作戰層格子 → 25×25 戰術地圖。主要地點手畫，其他依地形模板以固定 seed 生成。

座標：x 西→東、y 南→北（與作戰層一致：列 A→E 南→北）。所有建築都保證可進入、地圖保持連通。
"""
from __future__ import annotations

import random
import zlib

from engine import cells
from engine.tactical import relief
from engine.tactical.tmap import TacMap

SIZE = 25
TOWER_HILL = 2.0          # 中繼塔所在的緩丘高度（m，中央，往四邊降到 0）

TERRAIN_TEMPLATE = {
    "建築廢墟": "ruins", "多層建築": "ruins", "大型建築": "building", "堅固建築": "building",
    "燒毀建物": "ruins", "校舍": "building", "低矮民宅": "houses", "倉儲建築": "warehouse",
    "倉庫": "warehouse", "工業廠房": "factory", "貨櫃區": "containers", "設施": "factory",
    "開闊街道": "street", "開闊路口": "crossroad", "瓦礫空地": "rubble_field", "混凝土渠道": "street",
    "高架道路": "street", "地鐵站": "ruins", "枯樹林": "park", "開闊田野": "field",
    "鐵軌與車廂": "containers", "橋頭": "street", "碼頭": "containers", "高塔基座": "tower",
}


# 地面類型（只給畫面用，不影響規則）：r 柏油路 p 人行道／鋪面 c 水泥 d 泥土碎石 g 草地
GROUND = {"street": "p", "crossroad": "p", "rubble_field": "d", "ruins": "d", "houses": "g", "building": "p",
          "warehouse": "c", "factory": "c", "containers": "c", "field": "g", "park": "g", "tower": "c", "base": "c"}


def ground_at(template: str, x: int, y: int) -> str:
    """模板的地面類型（局部座標）；街道與路口的車道（gen_template 空出來的那幾條）是柏油路。"""
    name = template.split(":", 1)[-1]
    if name == "street" and abs(x - 12) <= 2:
        return "r"
    if name == "crossroad" and (abs(x - 12) <= 2 or abs(y - 12) <= 2):
        return "r"
    return GROUND.get(name, "d")


def seed_for(scenario_id: str, cell: str) -> int:
    return zlib.crc32(f"{scenario_id}:{cell}".encode()) & 0x7FFFFFFF


# ---------------------------------------------------------------------- primitives


def building(m: TacMap, x0: int, y0: int, x1: int, y1: int, rng: random.Random,
             windows: int = 3, doors: int = 2):
    """外牆一圈，隨機開窗與門（門至少一個）。有地形時先把範圍整平。"""
    if m.ground:
        relief.flatten(m, x0, y0, x1, y1)
    edge = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
            if (x in (x0, x1) or y in (y0, y1)) and not (x in (x0, x1) and y in (y0, y1))]
    rng.shuffle(edge)
    door_cells = set(edge[:max(1, doors)])
    win_cells = set(edge[max(1, doors):max(1, doors) + windows])
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if not (x in (x0, x1) or y in (y0, y1)):
                continue
            if (x, y) in door_cells:
                continue
            if (x, y) in win_cells:
                m.window_wall(x, y)
            else:
                m.wall(x, y)
    m.add_room(x0, y0, x1, y1, door_cells)
    return door_cells


def scatter(m: TacMap, rng: random.Random, n: int, kind: str, free: set, avoid_edges: bool = True):
    cells = [c for c in free if not avoid_edges or (2 <= c[0] <= SIZE - 3 and 2 <= c[1] <= SIZE - 3)]
    rng.shuffle(cells)
    for x, y in cells[:n]:
        if kind == "rubble":
            m.rubble(x, y)
        elif kind == "veg":
            m.veg(x, y)
        elif kind == "lowwall":
            m.low_wall(x, y, rng.choice("NSEW"))
        free.discard((x, y))


def _place_buildings(m: TacMap, rng: random.Random, count: int, wmin: int, wmax: int, free: set,
                     margin: int = 3) -> list[tuple[int, int, int, int]]:
    placed = []
    for _ in range(count * 25):
        if len(placed) >= count:
            break
        w = rng.randint(wmin, wmax)
        h = rng.randint(wmin, wmax)
        x0 = rng.randint(margin, SIZE - margin - w)
        y0 = rng.randint(margin, SIZE - margin - h)
        rect = (x0 - 1, y0 - 1, x0 + w, y0 + h)
        if any(not (rect[2] < p[0] - 1 or rect[0] > p[2] + 1 or rect[3] < p[1] - 1 or rect[1] > p[3] + 1)
               for p in placed):
            continue
        if any((x, y) not in free for x in range(x0 - 1, x0 + w + 1) for y in range(y0 - 1, y0 + h + 1)):
            continue
        building(m, x0, y0, x0 + w - 1, y0 + h - 1, rng, windows=rng.randint(2, 4), doors=rng.randint(1, 2))
        placed.append((x0, y0, x0 + w - 1, y0 + h - 1))
        for x in range(x0, x0 + w):
            for y in range(y0, y0 + h):
                free.discard((x, y))
    return placed


# ---------------------------------------------------------------------- templates


def _all_free() -> set:
    return {(x, y) for x in range(SIZE) for y in range(SIZE)}


def gen_template(name: str, rng: random.Random, m: TacMap | None = None,
                 rr: random.Random | None = None) -> TacMap:
    """依模板產生一格。m：已經套好地形的空地圖（沒給就全平）；rr：地形用的亂數（沒給就不加模板地形特徵）。"""
    m = m if m is not None else TacMap(w=SIZE, h=SIZE)
    if rr is not None:
        relief.features(m, name, rr)
    free = _all_free()
    big = None
    if name == "field":
        scatter(m, rng, 18, "veg", free)
        scatter(m, rng, 6, "lowwall", free)
        scatter(m, rng, 4, "rubble", free)
    elif name == "park":
        scatter(m, rng, 70, "veg", free)
        scatter(m, rng, 6, "lowwall", free)
        scatter(m, rng, 4, "rubble", free)
    elif name in ("street", "crossroad"):
        lanes = {12} if name == "street" else {12}
        for x in range(SIZE):
            for y in range(SIZE):
                if name == "crossroad" and (abs(x - 12) <= 2 or abs(y - 12) <= 2):
                    free.discard((x, y))
                elif name == "street" and abs(x - 12) <= 2:
                    free.discard((x, y))
        _place_buildings(m, rng, 4, 4, 6, free)
        scatter(m, rng, 10, "rubble", free | {(12, y) for y in range(3, 22, 4)})
        scatter(m, rng, 6, "lowwall", free)
        _ = lanes
    elif name == "rubble_field":
        scatter(m, rng, 45, "rubble", free)
        scatter(m, rng, 10, "lowwall", free)
        _place_buildings(m, rng, 1, 4, 5, free)
    elif name == "ruins":
        _place_buildings(m, rng, 5, 4, 6, free)
        scatter(m, rng, 30, "rubble", free)
        scatter(m, rng, 8, "lowwall", free)
    elif name == "houses":
        _place_buildings(m, rng, 8, 3, 4, free, margin=2)
        scatter(m, rng, 14, "veg", free)
        scatter(m, rng, 10, "lowwall", free)
    elif name in ("building", "warehouse", "factory"):
        size = {"building": (8, 11), "warehouse": (9, 13), "factory": (10, 14)}[name]
        placed = _place_buildings(m, rng, 1, size[0], size[1], free, margin=4)
        big = placed[0] if placed and name in ("warehouse", "factory") else None
        _place_buildings(m, rng, 3, 3, 5, free)
        scatter(m, rng, 14, "rubble", free)
        scatter(m, rng, 6, "lowwall", free)
    elif name == "containers":
        for _ in range(16):
            x, y = rng.randint(2, SIZE - 5), rng.randint(2, SIZE - 3)
            for dx in range(rng.randint(2, 3)):
                if (x + dx, y) in free:
                    m.wall(x + dx, y, height=2.6, tag="wall")
                    free.discard((x + dx, y))
        scatter(m, rng, 8, "rubble", free)
    elif name == "tower":
        _tower(m, rng, free)
    else:
        scatter(m, rng, 20, "rubble", free)
    if rr is not None and big is not None:
        relief.loading_dock(m, big, rr)          # 放完東西才做、只用空格：不改其他東西的位置
    ensure_connected(m)
    return m


# ---------------------------------------------------------------------- handmade


def _tower(m: TacMap, rng: random.Random, free: set):
    """通訊中繼塔：中央塔基建築（二樓平台＋樓梯），周圍矮牆與瓦礫。"""
    building(m, 10, 10, 14, 14, rng, windows=4, doors=2)
    for x in range(11, 14):
        for y in range(11, 14):
            m.platform(x, y)
            free.discard((x, y))
    m.stair(11, 11)
    for x in range(10, 15):
        for y in range(10, 15):
            free.discard((x, y))
    for x in (11, 13):
        m.side_panel(x, 13, "N", 0.0, 0.9, thick=0.2, l=1, tag="lowwall")
        m.side_panel(x, 11, "S", 0.0, 0.9, thick=0.2, l=1, tag="lowwall")
    for x, y, s in ((8, 8, "S"), (16, 8, "S"), (8, 16, "N"), (16, 16, "N"), (12, 7, "S"), (12, 17, "N")):
        m.low_wall(x, y, s)
        free.discard((x, y))
    scatter(m, rng, 12, "rubble", free)
    scatter(m, rng, 6, "veg", free)


def gen_base(rng: random.Random, m: TacMap | None = None) -> TacMap:
    """我方後方據點：南側集結點，沙包工事、兩棟小建築。"""
    m = m if m is not None else TacMap(w=SIZE, h=SIZE)
    free = _all_free()
    building(m, 4, 4, 8, 8, rng, windows=3, doors=2)
    building(m, 16, 4, 20, 8, rng, windows=3, doors=2)
    for x in range(9, 16):
        m.low_wall(x, 14, "N", height=1.1)
        free.discard((x, 14))
    for x, y in ((6, 14), (18, 14)):
        m.low_wall(x, y, "N", height=1.1)
        free.discard((x, y))
    for x in range(4, 9):
        for y in range(4, 9):
            free.discard((x, y))
    for x in range(16, 21):
        for y in range(4, 9):
            free.discard((x, y))
    scatter(m, rng, 10, "rubble", free)
    ensure_connected(m)
    return m


def gen_enemy_base(rng: random.Random, template: str, m: TacMap | None = None,
                   rr: random.Random | None = None) -> TacMap:
    """敵方據點：依地形模板再加一圈北側防線（敵方面向南）；有地形時防線後面有預挖的散兵坑。"""
    m = gen_template(template, rng, m, rr)
    for x in range(3, SIZE - 3, 2):
        if m.standable((x, 6, 0)):
            m.low_wall(x, 6, "S", height=1.1)
    if rr is not None:
        relief.foxholes(m, [(x, 8) for x in range(4, SIZE - 4, 3)], "S")
    ensure_connected(m)
    return m


# ---------------------------------------------------------------------- connectivity


def refresh_doors(m: TacMap):
    """門＝外牆上可站立、且內外都通的格子（連通修正可能在牆上打出新開口，也可能被瓦礫堵住）。"""
    for r in m.rooms:
        x0, y0, x1, y1 = r["rect"]
        doors = []
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if not (x in (x0, x1) or y in (y0, y1)) or (x in (x0, x1) and y in (y0, y1)):
                    continue
                if not m.standable((x, y, 0)):
                    continue
                inner = [(nb[0], nb[1]) for nb, _ in m.neighbors((x, y, 0))
                         if nb[2] == 0 and x0 < nb[0] < x1 and y0 < nb[1] < y1]
                outer = [(nb[0], nb[1]) for nb, _ in m.neighbors((x, y, 0))
                         if nb[2] == 0 and not (x0 <= nb[0] <= x1 and y0 <= nb[1] <= y1)]
                if inner and outer:
                    doors.append((x, y))
        r["doors"] = sorted(doors)


def ensure_connected(m: TacMap):
    """所有地面可站格必須互通：把不連通的區域用「拆掉最近的牆」打通到主區域。"""
    try:
        _ensure_connected(m)
    finally:
        refresh_doors(m)


def _ensure_connected(m: TacMap):
    for _ in range(40):
        comps = _components(m)
        if len(comps) <= 1:
            return
        comps.sort(key=len, reverse=True)
        main = comps[0]
        small = comps[1]
        best = None
        for (x, y) in small:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for k in range(1, 4):
                    nx, ny = x + dx * k, y + dy * k
                    if (nx, ny) in main:
                        if best is None or k < best[0]:
                            best = (k, [(x + dx * j, y + dy * j) for j in range(1, k)])
                        break
        if best is None:
            return
        for cx, cy in best[1]:
            _clear_cell(m, cx, cy)


def _clear_cell(m: TacMap, x: int, y: int):
    x0, y0 = m.cell_lo(x, y)
    x1, y1 = x0 + m.cell, y0 + m.cell
    m.boxes = [b for b in m.boxes
               if not (b.material == "solid" and b.tag != "floor" and b.lo[2] < 2.5
                       and b.lo[0] < x1 - 1e-6 and b.hi[0] > x0 + 1e-6
                       and b.lo[1] < y1 - 1e-6 and b.hi[1] > y0 + 1e-6)]
    m._cache.clear()


def _components(m: TacMap) -> list[set]:
    nodes = {(x, y) for x in range(m.w) for y in range(m.h) if m.standable((x, y, 0))}
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            c = stack.pop()
            if c in comp:
                continue
            comp.add(c)
            for nb, _ in m.neighbors((c[0], c[1], 0)):
                if nb[2] == 0 and (nb[0], nb[1]) in nodes and (nb[0], nb[1]) not in comp:
                    stack.append((nb[0], nb[1]))
        seen |= comp
        comps.append(comp)
    return comps


# ---------------------------------------------------------------------- entry


def generate(scenario_id: str, cell: str, info: dict, base: str, enemy_bases: list[str]) -> tuple[TacMap, str]:
    rng = random.Random(seed_for(scenario_id, cell))
    template = info.get("template") or TERRAIN_TEMPLATE.get(info.get("terrain", ""), "ruins")
    m = rr = None
    if relief.ENABLED:
        # 地形用自己的亂數：加了地形之後，建築、瓦礫、矮牆的位置和全平時相同
        m = TacMap(w=SIZE, h=SIZE)
        r, c = cells.parse(cell)
        relief.apply_base(m, relief.seed_of(scenario_id), c * SIZE, r * SIZE)
        rr = random.Random(seed_for(scenario_id, cell) ^ 0x5EED)
        if template == "tower":
            relief.hill(m, TOWER_HILL)
    if cell == base:
        return gen_base(rng, m), "base"
    if cell in enemy_bases:
        return gen_enemy_base(rng, template, m, rr), f"enemy_base:{template}"
    return gen_template(template, rng, m, rr), template
