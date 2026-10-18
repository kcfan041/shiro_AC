import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.models import World
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/multi_test.json"


class FakeLLM(LLMClient):
    async def chat_json(self, system, user, temperature=0.8):
        if "副官" in system:
            return {"summary": "測試", "suggestions": [
                {"label": "A", "squad": "alpha", "order": "固守"},
                {"label": "B", "squad": "bravo", "order": "固守"},
            ]}
        return {"action": "move", "target": "B1", "report": "收到"}


def card(name):
    with open(f"./setting example/squads/{name}.json", "r", encoding="utf8") as f:
        return json.load(f)


async def main():
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(FakeLLM(), save_path=PATH, rng=random.Random(1))
    sim.ensure_commander("100", "甲")
    sim.ensure_commander("200", "乙")
    a, created = await sim.deploy_squad(card("alpha"), "100")
    assert created
    b, _ = await sim.deploy_squad(card("bravo"), "200")

    await sim.issue_order("alpha", "去 B1")
    for _ in range(4):
        await sim.tick()
    pos_before = sim.world.squads["alpha"].pos
    tick_mem = len(sim.world.squads["alpha"].memory)

    again, created = await sim.deploy_squad(card("alpha"), "100")
    assert not created, "重複部署不應重置"
    assert again.pos == pos_before and len(again.memory) == tick_mem, "狀態被覆蓋"

    c1 = sim.world.commanders["100"]
    c2 = sim.world.commanders["200"]
    assert set(c1.intel["squads"]) == {"alpha"}, c1.intel["squads"].keys()
    assert set(c2.intel["squads"]) == {"bravo"}, c2.intel["squads"].keys()
    assert "bravo" not in sim.intel_text("100") and "alpha" not in sim.intel_text("200")

    adj1 = await sim.refresh_adjutant("100")
    assert all(s["squad"] == "alpha" for s in adj1["suggestions"]), adj1
    adj2 = await sim.refresh_adjutant("200")
    assert all(s["squad"] == "bravo" for s in adj2["suggestions"]), adj2

    fresh, created = await sim.deploy_squad(card("alpha"), "100", reset=True)
    assert created and fresh.pos == "A1" and fresh.memory == []

    reloaded = World.from_dict(json.load(open(PATH, encoding="utf8")))
    assert reloaded.squads["alpha"].commander == "100"
    assert set(reloaded.commanders) == {"100", "200"}

    roster = sim.reset()
    assert sorted(roster) == [("alpha", "100"), ("bravo", "200")]
    assert sim.world.commanders["100"].intel["squads"] == {}

    os.remove(PATH)
    print("multi_test OK")


if __name__ == "__main__":
    asyncio.run(main())
