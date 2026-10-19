"""連續地圖批次（不需要 LLM）：小隊從 B3 行軍去 C3（守軍 3～4 人），統計
接敵位置（離敵人多遠）、行軍途中陣亡、交火結果；以及接敵後下令撤回 A3 的成功率與耗時。

用法：python scripts/zone_batch.py [次數，預設 8]
"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, zone as zmod
from engine.models import Action, Enemy
from engine.sim import Simulation
from scripts.zone_test import Quiet, card

PATH = "./data/zone_batch.json"


async def one(seed, foes, retreat):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.tac_steps = 2
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sq.pos = "B3"
    sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=foes, route=["C3"], move_every=999, cooldown=999,
                                   tag="garrison", home="C3")
    roster.sync_world(sim.world)
    await sim.tick()
    sq.action = Action(type="move", target="C3", path=["C3"], duration=3, ordered=True)
    contact_dist = None
    lost_on_contact = None
    retreat_at = None
    for t in range(40):
        await sim.tick()
        z = sim.engagements.get(sq.pos)
        if contact_dist is None and sim.in_engagement(sq) and z is not None:
            b = z.battle
            c = zmod.squad_center(sq)
            reds = [u for u in b.units.values() if u.side == "red" and u.alive]
            if c and reds:
                contact_dist = min(((u.pos[0] - c[0]) ** 2 + (u.pos[1] - c[1]) ** 2) ** 0.5 for u in reds) * 2
            lost_on_contact = sum(1 for m in sq.members if m.lost or m.down)
            if retreat:
                sq.action = Action(type="retreat", target="A3", ordered=True)
                retreat_at = t
        if retreat and retreat_at is not None and sq.pos == "A3" and sq.action.type == "hold":
            break
        if sq.status != "active":
            break
    lost = sum(1 for m in sq.members if m.lost)
    e = sim.world.enemies["x"]
    return {"contact": contact_dist, "hurt_at_contact": lost_on_contact, "lost": lost,
            "killed": foes - (e.strength if e.alive else 0), "pos": sq.pos,
            "retreat_s": (t - retreat_at) * 5 if retreat_at is not None and sq.pos == "A3" else None}


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    for foes in (3, 4):
        rs = [await one(7000 + s, foes, False) for s in range(n)]
        cd = [r["contact"] for r in rs if r["contact"] is not None]
        print(f"行軍進 C3（守軍 {foes}）：接敵 {len(cd)}/{n}｜接敵距離平均 {sum(cd) / max(1, len(cd)):.0f}m"
              f"｜接敵當下傷亡 {sum(r['hurt_at_contact'] or 0 for r in rs) / n:.2f}"
              f"｜我陣亡 {sum(r['lost'] for r in rs) / n:.2f}｜擊倒敵 {sum(r['killed'] for r in rs) / n:.2f}", flush=True)
    rs = [await one(8000 + s, 4, True) for s in range(n)]
    ok = [r for r in rs if r["retreat_s"] is not None]
    print(f"接敵後撤回 A3：成功 {len(ok)}/{n}｜平均 {sum(r['retreat_s'] for r in ok) / max(1, len(ok)):.0f} 秒"
          f"｜陣亡 {sum(r['lost'] for r in rs) / n:.2f}", flush=True)
    if os.path.exists(PATH):
        os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
