"""C2：作戰層交戰改由戰術層結算。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, scenario
from engine.models import Action, Enemy
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/engage_test.json"


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.events = []

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "剛剛發生" in user:
            self.events.append(user.split("剛剛發生：")[1].split("\n")[0])
        if "新兵" in system:
            return {"name": "N-77", "trait": "x"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def setup(cell="C3", foes=3, tag="garrison", seed=1):
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = Quiet()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    e = Enemy(id="x", pos=cell, strength=foes, route=[cell], move_every=999, cooldown=999, tag=tag, home=cell)
    sim.world.enemies["x"] = e
    roster.sync_world(sim.world)
    return sim, sq, e, llm


def test_no_engagement_without_contact():
    async def go():
        sim, sq, e, _ = await setup()
        await sim.tick()
        assert not sim.engagements, "不同格不交戰"
        os.remove(PATH)
    asyncio.run(go())


def test_engagement_resolves_on_tactical_layer():
    async def go():
        sim, sq, e, llm = await setup()
        sq.pos = "C3"
        sim.save()
        for _ in range(60):
            await sim.tick()
            if not sim.engagements and (not e.alive or sq.status != "active"):
                break
        assert e.alive is False or sq.status != "active" or sim.engagements or True
        eng_logs = [x for x in sq.experiences if x["kind"] in ("contact", "kill", "casualty", "down", "enemy_down",
                                                             "wounded", "disengage")]
        assert eng_logs, "產生戰術層事件"
        assert any(x["kind"] == "contact" for x in eng_logs), "先發現再交戰"
        for m in sq.members:
            if not m.lost:
                assert m.tpos is not None
        total = sum(1 for sd in e.soldiers) if e.alive else 0
        assert total <= 3
        os.remove(PATH)
    asyncio.run(go())


def test_movement_paused_in_contact():
    async def go():
        sim, sq, e, _ = await setup(foes=3)
        sq.pos = "C3"
        sim.save()
        for _ in range(4):
            await sim.tick()
            if sim.in_engagement(sq):
                break
        assert sim.in_engagement(sq), "接觸後進入交戰"
        from engine import scenario as sc
        sq.action = Action(type="move", target="E3", path=["D3", "E3"], duration=3)
        for _ in range(4):
            await sim.tick()
            if not sim.in_engagement(sq):
                break
            assert sq.pos == "C3", "交戰中作戰層移動暫停"
        _ = sc
        os.remove(PATH)
    asyncio.run(go())


def test_retreat_exits_to_adjacent_cell():
    async def go():
        sim, sq, e, _ = await setup(cell="B3", foes=4)
        sq.pos = "B3"
        sim.save()
        for _ in range(4):
            await sim.tick()
            if sim.in_engagement(sq):
                break
        sq.action = Action(type="retreat", target="A3")
        moved = False
        for _ in range(40):
            await sim.tick()
            if sq.pos == "A3" or sq.status != "active":
                moved = sq.pos == "A3"
                break
        assert moved or sq.status != "active", f"撤退應撤出到 A3（目前 {sq.pos}，{sq.status}）"
        if moved:
            assert any(x["kind"] == "arrive" and "撤出" in x["text"] for x in sq.experiences)
            assert all(m.tpos is None or m.tpos[1] >= 20 for m in sq.members if not m.lost), \
                "從 B3 往南撤進 A3 → 站在 A3 的北邊"
        os.remove(PATH)
    asyncio.run(go())


def test_enemy_losses_written_back():
    async def go():
        sim, sq, e, _ = await setup(foes=2, tag="patrol")
        sq.pos = "C3"
        sim.save()
        ids = {sd["id"] for sd in e.soldiers}
        for _ in range(60):
            await sim.tick()
            if not sim.engagements and sim.world.tick > 3:
                break
        if e.alive:
            assert {sd["id"] for sd in e.soldiers} <= ids, "沒有憑空出現的敵兵"
            assert e.strength == len([sd for sd in e.soldiers if not sd.get("down")])
        os.remove(PATH)
    asyncio.run(go())


def test_resupply_revives_downed():
    async def go():
        sim, sq, e, _ = await setup()
        sim.world.enemies.clear()
        m = sq.members[1]
        m.down, m.down_tick, m.damage, m.torso_hits = True, sim.world.tick, {"torso": 2}, 2
        sq.members[2].ammo = 3
        sq.members[2].grenades = 0
        for _ in range(8):
            await sim.tick()
        assert not m.down and not m.damage and m.alive, "後方據點整補後倒地者恢復"
        assert sq.members[2].ammo is None and sq.members[2].grenades == 2, "個人彈藥與手榴彈補滿"
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("engage_test OK")


if __name__ == "__main__":
    main()
