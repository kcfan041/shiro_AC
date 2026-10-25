"""挖散兵坑（engine/tactical/timeline.py 挖坑、ai.py dig_plan，docs/new/terrain.md §8）：每 0.2 m 一級改地形、
兩段深度、前緣胸牆、進度就是地形、挖的人比較容易被發現、固守構工的 AI、連續地圖記進地圖變更層。不需要 LLM。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import timeline as tl
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import PINNED, SUPPRESSED, Unit

DT = 0.5


def field(*units):
    m = TacMap(w=20, h=20)
    b = Battle(m, list(units), seed=1)
    b.dt = DT
    return m, b


def run_for(b, secs, plans=None, every=False):
    """推進 secs 秒；every＝每一步都重新指派同樣的計畫（像批次工具不留進行中的動作）。"""
    for i in range(int(round(secs / DT))):
        if plans and (every or i == 0):
            b.assign(plans)
        b.advance()


def test_stages_and_berm():
    """前兩級每級 30 秒（60 秒 0.4 m、胸牆 0.2 m），再三級每級 60 秒（共 240 秒 1.0 m、胸牆 0.4 m）；
    胸牆在面向那一邊；挖到最深動作結束、這格不能再挖。"""
    u = Unit("a", "blue", (5, 5, 0), posture="stand", facing=(0, 1))
    m, b = field(u)
    dig = Plan(mode="dig", face=(9, 5, 0))           # 面向東
    run_for(b, 29.5, {"a": dig})
    assert m.ground.get((5, 5), 0) == 0 and u.posture == "crouch", "蹲著挖，第一級還沒挖好"
    assert b.digging("a")
    run_for(b, 30.5)
    assert m.ground[(5, 5)] == -2 and m.berm.get((5, 5, "E")) == 1, (m.ground, m.berm)
    assert (5, 5, "N") not in m.berm, "胸牆只在面向那一邊"
    run_for(b, 179.5)
    assert m.ground[(5, 5)] == -4 and b.digging("a")
    run_for(b, 0.5)
    assert m.ground[(5, 5)] == -5 and m.berm[(5, 5, "E")] == 2
    assert not b.digging("a") and tl.idle(b, "a") and not tl.can_dig(m, u.pos)
    assert [e["data"]["state"] for e in b.ev if e["type"] == "dig"][-1] == "done"


def test_progress_is_terrain():
    """挖到一半被換掉，坑停在那個深度；換別人接著挖從那裡繼續（不另外記進度）。每一步重新指派同樣的挖坑不會重來。"""
    a = Unit("a", "blue", (5, 5, 0), facing=(0, 1))
    c = Unit("c", "blue", (7, 5, 0), facing=(0, 1))
    m, b = field(a, c)
    run_for(b, 60, {"a": Plan(mode="dig", face=(5, 9, 0))}, every=True)
    assert m.ground[(5, 5)] == -2 and m.berm[(5, 5, "N")] == 1, "每步重新指派：進度延續"
    b.assign({"a": Plan(mode="move", path=[(6, 5, 0)])})
    run_for(b, 5)
    assert a.pos == (6, 5, 0) and m.ground[(5, 5)] == -2, "離開了，坑停在 0.4 m"
    b.assign({"a": Plan(mode="move", path=[(6, 6, 0)]), "c": Plan(mode="move", path=[(6, 5, 0), (5, 5, 0)])})
    run_for(b, 5)
    assert c.pos == (5, 5, 0)
    run_for(b, 59.5, {"c": Plan(mode="dig", face=(5, 9, 0))})
    assert m.ground[(5, 5)] == -2
    run_for(b, 0.5)
    assert m.ground[(5, 5)] == -3, "接著挖：第三級是深坑的 60 秒"


def test_interrupted_level_keeps_progress():
    """交火中被打斷：這一級挖了的秒數記在這一格，換回來接著挖（不從頭算）；換別人來挖也接著算；
    存讀檔帶著；地面高度被別的原因改了就不算。"""
    a = Unit("a", "blue", (5, 5, 0), facing=(0, 1))
    m, b = field(a)
    dig = Plan(mode="dig", face=(5, 9, 0))
    run_for(b, 20, {"a": dig})
    assert m.ground.get((5, 5), 0) == 0
    run_for(b, 10, {"a": Plan(mode="overwatch")})        # 被打斷 10 秒（例如起來射擊）
    assert b.dig_progress == {"5,5": [0, 20.0]}, b.dig_progress
    run_for(b, 9.5, {"a": dig})
    assert m.ground.get((5, 5), 0) == 0
    run_for(b, 0.5)
    assert m.ground[(5, 5)] == -1, "接著挖：再 10 秒就挖好第一級（不是重來 30 秒）"
    assert not b.dig_progress
    # 存讀檔：進度跟著 Battle 存
    run_for(b, 15)                                       # 第二級挖了 15 秒
    run_for(b, 0.5, {"a": Plan(mode="overwatch")})
    assert b.dig_progress == {"5,5": [-1, 15.0]}, b.dig_progress
    b2 = Battle(m, [Unit("a", "blue", (5, 5, 0), facing=(0, 1))], seed=1)
    tl.load(b2, tl.state(b))
    assert b2.dig_progress == b.dig_progress
    # 地面被別的原因改了（地形回復、爆炸）→ 留下的進度不算
    m.set_ground(5, 5, 0)
    have = tl._dig_have(b, (5, 5, 0), tl.DIG_SHALLOW_S)
    assert have == 0.0


def test_where_can_dig():
    """預先挖好的坑從周圍地面算（不會越挖越深）；建築內、有方塊的格子、樓上不能挖。"""
    m = TacMap(w=20, h=20)
    m.set_ground(5, 5, -3)
    m.set_berm(5, 5, "N", 2)
    assert tl.dig_ref(m, 5, 5) == 0 and tl.can_dig(m, (5, 5, 0))
    m.set_ground(5, 5, -5)
    assert not tl.can_dig(m, (5, 5, 0)), "已經是周圍地面下 1.0 m"
    m.low_wall(8, 8, "N")
    assert not tl.can_dig(m, (8, 8, 0)), "格子裡有牆"
    assert not tl.can_dig(m, (3, 3, 1)), "樓上"
    hill = TacMap(w=20, h=20)
    for x in range(20):
        for y in range(20):
            hill.set_ground(x, y, y)           # 斜坡：從這格自己的地面算
    assert tl.dig_ref(hill, 5, 5) == 5


def test_ai_fortify():
    """固守＋構工：沒事做的人就地挖，胸牆面向最近的已知威脅；被壓制、看得到敵人（先射擊）、沒下構工都不挖。"""
    a = Unit("a", "blue", (5, 5, 0), facing=(0, 1))
    m, b = field(a)
    ai = TacticalAI(b, "blue")
    p = ai.plan(Intent("hold", dig=True, dig_face=(15, 5, 0)))["a"]
    assert p.mode == "dig" and p.face == (15, 5, 0), p
    assert ai.plan(Intent("hold"))["a"].mode != "dig", "沒下構工"
    a.suppression = (SUPPRESSED + PINNED) / 2
    assert a.suppressed and ai.plan(Intent("hold", dig=True))["a"].mode != "dig", "被壓制不挖"
    # 有已知威脅（看不到）：胸牆面向它
    r = Unit("r", "red", (5, 16, 0), facing=(0, -1))
    m2 = TacMap(w=20, h=20)
    for x in range(20):
        m2.wall(x, 10)
    a2 = Unit("a", "blue", (5, 5, 0), facing=(1, 0))
    b2 = Battle(m2, [a2, r], seed=1)
    b2.intel["blue"].last_known["r"] = (r.pos, 0)
    p2 = TacticalAI(b2, "blue").plan(Intent("hold", dig=True))["a"]
    assert p2.mode == "dig" and p2.face == r.pos, p2
    # 看得到敵人：射擊優先
    r3 = Unit("r", "red", (5, 12, 0), facing=(0, -1))
    a3 = Unit("a", "blue", (5, 5, 0), facing=(0, 1))
    b3 = Battle(TacMap(w=20, h=20), [a3, r3], seed=1)
    b3.detect("init")
    assert "r" in b3.intel["blue"].spotted
    assert TacticalAI(b3, "blue").plan(Intent("hold", dig=True))["a"].mode == "shoot"


def test_digger_easier_to_detect():
    """挖坑的人偵測比照移動中：同樣的距離、姿勢，挖坑的人在多個種子下被發現的次數明顯比較多。"""
    def spotted(dig: bool) -> int:
        n = 0
        for seed in range(200):
            o = Unit("o", "red", (5, 45, 0), facing=(0, -1))
            t = Unit("t", "blue", (5, 5, 0), facing=(0, 1))
            b = Battle(TacMap(w=12, h=50), [o, t], seed=seed)
            if dig:
                tl.assign(b, {"t": Plan(mode="dig", face=(5, 9, 0))})
            b.detect("x")
            n += "t" in b.intel["red"].spotted
        return n
    idle, dig = spotted(False), spotted(True)
    assert dig > idle * 1.2 and dig - idle >= 10, (idle, dig)


def test_replay_filter():
    """玩家視圖：自己人挖坑的事件會送（事件欄顯示開始、挖好），看不到的敵人挖坑不送（不洩漏位置）。"""
    from engine.replay import filter_events
    evs = [{"seq": 1, "t": 1, "type": "dig", "actor": "s1", "target": None, "data": {"at": [3, 3], "state": "start"}},
           {"seq": 2, "t": 1, "type": "dig", "actor": "e1", "target": None, "data": {"at": [9, 9], "state": "done"}}]
    out = filter_events(evs, {"s1"}, set(), "c")
    assert [e["actor"] for e in out] == ["s1"]
    assert [e["actor"] for e in filter_events(evs, {"s1"}, {"e1"}, "c")] == ["s1", "e1"], "看得到的敵人照送"


def test_zone_records_terrain_edits():
    """連續地圖：挖坑經 Zone 的 terrain_hook 記進 world.terrain（存檔、工作行程同步、前端重烘都從這裡來）。"""
    from engine import scenario, terrain_edits as te
    from engine import zone as zmod
    from engine.tactical import worldmap
    w = scenario.new_world()
    te.bind(w)
    g = worldmap.global_map()
    z = zmod.Zone(w, {"C3"}, seed=1)
    from engine.tactical import planjob
    assert "terrain_hook" not in planjob.battle_snapshot(z.battle), "規劃快照不能深拷貝整個 Zone（會慢到卡住）"
    node = next((x, y, 0) for y in range(50, 75) for x in range(50, 75) if tl.can_dig(g, (x, y, 0)))
    ref = tl.dig_ref(g, *node[:2])
    u = Unit("blue:t:0", "blue", node, facing=(0, 1))
    z.battle.units[u.id] = u
    z.battle.dt = DT
    v0 = te.VERSION
    tl.assign(z.battle, {u.id: Plan(mode="dig", face=(node[0], node[1] + 4, 0))})
    for _ in range(int(60 / DT)):
        z.battle.advance()
    assert g.ground.get(node[:2], 0) == ref - 2 and te.VERSION > v0
    assert w.terrain["ground"][f"{node[0]},{node[1]}"] == ref - 2
    assert w.terrain["berm"][f"{node[0]},{node[1]},N"][0] == 1
    te.reset_all(w)
    assert g.ground.get(node[:2], 0) == g.base_ground.get(node[:2], 0)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("dig_test OK")


if __name__ == "__main__":
    main()
