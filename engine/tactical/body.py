"""人體：6 個採樣點（可見性）與簡化碰撞體（受擊）。"""
from __future__ import annotations

from engine.tactical.geometry import Box, Vec

PARTS = ("head", "torso", "larm", "rarm", "lleg", "rleg")

POSTURE = {
    "stand": {"height": 1.8, "eye": 1.6},
    "crouch": {"height": 1.15, "eye": 1.0},
    "prone": {"height": 0.35, "eye": 0.25},
}


def _facing_axes(facing: tuple[int, int]) -> tuple[tuple[float, float], tuple[float, float]]:
    fx, fy = facing
    n = (fx * fx + fy * fy) ** 0.5 or 1.0
    fwd = (fx / n, fy / n)
    side = (-fwd[1], fwd[0])
    return fwd, side


def eye(base: Vec, posture: str) -> Vec:
    return (base[0], base[1], base[2] + POSTURE[posture]["eye"])


def _upright_layout(posture: str) -> dict[str, tuple[float, float, float, float]]:
    """(側向偏移, 高度中心, 半寬（側向）, 半高)。前後半深固定。"""
    if posture == "stand":
        return {
            "head": (0.0, 1.68, 0.11, 0.11),
            "torso": (0.0, 1.25, 0.21, 0.32),
            "larm": (-0.3, 1.2, 0.07, 0.3),
            "rarm": (0.3, 1.2, 0.07, 0.3),
            "lleg": (-0.11, 0.46, 0.09, 0.46),
            "rleg": (0.11, 0.46, 0.09, 0.46),
        }
    return {
        "head": (0.0, 1.04, 0.11, 0.11),
        "torso": (0.0, 0.72, 0.21, 0.22),
        "larm": (-0.28, 0.7, 0.07, 0.22),
        "rarm": (0.28, 0.7, 0.07, 0.22),
        "lleg": (-0.13, 0.3, 0.1, 0.3),
        "rleg": (0.13, 0.3, 0.1, 0.3),
    }


def _prone_layout() -> dict[str, tuple[float, float, float, float, float]]:
    """(前後偏移, 側向偏移, 前後半長, 側向半寬, 半高)，高度中心固定 0.15。"""
    return {
        "head": (0.75, 0.0, 0.11, 0.11, 0.11),
        "torso": (0.25, 0.0, 0.32, 0.21, 0.13),
        "larm": (0.5, -0.3, 0.3, 0.07, 0.07),
        "rarm": (0.5, 0.3, 0.3, 0.07, 0.07),
        "lleg": (-0.45, -0.12, 0.42, 0.09, 0.09),
        "rleg": (-0.45, 0.12, 0.42, 0.09, 0.09),
    }


def parts(base: Vec, posture: str, facing: tuple[int, int]) -> dict[str, Box]:
    fwd, side = _facing_axes(facing)
    out = {}
    if posture == "prone":
        for name, (f_off, s_off, f_half, s_half, z_half) in _prone_layout().items():
            cx = base[0] + fwd[0] * f_off + side[0] * s_off
            cy = base[1] + fwd[1] * f_off + side[1] * s_off
            cz = base[2] + 0.15
            hx = abs(fwd[0]) * f_half + abs(side[0]) * s_half
            hy = abs(fwd[1]) * f_half + abs(side[1]) * s_half
            out[name] = Box((cx - hx, cy - hy, cz - z_half), (cx + hx, cy + hy, cz + z_half), "body", name)
        return out
    depth = 0.12
    for name, (s_off, zc, s_half, z_half) in _upright_layout(posture).items():
        cx = base[0] + side[0] * s_off
        cy = base[1] + side[1] * s_off
        hx = abs(side[0]) * s_half + abs(fwd[0]) * depth
        hy = abs(side[1]) * s_half + abs(fwd[1]) * depth
        z = base[2] + zc
        out[name] = Box((cx - hx, cy - hy, z - z_half), (cx + hx, cy + hy, z + z_half), "body", name)
    return out


_PARTS: dict = {}
_SAMPLES: dict = {}


def parts_cached(base: Vec, posture: str, facing: tuple[int, int]) -> dict[str, Box]:
    key = (base, posture, facing)
    v = _PARTS.get(key)
    if v is None:
        if len(_PARTS) > 100000:
            _PARTS.clear()
        v = _PARTS[key] = parts(base, posture, facing)
    return v


def sample_points(base: Vec, posture: str, facing: tuple[int, int]) -> dict[str, Vec]:
    key = (base, posture, facing)
    v = _SAMPLES.get(key)
    if v is None:
        if len(_SAMPLES) > 100000:
            _SAMPLES.clear()
        v = _SAMPLES[key] = {k: ((b.lo[0] + b.hi[0]) / 2, (b.lo[1] + b.hi[1]) / 2, (b.lo[2] + b.hi[2]) / 2)
                             for k, b in parts_cached(base, posture, facing).items()}
    return v


def torso_center(base: Vec, posture: str, facing: tuple[int, int]) -> Vec:
    return sample_points(base, posture, facing)["torso"]
