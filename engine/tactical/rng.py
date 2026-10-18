"""事件綁定亂數：每次擲骰由 (種子, tick, 單位, 動作, 序號, 用途) 決定，與處理順序無關。"""
from __future__ import annotations

import hashlib
import math
import struct


def _u64(*parts) -> int:
    h = hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8).digest()
    return struct.unpack("<Q", h)[0]


def uniform(*parts) -> float:
    return (_u64(*parts) >> 11) / float(1 << 53)


def gauss(*parts) -> float:
    u1 = max(uniform(*parts, "g1"), 1e-12)
    u2 = uniform(*parts, "g2")
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
