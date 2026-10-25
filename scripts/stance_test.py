"""站起來越過掩體射擊（engine/tactical/ai.py stand_targets、_settle_posture）：蹲在 1 m 矮牆後眼高和牆頂一樣，
只看得到比眼睛高的部分；蹲著看不到、站起來看得到已知敵人時站起來射擊，沒有目標就蹲回去。不需要 LLM。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import timeline as tl
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def plateau():
    """紅方在 2 m 高台邊的矮牆後（y＝17），藍方在下面開闊地（y＝5），相距 24 m。"""
    m = TacMap(w=20, h=28)
    for x in range(m.w):
        for y in range(11, m.h):
            m.set_ground(x, y, min(10, (y - 10) * 2))
    m.low_wall(8, 17, "S")
    r = Unit("r", "red", (8, 17, 0), facing=(0, -1))
    b = Unit("b", "blue", (8, 5, 0), facing=(0, 1))
    bt = Battle(m, [r, b], seed=0)
    return m, bt, r, b, TacticalAI(bt, "red")


def test_stand_when_crouch_blind():
    """高台矮牆後蹲著往下完全看不到、站起來看得到 → 站起來；知道敵人在哪才站（沒有情報不站）。"""
    m, bt, r, b, ai = plateau()
    assert not bt.visible_parts(r, b), "蹲著被自己的牆擋住"
    assert ai.fire_plan(r) is None, "不知道有敵人：不站"
    bt.intel["red"].last_known["b"] = (b.pos, 0)
    p = ai.fire_plan(r)
    assert p is not None and p.mode == "posture" and p.posture == "stand", p
    r.posture = "stand"
    assert bt.visible_parts(r, b), "站起來就看得到"


def test_uses_last_known_not_truth():
    """依情報裡的最後位置判斷：敵人其實已經躲到看不到的地方，只要最後位置站起來看得到就站（不讀真實位置）。"""
    m, bt, r, b, ai = plateau()
    m.wall(8, 6, height=3.0)
    b.pos = (8, 5, 0)                       # 真實位置在 3 m 牆後
    bt.intel["red"].last_known["b"] = ((12, 6, 0), 0)
    assert ai.stand_targets(r) == [b]


def test_no_stand_when_visible_or_suppressed():
    """蹲著就打得到的不站；被壓制時不站。"""
    m = TacMap(w=20, h=28)
    r = Unit("r", "red", (8, 17, 0), facing=(0, -1))
    b = Unit("b", "blue", (8, 5, 0), facing=(0, 1))
    bt = Battle(m, [r, b], seed=0)
    ai = TacticalAI(bt, "red")
    bt.intel["red"].spotted["b"] = 0
    bt.intel["red"].last_known["b"] = (b.pos, 0)
    p = ai.fire_plan(r)
    assert p is not None and p.mode == "shoot", "開闊地蹲著看得到：直接射擊"
    m2, bt2, r2, b2, ai2 = plateau()
    bt2.intel["red"].last_known["b"] = (b2.pos, 0)
    r2.suppression = 70.0
    assert ai2.fire_plan(r2) is None, "被壓制：不站起來"


def test_crouch_back_when_nothing_to_shoot():
    """站著卻沒有可打的目標：待命／警戒改成蹲下，移動時蹲著走（臥姿起身、站姿蹲下都不另花時間）。"""
    m, bt, r, b, ai = plateau()
    r.posture = "stand"
    plans = {"r": Plan(mode="overwatch")}
    ai._settle_posture(plans)
    assert plans["r"].mode == "posture" and plans["r"].posture == "crouch"
    plans = {"r": Plan(mode="move", path=[(9, 17, 0)])}
    ai._settle_posture(plans)
    assert plans["r"].posture == "crouch"
    plans = {"r": Plan(mode="shoot", target="b")}
    ai._settle_posture(plans)
    assert plans["r"].mode == "shoot", "要射擊就繼續站著"
    tl.start(bt, r, Plan(mode="move", path=[(9, 17, 0)], posture="crouch"))
    assert r.posture == "crouch", "站姿蹲下邊走"


def test_peek_fire():
    """探頭射擊：站著才看得到、蹲下看不到的目標 → 射擊帶 peek；打完（回穩後）自動蹲回去；開闊地蹲下也看得到就不探頭。"""
    m, bt, r, b, ai = plateau()
    r.posture = "stand"
    bt.intel["red"].spotted["b"] = 0
    bt.intel["red"].last_known["b"] = (b.pos, 0)
    p = ai.fire_plan(r)
    assert p is not None and p.mode == "shoot" and p.peek, p
    bt.dt = 0.5
    bt.assign({"r": p})
    for _ in range(12):                          # 最多 6 秒：反應、瞄準、開火、回穩、蹲下
        bt.advance()
        if r.posture == "crouch":
            break
    assert r.posture == "crouch", "打完蹲回去"
    assert any(e["type"] == "shot" and e["actor"] == "r" for e in bt.ev), "蹲下前有開火"
    m2 = TacMap(w=20, h=28)
    r2 = Unit("r", "red", (8, 17, 0), posture="stand", facing=(0, -1))
    b2 = Unit("b", "blue", (8, 5, 0), facing=(0, 1))
    bt2 = Battle(m2, [r2, b2], seed=0)
    ai2 = TacticalAI(bt2, "red")
    bt2.intel["red"].spotted["b"] = 0
    bt2.intel["red"].last_known["b"] = (b2.pos, 0)
    assert not ai2.fire_plan(r2).peek, "開闊地蹲下也看得到：不探頭"


def test_peek_cancelled_by_new_plan():
    """探頭射擊的蹲下屬於那一次射擊：中途換了新動作就作廢，照新動作走。"""
    m, bt, r, b, ai = plateau()
    r.posture = "stand"
    bt.intel["red"].spotted["b"] = 0
    bt.intel["red"].last_known["b"] = (b.pos, 0)
    bt.dt = 0.5
    bt.assign({"r": ai.fire_plan(r)})
    bt.advance()
    bt.assign({"r": Plan(mode="move", path=[(9, 17, 0)], posture="stand")})
    for _ in range(8):
        bt.advance()
    assert r.posture == "stand", "舊射擊的蹲下事件作廢"


def test_plateau_fight_has_stand_and_crouch():
    """整場：雙方都在矮牆後、藍方往高台推進，紅方會站起來射擊、之後蹲回去（固定 seed）。"""
    from engine.tactical import runner, scenarios
    stands = crouches = 0
    for seed in range(3):
        ev = runner.run(scenarios.TERRAIN["high_vs_low_cover"], seed=seed, max_ticks=60, keep_busy=True)["battle"].ev
        stands += sum(1 for e in ev if e["type"] == "posture" and e.get("data", {}).get("to") == "stand")
        crouches += sum(1 for e in ev if e["type"] == "posture" and e.get("data", {}).get("frm") == "stand")
    assert stands >= 1 and crouches >= 1, (stands, crouches)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("stance_test OK")


if __name__ == "__main__":
    main()
