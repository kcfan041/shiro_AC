"""C1：永久戰術地圖與個體化。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import enemy_ai, roster, scenario
from engine.models import Enemy, World
from engine.sim import Simulation
from engine.tactical import mapgen, worldmap
from lib.llm import LLMClient

PATH = "./data/tworld_test.json"


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "指揮官剛剛在無線電說" in user:
            return {"kind": "order", "confidence": "high", "action": "move", "target": "C3", "report": "ok"}
        if "新兵" in system:
            return {"name": "N-77", "trait": "x"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


def test_maps_deterministic_and_connected():
    worldmap.clear_cache()
    first = {c: len(worldmap.tmap(c).boxes) for c in scenario.CELLS}
    worldmap.clear_cache()
    again = {c: len(worldmap.tmap(c).boxes) for c in scenario.CELLS}
    assert first == again, "同一格每次生成相同"
    for c in scenario.CELLS:
        m = worldmap.tmap(c)
        assert (m.w, m.h) == (mapgen.SIZE, mapgen.SIZE)
        assert len(mapgen._components(m)) == 1, f"{c} 地面不連通"
        for side in "NSEW":
            assert len(worldmap.edge_nodes(m, side)) >= 8, f"{c} {side} 邊可進場位置不足"
    assert worldmap.template(scenario.BASE) == "base"
    assert worldmap.template(scenario.MISSION_CELLS["hold"]) == "tower"
    assert all(worldmap.template(b).startswith("enemy_base") for b in scenario.ENEMY_BASES)
    tower = worldmap.tmap(scenario.MISSION_CELLS["hold"])
    assert any(tower.standable((x, y, 1)) for x in range(25) for y in range(25)), "中繼塔有二樓"


def test_entry_side():
    assert worldmap.entry_side("C3", "D3") == "S", "往北走 → 從南邊進場"
    assert worldmap.entry_side("D3", "C3") == "N"
    assert worldmap.entry_side("C2", "C3") == "W"
    assert worldmap.entry_side("C4", "C3") == "E"
    assert worldmap.entry_side(None, "C3") is None


def test_new_world_individuals():
    w = scenario.new_world()
    for e in w.enemies.values():
        assert len(e.soldiers) == e.strength, e.id
        assert all(sd["tpos"] for sd in e.soldiers), "開局全部有位置"
        assert len({tuple(sd["tpos"]) for sd in e.soldiers}) == len(e.soldiers), "不重疊"
    g = next(e for e in w.enemies.values() if e.tag == "garrison")
    m = worldmap.tmap(g.pos)
    good = 0
    for sd in g.soldiers:
        x, y, l = sd["tpos"]
        tags = {b.tag for b in m._boxes_in_cell(x, y)}
        good += l == 1 or bool(tags & {"window", "lowwall"})
    assert good >= 1, "守軍至少有人布在窗後／矮牆後／高處"


def test_squad_weapons_and_placement():
    async def go():
        if os.path.exists(PATH):
            os.remove(PATH)
        sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(1))
        sim.enemy_ai_enabled = False
        sim.ensure_commander("1", "甲")
        sq, _ = await sim.deploy_squad(card("alpha"), "1")
        assert sorted(m.weapon for m in sq.members) == sorted(roster.balanced_weapons(4))
        assert all(m.tpos for m in sq.members), "部署後放上戰術地圖"
        start = {m.name: tuple(m.tpos) for m in sq.members}
        sq.pos = "B3"
        sim.save()
        for m in sq.members:
            assert m.tpos[1] <= 2, f"從 A3 往北進 B3 → 南邊進場：{m.tpos}"
        sim.save()
        assert {m.name: tuple(m.tpos) for m in sq.members} != start
        snap = {m.name: tuple(m.tpos) for m in sq.members}
        sim.save()
        assert {m.name: tuple(m.tpos) for m in sq.members} == snap, "沒換格不會重新放置"
        sq.members[1].hp = 0
        sim.save()
        assert sq.members[1].tpos is None
        w = World.from_dict(json.load(open(PATH, encoding="utf8")))
        assert w.squads[sq.id].members[0].tpos == sq.members[0].tpos, "存讀檔保留位置"
        assert w.squads[sq.id].members[0].weapon == sq.members[0].weapon
        os.remove(PATH)
    asyncio.run(go())


def test_enemy_merge_split_keep_individuals():
    w = World(scenario=scenario.SCENARIO_ID)
    base = scenario.ENEMY_BASES[0]
    g = Enemy(id="g", pos=base, strength=6, route=[base], move_every=999, cooldown=999, tag="garrison", home=base)
    w.enemies["g"] = g
    roster.sync_world(w)
    ids = {sd["id"] for sd in g.soldiers}
    new = enemy_ai._split(w, g, 3, "m", "mobile")
    assert len(g.soldiers) == 3 and len(new.soldiers) == 3 and g.strength == 3 and new.strength == 3
    assert {sd["id"] for sd in g.soldiers + new.soldiers} == ids, "分兵保留身分"
    new.soldiers[0]["damage"] = {"lleg": True}
    enemy_ai._merge(new, g)
    assert not new.alive and g.strength == 6 and len(g.soldiers) == 6
    assert any(sd.get("damage") == {"lleg": True} for sd in g.soldiers), "合併保留傷勢"
    roster.sync_world(w)
    assert all(sd["tpos"] for sd in g.soldiers)


def test_enemy_move_replaced_on_edge():
    w = World(scenario=scenario.SCENARIO_ID)
    e = Enemy(id="p", pos="C3", strength=3, route=["C3", "D3"], move_every=1, cooldown=1, tag="patrol")
    w.enemies["p"] = e
    roster.sync_world(w)
    e.pos = "D3"
    roster.sync_world(w)
    assert all(sd["tpos"][1] <= 2 for sd in e.soldiers), "敵兵從南邊進入 D3"


def test_strength_sync():
    w = World(scenario=scenario.SCENARIO_ID)
    e = Enemy(id="x", pos="C3", strength=2, route=["C3"], move_every=999, cooldown=999)
    w.enemies["x"] = e
    roster.sync_world(w)
    keep = e.soldiers[0]["id"]
    e.strength = 4
    roster.sync_world(w)
    assert len(e.soldiers) == 4 and e.soldiers[0]["id"] == keep
    e.strength = 1
    roster.sync_world(w)
    assert len(e.soldiers) == 1 and e.soldiers[0]["id"] == keep


def test_full_sim_runs():
    async def go():
        if os.path.exists(PATH):
            os.remove(PATH)
        sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(3))
        sim.ensure_commander("1", "甲")
        await sim.deploy_squad(card("alpha"), "1")
        await sim.issue_order("alpha", "go")
        for _ in range(80):
            await sim.tick()
        for e in sim.world.enemies.values():
            if e.alive:
                assert len([sd for sd in e.soldiers if not sd.get("down")]) == e.strength
                assert all(sd["tpos"] for sd in e.soldiers)
        for cell in scenario.CELLS:
            occ = []
            for s in sim.world.squads.values():
                if s.pos == cell and s.status == "active":
                    occ += [tuple(m.tpos) for m in s.members if not m.lost]
            for e in sim.world.enemies.values():
                if e.pos == cell and e.alive:
                    occ += [tuple(sd["tpos"]) for sd in e.soldiers]
            assert len(occ) == len(set(occ)), f"{cell} 有個體重疊"
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("tworld_test OK")


if __name__ == "__main__":
    main()
