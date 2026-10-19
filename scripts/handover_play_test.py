"""需要 LLM：隊長倒下 → 接手者第一次開口才寫個性，口氣不同。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import persona
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/handover_play_test.json"


def show(sim):
    for item in sim.drain():
        if "text" in item:
            print(f"  [{item.get('name', '系統')}] {item['text']}")


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(LLMClient(), save_path=PATH, rng=random.Random(8))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "測試")
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        sq, _ = await sim.deploy_squad(json.load(f), "1")
    sq.pos = "B3"
    await sim.tick()
    print("== 原隊長 ==")
    await sim.issue_order("alpha", "回報狀況")
    show(sim)
    nxt = sq.members[1]
    nxt.traits = {"aggr": 0.7, "caut": -0.4, "comp": 0.2, "bond": 0.5}
    nxt.persona = 0.6
    sq.members[0].down = True
    await sim.tick()
    show(sim)
    print(f"\n接手者 {nxt.name} {persona.traits_text(nxt)}")
    print("  個性：", nxt.personality)
    print("  說話：", nxt.speech)
    print("  信念：", [b["text"] for b in sq.beliefs])
    print("\n== 接手者 ==")
    await sim.issue_order("alpha", "回報狀況")
    show(sim)
    os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
