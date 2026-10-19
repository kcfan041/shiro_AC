"""C3 實測（需要 LLM）：小隊在 C3 遭遇守軍，隊長自行選戰術，指揮官中途下令包抄與撤退。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import roster
from engine.models import Enemy
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/combat_play_test.json"


def show(sim, sq):
    for item in sim.drain():
        if "text" in item:
            print(f"  [T{sim.world.tick} {item.get('name', '系統')}] {item['text']}")
    print(f"    ── 行動：{sim._action_text(sq)}｜{sq.pos}｜可戰 {len(sq.alive_members())}/{len(sq.members)}"
          f"｜倒地 {sum(1 for m in sq.members if m.down)}｜敵 {sum(e.strength for e in sim.world.enemies.values() if e.alive and e.pos == sq.pos)}")


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        card = json.load(f)
    sim = Simulation(LLMClient(), save_path=PATH, rng=random.Random(5))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "測試")
    sq, _ = await sim.deploy_squad(card, "1")
    sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999, cooldown=999,
                                   tag="garrison", home="C3")
    sq.pos = "C3"
    roster.sync_world(sim.world)
    sim.save()
    for _ in range(4):
        await sim.tick()
        show(sim, sq)
    print("\n" + sim.situation(sq).split("戰術狀況")[1].split("近期經歷")[0] if "戰術狀況" in sim.situation(sq) else "")
    if sim.in_engagement(sq):
        print("\n指揮官：從右邊包抄他們")
        r = await sim.issue_order("alpha", "從右邊包抄他們")
        print(f"  → {r['action']} {r['target']}｜{r['outcome']}")
        show(sim, sq)
    for _ in range(8):
        await sim.tick()
        show(sim, sq)
        if not sim.in_engagement(sq):
            break
    if sim.in_engagement(sq):
        print("\n指揮官：撤退，回 B3")
        r = await sim.issue_order("alpha", "撤退，回 B3")
        print(f"  → {r['action']} {r['target']}｜{r['outcome']}")
        show(sim, sq)
        for _ in range(20):
            await sim.tick()
            show(sim, sq)
            if sq.pos != "C3" or sq.status != "active":
                break
    await sim.llm.close() if hasattr(sim.llm, "close") else None


if __name__ == "__main__":
    asyncio.run(main())
