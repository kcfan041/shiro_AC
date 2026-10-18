import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import missions, scenario
from engine.models import Action
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/mission_test.json"


class FakeLLM(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.n = 0

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        self.n += 1
        if "新兵" in system:
            return {"name": f"T-{self.n:02d}", "trait": "測試個性"}
        if "副官" in system:
            return {"summary": "s", "suggestions": []}
        return {"action": "keep", "report": None, "interp": "ok"}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def run(sim, n):
    for _ in range(n):
        await sim.tick()


async def main():
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(FakeLLM(), save_path=PATH, rng=random.Random(2))
    sim.world.enemies.clear()
    sim.enemy_ai_enabled = False
    c = sim.ensure_commander("1", "甲")
    RECON, HOLD, CLEAR = (missions.MAIN_LINE[i]["cell"] for i in range(3))
    SIDE, NEAR = "C2", "C1"

    d = card("alpha")
    d["start"] = "B2"
    sq, _ = await sim.deploy_squad(d, "1")
    assert sq.pos == "B2", "start 欄位"

    await run(sim, 1)
    assert c.missions and c.missions[0]["key"] == "m1_recon", c.missions
    starts = [x for x in sim.drain() if x["type"] == "mission"]
    assert starts and starts[0]["event"] == "start"

    sq.pos = RECON
    await run(sim, 1)
    assert any(h["title"] == "偵察中繼塔" and h["success"] for h in c.history), c.history
    await run(sim, 1)
    m2 = next(m for m in c.missions if m["line"] == "main")
    assert m2["key"] == "m2_hold" and m2["spawned"], m2
    for eid in m2["spawned"]:
        sim.world.enemies[eid].alive = False
    sq.pos = HOLD
    await run(sim, 20)
    assert any(h["title"] == "奪取並守住中繼塔" and h["success"] for h in c.history), c.history

    m3 = next(m for m in c.missions if m["line"] == "main")
    assert m3["key"] == "m3_clear"
    m3["deadline"] = sim.world.tick + 1
    await run(sim, 2)
    assert any(h["title"] == "攻擊敵方據點" and not h["success"] and h["reason"] == "超過時限" for h in c.history)
    m4 = next(m for m in c.missions if m["line"] == "main")
    assert m4["key"] == "m4_extract", "失敗後換下一個主線"

    print("== 補給 ==")
    sq.pos = scenario.BASE
    sq.action = Action(type="hold")
    sq.contact = None
    sq.ammo = 10
    sq.members[1].hp = 30
    sq.members[2].hp = 0
    before = len(sq.alive_members())
    await run(sim, 3)
    assert sim.rest_progress(sq) == "整補中 3/6", sim.rest_progress(sq)
    assert c.intel["squads"]["alpha"]["rest"] == "整補中 3/6", c.intel["squads"]["alpha"]
    await run(sim, 4)
    assert sim.rest_progress(sq) == "整補完成，待命中", sim.rest_progress(sq)
    assert sq.ammo == 100, sq.ammo
    assert sq.members[1].hp >= 90, sq.members[1].hp
    assert len(sq.alive_members()) == before + 1, "後方補充新兵"
    newbie = sq.members[-1]
    assert newbie.trait == "測試個性" and newbie.joined > 0
    print("  新兵：", newbie.name, newbie.trait)

    print("== 支線 ==")
    for kind in missions.SIDE_TEMPLATES:
        spec = missions._side(kind, SIDE, random.Random(1))
        sim._start_mission(c, spec, "side")
        m = c.missions[-1]
        if kind == "crate":
            sq.ammo = 20
            sq.pos = SIDE
            await run(sim, 1)
            assert sq.ammo >= 70 and m not in c.missions, "補給箱"
        elif kind == "rescue":
            sq.members[1].hp = 0
            size = len(sq.alive_members())
            sq.pos = SIDE
            await run(sim, 1)
            assert m not in c.missions and len(sq.alive_members()) == size + 1, "營救殘部併入"
        elif kind == "intercept":
            for eid in m["spawned"]:
                sim.world.enemies[eid].alive = False
            await run(sim, 1)
            assert m not in c.missions, "攔截"
        else:
            sq.pos = NEAR
            await run(sim, 1)
            sq.pos = SIDE
            await run(sim, 1)
            assert m not in c.missions, "訊號"
        sq.pos = scenario.BASE

    print("== 隨機支線自動出現 ==")
    c.next_side = sim.world.tick + 1
    await run(sim, 2)
    assert any(m["line"] == "side" for m in c.missions), c.missions

    print("== 全滅導致失敗 ==")
    for mem in sq.members:
        mem.hp = 0
    await run(sim, 2)
    assert not c.missions, c.missions
    assert c.history[-1]["reason"] in ("沒有可作戰的小隊",), c.history[-1]

    print("== wipe ==")
    sim.wipe()
    assert not sim.world.squads and not sim.world.commanders and os.path.exists(PATH + ".bak")
    os.remove(PATH + ".bak")

    os.remove(PATH)
    print("mission_test OK")


if __name__ == "__main__":
    asyncio.run(main())
