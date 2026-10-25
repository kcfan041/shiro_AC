"""面向細分（docs/new/facing.md、engine/tactical/facing.py）：面向是 0～359 的整數角度、存成表裡的單位向量。
不需要 LLM。"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import facing, timeline as tl
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def test_table_and_rounding():
    """表：正東正北是精確值、和舊的整數向量相等；角度四捨五入到整數、同一個角度永遠同一組數字。"""
    assert facing.vec(0) == (1.0, 0.0) and facing.vec(90) == (0.0, 1.0)
    assert facing.vec(180) == (-1.0, 0.0) and facing.vec(270) == (0.0, -1.0)
    assert facing.vec(90) == (0, 1) and hash(facing.vec(90)) == hash((0, 1)), "和舊的整數向量相等，可當同一個快取鍵"
    assert abs(facing.vec(45)[0] - math.sqrt(0.5)) < 1e-6
    assert facing.deg_of(3, 1) == 18 and facing.deg_of(-1, -1) == 225 and facing.deg_of(0, 0) == 90
    assert facing.vec(360) == facing.vec(0) and facing.vec(-90) == facing.vec(270)
    for d in range(360):
        v = facing.vec(d)
        assert abs(math.hypot(*v) - 1) < 1e-5
        assert facing.deg_of(*v) == d, "表裡的向量換回同一個角度"
        assert facing.normalize(v) == v


def test_cardinal_matches_old_rule():
    """最接近的上下左右：和舊的 TacMap.facing_to 同一個規則（東西較明顯取東西，平手取南北）。"""
    def old(dx, dy):
        if abs(dx) > abs(dy):
            return (1 if dx > 0 else -1, 0)
        if dy:
            return (0, 1 if dy > 0 else -1)
        return (0, 1)
    for dx in range(-5, 6):
        for dy in range(-5, 6):
            assert facing.cardinal(facing.toward(dx, dy)) == old(dx, dy) or (dx, dy) == (0, 0), (dx, dy)


def test_facing_to_is_exact():
    """面向目標不再吸到東南西北：(0,0) 看 (2,1) 是 27°。"""
    f = TacMap.facing_to((0, 0, 0), (2, 1, 0))
    assert facing.deg_of(*f) == 27, f
    assert TacMap.facing_to((5, 5, 0), (5, 5, 0)) == (0.0, 1.0), "同一格當作正北"


def test_fov_uses_true_direction():
    """視野：面向 39° 方向的威脅時，95° 方向的人在正面視野（±60°）內；舊的做法面向被吸到正東，他會被算成側面（0.5）。"""
    m = TacMap(w=30, h=30)
    o = Unit("o", "blue", (5, 5, 0))
    threat = Unit("t", "red", (15, 13, 0))          # 約 39°
    side = Unit("s", "red", (4, 16, 0))             # 約 95°
    bt = Battle(m, [o, threat, side], seed=0)
    o.facing = m.facing_to(o.pos, threat.pos)
    assert bt.fov_factor(o, side) == 1.0, bt.fov_factor(o, side)
    o.facing = (1, 0)                               # 舊的吸附結果
    assert bt.fov_factor(o, side) == 0.5


def test_old_saves_and_roundtrip():
    """舊存檔的整數向量讀進來換成表裡的向量；存讀來回不變。"""
    u = Unit("u", "blue", (1, 1, 0), facing=(1, 1))
    assert u.facing == facing.vec(45)
    back = Unit.from_dict(u.to_dict())
    assert back.facing == u.facing
    assert Unit("w", "blue", (1, 1, 0), facing=[0, -1]).facing == (0.0, -1.0)


def test_hug_uses_nearest_side():
    """貼掩體看面向最接近的上下左右：北邊有矮牆時，面向 60° 會貼北牆、面向 30° 不貼（最接近的是東邊）。"""
    m = TacMap(w=10, h=10)
    m.low_wall(5, 5, "N")
    c = m.center((5, 5, 0))
    assert m.hug_base((5, 5, 0), facing.vec(60))[1] > c[1]
    assert m.hug_base((5, 5, 0), facing.vec(30)) == c
    assert m.hug_base((5, 5, 0), facing.vec(90)) == m.hug_base((5, 5, 0), (0, 1)), "快取鍵用上下左右"


def test_body_turns_with_facing():
    """身體方塊跟著實際角度轉：面向 30° 和 0° 的軀幹採樣點不同。"""
    m = TacMap(w=10, h=10)
    a = Unit("a", "blue", (5, 5, 0), posture="stand", facing=facing.vec(0))
    b = Unit("b", "blue", (5, 5, 0), posture="stand", facing=facing.vec(30))
    assert a.samples(m)["larm"] != b.samples(m)["larm"]


def test_move_facing_diagonal():
    """移動時面向這一步的方向：斜走是 45°（舊的是整數向量 (1, 1)）。"""
    m = TacMap(w=10, h=10)
    u = Unit("u", "blue", (2, 2, 0))
    bt = Battle(m, [u], seed=0)
    tl.set_move_facing(bt, u, Plan(mode="move", path=[(3, 3, 0)]), (3, 3, 0))
    assert u.facing == facing.vec(45)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("facing_test OK")


if __name__ == "__main__":
    main()
