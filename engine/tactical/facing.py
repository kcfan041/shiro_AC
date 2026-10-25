"""個體的面向（docs/new/facing.md）：0～359 的整數角度（0＝東、90＝北，逆時針），存成那個角度的單位向量。

存向量是為了讓「當方向用」的程式（視野夾角、身體方塊、內積判斷前後）都不用改；向量一律由這張表產生，
同一個角度永遠是同一組數字，所以快取鍵有限、存讀與規劃工作行程之間結果一致。
正東、正北等角度是精確的 (1.0, 0.0)、(0.0, 1.0)，和舊的整數向量 (1, 0)、(0, 1) 相等；舊存檔的整數向量讀進來照樣可以用。
"""
from __future__ import annotations

import math

_TABLE: tuple[tuple[float, float], ...] = tuple(
    (round(math.cos(math.radians(d)), 6) + 0.0, round(math.sin(math.radians(d)), 6) + 0.0) for d in range(360))


def vec(deg: int) -> tuple[float, float]:
    """整數角度 → 面向向量。"""
    return _TABLE[int(deg) % 360]


def deg_of(dx: float, dy: float) -> int:
    """方向 → 最接近的整數角度（0～359）；(0, 0) 當作正北。"""
    if dx == 0 and dy == 0:
        return 90
    return int(round(math.degrees(math.atan2(dy, dx)))) % 360


def toward(dx: float, dy: float) -> tuple[float, float]:
    """朝 (dx, dy) 方向的面向向量（量化到整數角度）。"""
    return vec(deg_of(dx, dy))


def cardinal(f) -> tuple[int, int]:
    """面向最接近的上下左右（東西較明顯取東西，平手取南北；和舊的 TacMap.facing_to 同一個規則）。"""
    fx, fy = f[0], f[1]
    if abs(fx) > abs(fy) + 1e-9:
        return (1 if fx > 0 else -1, 0)
    if fy:
        return (0, 1 if fy > 0 else -1)
    return (0, 1)


_OCT: dict = {}


def octant(f) -> tuple[float, float]:
    """面向最接近的 45° 方向（8 個）：身體方塊用（彈道命中、可見部位），面向、視野、前後判斷仍用精確角度。
    身體方塊與視線結果都依它快取，量化到 8 個方向讓快取命中率維持在細分前的水準（原本移動時就是 8 方向）。"""
    k = (f[0], f[1])
    v = _OCT.get(k)
    if v is None:
        v = _OCT[k] = vec((int(round(deg_of(f[0], f[1]) / 45.0)) * 45) % 360)
    return v


def normalize(f) -> tuple[float, float]:
    """任何方向（舊存檔的整數向量、清單）→ 表裡的面向向量。"""
    return toward(f[0], f[1])
