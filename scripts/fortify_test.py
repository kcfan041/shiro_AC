"""構工的下令入口（docs/new/terrain.md §8.3、§8.4）：口頭 fortify、戰術圖「陣地」標定點、自主構工（自由直接挖，
待命、自衛提議）、敵方守軍構工、隊長簡報的地形描述、陣地構築完成事件。不需要 LLM。"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, scenario, terrain_edits as te, zone as zmod
from engine.models import Action
from engine.sim import Simulation
from engine.tactical import timeline as tl
from scripts.zone_test import Quiet, card

PATH = "./data/fortify_test.json"


async def setup(seed=3, autonomy=None):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.tac_steps = 1
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("100", "甲")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    if autonomy:
        a.autonomy = autonomy
    roster.sync_world(sim.world)
    await sim.tick()
    return sim, a


async def walk_to(sim, a, cell):
    a.action = Action(type="move", target=cell, path=scenario.find_route(a.pos, cell, [], set()), duration=3,
                      ordered=True)
    for _ in range(80):
        await sim.tick()
        if a.action.type == "hold":
            break
    assert a.pos == cell and a.action.type == "hold", (a.pos, a.action.type)


def done():
    if os.path.exists(PATH):
        os.remove(PATH)


def zone_of(sim, a):
    return sim.engagements[a.pos]


def test_ordered_fortify_digs_and_reports():
    """下令構工：每人分到一個坑位（彼此至少隔 2 格），挖出來的坑記進地圖變更層；全部挖到最深 → 一次「陣地構築完成」，
    隊長簡報寫出在散兵坑裡。"""
    async def go():
        sim, a = await setup()
        await walk_to(sim, a, "B3")
        v0 = len(sim.world.terrain["ground"])
        a.action = Action(type="fortify", ordered=True)
        await sim.tick()
        z = zone_of(sim, a)
        intent = z.intents[f"s:{a.id}"]
        assert intent.kind == "hold" and intent.dig and intent.dig_spots, intent
        spots = list(intent.dig_spots.values())
        assert len(spots) == len([m for m in a.members if m.alive]) and all(
            max(abs(p[0] - q[0]), abs(p[1] - q[1])) >= 2 for i, p in enumerate(spots) for q in spots[i + 1:]), spots
        for _ in range(120):
            await sim.tick()
            if a.action.progress:
                break
        assert a.action.progress == 1 and a.action.type == "fortify"
        assert len(sim.world.terrain["ground"]) >= v0 + len(spots) - 1, sim.world.terrain["ground"]
        assert sum(1 for x in a.experiences if x["kind"] == "fortified") == 1
        for _ in range(5):
            await sim.tick()
        assert sum(1 for x in a.experiences if x["kind"] == "fortified") == 1, "完成事件只報一次"
        assert "散兵坑" in z.brief(a), z.brief(a)
        te.reset_all(sim.world)
        done()
    asyncio.run(go())


def test_mark_fortify_marches_then_digs():
    """戰術圖「陣地」標定點：在相鄰格標定 → 先行軍過去，到了在標定點附近挖；標定點畫在區域畫面上。"""
    async def go():
        sim, a = await setup()
        res = await sim.mark_order(a.id, "fortify", [12, 12], cell="B3")
        assert res is not None and a.action.type == "fortify" and a.action.mark_kind == "fortify", (res, a.action)
        node = zmod.Zone.mark_node(zone_of(sim, a), a)
        for _ in range(150):
            await sim.tick()
            if a.action.progress:
                break
        z = zone_of(sim, a)
        mine = z._mine(a)
        assert a.pos == "B3"
        near = [u for u in mine if max(abs(u.pos[0] - node[0]), abs(u.pos[1] - node[1])) <= zmod.FORT_ARRIVE + 4]
        assert len(near) == len(mine), [(u.pos, node) for u in mine]
        pits = [u for u in mine if tl.dig_ref(z.battle.map, *u.pos[:2]) - z.battle.map.ground.get(u.pos[:2], 0) >= 2]
        assert pits, "到了標定點附近有挖"
        from engine import views
        marks = [mk for zd in views.zone_view(sim, "100")["zones"] for mk in zd.get("marks", [])]
        assert any(mk["kind"] == "fortify" and mk["squad"] == a.id for mk in marks), marks
        te.reset_all(sim.world)
        done()
    asyncio.run(go())


def test_auto_fortify_free_and_proposal():
    """自主構工：不在交戰、固守 60 秒 → 自由直接構工（標自主）；待命提議（同一格只提一次），同意後照辦。在後方據點不挖。"""
    async def go():
        sim, a = await setup(autonomy="free")
        for _ in range(20):
            await sim.tick()
        assert a.action.type == "hold", "在後方據點不自主構工"
        await walk_to(sim, a, "B3")
        for _ in range(int(60 / sim.tick_seconds) + 2):
            await sim.tick()
        assert a.action.type == "fortify" and a.action.auto and not a.action.ordered, a.action
        te.reset_all(sim.world)
        done()

        sim, a = await setup(autonomy="standby")
        await walk_to(sim, a, "B3")
        for _ in range(int(60 / sim.tick_seconds) + 2):
            await sim.tick()
        assert a.action.type == "hold" and a.proposal and a.proposal["action"] == "fortify", (a.action, a.proposal)
        a.proposal = None
        for _ in range(int(120 / sim.tick_seconds)):
            await sim.tick()
        assert a.proposal is None, "同一格只提一次"
        sim.dig_proposed.clear()
        for _ in range(int(60 / sim.tick_seconds) + 2):
            await sim.tick()
        assert a.proposal and a.proposal["action"] == "fortify"
        r = await sim.accept_proposal(a.id)
        assert r and a.action.type == "fortify" and a.action.ordered
        te.reset_all(sim.world)
        done()
    asyncio.run(go())


def test_allowed_only_on_continuous_map():
    """可選行動：連續地圖才有 fortify（挖坑改地形要經地圖變更層）；舊的單格地圖沒有。"""
    async def go():
        sim, a = await setup()
        allowed, text = sim._allowed(a)
        assert "fortify" in allowed and "- fortify：" in text
        sim.zone_mode = False
        allowed, text = sim._allowed(a)
        assert "fortify" not in allowed
        done()
    asyncio.run(go())


def test_enemy_garrison_fortifies():
    """敵方守軍：連續地圖上沒發現敵人就構工，胸牆面向我方後方據點；舊的單格交戰照舊只固守。"""
    from engine.enemy_tactics import EnemyCommander
    from engine.models import Enemy

    class Stub:
        zone = True
    e = Enemy(id="g1", pos="D4", strength=3, route=[], tag="garrison")
    it, why = EnemyCommander(Stub())._choose(e, [object()], [], 99.0, "normal")
    assert it.kind == "hold" and it.dig and it.dig_face == zmod.cell_center(scenario.BASE), (it, why)
    Stub.zone = False
    it, _ = EnemyCommander(Stub())._choose(e, [object()], [], 99.0, "normal")
    assert it.kind == "hold" and not it.dig


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("fortify_test OK")


if __name__ == "__main__":
    main()
