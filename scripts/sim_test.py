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


def show(sim: Simulation):
    for item in sim.drain():
        tag = item.get("name", "系統")
        print(f"  [{tag}] {item['text']}")


async def main():
    load_dotenv()
    with open("./setting example/squads/alpha.json", "r", encoding="utf8") as f:
        card = json.load(f)
    llm = LLMClient()
    sim = Simulation(llm, save_path="./data/sim_test.json", rng=random.Random(7))
    sim.reset(keep_squads=False)
    sim.world.commanders.clear()
    sim.world.squads.clear()
    sim.ensure_commander("1", "測試指揮官")
    await sim.deploy_squad(card, "1")

    print("== 下令：前往 B2 ==")
    await sim.issue_order("alpha", "往北移動到中央十字路口 B2，小心前進。")
    show(sim)

    for _ in range(4):
        await sim.tick()
        show(sim)
    sq = sim.resolve("alpha")
    print(f"T{sim.world.tick} 位置 {sq.pos} 行動 {sq.action.type}")

    print("== 切斷通訊 ==")
    await sim.set_link("alpha", False)
    show(sim)
    for _ in range(14):
        await sim.tick()
        show(sim)
    sq = sim.resolve("alpha")
    print(f"T{sim.world.tick} 位置 {sq.pos} 行動 {sq.action.type} 離線事件 {len(sq.offline_events)}")

    print("== 恢復通訊 ==")
    await sim.set_link("alpha", True)
    show(sim)

    print("== 副官 ==")
    adj = await sim.refresh_adjutant("1")
    print(json.dumps(adj, ensure_ascii=False, indent=2))
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
