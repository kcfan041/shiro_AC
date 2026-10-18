"""純幾何：向量、射線與軸對齊方塊（AABB）求交。"""
from __future__ import annotations

import math
from dataclasses import dataclass

Vec = tuple[float, float, float]
EPS = 1e-9


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def norm(a: Vec) -> Vec:
    n = length(a)
    return (0.0, 0.0, 1.0) if n < EPS else mul(a, 1.0 / n)


@dataclass(frozen=True)
class Box:
    lo: Vec
    hi: Vec
    material: str = "solid"
    tag: str = ""

    def contains_xy(self, x: float, y: float) -> bool:
        return self.lo[0] <= x <= self.hi[0] and self.lo[1] <= y <= self.hi[1]


def ray_box(origin: Vec, direction: Vec, box: Box, t_max: float) -> tuple[float, float] | None:
    """slab 法。回傳 (t_enter, t_exit)；direction 不需單位化，t 以 direction 長度為單位。"""
    t0, t1 = 0.0, t_max
    for i in range(3):
        o, d = origin[i], direction[i]
        lo, hi = box.lo[i], box.hi[i]
        if abs(d) < EPS:
            if o < lo or o > hi:
                return None
            continue
        a, b = (lo - o) / d, (hi - o) / d
        if a > b:
            a, b = b, a
        t0 = max(t0, a)
        t1 = min(t1, b)
        if t0 > t1:
            return None
    return t0, t1


def closest_point_on_segment(a: Vec, b: Vec, p: Vec) -> tuple[Vec, float]:
    ab = sub(b, a)
    denom = dot(ab, ab)
    t = 0.0 if denom < EPS else max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
    return add(a, mul(ab, t)), t


def perpendiculars(d: Vec) -> tuple[Vec, Vec]:
    ref = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = norm(cross(d, ref))
    v = norm(cross(d, u))
    return u, v
