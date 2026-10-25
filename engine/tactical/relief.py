"""自然地形（docs/new/terrain.md §7）：每格戰術地圖的地面高度與邊緣土堤。

三層疊起來：
1. 全圖底層起伏：用全圖座標算的平緩雜訊，作戰格交界自然連續。
2. 模板特徵：依地圖模板加路緣、瓦礫丘、土丘、田埂與溝渠、彈坑、卸貨平台；離作戰格邊緣 3 格內不放，交界不會突然一階。
3. 手放重點格：據點、中繼塔、敵方據點（在 mapgen 裡呼叫）。
建築的範圍在蓋之前先整平（flatten）。

用自己的亂數（和放建築、瓦礫的亂數分開），所以加了地形之後每格的建築、瓦礫、矮牆位置都不變。
相鄰兩格的高差盡量 ≤ 0.4 m（正常走得過），只有刻意的地形（卸貨平台邊、散兵坑）才要翻越；不做走不過去的斷崖。
關掉：環境變數 SHIRO_RELIEF=0（A/B 對照用，地圖全平、結果與加入地形前相同）。
"""
from __future__ import annotations

import math
import os
import random
import zlib

from engine.tactical.tmap import H_STEP, TacMap

ENABLED = os.environ.get("SHIRO_RELIEF", "1") != "0"
BASE_AMP = 0.8           # 全圖底層起伏的幅度（m，大約 ±）
BASE_WAVE = (26.0, 11.0)  # 兩層雜訊的波長（小格）
BASE_MIX = (1.0, 0.3)
EDGE_KEEP = 3            # 模板特徵離作戰格邊緣至少這麼多格


def units(h: float) -> int:
    """公尺 → 地面高度的整數單位（H_STEP）。"""
    return int(round(h / H_STEP))


def _lattice(seed: int, ix: int, iy: int) -> float:
    h = zlib.crc32(f"{seed}:{ix}:{iy}".encode())
    return (h & 0xFFFF) / 0x7FFF - 1.0


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def noise(seed: int, x: float, y: float, wave: float) -> float:
    """值雜訊（-1～1），只看全圖座標，同一點每次一樣。"""
    gx, gy = x / wave, y / wave
    ix, iy = math.floor(gx), math.floor(gy)
    fx, fy = _smooth(gx - ix), _smooth(gy - iy)
    a, b = _lattice(seed, ix, iy), _lattice(seed, ix + 1, iy)
    c, d = _lattice(seed, ix, iy + 1), _lattice(seed, ix + 1, iy + 1)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def base_height(seed: int, gx: float, gy: float) -> float:
    """全圖底層起伏（m）。"""
    total = sum(k * noise(seed + i, gx, gy, w) for i, (w, k) in enumerate(zip(BASE_WAVE, BASE_MIX)))
    return BASE_AMP * total / sum(BASE_MIX)


def apply_base(m: TacMap, seed: int, ox: int, oy: int):
    """整張格子地圖套上全圖底層起伏（ox、oy＝這格在全圖的左下角）。"""
    for y in range(m.h):
        for x in range(m.w):
            u = units(base_height(seed, ox + x + 0.5, oy + y + 0.5))
            if u:
                m.ground[(x, y)] = u                # 直接寫（一次清快取就好）
            else:
                m.ground.pop((x, y), None)
    m._cache.clear()


def add(m: TacMap, x: int, y: int, dh: float):
    if m.in_bounds(x, y):
        m.set_ground(x, y, m.ground.get((x, y), 0) + units(dh))


def mound(m: TacMap, cx: float, cy: float, radius: float, height: float):
    """圓丘（height 負的就是窪地）：邊緣平緩，坡度控制在一格 0.4 m 內（呼叫端給的半徑要夠大）。"""
    r = int(math.ceil(radius))
    for y in range(int(cy) - r, int(cy) + r + 1):
        for x in range(int(cx) - r, int(cx) + r + 1):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy) / radius
            if d < 1.0:
                add(m, x, y, height * (1 - _smooth(d)))


def flatten(m: TacMap, x0: int, y0: int, x1: int, y1: int):
    """把矩形（含邊）整平成平均高度（蓋建築前用，樓板與牆從同一個高度算起）。"""
    pts = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) if m.in_bounds(x, y)]
    if not pts:
        return
    avg = round(sum(m.ground.get(p, 0) for p in pts) / len(pts))
    for x, y in pts:
        m.set_ground(x, y, avg)


def _interior(rng: random.Random, margin: int, size: int) -> tuple[int, int]:
    return rng.randint(margin, size - 1 - margin), rng.randint(margin, size - 1 - margin)


def features(m: TacMap, template: str, rng: random.Random):
    """依地圖模板加地形特徵（在放建築之前；建築的範圍之後會整平）。"""
    size, keep = m.w, EDGE_KEEP
    if template in ("street", "crossroad"):
        for y in range(keep, size - keep):          # 人行道比車道高一階（路緣）
            for x in range(keep, size - keep):
                road = abs(x - 12) <= 2 or (template == "crossroad" and abs(y - 12) <= 2)
                if not road:
                    add(m, x, y, H_STEP)
    elif template == "rubble_field":
        for _ in range(rng.randint(2, 3)):         # 瓦礫丘（半徑夠大，坡度一格約 0.3 m 內）
            cx, cy = _interior(rng, keep + 2, size)
            mound(m, cx + 0.5, cy + 0.5, rng.uniform(4.0, 5.0), rng.uniform(0.8, 1.2))
    elif template == "park":
        for _ in range(rng.randint(1, 2)):         # 土丘
            cx, cy = _interior(rng, keep + 3, size)
            mound(m, cx + 0.5, cy + 0.5, rng.uniform(4.5, 5.5), rng.uniform(0.8, 1.2))
    elif template == "field":
        rows = rng.sample(range(keep + 1, size - keep - 1), 2)
        for ry in rows:                             # 田埂（土堤 0.4 m，中間留缺口）
            gap = rng.randint(keep + 2, size - keep - 3)
            for x in range(keep, size - keep):
                if abs(x - gap) > 1:
                    m.set_berm(x, ry, "N", units(0.4))
        dx = rng.randint(keep + 2, size - keep - 3)
        for y in range(keep, size - keep):          # 溝渠（−0.4 m）
            add(m, dx, y, -0.4)
    elif template in ("ruins", "houses", "building", "tower"):
        for _ in range(rng.randint(1, 3)):         # 彈坑
            cx, cy = _interior(rng, keep + 1, size)
            mound(m, cx + 0.5, cy + 0.5, rng.uniform(1.5, 2.2), -rng.uniform(0.4, 0.6))


RAMP = (0.8, 0.4, 0.0)   # 卸貨平台一端的坡道：離平台 1、2、3 格的高度（m，相對平台底）


def _empty(m: TacMap, x: int, y: int) -> bool:
    """在作戰格內側、格子裡沒有任何方塊（牆、瓦礫、植被…）。"""
    return (EDGE_KEEP <= x < m.w - EDGE_KEEP and EDGE_KEEP <= y < m.h - EDGE_KEEP
            and not m._boxes_in_cell(x, y))


def loading_dock(m: TacMap, rect: tuple[int, int, int, int], rng: random.Random) -> bool:
    """倉庫、工廠的大建築旁的卸貨平台：沿一邊外側 2 格寬、高 1.2 m，一端做成三階坡道（0.8、0.4、0）。
    在放完所有東西之後才做，只用空格；放不下就不做。"""
    x0, y0, x1, y1 = rect
    for side in rng.sample("NSEW", 4):
        if side in "NS":
            lines = [y1 + 1, y1 + 2] if side == "N" else [y0 - 2, y0 - 1]
            strip = [(x, y) for x in range(x0, x1 + 1) for y in lines]
            ramp = [[(x0 - k, y) for y in lines] for k in (1, 2, 3)]
        else:
            lines = [x1 + 1, x1 + 2] if side == "E" else [x0 - 2, x0 - 1]
            strip = [(x, y) for y in range(y0, y1 + 1) for x in lines]
            ramp = [[(x, y0 - k) for x in lines] for k in (1, 2, 3)]
        if not all(_empty(m, x, y) for x, y in strip + [p for row in ramp for p in row]):
            continue
        base = min(m.ground.get(p, 0) for p in strip)
        for x, y in strip:
            m.set_ground(x, y, base + units(1.2))
        for row, h in zip(ramp, RAMP):
            for x, y in row:
                m.set_ground(x, y, base + units(h))
        return True
    return False


def hill(m: TacMap, height: float):
    """整格是一座緩丘（中繼塔）：中央最高，往四邊降到 0。"""
    c = (m.w - 1) / 2
    for y in range(m.h):
        for x in range(m.w):
            d = max(abs(x - c), abs(y - c)) / (c + 0.5)
            add(m, x, y, height * (1 - _smooth(min(1.0, d))))


def foxholes(m: TacMap, cells: list[tuple[int, int]], face: str) -> list[tuple[int, int]]:
    """預挖的散兵坑：下挖 0.6 m，面向 face 那一邊堆 0.4 m 胸牆；只挖空格，回傳挖了哪些。"""
    dug = []
    for x, y in cells:
        if not _empty(m, x, y):
            continue
        add(m, x, y, -0.6)
        m.set_berm(x, y, face, units(0.4))
        dug.append((x, y))
    return dug


def seed_of(scenario_id: str) -> int:
    return zlib.crc32(f"{scenario_id}:relief".encode()) & 0x7FFFFFFF
