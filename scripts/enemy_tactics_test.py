"""C4：敵方戰術指揮。不需要 LLM。"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import enemy_tactics as et
from engine import roster
from engine.models import Enemy
from scripts.engage_test import PATH, card, Quiet
from engine.sim import Simulation


async def make(foes=3, tag="garrison", cell="C3", eid="x", squad=True, seed=2):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    e = Enemy(id=eid, pos=cell, strength=foes, route=[cell], move_every=999, cooldown=999, tag=tag, home=cell)
    sim.world.enemies[eid] = e
    if squad:
        sq.pos = cell
    roster.sync_world(sim.world)
    sim.save()
    for _ in range(4):
        await sim.tick()
        if sim.engagements.get(cell):
            break
    return sim, sq, e


def fake_known(eng, n):
    """讓紅方情報知道 n 名藍方。"""
    b = eng.battle
    blues = [uid for uid, u in b.units.items() if u.side.startswith("blue") and u.alive][:n]
    b.intel["red"].last_known = {uid: (b.units[uid].pos, b.tick) for uid in blues}
    return blues


def test_rules():
    async def go():
        sim, sq, e = await make(foes=3, tag="garrison")
        eng = sim.engagements["C3"]
        cmd = eng.commander
        units = cmd._units(e)
        b = eng.battle

        b.intel["red"].last_known = {}
        assert cmd._choose(e, units, [], 99, "normal")[0].kind == "hold", "守軍沒看到敵人 → 固守"
        e.tag = "mobile"
        assert cmd._choose(e, units, [], 99, "normal")[0].kind == "advance", "機動隊沒看到 → 搜索"

        fake_known(eng, 1)
        known = cmd._known()
        b.intel["red"].believed_seen = {u.id: (b.tick, "near_miss") for u in units}
        it, why = cmd._choose(e, units, known, len(units) / 1, "normal")
        assert it.kind == "flank", f"機動隊兵力優勢 → 包抄（{it.kind} {why}）"
        it2, _ = cmd._choose(e, units, known, len(units) / 1, "normal")
        assert it2.side != it.side, "包抄方向輪替"
        e.tag = "garrison"
        assert cmd._choose(e, units, known, 3, "normal")[0].kind == "supp_advance", "野外守軍優勢 → 壓制推進"
        e.pos = "E2"
        assert cmd._choose(e, units, known, 3, "normal")[0].kind == "hold", "據點守軍不出擊"
        e.pos = "C3"

        fake_known(eng, 4)
        known = cmd._known()
        it, why = cmd._choose(e, units[:1], known, 1 / 4, "normal")
        assert it.kind == "retreat", "兵力劣勢 → 撤退"
        assert it.toward is not None
        assert cmd._choose(e, units, known, 1, "rout")[0].kind == "retreat", "士氣崩潰 → 撤退"
        e.pos = "E2"
        assert cmd._choose(e, units, known, 1 / 4, "rout")[0].kind != "retreat", "據點不撤"
        e.pos = "C3"

        b.intel["red"].believed_seen = {}
        fake_known(eng, 1)
        known = cmd._known()
        for u in units:
            u.pos = (u.pos[0], 24, 0)
        it, _ = cmd._choose(e, units, known, 3, "normal")
        assert it.kind == "ambush", "對方沒發現我們且距離遠 → 設伏"

        e.order = "withdraw"
        e.goal = "E3"
        it, _ = cmd._choose(e, units, known, 3, "normal")
        assert it.kind == "retreat" and it.toward == (0, 1), "奉命撤回 → 往目標方向"
        e.order, e.goal = None, None

        hx = Enemy(id="h9", pos="C3", strength=2, route=["C3"], move_every=999, cooldown=999, tag="mobile", home="C3")
        cmd.fired["h9"] = et.HARASS_FIRE
        it, why = cmd._choose(hx, units, known, 3, "normal")
        assert it.kind == "retreat", f"騷擾隊打完就跑（{why}）"
        os.remove(PATH)
    asyncio.run(go())


def test_decisions_logged_and_hysteresis():
    async def go():
        sim, sq, e = await make(foes=3, tag="mobile")
        eng = sim.engagements["C3"]
        for _ in range(6):
            await sim.tick()
        assert any("@C3" in l for l in sim.enemy_log), sim.enemy_log
        cmd = eng.commander
        if e.id in cmd.current:
            intent, t, _ = cmd.current[e.id]
            assert eng.battle.tick - t <= et.RETHINK + 1
        os.remove(PATH)
    asyncio.run(go())


def test_enemy_retreat_exits_cell():
    async def go():
        sim, sq, e = await make(foes=2, tag="mobile", cell="C3")
        eng = sim.engagements["C3"]
        e.order = "withdraw"
        e.goal = "E3"
        for u in eng.commander._units(e):
            u.pos = (u.pos[0], 23, 0)
        ids = {sd["id"] for sd in e.soldiers}
        moved = False
        for _ in range(30):
            await sim.tick()
            if e.pos != "C3" or not e.alive:
                moved = e.pos == "D3"
                break
        assert moved or not e.alive, f"撤退敵軍撤出到北邊 D3（目前 {e.pos}）"
        if moved:
            assert {sd["id"] for sd in e.soldiers} <= ids
            assert all(sd["tpos"][1] <= 4 for sd in e.soldiers), "從南邊進入 D3"
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("enemy_tactics_test OK")


if __name__ == "__main__":
    main()
