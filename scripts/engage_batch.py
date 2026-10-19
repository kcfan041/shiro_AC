"""C4 批次：4 人小隊在 C3 遭遇不同規模／類型的敵軍，小隊固定行動，統計結果。不需要 LLM。

用法：python scripts/engage_batch.py [每組次數，預設 12]
"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster
from engine.models import Action, Enemy
from engine.sim import Simulation
from scripts.engage_test import PATH, Quiet, card

TICKS = 40


async def one(seed, foes, tag, action, flank=None):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    e = Enemy(id="x", pos="C3", strength=foes, route=["C3"], move_every=999, cooldown=999, tag=tag, home="C3")
    sim.world.enemies["x"] = e
    sq.pos = "C3"
    roster.sync_world(sim.world)
    sim.save()
    kinds = set()
    for _ in range(TICKS):
        if sim.in_engagement(sq) and sq.action.type != action and action != "hold":
            sq.action = Action(type=action, flank=flank)
        await sim.tick()
        eng = sim.engagements.get("C3")
        if eng and "x" in eng.commander.current:
            kinds.add(eng.commander.current["x"][0].kind)
        if sq.status != "active" or not e.alive or e.pos != "C3" or sq.pos != "C3":
            break
    lost = sum(1 for m in sq.members if m.lost)
    down = sum(1 for m in sq.members if m.down and not m.lost)
    if sq.status != "active":
        res = "我方全滅"
    elif not e.alive:
        res = "敵全滅"
    elif e.pos != "C3":
        res = "敵撤出"
    elif sq.pos != "C3":
        res = "我方撤出"
    else:
        res = "未分勝負"
    return res, lost, down, foes - e.strength if e.alive else foes, kinds


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rows = []
    for foes, tag in ((2, "mobile"), (3, "garrison"), (3, "mobile"), (5, "mobile")):
        for action, flank in (("hold", None), ("supp_advance", None), ("flank", "left"), ("assault", None)):
            stats = {}
            lost = down = kills = 0
            kinds = set()
            for s in range(n):
                r, l, d, k, kk = await one(1000 + s, foes, tag, action, flank)
                stats[r] = stats.get(r, 0) + 1
                lost += l
                down += d
                kills += k
                kinds |= kk
            rows.append(f"敵 {foes}（{tag:8}）× 我 {action:12}｜"
                        + " ".join(f"{k}{v}" for k, v in sorted(stats.items()))
                        + f"｜我陣亡 {lost / n:.1f} 倒地 {down / n:.1f}｜擊倒敵 {kills / n:.1f}"
                        + f"｜敵意圖 {','.join(sorted(kinds))}")
            print(rows[-1], flush=True)
    if os.path.exists(PATH):
        os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
