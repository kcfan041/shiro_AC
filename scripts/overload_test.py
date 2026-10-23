"""過載處理測試（模擬重構第 10 步）：負載高就降級非玩家 AI、時間倍率、降級時的規劃間隔。不需要 LLM。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engagement import PLAN_GAP_S, PLAN_MAX_S, Engagement
from engine.overload import LOD_MAX, WINDOW, Overload
from engine.tactical import timeline as tl
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def test_lod_rises_and_falls_with_hysteresis():
    """近 10 拍平均負載 > 0.8 升一級、< 0.4 降一級；每次調整後隔 10 拍才再調；最多 2 級。"""
    o = Overload()
    levels = [o.record(0.45, 0.5) for _ in range(WINDOW * 5)]
    assert levels[WINDOW - 2] == 0 and levels[WINDOW - 1] == 1, levels[:WINDOW + 1]
    assert levels[2 * WINDOW - 1] == 1 and levels[2 * WINDOW] == 2, "調整後隔 10 拍才再調"
    assert max(levels) == LOD_MAX
    down = [o.record(0.1, 0.5) for _ in range(WINDOW * 4)]
    assert down[-1] == 0 and 1 in down, down
    o2 = Overload()
    assert all(o2.record(0.3, 0.5) == 0 for _ in range(50)), "負載 0.6 不降級"


def test_time_scale_only_shown_when_slow():
    """時間倍率＝遊戲秒數 ÷ 實際秒數（近 20 拍）；0.95 以上不顯示。"""
    o = Overload()
    for _ in range(20):
        o.record(0.2, 0.5, wall_secs=0.5)
    assert o.view()["time_scale"] == 1.0
    for _ in range(20):
        o.record(0.6, 0.5, wall_secs=0.625)
    assert abs(o.time_scale - 0.8) < 1e-6 and o.view()["time_scale"] == 0.8


def battle():
    m = TacMap(w=60, h=10)
    us = [Unit("p", "blue", (2, 3, 0)), Unit("r", "red", (10, 3, 0)), Unit("far", "red", (55, 3, 0))]
    b = Battle(m, us, seed=1)
    b.dt = 0.5
    b.intel["red"].last_known["p"] = ((2, 3, 0), 0)     # r 在 60 m 內知道 p → 接敵；far 在 60 m 外
    return b


def test_degraded_planning_gaps():
    """降級時的規劃間隔：玩家小隊永遠 1 秒；第 1 級非玩家未接敵 2.5 秒、接敵仍 1 秒；第 2 級非玩家一律 2.5 秒。"""
    class Fake:
        _due = Engagement._due
        _min_gap = Engagement._min_gap
        _sig = Engagement._sig

    b = battle()
    f = Fake()
    f.battle = b
    p, r, far = b.units["p"], b.units["r"], b.units["far"]
    want = {0: (PLAN_GAP_S, PLAN_GAP_S, PLAN_GAP_S), 1: (PLAN_GAP_S, PLAN_GAP_S, PLAN_MAX_S),
            2: (PLAN_GAP_S, PLAN_MAX_S, PLAN_MAX_S)}
    for lod, (gp, gr, gf) in want.items():
        f.lod = lod
        assert (f._min_gap([p], True), f._min_gap([r], False), f._min_gap([far], False)) == (gp, gr, gf), lod
    # 實際節奏：第 2 級時，閒著的敵軍要等 2.5 秒；玩家小隊 1 秒就重新規劃；新命令不受影響
    f.lod = 2
    f.plan_t, f.plan_sig = {"g": 0.0, "pl": 0.0}, {"g": f._sig([r]), "pl": f._sig([p])}
    while b.now < 1.0 - 1e-9:
        b.advance()
    assert tl.idle(b, "r") and tl.idle(b, "p")
    assert f._due("pl", [p], False, True), "玩家小隊 1 秒"
    assert not f._due("g", [r], False, False), "降級的敵軍還不到 2.5 秒"
    assert f._due("g", [r], True, False), "新命令立刻"
    while b.now < 2.5 - 1e-9:
        b.advance()
    assert f._due("g", [r], False, False)
    _ = Plan


def test_state_carries_time_scale():
    """指揮官畫面帶時間倍率與降級等級（變慢時頂部顯示）。"""
    import asyncio
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from engine import views
    from zone_test import setup, done

    async def go():
        sim, a, _ = await setup()
        sim.overload_view = {"time_scale": 0.8, "lod": 2}
        st = views.commander_state(sim, "100")
        assert st["time_scale"] == 0.8 and st["lod"] == 2
        done()
    asyncio.run(go())


def main():
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("overload_test OK")


if __name__ == "__main__":
    main()
