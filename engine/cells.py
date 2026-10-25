"""作戰格的座標與名稱。

格子 ID 是「列字母＋欄數字」字串（A1、C3、AB12…），存檔、網址、LLM 都用這個字串；
計算一律先換成整數（列, 欄）：列 A→Z→AA→AB… 由南到北（0 起算），欄 1、2、3… 由西到東（0 起算）。
沒有 26 列、9 欄的限制；所有拆字串、相鄰、距離都在這裡，其他地方不要自己切字串。
"""
from __future__ import annotations

from functools import lru_cache


def row_label(r: int) -> str:
    """列序號（0 起算）→ 字母：0→A、25→Z、26→AA、27→AB…"""
    if r < 0:
        raise ValueError(f"列序號不能是負的：{r}")
    out = ""
    n = r + 1
    while n:
        n, k = divmod(n - 1, 26)
        out = chr(ord("A") + k) + out
    return out


def label(r: int, c: int) -> str:
    """（列, 欄）→ 格子 ID。"""
    if c < 0:
        raise ValueError(f"欄序號不能是負的：{c}")
    return f"{row_label(r)}{c + 1}"


@lru_cache(maxsize=None)
def parse(cell: str) -> tuple[int, int]:
    """格子 ID → （列, 欄）。不合格式丟 ValueError。"""
    s = str(cell).strip().upper()
    i = 0
    while i < len(s) and "A" <= s[i] <= "Z":
        i += 1
    letters, digits = s[:i], s[i:]
    if not letters or not digits.isdigit() or int(digits) < 1:
        raise ValueError(f"不是格子代號：{cell!r}")
    r = 0
    for ch in letters:
        r = r * 26 + (ord(ch) - ord("A") + 1)
    return r - 1, int(digits) - 1


def try_parse(cell) -> tuple[int, int] | None:
    try:
        return parse(cell)
    except (ValueError, TypeError):
        return None


def row(cell: str) -> int:
    return parse(cell)[0]


def col(cell: str) -> int:
    return parse(cell)[1]


def offset(cell: str, dr: int, dc: int) -> str | None:
    """往北 dr 列、往東 dc 欄的格子 ID；超出座標範圍（負的）回傳 None（地圖邊界由呼叫端判斷）。"""
    r, c = parse(cell)
    if r + dr < 0 or c + dc < 0:
        return None
    return label(r + dr, c + dc)


def manhattan(a: str, b: str) -> int:
    (ra, ca), (rb, cb) = parse(a), parse(b)
    return abs(ra - rb) + abs(ca - cb)


def chebyshev(a: str, b: str) -> int:
    (ra, ca), (rb, cb) = parse(a), parse(b)
    return max(abs(ra - rb), abs(ca - cb))


def delta(frm: str, to: str) -> tuple[int, int]:
    """從 frm 到 to 的（列差, 欄差）：列差正＝往北、欄差正＝往東。"""
    (ra, ca), (rb, cb) = parse(frm), parse(to)
    return rb - ra, cb - ca
