import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import enemy_ai, scenario
from engine.models import Enemy, Member, Squad, World


def squad(pos, n=4, sid="alpha"):
    return Squad(id=sid, callsign=sid, leader={"name": "L"}, members=[Member(f"m{i}") for i in range(n)], pos=pos)


def unit(world, eid, pos, strength, tag="mobile", **kw):
    e = Enemy(id=eid, pos=pos, strength=strength, route=[pos], move_every=999, cooldown=999, tag=tag,
              home=pos, max_strength=strength, **kw)
    world.enemies[eid] = e
    return e


def run_move(world, n):
    for _ in range(n):
        world.tick += 1
        enemy_ai.move(world)


def main():
    rng = random.Random(1)

    print("== 感知只依目擊 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    unit(w, "a", "C3", 3, tag="patrol")
    far = squad("A1")
    enemy_ai.observe(w, rng, [far])
    assert "A1" not in w.enemy_intel, "看不到遠方"
    near = squad("C3")
    enemy_ai.observe(w, rng, [near])
    assert w.enemy_intel["C3"]["est"] == 4
    w.tick += enemy_ai.INTEL_TTL + 1
    enemy_ai.observe(w, rng, [])
    assert not w.enemy_intel, "情報過期"

    print("== 撤退與合併 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    base = scenario.ENEMY_BASES[0]
    g = unit(w, "g", base, 3, tag="garrison")
    weak = unit(w, "w", "C3", 1)
    enemy_ai.think(w, rng, [])
    assert weak.order == "withdraw" and weak.goal == enemy_ai.nearest_base("C3")
    weak.goal_base = weak.goal
    run_move(w, enemy_ai.STEP_TICKS * 8)
    target_base = enemy_ai.nearest_base("C3")
    if target_base == base:
        assert not weak.alive and g.strength == 4, (weak.alive, g.strength)
    else:
        assert weak.alive and weak.tag == "garrison" and weak.pos == target_base

    print("== 增援交戰中的友軍 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    fight = unit(w, "f", "C3", 1, tag="garrison", engaged_with="alpha")
    fight.strength = 2
    helper = unit(w, "h", "D3", 3)
    w.enemy_intel["C3"] = {"tick": 0, "est": 4}
    enemy_ai.think(w, rng, [])
    assert helper.order == "reinforce" and helper.goal == "C3", (helper.order, helper.goal)
    run_move(w, enemy_ai.STEP_TICKS)
    assert helper.pos == "C3" and helper.tag == "garrison"

    print("== 反攻任務目標 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    mob = unit(w, "m", "E3", 3)
    w.enemy_intel["D3"] = {"tick": 0, "est": 2}
    enemy_ai.think(w, rng, ["D3"])
    assert mob.goal == "D3" and mob.order in ("counter", "hunt"), (mob.order, mob.goal)

    print("== 不知道就不反攻 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    mob = unit(w, "m", "E3", 3)
    enemy_ai.think(w, rng, ["D3"])
    assert mob.order is None, "敵方沒看到我方，不應反攻"

    print("== 騷擾後方 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    unit(w, "g", base, 6, tag="garrison")
    w.enemy_next_harass = 1
    w.tick = 2
    log = enemy_ai.think(w, random.Random(3), [])
    raiders = [e for e in w.enemies.values() if e.order == "harass"]
    assert raiders and raiders[0].goal[0] == scenario.ROWS[0] and raiders[0].goal != scenario.BASE, log
    assert w.enemies["g"].strength == 6 - enemy_ai.HARASS_SIZE

    print("== 兵力上限依指揮官人數 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    assert enemy_ai.strength_cap(w) == 10
    for i, n in enumerate((10, 14, 18, 22, 22)):
        w.squads[f"c{i}.a"] = Squad(id=f"c{i}.a", callsign="x", leader={"name": "L"}, members=[Member("m")],
                                    pos="A3", commander=f"c{i}")
        assert enemy_ai.strength_cap(w) == n, (i + 1, enemy_ai.strength_cap(w))

    print("== 兵力上限與補充 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    unit(w, "big", "E1", enemy_ai.strength_cap(w), tag="garrison")
    assert not enemy_ai.can_spawn(w, 1)
    w.tick = enemy_ai.BASE_REGEN_EVERY - 1
    w.enemy_next_think = 0
    w.tick += 1
    before = len(w.enemies)
    enemy_ai.think(w, rng, [])
    assert len(w.enemies) == before and enemy_ai.total_strength(w) <= enemy_ai.strength_cap(w), "上限內不重建"
    w = World(scenario=scenario.SCENARIO_ID)
    w.tick = 0
    enemy_ai.think(w, rng, [])
    assert all(any(e.pos == b and e.alive for e in w.enemies.values()) for b in scenario.ENEMY_BASES), "據點重建"

    print("== 機動隊編成 ==")
    w = World(scenario=scenario.SCENARIO_ID)
    unit(w, "g", base, enemy_ai.MOBILE_SIZE + 3, tag="garrison")
    w.tick = 0
    enemy_ai.think(w, rng, [])
    assert any(e.tag == "mobile" and e.pos == base for e in w.enemies.values())

    print("== 整合：模擬 300 tick 敵軍不爆量 ==")
    import asyncio
    from engine.sim import Simulation
    from lib.llm import LLMClient

    class Quiet(LLMClient):
        async def chat_json(self, system, user, temperature=0.8, priority=1):
            return {"action": "keep", "report": None}

    path = "./data/enemy_test.json"
    if os.path.exists(path):
        os.remove(path)
    sim = Simulation(Quiet(base_url="x", model="x"), save_path=path, rng=random.Random(5))
    start = enemy_ai.total_strength(sim.world)

    async def go():
        for _ in range(300):
            await sim.tick()
    asyncio.run(go())
    assert enemy_ai.total_strength(sim.world) <= max(enemy_ai.strength_cap(sim.world), start), \
        "超過上限的初始敵軍不刪，但不再補到上限以上"
    assert all(e.pos in scenario.CELLS for e in sim.world.enemies.values())
    os.remove(path)
    print("enemy_test OK")


if __name__ == "__main__":
    main()
