"""需要 LLM：失聯友軍殘部求救、被救出後自成小隊（臨時隊長的白紙人格此時寫好）。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import missions, persona
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/remnant_play_test.json"


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(LLMClient(), save_path=PATH, rng=random.Random(12))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    c = sim.ensure_commander("1", "測試")
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        sq, _ = await sim.deploy_squad(json.load(f), "1")
    sim._start_mission(c, missions._side("rescue", "C4", random.Random(3)), "side")
    m = c.missions[-1]
    r = sim.world.remnants[m["remnant"]]
    print("殘部：", [(x.name, persona.traits_text(x)) for x in r.members], "臨時隊長", r.leader)
    shown = 0
    for _ in range(8):
        await sim.tick()
        for e in c.events[shown:]:
            print(" ", e)
        shown = len(c.events)
        if r.status != "holding":
            break
    print("\n— 指揮官派 Alpha 抵達並清空敵軍 —")
    for eid in m["spawned"]:
        sim.world.enemies[eid].alive = False
    sq.pos = r.pos
    await sim.tick()
    for e in c.events[shown:]:
        print(" ", e)
    for s in sim.world.squads.values():
        if s.id == sq.id:
            continue
        lead = s.members[0]
        print(f"\n新小隊 {s.callsign}：隊長 {lead.name}")
        print("  個性：", lead.personality)
        print("  說話：", lead.speech)
        print("  信念：", [b["text"] for b in s.beliefs])
        print("  隊員：", [(x.name, persona.describe_short(x)) for x in s.members[1:]])
    print("任務：", c.history[-1])
    os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
