"""作戰層的地形摘要（docs/new/terrain.md §7.4）：每個作戰格從全圖地形算出「高地」「散兵坑」「土堤」等標籤，
給隊長簡報、作戰圖、敵方作戰層 AI 用。看的是目前的地形（含地圖變更層：挖出來的坑、炸低的土堤），依地形版本快取。
只在全圖已經建好時算（連續地圖）；舊的單格模式沒有全圖，回傳空的。"""
from __future__ import annotations

import statistics

from engine import scenario, terrain_edits

HILL_M = 1.0        # 格內最高處比中位數高這麼多（m）→ 高地
PITS_MIN = 3        # 這麼多格是坑（比周圍原始地面低 0.4 m 以上）→ 散兵坑
BERMS_MIN = 6       # 這麼多段土堤、而且比坑多（不只是散兵坑的胸牆）→ 土堤

_CACHE: dict = {}   # (cell, 地形版本) → 摘要


def _global():
    from engine.tactical import worldmap
    return worldmap._GLOBAL.get("map")


def cell_terrain(cell: str) -> dict:
    """{"tags": ["高地（最高處比周圍高約 1.4 m）", "散兵坑 6 處", ...], "marks": "▲◡≡", "hill": m, "pits": n, "berms": n}；
    沒有全圖或不是作戰格回傳空摘要。"""
    g = _global()
    empty = {"tags": [], "marks": "", "hill": 0.0, "pits": 0, "berms": 0}
    if g is None or cell not in scenario.CELLS:
        return empty
    key = (cell, terrain_edits.VERSION)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    from engine.tactical import timeline, worldmap
    ox, oy = worldmap.cell_origin(cell)
    n = worldmap.SIZE
    cells = [(x, y) for x in range(ox, ox + n) for y in range(oy, oy + n)]
    hs = [g.ground.get(c, 0) for c in cells]
    hill = (max(hs) - statistics.median(hs)) * timeline.H_STEP if hs else 0.0
    pits = sum(1 for x, y in cells if timeline.dig_ref(g, x, y) - g.ground.get((x, y), 0) >= timeline.DIG_SHALLOW)
    berms = sum(1 for (x, y, _s), u in g.berm.items() if u > 0 and ox <= x < ox + n and oy <= y < oy + n)
    tags, marks = [], ""
    if hill >= HILL_M - 1e-9:
        tags.append(f"高地（最高處比周圍高約 {hill:.1f} m）")
        marks += "▲"
    if pits >= PITS_MIN:
        tags.append(f"散兵坑 {pits} 處")
        marks += "◡"
    if berms >= BERMS_MIN and berms > pits:
        tags.append(f"土堤 {berms} 段")
        marks += "≡"
    out = {"tags": tags, "marks": marks, "hill": round(hill, 1), "pits": pits, "berms": berms}
    if len(_CACHE) > 400:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def text(cell: str) -> str:
    """「通訊中繼塔（D3）：高地（…）」；沒有標籤回傳空字串。"""
    t = cell_terrain(cell)["tags"]
    return f"{scenario.cell_desc(cell)}：{'、'.join(t)}" if t else ""


def brief(center: str) -> str:
    """隊長簡報的「地形」一行：所在格與相鄰格裡有標籤的；都沒有回傳空字串。"""
    rows = [x for x in (text(c) for c in [center, *scenario.adjacent(center)]) if x]
    return "；".join(rows)


def value(cell: str) -> int:
    """防守價值（敵方作戰層排優先用）：高地 2、散兵坑 1、土堤 1。"""
    t = cell_terrain(cell)
    return 2 * bool(t["marks"].count("▲")) + bool(t["marks"].count("◡")) + bool(t["marks"].count("≡"))
