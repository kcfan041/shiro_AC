"""指揮部支援批次：4 人小隊在 C3 對守軍，比較「無支援／煙幕＋推進／無人機＋壓制推進／空投＋固守」。
確認支援有效但不會一用就贏。不需要 LLM。

用法：python scripts/support_batch.py [每組次數，預設 12]
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


def _center(eng, side_prefix):
    us = [u for u in eng.battle.units.values() if u.alive and u.side.startswith(side_prefix)]
    if not us:
        return None
    return [sum(u.pos[0] for u in us) / len(us), sum(u.pos[1] for u in us) / len(us)]


async def one(seed, foes, plan):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    e = Enemy(id="x", pos="C3", strength=foes, route=["C3"], move_every=999, cooldown=999, tag="garrison", home="C3")
    sim.world.enemies["x"] = e
    sq.pos = "C3"
    roster.sync_world(sim.world)
    sim.save()
    action, support = plan
    used = 0
    for t in range(TICKS):
        if sim.in_engagement(sq) and sq.action.type != action and action != "hold":
            sq.action = Action(type=action)
        eng = sim.engagements.get("C3")
        if eng and eng.hot() and support and used < 3 and t % 6 == 3:
            me, foe = _center(eng, "blue"), None
            lk = eng.battle.intel["blue"].last_known
            if lk:
                ns = [n for n, _ in lk.values()]
                foe = [sum(n[0] for n in ns) / len(ns), sum(n[1] for n in ns) / len(ns)]
            if me:
                if support == "smoke" and foe:
                    pt = [(me[0] + foe[0]) / 2, (me[1] + foe[1]) / 2]
                elif support == "drone":
                    reds = [u for u in eng.battle.units.values() if u.alive and u.side == "red"]
                    rng = random.Random(seed * 7 + t)
                    pt = foe or ([sum(u.pos[0] for u in reds) / len(reds) + rng.uniform(-4, 4),
                                  sum(u.pos[1] for u in reds) / len(reds) + rng.uniform(-4, 4)] if reds else [12, 18])
                else:
                    pt = me
                ok, _ = await sim.call_support("1", support, "C3", pt)
                used += ok
        await sim.tick()
        if sq.status != "active" or not e.alive or e.pos != "C3" or sq.pos != "C3":
            break
    lost = sum(1 for m in sq.members if m.lost)
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
    return res, lost, foes - e.strength if e.alive else foes


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    plans = [("supp_advance", None), ("supp_advance", "smoke"), ("supp_advance", "drone"), ("hold", None),
             ("hold", "airdrop"), ("assault", None), ("assault", "smoke")]
    for foes in (3, 4):
        for plan in plans:
            stats, lost, kills = {}, 0, 0
            for s in range(n):
                r, l, k = await one(2000 + s, foes, plan)
                stats[r] = stats.get(r, 0) + 1
                lost += l
                kills += k
            print(f"敵 {foes}｜{plan[0]:12} + {plan[1] or '無支援':8}｜"
                  + " ".join(f"{k}{v}" for k, v in sorted(stats.items()))
                  + f"｜我陣亡 {lost / n:.1f}｜擊倒敵 {kills / n:.1f}", flush=True)
    if os.path.exists(PATH):
        os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
