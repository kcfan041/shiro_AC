"""連續地圖（zone 模式）：真實行軍跨格、跨格接敵與射擊、敵軍進出活動區域、兩位玩家區域合併、撤退、偵察、
任務抵達。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, zone as zmod
from engine.models import Action, Enemy
from engine.sim import Simulation
from engine.tactical import worldmap
from lib.llm import LLMClient

PATH = "./data/zone_test.json"


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Kilo"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def setup(seed=3, two=False):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.tac_steps = 1
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("100", "甲")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    b = None
    if two:
        sim.ensure_commander("200", "乙")
        b, _ = await sim.deploy_squad(card("bravo"), "200")
    roster.sync_world(sim.world)
    return sim, a, b


def done():
    if os.path.exists(PATH):
        os.remove(PATH)


def test_march_crosses_cells_continuously():
    async def go():
        sim, a, _ = await setup()
        await sim.tick()
        assert all(m.gpos for m in a.members), "部署後有全圖座標"
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        seen_cells, jumps = [], 0
        last = {m.name: tuple(m.gpos) for m in a.members}
        for _ in range(60):
            await sim.tick()
            for m in a.alive_members():
                p = tuple(m.gpos)
                if max(abs(p[0] - last[m.name][0]), abs(p[1] - last[m.name][1])) > 8:
                    jumps += 1
                last[m.name] = p
            if not seen_cells or seen_cells[-1] != a.pos:
                seen_cells.append(a.pos)
            if a.action.type == "hold":
                break
        assert jumps == 0, "沒有瞬移"
        assert seen_cells[:3] == ["A3", "B3", "C3"], seen_cells
        assert a.pos == "C3" and a.action.type == "hold", (a.pos, a.action.type)
        assert any(x["kind"] == "arrive" for x in a.experiences)
        done()
    asyncio.run(go())


def test_cross_cell_contact_and_fire():
    async def go():
        sim, a, _ = await setup()
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999, cooldown=999,
                                       tag="garrison", home="C3")
        sim.world.enemies["x"].tcell = "C3"
        oy = worldmap.cell_origin("C3")[1]
        ox = worldmap.cell_origin("C3")[0]
        m0 = worldmap.global_map()
        spots = [zmod.nearest_standable(m0, (ox + 10 + 2 * i, oy + 2, 0)) for i in range(3)]
        roster.sync_world(sim.world)
        for sd, sp in zip(sim.world.enemies["x"].soldiers, spots):
            sd["gpos"] = list(sp)
            sd["facing"] = [0, -1]
        by = worldmap.cell_origin("B3")[1]
        for i, m in enumerate(a.members):
            m.gpos = list(zmod.nearest_standable(m0, (ox + 9 + 2 * i, by + 20, 0)))
            m.facing = [0, 1]
        cross = 0
        for _ in range(16):
            await sim.tick()
            z = sim.engagements.get("B3")
            b = z.battle
            for s in b.last_shots:
                f, t = worldmap.cell_of(s["from"][0] / 2, s["from"][1] / 2), worldmap.cell_of(s["to"][0] / 2, s["to"][1] / 2)
                if f != t:
                    cross += 1
        assert a.pos == "B3", "小隊還在 B3"
        assert sim.in_engagement(a), "隔壁格的敵人也算接敵"
        assert cross > 0, "有跨格射擊"
        assert any(x["kind"] == "contact" for x in a.experiences)
        done()
    asyncio.run(go())


def test_enemy_enters_and_leaves_zone():
    async def go():
        sim, a, _ = await setup()
        sim.enemy_ai_enabled = True
        await sim.tick()
        e = Enemy(id="p9", pos="C3", strength=2, route=["C3", "B3", "C3"], move_every=1, cooldown=1, tag="patrol",
                  home="C3")
        sim.world.enemies["p9"] = e
        roster.sync_world(sim.world)
        z = sim.zones[0]
        assert "C3" not in z.cells and "B3" in z.cells, z.cells
        entered = left = False
        for _ in range(12):
            await sim.tick()
            z = sim.zones[0]
            uids = [u for u, o in z.owner.items() if o[1] == "p9"]
            if e.pos in z.cells:
                entered = entered or bool(uids)
                gs = [sd["gpos"] for sd in e.soldiers if sd.get("gpos")]
                assert not gs or any(worldmap.cell_of(g[0], g[1]) in z.cells for g in gs)
            else:
                if entered:
                    left = True
                assert not uids, "在活動區域外 → 不在戰術層"
        assert entered, "巡邏隊走進活動區域（戰術層）"
        assert left, "走出活動區域"
        done()
    asyncio.run(go())


def test_two_players_zones_merge_and_split():
    async def go():
        sim, a, b = await setup(two=True)
        a.pos, b.pos = "A1", "A5"
        for m in a.members + b.members:
            m.gpos = None
        await sim.tick()
        assert len(sim.zones) == 2, [z.cells for z in sim.zones]
        b.pos = "A3"
        for m in b.members:
            m.gpos = None
        await sim.tick()
        assert len(sim.zones) == 1, "相連 → 同一場模擬"
        z = sim.zones[0]
        assert {u for u, o in z.owner.items() if o[1] == a.id} and {u for u, o in z.owner.items() if o[1] == b.id}
        done()
    asyncio.run(go())


def test_retreat_moves_to_adjacent_cell():
    async def go():
        sim, a, _ = await setup()
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        a.action = Action(type="retreat", target="A3", ordered=True)
        for _ in range(40):
            await sim.tick()
            if a.action.type == "hold":
                break
        assert a.pos == "A3" and a.action.type == "hold", (a.pos, a.action.type)
        done()
    asyncio.run(go())


def test_scout_stops_short_and_observes():
    async def go():
        sim, a, _ = await setup()
        sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=2, route=["C3"], move_every=999, cooldown=999,
                                       tag="garrison", home="C3")
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        roster.sync_world(sim.world)
        await sim.tick()
        a.action = Action(type="scout", target="C3", duration=2, ordered=True)
        for _ in range(30):
            await sim.tick()
            if a.action.type != "scout":
                break
        assert a.pos == "B3", "偵察不進入目標格"
        assert any(x["kind"] in ("scout", "contact") for x in a.experiences)
        done()
    asyncio.run(go())


def test_rout_at_world_edge_no_exit():
    """連續地圖：在世界邊緣（A3，y 小）士氣崩潰 → 沒有人「撤出戰區」消失；人都還在戰術層、看得到。"""
    async def go():
        sim, a, _ = await setup()
        a.pos = "A3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        m0 = worldmap.global_map()
        ox, oy = worldmap.cell_origin("A3")
        for i, m in enumerate(a.members):
            m.gpos = list(zmod.nearest_standable(m0, (ox + 8 + 2 * i, oy + 2, 0)))
        sim.world.enemies["x"] = Enemy(id="x", pos="B3", strength=3, route=["B3"], move_every=999, cooldown=999,
                                       tag="garrison", home="B3")
        roster.sync_world(sim.world)
        a.morale = 5
        for _ in range(10):
            await sim.tick()
            z = sim.engagements.get(a.pos)
            a.morale = 5
            if z is not None:
                z.battle.morale[z.side_of(a.id)] = 5.0
                mine = [u for u, o in z.owner.items() if o[1] == a.id]
                assert not any(z.battle.units[u].exited for u in mine), "沒有人撤出戰區"
        alive = [m for m in a.members if not m.lost]
        z = sim.engagements.get(a.pos)
        ids = {o[2].name for u, o in z.owner.items() if o[1] == a.id}
        assert all(m.name in ids for m in alive), "活著的人都還在戰術層"
        done()
    asyncio.run(go())


def test_stray_member_does_not_pin_squad_cell():
    """一名隊員卡在遠處（例如舊存檔撤出到世界邊緣）：小隊位置看主體，行軍照常前進到下一格。"""
    async def go():
        sim, a, _ = await setup()
        await sim.tick()
        stray = a.members[1]
        stray.gpos = [75 + 1, 0, 0]
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        cells = []
        for _ in range(60):
            await sim.tick()
            if not cells or cells[-1] != a.pos:
                cells.append(a.pos)
            if "C3" in cells:
                break
        assert "B3" in cells and "C3" in cells, cells
        done()
    asyncio.run(go())


def test_perf_step():
    async def go():
        sim, a, b = await setup(two=True)
        sim.enemy_ai_enabled = True
        b.pos = "B4"
        for m in b.members:
            m.gpos = None
        for i, c in enumerate(("C3", "C4", "B2", "D3")):
            sim.world.enemies[f"x{i}"] = Enemy(id=f"x{i}", pos=c, strength=3, route=[c], move_every=999,
                                              cooldown=999, tag="garrison", home=c)
        roster.sync_world(sim.world)
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        times = []
        for _ in range(12):
            t = time.time()
            await sim.tick()
            times.append(time.time() - t)
        print(f"  zone 每 tick 平均 {sum(times) / len(times):.2f}s，最慢 {max(times):.2f}s")
        assert sum(times) / len(times) < 1.0
        done()
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("zone_test OK")


if __name__ == "__main__":
    main()
