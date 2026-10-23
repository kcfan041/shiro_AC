"""時間線測試（模擬重構第 8 步）：每人一個進行中的動作、事件依時間結算、0.5 秒執行步、規劃節奏。不需要 LLM。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import timeline as tl
from engine.tactical.sim import DOWN_SECONDS, TICK_S, Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import WEAPONS, Unit


def duel(dist=3, dt=0.5, seed=0, **kw):
    m = TacMap()
    a = Unit("a", "blue", (4, 2, 0), posture="stand", facing=(0, 1))
    b = Unit("b", "red", (4, 2 + dist, 0), posture="stand", facing=(0, -1), **kw)
    bt = Battle(m, [a, b], seed=seed)
    bt.dt = dt
    bt.detect("init")
    bt.intel["blue"].spotted["b"] = 0
    bt.intel["red"].spotted["a"] = 0
    return bt


def shot_times(bt, uid):
    return [e["ts"] for e in bt.ev if e["type"] == "shot" and e["actor"] == uid]


def run_until(bt, secs):
    while bt.now < secs - 1e-9:
        bt.advance()


def test_aim_time_depends_on_distance():
    """點放的開火時間＝瞄準時間（距離越遠越久），之後要回穩才算動作結束。"""
    out = []
    jitter, tl.REACT_JITTER = tl.REACT_JITTER, 0.0        # 比較瞄準時間：先拿掉反應差
    for dist in (3, 15):
        bt = duel(dist)
        bt.assign({"a": Plan(mode="shoot", target="b", fire="aimed")})
        aim = tl.aim_time(bt, bt.units["a"], Plan(mode="shoot", target="b", fire="aimed"))
        run_until(bt, 2.5)
        ts = shot_times(bt, "a")
        assert ts and aim - 0.05 <= ts[0] <= aim + tl.REACT_JITTER + 0.05, (dist, ts, aim)
        assert len(ts) == 1, "一個點放動作只開一槍"
        out.append(ts[0])
    tl.REACT_JITTER = jitter
    assert out[1] > out[0], ("遠的目標瞄比較久", out)


def test_suppress_two_volleys():
    """壓制分兩波（0.4、1.4 秒）打出，合計發數與原本一步相同。"""
    bt = duel(10)
    bt.assign({"a": Plan(mode="shoot", target="b", fire="suppress")})
    run_until(bt, 2.5)
    ev = [e for e in bt.ev if e["type"] == "shot" and e["actor"] == "a"]
    ts = [e["ts"] for e in ev]
    assert len(ts) == 2 and abs(ts[1] - ts[0] - 1.0) < 1e-6 and 0.4 <= ts[0] <= 0.4 + tl.REACT_JITTER + 1e-6, ts
    assert sum(e["data"]["rounds"] for e in ev) == WEAPONS["rifle"]["supp_rounds"]


def test_same_instant_is_simultaneous():
    """同一時刻的射擊一起套用：即使先處理的人把對方打倒，對方這一槍照樣打出（換順序結果相同）。"""
    jitter, tl.REACT_JITTER = tl.REACT_JITTER, 0.0        # 反應差拿掉，兩人才會同一瞬間開火
    for seed in range(30):
        bt = duel(3, seed=seed)
        p = Plan(mode="shoot", target="b", fire="burst")
        q = Plan(mode="shoot", target="a", fire="burst")
        bt.assign({"a": p, "b": q})
        assert bt.queue[0][0] == bt.queue[1][0], "同距離、同武器 → 同一時刻開火"
        run_until(bt, 2.5)
        assert shot_times(bt, "a") and shot_times(bt, "b"), seed
    tl.REACT_JITTER = jitter


def test_reaction_jitter_spreads_shooters():
    """同一步規劃的壓制射手，不會全部在同一瞬間開火（每人 0～0.3 秒的反應差，依種子決定）。"""
    m = TacMap(w=30, h=30)
    us = [Unit(f"a{i}", "blue", (2 + i, 2, 0), posture="stand", facing=(0, 1)) for i in range(8)]
    us.append(Unit("e", "red", (6, 20, 0), posture="stand", facing=(0, -1)))
    bt = Battle(m, us, seed=3)
    bt.dt = 0.5
    bt.intel["blue"].spotted["e"] = 0
    bt.assign({f"a{i}": Plan(mode="shoot", target="e", fire="suppress") for i in range(8)})
    first = sorted({q[0] for q in bt.queue if q[4] == "volley" and q[6]["k"] == 0})
    assert len(first) >= 3 and first[-1] - first[0] >= 0.1, first
    assert all(0.4 - 1e-6 <= x <= 0.4 + tl.REACT_JITTER + 1e-6 for x in first), first


def test_downed_unit_events_cancelled():
    """先倒下的人，排在後面的射擊作廢。"""
    cancelled = 0
    for seed in range(60):
        bt = duel(3, seed=seed)
        bt.units["b"].weapon = "dmr"             # b 瞄得比較久
        bt.assign({"a": Plan(mode="shoot", target="b", fire="burst"),
                   "b": Plan(mode="shoot", target="a", fire="aimed")})
        run_until(bt, 2.5)
        if bt.units["b"].down:
            first_down = min(e["ts"] for e in bt.ev if e["type"] in ("down", "kill") and e["target"] == "b")
            assert all(t <= first_down for t in shot_times(bt, "b")), seed
            if not shot_times(bt, "b"):
                cancelled += 1
    assert cancelled > 0, "應該有 b 還沒開火就被打倒的場次"


def test_grenade_fuse_survives_thrower():
    """手榴彈：（反應差＋）出手 0.8 秒、再 1.2 秒爆炸；出手後投擲者倒下也照樣爆。"""
    bt = duel(6)
    bt.units["a"].grenades = 1
    point = bt.units["b"].torso(bt.map)
    bt.assign({"a": Plan(mode="grenade", point=point)})
    run_until(bt, 1.5)
    assert bt.units["a"].grenades == 0, "0.8 秒（加反應差）出手"
    bt.units["a"].down = True
    run_until(bt, 2.5)
    blasts = [e for e in bt.ev if e["type"] == "blast"]
    assert blasts and 2.0 - 1e-6 <= blasts[0]["ts"] <= 2.0 + tl.REACT_JITTER + 1e-6, [e.get("ts") for e in blasts]


def test_move_speed_and_progress_carry():
    """接敵時一格（成本 1）1.25 秒；同方向的新計畫延續這一格的進度，不會因為重新規劃而停住。（量速度：先拿掉反應差）"""
    jitter, tl.REACT_JITTER = tl.REACT_JITTER, 0.0
    try:
        _move_speed_and_progress_carry()
    finally:
        tl.REACT_JITTER = jitter


def _move_speed_and_progress_carry():
    m = TacMap()
    a = Unit("a", "blue", (2, 2, 0), facing=(1, 0))
    e = Unit("e", "red", (2, 12, 0), facing=(0, -1))
    bt = Battle(m, [a, e], seed=1)
    bt.dt = 0.5
    bt.intel["blue"].last_known["e"] = (e.pos, 0)      # 接敵：每 2.5 秒 2 點行動點
    path = [(3, 2, 0), (4, 2, 0), (5, 2, 0), (6, 2, 0)]
    bt.assign({"a": Plan(mode="move", path=list(path))})
    for _ in range(3):
        bt.advance()
    assert bt.units["a"].pos == (3, 2, 0), "1.25 秒走完第一格"
    bt.assign({"a": Plan(mode="move", path=list(path[1:]))})    # 1.5 秒時重新規劃
    run_until(bt, 2.5)
    assert bt.units["a"].pos == (4, 2, 0), bt.units["a"].pos
    bt.assign({"a": Plan(mode="move", path=[(5, 2, 0), (6, 2, 0)])})
    bt.advance()
    bt.assign({"a": Plan(mode="move", path=[(5, 2, 0), (6, 2, 0)])})   # 半步就重新規劃：同一格的進度延續
    run_until(bt, 5.0)
    assert bt.units["a"].pos == (6, 2, 0), bt.units["a"].pos


def test_move_start_times_for_playback():
    """前端連續內插要的資料：每走一格記起步時間（早於抵達）；還沒走到的那一格有起步與預計抵達時間。被擋住時不往前推。"""
    m = TacMap()
    a = Unit("a", "blue", (2, 2, 0), facing=(1, 0))
    e = Unit("e", "red", (2, 12, 0), facing=(0, -1))
    bt = Battle(m, [a, e], seed=1)
    bt.dt = 0.5
    bt.assign({"a": Plan(mode="move", path=[(3, 2, 0), (4, 2, 0), (5, 2, 0)])})
    bt.advance()
    act = bt.acts["a"]
    assert act["seg_t"] is not None and act["seg_t"] < act["next_move"], act
    starts, arrives = [], []
    while bt.now < 4.0 - 1e-9:
        bt.advance()
        starts += bt.path_st.get("a", [])
        arrives += bt.path_t.get("a", [])
    assert len(starts) == len(arrives) >= 2 and all(s < t for s, t in zip(starts, arrives)), (starts, arrives)
    assert all(abs(s - t0) < 1e-6 for s, t0 in zip(starts[1:], arrives[:-1])), "下一格在上一格抵達時起步"
    b2 = Unit("b", "blue", (3, 2, 0))
    bt2 = Battle(m, [Unit("a", "blue", (2, 2, 0)), b2], seed=1)
    bt2.dt = 0.5
    bt2.assign({"a": Plan(mode="move", path=[(3, 2, 0)])})
    while bt2.now < 2.0 - 1e-9:
        bt2.advance()
    assert bt2.units["a"].pos == (2, 2, 0) and bt2.acts["a"]["seg_t"] is None, "前進格被佔：停著等，前端不往前推"


def test_overwatch_reaction_time():
    """警戒射擊在看到移動後的反應時間開火：已架槍 0.2 秒、沒架槍 0.45 秒（對齊 0.05 秒）。（移動者的反應差先拿掉）"""
    jitter, tl.REACT_JITTER = tl.REACT_JITTER, 0.0
    try:
        _overwatch_reaction_time()
    finally:
        tl.REACT_JITTER = jitter


def _overwatch_reaction_time():
    for braced in (False, True):
        m = TacMap()
        w = Unit("w", "blue", (4, 2, 0), posture="stand", facing=(0, 1))
        r = Unit("r", "red", (4, 9, 0), posture="stand", facing=(1, 0))
        bt = Battle(m, [w, r], seed=2)
        bt.dt = 0.5
        bt.intel["blue"].spotted["r"] = 0
        bt.intel["blue"].last_known["r"] = (r.pos, 0)
        bt.intel["red"].last_known["w"] = (w.pos, 0)
        if braced:
            w.braced = (0.0, 1.0)
        bt.assign({"w": Plan(mode="overwatch"), "r": Plan(mode="move", path=[(5, 9, 0), (6, 9, 0)])})
        run_until(bt, 2.5)
        moves = [e["ts"] for e in bt.ev if e["type"] == "shot" and e["actor"] == "w"]
        assert moves, braced
        react = tl.OW_REACT_BRACED if braced else tl.OW_REACT
        assert abs(moves[0] - tl.q(1.25 + react)) < 1e-6, (braced, moves)


def test_rates_match_across_step_lengths():
    """以秒定義的規則和步長無關：壓制消退 2.5 秒的量相同；倒地沒穩定 30 秒離線。"""
    left = {}
    for dt in (2.5, 0.5):
        bt = duel(40, dt=dt)
        bt.units["a"].suppression = 80.0
        run_until(bt, 2.5)
        left[dt] = bt.units["a"].suppression
    assert abs(left[2.5] - left[0.5]) < 1e-6, left
    bt = duel(40, dt=0.5)
    bt.units["b"].down = True
    bt.units["b"].down_tick = 0
    run_until(bt, DOWN_SECONDS - 0.5)
    assert not bt.units["b"].dead
    run_until(bt, DOWN_SECONDS)
    assert bt.units["b"].dead


def test_perception_runs_each_second():
    """0.5 秒一步時完整感知（偵測、士氣、暴露推算）每秒一次：站著不動、看得到的敵人會被發現。
    （第 8 步的錯誤：上次感知時間從沒記下來，0.5 秒一步時完整感知一次都沒做，只有移動的人會被偵測。）"""
    m = TacMap(w=30, h=10)
    a = Unit("a", "blue", (2, 3, 0), posture="stand", facing=(1, 0))
    e = Unit("e", "red", (12, 3, 0), posture="stand", facing=(-1, 0))
    bt = Battle(m, [a, e], seed=5)
    bt.dt = 0.5
    passes = []
    while bt.now < 5.0 - 1e-9:
        bt.advance()
        if bt._percept_t is not None and (not passes or passes[-1] != bt._percept_t):
            passes.append(bt._percept_t)
    assert len(passes) >= 4, ("每秒一次完整感知", passes)
    assert "e" in bt.intel["blue"].spotted and "a" in bt.intel["red"].spotted, "不動的人也會被發現"


def test_suppression_peak_holds():
    """被壓制、釘死看最近 SUPP_HOLD_S 秒內的最高壓制值：一陣火力把人壓到 85，目前值掉到 80 以下後仍釘死到峰值過期。"""
    from engine.tactical.sim import SUPP_HOLD_S
    bt = duel(10)
    u = bt.units["a"]
    bt.add_suppression(u, 85.0)
    assert u.pinned
    states = []
    while bt.now < SUPP_HOLD_S + 1.0 - 1e-9:
        bt.advance()
        states.append((bt.now, round(u.suppression, 1), u.pinned))
    below = [s for s in states if s[1] < 80]
    assert below and all(p for t, _, p in below if t < SUPP_HOLD_S - 1e-9), ("峰值內仍釘死", states)
    assert not states[-1][2] and states[-1][1] < 80, ("峰值過期、目前值也低 → 不再釘死", states)
    assert u.supp_level == u.suppression, "過期後只看目前值"


def test_state_roundtrip_mid_action():
    """存讀：進行中的動作、事件佇列都帶著；讀回後繼續推進與直接推進結果相同。"""
    def make():
        bt = duel(8, seed=4)
        bt.units["a"].grenades = 1
        bt.assign({"a": Plan(mode="grenade", point=bt.units["b"].torso(bt.map)),
                   "b": Plan(mode="shoot", target="a", fire="suppress")})
        bt.advance()
        bt.advance()
        return bt
    x = make()
    y = make()
    st = y.state()
    z = Battle(TacMap(), [], seed=0)
    z.load_state(st)
    z.dt = y.dt
    run_until(x, 5.0)
    run_until(z, 5.0)
    assert x.state()["units"] == z.state()["units"], "讀回後結果不同"
    assert len(st["timeline"]["queue"]) > 0


def test_engagement_cadence():
    """Engagement：同一組人每秒最多規劃一次；射擊做到一半不被重新規劃打斷；新命令立刻換掉。"""
    from engine.engagement import PLAN_GAP_S, Engagement

    class Fake:
        _due = Engagement._due
        _min_gap = Engagement._min_gap
        _sig = Engagement._sig
        lod = 0

    f = Fake()
    bt = duel(10)
    f.battle = bt
    f.plan_t, f.plan_sig = {}, {}
    units = [bt.units["a"]]
    assert f._due("g", units, False), "第一次一定規劃"
    f.plan_t["g"] = bt.now
    f.plan_sig["g"] = f._sig(units)
    bt.assign({"a": Plan(mode="shoot", target="b", fire="aimed")})
    bt.advance()
    assert not f._due("g", units, False), f"{PLAN_GAP_S} 秒內不再規劃"
    assert f._due("g", units, True), "新命令不受間隔限制"
    assert tl.busy(bt, "a") and not tl.idle(bt, "a"), "瞄準中：不能中途換掉"
    run_until(bt, 2.5)
    assert tl.idle(bt, "a"), "開完火、回穩後閒下來"
    assert f._due("g", units, False)
    assert TICK_S == 2.5


def main():
    tests = {k: v for k, v in globals().items() if k.startswith("test_")}
    for k, v in tests.items():
        v()
        print("OK", k)
    print("timeline_test OK")


if __name__ == "__main__":
    main()
