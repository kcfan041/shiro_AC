"""誤擊統計（不需要 LLM）：4 人小隊對守軍／機動隊，數我方子彈命中我方的次數。

用法：python scripts/ff_batch.py [每組次數，預設 10]
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


def close_in(sim, sq, cell="C3", y=6):
    from engine.tactical import worldmap
    sq.pos = cell
    roster.sync_world(sim.world)
    m = worldmap.tmap(cell)
    spots = [n for n in worldmap.edge_nodes(m, "S", depth=y + 3) if n[1] >= y][:len(sq.members) * 3]
    taken = worldmap.occupied(sim.world, cell, exclude={id(x) for x in sq.members})
    spots = [n for n in spots if n not in taken]
    for i, mem in enumerate([x for x in sq.members if not x.lost]):
        mem.tpos = list(spots[i * 2 % len(spots)])
        mem.facing = [0, 1]


async def one(seed, tag, action):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999, cooldown=999,
                                   tag=tag, home="C3")
    roster.sync_world(sim.world)
    close_in(sim, sq)
    ff = shots = 0
    for _ in range(30):
        if sim.in_engagement(sq) and action != "hold" and sq.action.type != action:
            sq.action = Action(type=action)
        await sim.tick()
        eng = sim.engagements.get("C3")
        if eng is None:
            if sq.pos != "C3" or sq.status != "active":
                break
            continue
        b = eng.battle
        for s in b.shots[b._shot_mark:]:
            src = b.units.get(s["shooter"])
            if src is None or not src.side.startswith("blue"):
                continue
            shots += 1
            if s["kind"] == "body" and b.units[s["ref"][0]].side.startswith("blue"):
                ff += 1
    return ff, shots


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    for tag in ("garrison", "mobile"):
        for action in ("hold", "supp_advance", "flank", "retreat"):
            ff = shots = 0
            for s in range(n):
                a, b = await one(6000 + s, tag, action)
                ff += a
                shots += b
            print(f"{tag:8} × {action:12}｜我方射擊 {shots}｜誤擊 {ff}（{ff / max(1, shots):.2%}）", flush=True)
    if os.path.exists(PATH):
        os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
