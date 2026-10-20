"""友方電腦小隊（engine/allies.py）：建立、守家、支援玩家交戰、打完回家、不建活動區域、不影響玩家行軍、
傷亡不進預備兵力池。不需要 LLM。"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import allies, reserve, roster, scenario, zone as zmod
from engine.models import Action, Enemy
from engine.sim import Simulation
from scripts.zone_test import Quiet, card

PATH = "./data/allies_test.json"


async def setup(seed=3):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.allies_enabled = True
    sim.tac_steps = 1
    sim.enemy_ai_enabled = False
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    sim.ensure_commander("100", "甲")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    roster.sync_world(sim.world)
    return sim, a


def done():
    if os.path.exists(PATH):
        os.remove(PATH)


def test_created_guard_no_zone():
    async def go():
        sim, a = await setup()
        await sim.tick()
        al = allies.ally_squads(sim.world)
        assert len(al) == 2, [s.id for s in al]
        assert all(s.commander is None and s.ai["task"] == "guard" for s in al)
        assert {s.pos for s in al} <= set(allies.homes()) | {scenario.BASE}
        regions = zmod.build_zones(sim.world)
        assert len(regions) == 1 and a.pos in regions[0], "友軍不自己建活動區域"
        pool = reserve.remaining(sim.world)
        for _ in range(4):
            await sim.tick()
        assert reserve.remaining(sim.world) == pool, "友軍不動用預備兵力池"
        done()
    asyncio.run(go())


def test_support_then_return():
    async def go():
        sim, a = await setup()
        await sim.tick()
        home_cells = allies.homes()
        target = next(c for c in scenario.CELLS if scenario.distance(c, home_cells[0]) == 1 and c != scenario.BASE
                      and c not in home_cells)
        a.pos = target
        for m in a.members:
            m.gpos = None
        foe_cell = next(c for c in scenario.adjacent(target) if c not in home_cells and c != scenario.BASE)
        sim.world.enemies["x"] = Enemy(id="x", pos=foe_cell, strength=2, route=[foe_cell], move_every=999,
                                       cooldown=999, tag="garrison", home=foe_cell)
        roster.sync_world(sim.world)
        sent = None
        for _ in range(40):
            await sim.tick()
            sup = [s for s in allies.ally_squads(sim.world) if s.ai["task"] == "support"]
            if sup:
                sent = sup[0]
                break
        assert sent is not None, "附近玩家交戰 → 派一支支援"
        assert len([s for s in allies.ally_squads(sim.world) if s.ai["task"] == "support"]) == 1, "一場只派一支"
        sim.world.enemies.clear()
        z = sim.engagements.get(a.pos)
        if z is not None:
            for uid in [u for u, o in z.owner.items() if o[0] == "e"]:
                z.battle.units.pop(uid, None)
                z.owner.pop(uid, None)
            for it in z.battle.intel.values():
                it.spotted.clear()
                it.last_known.clear()
        back = False
        for _ in range(120):
            await sim.tick()
            if sent.ai["task"] in ("return", "guard"):
                back = True
            if sent.ai["task"] == "guard" and sent.pos == sent.ai["home"]:
                break
        assert back, ("交戰結束 → 回家", sent.ai)
        assert sent.pos == sent.ai["home"], (sent.pos, sent.ai)
        done()
    asyncio.run(go())


def test_player_march_unaffected():
    async def go():
        sim, a = await setup()
        await sim.tick()
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        for _ in range(60):
            await sim.tick()
            if a.action.type == "hold":
                break
        assert a.pos == "C3", a.pos
        c = zmod.squad_center(a)
        assert c is not None
        done()
    asyncio.run(go())


def test_destroyed_rebuilds_without_pool():
    async def go():
        sim, a = await setup()
        await sim.tick()
        s = allies.ally_squads(sim.world)[0]
        for m in s.members:
            m.hp = 0
        pool = reserve.remaining(sim.world)
        await sim.tick()
        assert s.status == "destroyed"
        sim.world.tick += allies.REBUILD_EVERY
        await sim.tick()
        new = sim.world.squads[s.id]
        assert new.status == "active" and len(new.alive_members()) == 4, "過一陣子在據點重新編成"
        assert reserve.remaining(sim.world) == pool, "傷亡不進／不取預備兵力池"
        done()
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("allies_test OK")


if __name__ == "__main__":
    main()
