"""節奏與戰術行為批次（不需要 LLM）：
- 撤退：接敵後下令撤退，幾步內撤出這一格？撤退時陣亡多少？（含邊緣格 A3）
- 固守：接敵後固守，有沒有人一直站在暴露位置？
- 進場：從相鄰格走進有守軍的格子，進場後 3 步內的傷亡。

用法：python scripts/pace_batch.py [每組次數，預設 10]
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


async def make(seed, cell, foes, tag="garrison", frm=None):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.tac_steps = 2
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sim.world.enemies["x"] = Enemy(id="x", pos=cell, strength=foes, route=[cell], move_every=999, cooldown=999,
                                   tag=tag, home=cell)
    if frm:
        sq.pos = frm
        roster.sync_world(sim.world)
        sq.pos = cell
        sq.tcell = frm
    else:
        sq.pos = cell
    roster.sync_world(sim.world)
    return sim, sq


async def retreat_run(seed, cell):
    sim, sq = await make(seed, cell, 4, "mobile", frm="B3" if cell != "B3" else "A3")
    for _ in range(12):
        await sim.tick()
        if sim.in_engagement(sq):
            break
    if not sim.in_engagement(sq):
        return None
    lost0 = sum(1 for m in sq.members if m.lost)
    sq.action = Action(type="retreat", ordered=True)
    steps = 0
    for _ in range(20):
        await sim.tick()
        steps += sim.tac_steps
        if sq.pos != cell or sq.status != "active":
            break
    return {"out": sq.pos != cell, "steps": steps, "lost": sum(1 for m in sq.members if m.lost) - lost0}


async def hold_run(seed):
    sim, sq = await make(seed, "C3", 3, "garrison", frm="B3")
    exposed_ticks = total = 0
    for _ in range(14):
        await sim.tick()
        eng = sim.engagements.get("C3")
        if eng is None or not eng.hot():
            continue
        ai = eng.ais.get(eng.side_of(sq.id))
        th = ai.threats() if ai else []
        if not th:
            continue
        for uid, o in eng.owner.items():
            if o[1] == sq.id and eng.battle.units[uid].alive:
                total += 1
                if ai.exposure(eng.battle.units[uid].pos, th) > 0:
                    exposed_ticks += 1
    return {"exposed": exposed_ticks / total if total else 0.0, "lost": sum(1 for m in sq.members if m.lost)}


async def entry_run(seed):
    sim, sq = await make(seed, "C3", 3, "garrison", frm="B3")
    for _ in range(2):
        await sim.tick()
    return {"lost": sum(1 for m in sq.members if m.lost), "down": sum(1 for m in sq.members if m.down)}


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    for cell in ("C3", "A3"):
        rs = [r for r in [await retreat_run(3000 + s, cell) for s in range(n)] if r]
        if rs:
            out = sum(r["out"] for r in rs)
            st = [r["steps"] for r in rs if r["out"]]
            print(f"撤退 {cell}：撤出 {out}/{len(rs)}｜平均 {sum(st) / max(1, len(st)):.1f} 步（{2.5 * sum(st) / max(1, len(st)):.0f} 秒）"
                  f"｜撤退中陣亡 {sum(r['lost'] for r in rs) / len(rs):.2f}", flush=True)
    hs = [await hold_run(4000 + s) for s in range(n)]
    print(f"固守：暴露時間比例 {sum(h['exposed'] for h in hs) / n:.0%}｜陣亡 {sum(h['lost'] for h in hs) / n:.2f}", flush=True)
    es = [await entry_run(5000 + s) for s in range(n)]
    print(f"進場（5 秒內）：陣亡 {sum(e['lost'] for e in es) / n:.2f}｜倒地 {sum(e['down'] for e in es) / n:.2f}", flush=True)
    if os.path.exists(PATH):
        os.remove(PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
