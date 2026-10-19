"""需要 LLM：從預備兵池組增援小隊，隊長的白紙人格由 LLM 依性格數字寫出。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import persona, reserve
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/reserve_play_test.json"


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(LLMClient(), save_path=PATH, rng=random.Random(7))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    c = sim.ensure_commander("1", "測試")
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        await sim.deploy_squad(json.load(f), "1")
    for i in range(2):
        c.reinforce_ready = 0
        sq = await sim._reinforce(c)
        if not sq:
            print("增援失敗")
            continue
        lead = sq.members[0]
        print(f"\n== {sq.callsign}（{sq.id}）預備兵力剩 {reserve.remaining(sim.world)} ==")
        print("隊長", lead.name, persona.traits_text(lead))
        print("  個性：", lead.personality)
        print("  說話：", lead.speech)
        print("  背景：", lead.background)
        print("  台詞：", lead.sample_lines)
        print("  信念：", [b["text"] for b in sq.beliefs])
        for m in sq.members[1:]:
            print("隊員", m.name, persona.traits_text(m), "→", persona.describe_short(m))
    await sim.llm.close() if hasattr(sim.llm, "close") else None
    os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
