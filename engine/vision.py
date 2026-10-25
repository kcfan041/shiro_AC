"""我方視野多邊形（只給畫面用：戰術圖的視野遮罩，方案 §6、§7.4）。

從個體的眼睛（照姿勢的眼高）往四周水平射出 RAYS 條線，碰到實心方塊（牆、窗台、矮牆、瓦礫；植被不擋）或地形（高地、土堤）就停，
最遠 DETECT_CUTOFF（偵測不再計算的距離）；煙霧照偵測的算法累積厚度，光學可見程度降到一半就停。
地形是靜態的，所以「同一格、同一個眼高」的結果快取起來，只有煙霧每次重算。
不碰任何戰鬥狀態與亂數：結果只是把偵測已經在用的視線換成一個形狀給玩家看。
"""
from __future__ import annotations

import math

from engine.tactical.sim import DETECT_CUTOFF, SMOKE_FULL

RAYS = 48
_CACHE: dict = {}
_CACHE_MAX = 20000


def _base(m, node, eye) -> list[tuple[float, float, float]]:
    """沒有煙霧時每條射線的（方向 x、方向 y、長度），單位 m。"""
    key = (id(m), tuple(node[:3]) if len(node) > 2 else tuple(node), round(eye[2], 2))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    W, H = m.w * m.cell, m.h * m.cell
    out = []
    for i in range(RAYS):
        a = 2 * math.pi * i / RAYS
        dx, dy = math.cos(a), math.sin(a)
        t_edge = DETECT_CUTOFF
        if dx > 1e-9:
            t_edge = min(t_edge, (W - eye[0]) / dx)
        elif dx < -1e-9:
            t_edge = min(t_edge, -eye[0] / dx)
        if dy > 1e-9:
            t_edge = min(t_edge, (H - eye[1]) / dy)
        elif dy < -1e-9:
            t_edge = min(t_edge, -eye[1] / dy)
        r = m.raycast(eye, (dx, dy, 0.0), max(0.0, t_edge))
        t = min(t_edge, r[0]) if r else max(0.0, t_edge)
        tg = m.terrain_t(eye, (dx, dy, 0.0), t)               # 高地、土堤擋住視線（全平的地圖是 None）
        out.append((dx, dy, tg if tg is not None else t))
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def _smoke_clip(b, eye, dx: float, dy: float, t: float) -> float:
    """沿射線累積煙霧厚度（和偵測一樣用圓柱近似），光學可見程度降到一半（厚度 SMOKE_FULL / 2）就停。"""
    hits = []
    for s in b.active_smokes():
        if eye[2] > s["top"]:
            continue
        fx, fy = eye[0] - s["x"], eye[1] - s["y"]
        bq = fx * dx + fy * dy
        c = fx * fx + fy * fy - s["r"] * s["r"]
        disc = bq * bq - c
        if disc <= 0:
            continue
        r = math.sqrt(disc)
        t0, t1 = max(0.0, -bq - r), min(t, -bq + r)
        if t1 > t0:
            hits.append((t0, t1, b.smoke_density(s)))
    if not hits:
        return t
    need = SMOKE_FULL / 2
    for t0, t1, dens in sorted(hits):
        if dens <= 0:
            continue
        span = (t1 - t0) * dens
        if span >= need:
            return t0 + need / dens
        need -= span
    return t


def polygon(b, u) -> list[list[float]]:
    """個體 u 的視野多邊形（全圖小格座標，四捨五入到 0.1 格）。"""
    m = b.map
    eye = u.eye(m)
    pts = []
    for dx, dy, t in _base(m, u.pos, eye):
        if b.smokes:
            t = _smoke_clip(b, eye, dx, dy, t)
        pts.append([round((eye[0] + dx * t) / m.cell, 1), round((eye[1] + dy * t) / m.cell, 1)])
    return pts
