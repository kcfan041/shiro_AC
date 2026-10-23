"""規劃快照（模擬重構第 6 步）：規劃改在快照重建的 Battle 複本上跑，結果必須和直接規劃完全相同；
規劃不改 Battle 狀態；快照與 AI 狀態能 pickle（第 7 步送到別的行程）。不需要 LLM。"""
import asyncio
import hashlib
import os
import pickle
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.engagement as eg
from engine.tactical import planjob
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle


def with_snapshot(on: bool, fn):
    old = eg.PLAN_VIA_SNAPSHOT
    eg.PLAN_VIA_SNAPSHOT = on
    try:
        return fn()
    finally:
        eg.PLAN_VIA_SNAPSHOT = old


def world_digest(sim) -> str:
    w = sim.world
    rows = [(s.id, m.name, tuple(m.gpos or ()), m.hp, m.down, m.lost, m.ammo, m.grenades, m.smokes)
            for s in w.squads.values() for m in s.members]
    rows += [(e.id, sd["id"], tuple(sd.get("gpos") or ()), bool(sd.get("down"))) for e in w.enemies.values()
             for sd in e.soldiers]
    return hashlib.sha256(repr((w.tick, sorted(map(str, rows)), sim.rng.random())).encode()).hexdigest()


def test_encounter_same_via_snapshot():
    import event_test as et
    d, _ = with_snapshot(True, lambda: asyncio.run(et.run_digest()))
    assert d.startswith(et.GOLDEN), d[:12]


async def two_commanders(ticks: int, tac: int = 2) -> str:
    from engine import roster
    from engine.models import Action, Enemy
    from engine.sim import Simulation
    from scripts.zone_test import Quiet, card
    path = "./data/planjob_test.json"
    if os.path.exists(path):
        os.remove(path)
    sim = Simulation(Quiet(), save_path=path, rng=random.Random(8), tick_seconds=5)
    sim.zone_mode, sim.tac_steps, sim.background, sim.allies_enabled = True, tac, True, True
    sim.enemy_ai_enabled = True
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    for cid, name, dest in (("1", "alpha", "C3"), ("2", "bravo", "C4")):
        sim.ensure_commander(cid, cid)
        sq, _ = await sim.deploy_squad(card(name), cid)
        sq.action = Action(type="move", target=dest, path=["B" + dest[1], dest], ordered=True)
    for i, cell in enumerate(("C3", "C4", "D3")):
        sim.world.enemies[f"x{i}"] = Enemy(id=f"x{i}", pos=cell, strength=3, route=[cell], move_every=999,
                                            cooldown=999, tag="garrison" if i < 2 else "mobile", home=cell)
    roster.sync_world(sim.world)
    for _ in range(ticks):
        await sim.tick()
    await sim.settle()
    os.remove(path)
    if sim.zones:
        LAST_ZONE["z"] = max(sim.zones, key=lambda z: len(z.battle.units))
    return world_digest(sim)


def test_zone_two_commanders_same_via_snapshot():
    """連續地圖、兩位指揮官、友軍、敵方機動隊：快照規劃與直接規劃逐拍相同。"""
    for ticks, tac in ((20, 2), (8, 10)):          # 2.5 秒一步、0.5 秒一步（網頁版）
        a = with_snapshot(False, lambda: asyncio.run(two_commanders(ticks, tac)))
        b = with_snapshot(True, lambda: asyncio.run(two_commanders(ticks, tac)))
        assert a == b, tac


async def remnant_siege(ticks: int) -> str:
    from scripts.remnant_test import end_standoff, fresh
    sim, sq, c, m, _ = await fresh(seed=3)
    end_standoff(sim, m)
    for _ in range(ticks):
        await sim.tick()
    d = world_digest(sim)
    r = sim.world.remnants.get(m["remnant"])
    os.remove("./data/remnant_test.json")
    return d + (r.status if r else "-")


def test_remnant_breach_same_via_snapshot():
    """營救殘部：圍攻敵軍破門強攻（破門狀態、綠方）——快照規劃與直接規劃相同。"""
    a = with_snapshot(False, lambda: asyncio.run(remnant_siege(14)))
    b = with_snapshot(True, lambda: asyncio.run(remnant_siege(14)))
    assert a == b


def test_planning_is_read_only():
    """規劃只讀：規劃前後 Battle 的單位與情報完全相同（不再暫改位置、exited）。"""
    from scripts import sim_bench as sb
    from engine.tactical import worldmap
    g = worldmap.global_map()
    b, ais = sb.make_battle(g, sb.place(g, "firefight", 40, random.Random(5)), "firefight")
    for _ in range(3):
        b.step(sb.plan_all(b, ais, "firefight"))
    before = (b.state(), {s: dict(i.believed_seen) for s, i in b.intel.items()})
    sb.plan_all(b, ais, "firefight")
    after = (b.state(), {s: dict(i.believed_seen) for s, i in b.intel.items()})
    assert before == after


def test_snapshot_pickles_and_runs():
    """快照與 AI 狀態能 pickle；在反序列化的快照上規劃，結果與直接規劃相同。"""
    from scripts import sim_bench as sb
    from engine.tactical import worldmap
    g = worldmap.global_map()
    b, ais = sb.make_battle(g, sb.place(g, "firefight", 24, random.Random(6)), "firefight")
    for _ in range(3):
        b.step(sb.plan_all(b, ais, "firefight"))
    side = sorted(s for s in ais if s.startswith("blue"))[0]
    units = b.alive(side)
    intent = Intent("supp_advance", goal=(units[0].pos[0], units[0].pos[1] + 15, 0))
    snap = pickle.loads(pickle.dumps(planjob.battle_snapshot(b)))
    state = pickle.loads(pickle.dumps(planjob.ai_state(ais[side])))
    via, _ = planjob.run(g, snap, side, state, [(intent, [u.id for u in units])])
    direct = ais[side].plan(intent, units)
    key = lambda ps: {k: (p.mode, tuple(p.path or ()), p.target, p.fire, p.watch, p.why) for k, p in ps.items()}
    assert key(via) == key(direct)
    _ = (Battle, TacticalAI)


def test_parallel_same_as_sequential():
    """多行程平行規劃（每個 AI 一份工作）：結果與依序規劃完全相同（連續地圖、兩位指揮官、友軍、三組敵軍）。"""
    for ticks, tac in ((14, 2), (8, 10)):
        a = asyncio.run(two_commanders(ticks, tac))
        planjob.configure(workers=2, min_units=1)
        try:
            b = asyncio.run(two_commanders(ticks, tac))
        finally:
            planjob.configure(workers=0, min_units=40)
        assert a == b, tac


def test_budget_degrades_late_jobs():
    """時間預算到了還沒算完的工作：改用便宜計畫（只射擊或警戒，不移動），那個 AI 的狀態不提交。"""
    from scripts import sim_bench as sb
    from engine.tactical import worldmap
    g = worldmap.global_map()
    b, ais = sb.make_battle(g, sb.place(g, "firefight", 24, random.Random(7)), "firefight")
    for _ in range(2):
        b.step(sb.plan_all(b, ais, "firefight"))
    jobs = [(ai, Intent("hold"), b.alive(ai.side) if not k.startswith("red#") else
             [u for u in b.alive("red") if u.id.startswith(f"r{k.split('#c')[1]}_")]) for k, ai in ais.items()]
    jobs = [j for j in jobs if j[2]]
    planjob.configure(workers=2, budget=1e-6, min_units=1)
    try:
        plans, states, degraded = planjob.run_parallel(b, jobs)
    finally:
        planjob.configure(workers=0, budget=0, min_units=40)
    assert degraded >= 1
    assert {u.id for u in b.alive()} <= set(plans), "每個人都有計畫"
    for (ai, _, units), st in zip(jobs, states):
        if st is None:
            assert all(plans[u.id].mode != "move" for u in units), "降級計畫不移動"


def test_enemy_groups_have_own_ai():
    """每組敵軍各自一個戰術 AI（耐心、固守、破門分開）；某組卡住只重置自己的 AI。"""
    from engine.enemy_tactics import EnemyCommander
    asyncio.run(two_commanders(16))      # 行軍約 1.5 m/s：要走到敵軍所在的格子
    z = LAST_ZONE["z"]
    keys = sorted(k for k in z.ais if k.startswith("red#"))
    assert len(keys) >= 2 and len({id(z.ais[k]) for k in keys}) == len(keys), keys
    e0, e1 = [z.world.enemies[k.split("#")[1]] for k in keys[:2]]
    z.enemy_ai(e0).reports.append("T0 無法推進：……請求新指示")
    cmd: EnemyCommander = z.commander
    assert cmd._stalled_report(e0) and not cmd._stalled_report(e1)


LAST_ZONE: dict = {}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("planjob_test OK")


if __name__ == "__main__":
    main()
