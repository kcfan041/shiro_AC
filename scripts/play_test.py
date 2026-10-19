import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/play_test.json"


def show(sim):
    for item in sim.drain():
        if item["type"] == "mission":
            m = item["mission"]
            print(f"  📜 [{item['event']}] {m['title']} {item.get('reason', '')}")
        elif "text" in item:
            print(f"  [T{sim.world.tick} {item.get('name', '系統')}] {item['text']}")
        else:
            print(f"  （{item['type']}）")


async def order(sim, text):
    print(f"\n指揮官（T{sim.world.tick}）：{text}")
    await sim.issue_order("alpha", text)
    show(sim)


async def ticks(sim, n, stop=None):
    for _ in range(n):
        await sim.tick()
        show(sim)
        if stop and stop():
            return


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        card = json.load(f)
    llm = LLMClient()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(11))
    sim.ensure_commander("1", "測試")
    await sim.deploy_squad(card, "1")
    sq = sim.resolve("alpha")
    await ticks(sim, 1)
    print("\n任務：\n" + sim.missions_text("1"))

    await order(sim, "去 D3 中繼塔看看，小心點")
    await ticks(sim, 45, stop=lambda: sq.pos == "D3" and sq.action.type == "hold" and not sq.contact)
    print("\n任務：\n" + sim.missions_text("1"))

    sq.ammo = 15
    sq.members[1].hp = 35
    sq.members[3].hp = 0
    await order(sim, "回 A3 集結點整補")
    await ticks(sim, 45, stop=lambda: sq.pos == "A3" and sq.rest_ticks > 7)
    print(f"\n隊員：{[(m.name, m.hp, m.trait) for m in sq.members]} 彈藥 {sq.ammo}")

    sq.next_chatter = sim.world.tick + 1
    await ticks(sim, 2)
    await llm.close()
    os.remove(PATH)


if __name__ == "__main__":
    asyncio.run(main())
