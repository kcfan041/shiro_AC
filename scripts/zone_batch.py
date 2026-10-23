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
ATTACK_TICKS = int(os.environ.get("ATTACK_TICKS", "120"))   # 作戰 tick（5 秒）：120 ＝ 10 分鐘


def cleared(sim, e) -> bool:
    """拿下 C3：守軍全倒／撤退，且 C3 格內沒有還能作戰的敵兵。"""
    from engine.tactical import worldmap
    if not e.alive or e.strength <= 0 or e.order == "withdraw":
        return True
    standing = [sd for sd in e.soldiers if not sd.get("down") and not sd.get("dead") and sd.get("gpos")]
    return not any(worldmap.cell_of(sd["gpos"][0], sd["gpos"][1]) == "C3" for sd in standing) and \
        all(worldmap.cell_of(sd["gpos"][0], sd["gpos"][1]) not in ("C3", "B3") for sd in standing)


def battle_secs(sim) -> float:
    """一個作戰 tick 推進多少秒的戰鬥時間。"""
    from engine.tactical.sim import TICK_S
    return min(TICK_S, sim.step_seconds) * sim.tac_steps


async def attack(seed, foes, action, ticks=60):
    """行軍到 B3 北側接敵後下令 action（supp_advance／flank／assault），看能不能打下 C3。"""
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed),
                     tick_seconds=int(os.environ.get("TICK_SECONDS", "10")))
    sim.zone_mode = True
    sim.tac_steps = int(os.environ.get("TAC_STEPS", "2"))
    sim.enemy_ai_enabled = False
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sq.pos = "B3"
    sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=foes, route=["C3"], move_every=999, cooldown=999,
                                   tag="garrison", home="C3")
    roster.sync_world(sim.world)
    await sim.tick()
    sq.action = Action(type="move", target="C3", path=["C3"], duration=3, ordered=True)
    ordered = False
    t_contact = None
    for t in range(ticks):
        await sim.tick()
        if not ordered and sim.in_engagement(sq):
            sq.action = Action(type=action, flank="left" if action == "flank" else None, ordered=True)
            ordered = True
            t_contact = t
        e = sim.world.enemies["x"]
        if sq.status != "active" or cleared(sim, e):
            break
    e = sim.world.enemies["x"]
    win = cleared(sim, e) and sq.status == "active"
    return {"win": win, "lost": sum(1 for m in sq.members if m.lost), "killed": foes - (e.strength if e.alive else 0),
            "secs": (t - t_contact) * battle_secs(sim) if win and t_contact is not None else None,
            "wiped": sq.status != "active"}


async def one(seed, foes, retreat):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed),
                     tick_seconds=int(os.environ.get("TICK_SECONDS", "10")))
    sim.zone_mode = True
    sim.tac_steps = int(os.environ.get("TAC_STEPS", "2"))
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
    for foes in (3, 4):
        for action in ("hold", "supp_advance", "flank", "assault"):
            rs = [await attack(9000 + s, foes, action, ticks=ATTACK_TICKS) for s in range(n)]
            wins = [r for r in rs if r["win"]]
            print(f"守軍 {foes}｜接敵後 {action:12}｜拿下 {len(wins)}/{n}"
                  f"（平均 {sum(r['secs'] for r in wins) / max(1, len(wins)):.0f} 秒）"
                  f"｜我陣亡 {sum(r['lost'] for r in rs) / n:.2f}｜全滅 {sum(r['wiped'] for r in rs)}"
                  f"｜擊倒敵 {sum(r['killed'] for r in rs) / n:.2f}", flush=True)
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
