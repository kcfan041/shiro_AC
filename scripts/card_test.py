import asyncio
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import obedience
from engine.sim import Simulation
from lib.llm import LLMClient

ORDER = sys.argv[1] if len(sys.argv) > 1 else "直接衝進中央十字路口 B2，正面攻擊可能在那裡的敵軍。"


async def main():
    load_dotenv()
    llm = LLMClient()
    sim = Simulation(llm, save_path="./data/card_test.json", rng=random.Random(3))
    sim.reset(keep_squads=False)
    sim.ensure_commander("1", "測試指揮官")
    for path in sorted(glob.glob("./setting example/squads/*.json")):
        with open(path, "r", encoding="utf8") as f:
            await sim.deploy_squad(json.load(f), "1")
    print(f"命令：{ORDER}\n")
    for sid, squad in sim.world.squads.items():
        await sim.issue_order(sid, ORDER)
        for item in sim.drain():
            print(f"[{item.get('name', '系統')}]（服從 {obedience.score(squad)}，行動 {squad.action.type} {squad.action.target or ''}）")
            print(f"  {item['text']}\n")
    await llm.close()
    os.remove("./data/card_test.json")


if __name__ == "__main__":
    asyncio.run(main())
