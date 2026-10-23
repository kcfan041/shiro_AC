"""自我暴露推算：每方只用自己知道的資訊推算「我們是否已被看到」。不需要 LLM。"""
import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import sim as tsim
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def field(blue, red, w=12, h=16):
    m = TacMap(w=w, h=h)
    units = [Unit(id=f"b{i}", side="blue", pos=p, facing=(0, 1)) for i, p in enumerate(blue)] + \
            [Unit(id=f"r{i}", side="red", pos=p, facing=(0, -1)) for i, p in enumerate(red)]
    return Battle(m, units, seed=7)


def test_no_clues_no_belief():
    b = field([(2, 1, 0)], [(9, 14, 0)])
    b.units["r0"].facing = (1, 0)
    b.step({})
    assert not b.intel["blue"].believed_seen and not b.intel["red"].believed_seen


def test_hit_marks_and_spreads_and_decays():
    b = field([(2, 1, 0), (4, 1, 0), (11, 1, 0)], [(3, 6, 0)])
    b.units["r0"].facing = (1, 0)
    b.shots.append({"shooter": "r0", "from": b.units["r0"].eye(b.map), "to": b.units["b0"].torso(b.map),
                    "kind": "body", "ref": ("b0", "torso")})
    b._shot_mark = len(b.shots) - 1
    b.update_beliefs()
    assert b.believes_seen(b.units["b0"]) == "hit"
    assert b.believes_seen(b.units["b1"]) == "near_miss", "附近隊友也暴露"
    assert b.believes_seen(b.units["b2"]) is None, "遠處隊友不受影響"
    assert not b.intel["red"].believed_seen, "射手不因此推算自己暴露（沒有被打、沒有被盯）"
    b._shot_mark = len(b.shots)
    for _ in range(int(round(tsim.BELIEF_TTL_S / tsim.TICK_S))):
        b.tick += 1
        b.update_beliefs()
    assert not b.intel["blue"].believed_seen, "沒有新線索 → 衰減"


def test_fired_exposed_only_if_known_enemy_has_sight():
    b = field([(5, 1, 0)], [(5, 8, 0)])
    u = b.units["b0"]
    u.fired = True
    b.update_beliefs()
    assert b.believes_seen(u) is None, "不知道敵人在哪 → 開火不算確定暴露"
    b.intel["blue"].last_known["r0"] = (b.units["r0"].pos, b.tick)
    b.update_beliefs()
    assert b.believes_seen(u) == "fired"


def test_watched_rules():
    b = field([(5, 1, 0)], [(5, 8, 0)])
    me = b.units["b0"]
    foe = b.units["r0"]
    b.intel["blue"].spotted["r0"] = b.tick
    foe.facing = (0, -1)
    b.update_beliefs()
    assert b.believes_seen(me) == "watched", "看得到的敵人 14 m 內面朝我"
    b.intel["blue"].believed_seen.clear()
    foe.facing = (1, 0)
    b.update_beliefs()
    assert b.believes_seen(me) is None, "面朝別處不算"
    foe.facing = (0, -1)
    foe.pos = (5, 13, 0)
    b.update_beliefs()
    assert b.believes_seen(me) is None, "超過 20 m 不算"
    foe.pos = (5, 8, 0)
    b.intel["blue"].spotted.clear()
    b.update_beliefs()
    assert b.believes_seen(me) is None, "沒看到那個敵人就不知道他面向哪"


def test_isolation_from_enemy_intel():
    b = field([(2, 1, 0), (6, 2, 0)], [(3, 10, 0), (8, 12, 0)])
    for _ in range(3):
        b.step({})
    b2 = copy.deepcopy(b)
    b2.intel["blue"].spotted = {"r0": b2.tick, "r1": b2.tick}
    b2.intel["blue"].last_known = {"r0": (b2.units["r0"].pos, b2.tick), "r1": (b2.units["r1"].pos, b2.tick)}
    b2.update_beliefs()
    b.update_beliefs()
    assert b.intel["red"].believed_seen == b2.intel["red"].believed_seen, "紅方推算不受藍方情報影響"


def test_enemy_commander_uses_own_belief():
    from scripts.enemy_tactics_test import make, fake_known
    from engine.enemy_tactics import EnemyCommander

    async def go():
        sim, sq, e = await make(foes=3, tag="mobile", seed=4)
        eng = sim.engagements["C3"]
        cmd = EnemyCommander(eng)
        units = cmd._units(e)
        b = eng.battle
        fake_known(eng, 1)
        known = cmd._known()
        for u in units:
            u.pos = (u.pos[0], 24, 0)
        b.intel["red"].believed_seen.clear()
        b.intel["blue"].last_known = {u.id: (u.pos, b.tick) for u in units}
        it, why = cmd._choose(e, units, known, 3, "normal")
        assert it.kind == "ambush", f"藍方其實知道，但紅方沒有線索 → 仍會設伏（不偷看）：{why}"
        b.intel["blue"].last_known = {}
        b.intel["red"].believed_seen[units[0].id] = (b.tick, "near_miss")
        cmd.current[e.id] = (it, b.tick, why)
        it2, why2 = cmd._choose(e, units, known, 3, "normal")
        assert it2.kind != "ambush", f"紅方推算已暴露 → 放棄設伏：{why2}"
        assert "放棄設伏" in why2
        from scripts.engage_test import PATH
        os.remove(PATH)
    asyncio.run(go())


def test_brief_shows_exposure():
    from scripts.engage_test import _engaged, PATH

    async def go():
        sim, sq, e, _ = await _engaged()
        eng = sim.engagements[sq.pos]
        b = eng.battle
        mine = [uid for uid, o in eng.owner.items() if o[1] == sq.id and b.units[uid].alive]
        b.intel["blue"].believed_seen.clear()
        text = eng.brief(sq)
        assert "似乎還沒察覺" in text
        b.intel["blue"].believed_seen[mine[0]] = (b.tick, "hit")
        text = eng.brief(sq)
        name = eng.owner[mine[0]][2].name
        assert "⚠中彈" in text and f"敵方應已發現 {name}" in text
        os.remove(PATH)
    asyncio.run(go())


def test_ambush_springs_when_compromised():
    from engine.tactical.ai import Intent, TacticalAI
    b = field([(5, 1, 0), (6, 1, 0)], [(5, 12, 0)])
    b.intel["blue"].spotted["r0"] = 0
    b.intel["blue"].last_known["r0"] = ((5, 12, 0), 0)
    ai = TacticalAI(b, "blue")
    units = [b.units["b0"], b.units["b1"]]
    plans = ai.ambush_plans(units, Intent("ambush", ambush_range=6), set())
    assert not any(p.mode == "shoot" for p in plans.values()), "敵人在伏擊距離外 → 不開火"
    b.intel["blue"].believed_seen["b0"] = (b.tick, "watched")
    ai2 = TacticalAI(b, "blue")
    plans = ai2.ambush_plans(units, Intent("ambush", ambush_range=6), set())
    assert ai2.ambush_state["sprung"], "推算已暴露 → 提早開火"
    assert any("提早開火" in r for r in ai2.reports)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("awareness_test OK")


if __name__ == "__main__":
    main()
