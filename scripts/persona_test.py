"""階段 2：性格 × 人格值影響戰術 AI、交戰後性格演化。不需要 LLM。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle
from engine.tactical.tmap import TacMap
from engine.tactical.units import PINNED, Unit


def with_traits(builder, side, traits, persona):
    def b():
        m, units, bi, ri = builder()
        for u in units:
            if u.side == side:
                u.traits = dict(traits)
                u.persona = persona
        return m, units, bi, ri
    return b


def test_zero_persona_identical():
    for name in ("assault_suppress", "rescue", "ambush"):
        base = scenarios.SCENARIOS[name]
        wild = with_traits(base, "blue", {"aggr": 1, "caut": -1, "comp": -1, "bond": 1}, 0.0)
        for s in range(3):
            a = runner.run(base, seed=s, max_ticks=30)
            b = runner.run(wild, seed=s, max_ticks=30)
            assert a["positions"] == b["positions"] and a["winner"] == b["winner"], f"{name} 人格值 0 必須一樣"


def test_composure_shifts_pin_threshold():
    u = Unit(id="a", side="blue", pos=(0, 0, 0))
    u.suppression = PINNED + 5
    assert u.pinned
    u.traits, u.persona = {"comp": 1.0}, 1.0
    assert not u.pinned, "沉著的人 85 還沒被釘死"
    u.traits = {"comp": -1.0}
    u.suppression = PINNED - 10
    assert u.pinned, "容易慌的人 70 就被釘死"


def test_risk_shift_and_aid_reach():
    bold = Unit(id="a", side="blue", pos=(0, 0, 0), traits={"aggr": 1.0, "caut": -1.0}, persona=1.0)
    shy = Unit(id="b", side="blue", pos=(0, 0, 0), traits={"aggr": -1.0, "caut": 1.0}, persona=1.0)
    assert TacticalAI.risk_shift(bold) >= 2 and TacticalAI.risk_shift(shy) <= -2
    half = Unit(id="c", side="blue", pos=(0, 0, 0), traits={"aggr": 1.0}, persona=0.2)
    assert TacticalAI.risk_shift(half) == 0, "人格值低 → 影響小"


def test_bond_aids_under_fire():
    m = TacMap(w=12, h=16)
    units = [Unit(id="b0", side="blue", pos=(5, 3, 0)), Unit(id="b1", side="blue", pos=(5, 4, 0)),
             Unit(id="r0", side="red", pos=(5, 12, 0), facing=(0, -1))]
    units[1].down, units[1].down_tick = True, 0
    units[0].smokes = 0
    b = Battle(m, units, seed=1)
    b.intel["blue"].spotted["r0"] = 0
    b.intel["blue"].last_known["r0"] = ((5, 12, 0), 0)
    p = TacticalAI(b, "blue").plan(Intent("hold"))
    assert p["b0"].mode != "aid", "一般人看到敵人時不去救"
    b.units["b0"].traits, b.units["b0"].persona = {"bond": 1.0}, 1.0
    p = TacticalAI(b, "blue").plan(Intent("hold"))
    assert p["b0"].mode == "aid", "重情的人冒險先救"


def test_batch_difference_bounded():
    """全隊高積極 vs 高謹慎：差異看得到但不失控。"""
    base = scenarios.SCENARIOS["assault_suppress"]
    bold = with_traits(base, "blue", {"aggr": 0.8, "caut": -0.5, "comp": 0.0, "bond": 0.0}, 0.8)
    shy = with_traits(base, "blue", {"aggr": -0.5, "caut": 0.8, "comp": 0.0, "bond": 0.0}, 0.8)
    n = 30
    bt = sum(runner.run(bold, seed=s, max_ticks=40)["ticks"] for s in range(n)) / n
    st = sum(runner.run(shy, seed=s, max_ticks=40)["ticks"] for s in range(n)) / n
    bw = sum(runner.run(bold, seed=s, max_ticks=40)["winner"] == "blue" for s in range(n))
    sw = sum(runner.run(shy, seed=s, max_ticks=40)["winner"] == "blue" for s in range(n))
    print(f"    高積極：平均 {bt:.1f} tick、藍勝 {bw}/{n}｜高謹慎：平均 {st:.1f} tick、藍勝 {sw}/{n}")
    assert bt != st or bw != sw, "性格要有差異"
    assert bw >= n * 0.2 and sw >= n * 0.2, "不可失控到幾乎必輸"


def test_evolution_after_engagement():
    from scripts.engage_test import _engaged, PATH

    async def go():
        sim, sq, e, _ = await _engaged()
        eng = sim.engagements[sq.pos]
        uids = [uid for uid, o in eng.owner.items() if o[0] == "s"]
        m = eng.owner[uids[1]][2]
        before_c, before_p = m.traits["caut"], m.persona
        eng.exp[uids[1]] = {"fought", "hit", "pressed"}
        eng.evolve()
        assert m.traits["caut"] > before_c, "中彈 → 謹慎上升"
        assert m.persona > before_p and m.battles == 1
        eng.exp[uids[2]] = set()
        p2 = eng.owner[uids[2]][2].persona
        eng.evolve()
        assert eng.owner[uids[2]][2].persona == p2, "沒實際接觸不算一場"
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("persona_test OK")


if __name__ == "__main__":
    main()
