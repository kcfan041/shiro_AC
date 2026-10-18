import asyncio
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

PATH = "./data/reflect_test.json"


def show(sim):
    for item in sim.drain():
        if item["type"] in ("report", "system"):
            print(f"    [{item.get('name', '系統')}] {item['text']}")


def state(sq):
    print(f"  信念：\n{obedience.beliefs_text(sq)}\n  傾向：{obedience.tendency_text(sq)}\n"
          f"  服從基準 {obedience.score(sq)} / 危險 {obedience.score(sq, True)}")


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        card = json.load(f)
    llm = LLMClient()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(9))
    sim.world.enemies.clear()
    sim.ensure_commander("1", "測試")
    sq, _ = await sim.deploy_squad(card, "1")
    print("== 初始 ==")
    state(sq)

    for text in ["發動古代魔法", "幹幹叫", "立刻自爆"]:
        print(f"  指揮官：{text}")
        await sim.issue_order("alpha", text)
        show(sim)
    sq.members[2].hp = 0
    sim._want_reflect(sq, "隊員 R-15 在執行指揮官命令時陣亡")
    print("\n== 反思 ==")
    await sim._reflect(sq)
    state(sq)
    ref = sq.experiences[-1]
    print(f"  新增：{ref['added']}\n  移除：{ref['dropped']}\n  傾向變化：{ref['tendencies']}\n"
          f"  心聲：{ref['interp'].get('thought')}\n  長期記憶：{sq.long_memory}")

    print("\n== 反思後下危險命令 ==")
    print("  指揮官：直衝 C2，不用偵察")
    r = await sim.issue_order("alpha", "直衝 C2 中繼塔，不用偵察")
    show(sim)
    print(f"    risky={r['risky']} obedience={r['obedience']} outcome={r['outcome']}")

    print("\n== LLM 生成角色卡 ==")
    gen = await sim.generate_card("指揮部新編並派往前線的小隊")
    print(json.dumps(gen, ensure_ascii=False, indent=2))
    await llm.close()
    os.remove(PATH)


if __name__ == "__main__":
    asyncio.run(main())
