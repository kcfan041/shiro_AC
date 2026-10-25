"""作戰格座標（engine/cells.py）與地點名稱（scenario.cell_desc、resolve_place）：
格子 ID 與整數（列, 欄）互換、沒有 26 列 9 欄的限制、5×5 的結果與原本切字串的算法相同、地名為主的稱呼。不需要 LLM。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import cells, scenario
from engine.tactical import worldmap


def test_label_parse_roundtrip():
    """列 A→Z→AA→AB…、欄 1 起算；互換一致，超過 26 列、9 欄也可以。"""
    assert cells.row_label(0) == "A" and cells.row_label(25) == "Z"
    assert cells.row_label(26) == "AA" and cells.row_label(27) == "AB" and cells.row_label(51) == "AZ"
    assert cells.row_label(52) == "BA" and cells.row_label(701) == "ZZ" and cells.row_label(702) == "AAA"
    assert cells.label(2, 2) == "C3" and cells.label(27, 11) == "AB12"
    for r in range(0, 800, 7):
        for c in range(0, 120, 11):
            assert cells.parse(cells.label(r, c)) == (r, c)
    assert cells.parse("c3") == (2, 2), "小寫也認"
    assert cells.parse("D10") == (3, 9), "欄超過 9"


def test_bad_labels():
    for bad in ("", "3C", "C", "C0", "C-1", "中繼塔", "C3x", None):
        assert cells.try_parse(bad) is None, bad
    try:
        cells.parse("Q")
        raise AssertionError("應該丟 ValueError")
    except ValueError:
        pass


def test_offsets_and_distances():
    assert cells.offset("C3", 1, 0) == "D3" and cells.offset("C3", 0, -2) == "C1"
    assert cells.offset("A1", -1, 0) is None
    assert cells.manhattan("A1", "C3") == 4 and cells.chebyshev("A1", "C3") == 2
    assert cells.delta("C3", "E2") == (2, -1), "列差正＝往北、欄差正＝往東"


def _old_rc(cell):
    return "ABCDE".index(cell[0]), "12345".index(cell[1])


def test_5x5_matches_old_string_math():
    """現在的 5×5 地圖：相鄰、距離、全圖座標、區域、進場邊都和原本切字串的算法相同。"""
    assert scenario.N_ROWS == 5 and scenario.N_COLS == 5
    assert scenario.ROWS == list("ABCDE") and scenario.COLS == list("12345")
    all_cells = [r + c for r in "ABCDE" for c in "12345"]
    assert sorted(scenario.CELLS) == sorted(all_cells)
    for a in all_cells:
        ra, ca = _old_rc(a)
        old_adj = []
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if 0 <= ra + dr < 5 and 0 <= ca + dc < 5:
                old_adj.append("ABCDE"[ra + dr] + "12345"[ca + dc])
        assert scenario.adjacent(a) == old_adj, a
        assert worldmap.cell_origin(a) == (ca * 25, ra * 25), a
        assert worldmap.cell_of(ca * 25 + 3, ra * 25 + 24) == a
        assert scenario.in_bounds(a)
        for b in all_cells:
            rb, cb = _old_rc(b)
            assert scenario.distance(a, b) == abs(ra - rb) + abs(ca - cb)
            if a != b:
                dr, dc = ra - rb, ca - cb
                old = ("S" if dr < 0 else "N") if abs(dr) >= abs(dc) else ("W" if dc < 0 else "E")
                assert worldmap.entry_side(a, b) == old, (a, b)
    assert worldmap.cell_of(125, 3) is None and worldmap.cell_of(-1, 3) is None
    assert worldmap.zone_cells("A1") == {"A1", "A2", "B1", "B2"}
    assert len(worldmap.zone_cells("C3")) == 9
    assert not scenario.in_bounds("F1") and not scenario.in_bounds("A6") and not scenario.in_bounds("中繼塔")


def test_cell_desc_name_first():
    """給人與隊長看的地點：地名為主、格子代號備用。"""
    assert scenario.cell_desc("D3") == "通訊中繼塔（D3）"
    assert scenario.cell_desc("Z9") == "Z9", "地圖外照原樣"


def test_resolve_place():
    """隊長或指揮官說的地點：格子代號、地名、部分地名（唯一才算）、混寫都認得。"""
    assert scenario.resolve_place("D3") == "D3" and scenario.resolve_place("d3") == "D3"
    assert scenario.resolve_place("通訊中繼塔") == "D3"
    assert scenario.resolve_place("中繼塔") == "D3", "只說一部分"
    assert scenario.resolve_place("通訊中繼塔（D3）") == "D3"
    assert scenario.resolve_place("醫院廢墟（B4）") == "B4"
    assert scenario.resolve_place("前往南大街") == "B3", "句子裡含完整地名"
    assert scenario.resolve_place("倉庫") == "C5", "河岸倉庫是唯一含「倉庫」的地名"
    assert scenario.resolve_place("北") is None, "一個字太短、不猜"
    assert scenario.resolve_place("區") is None
    assert scenario.resolve_place("火星基地") is None
    assert scenario.resolve_place(None) is None and scenario.resolve_place("") is None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("cells_test OK")


if __name__ == "__main__":
    main()
