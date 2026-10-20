"""Phase 0 固定遭遇（不需要 LLM）：Alpha 4 人從 B3 行軍進 C3，接敵後下令左翼包抄，守軍 3 人；
接敵後第 SMOKE_AT 個作戰 tick 在敵我之間叫一次煙幕支援。固定 seed，每次結果一樣。

用法：
  python scripts/encounter.py              跑一次，存回放到 data/encounter_replays/，印出基線（每步耗時、推送量、事件數）
  python scripts/encounter.py scan 9000 30 掃 seed，找有傷亡、有急救／拖人的場次
  python scripts/encounter.py json         只印結果摘要（JSON，給測試比對）
"""
import asyncio
import json
import os
import random
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, views
from engine.models import Action, Enemy
from engine.replay import ReplayStore, for_viewer
from engine.sim import Simulation
from engine.tactical import sim as tsim
from scripts.zone_test import Quiet, card

SEED = 9007
FOES = 3
ACTION = "flank"
SMOKE_AT = 3
TICKS = 80
PATH = "./data/encounter.json"
REPLAYS = "./data/encounter_replays"


class Probe:
    """量測：每個戰術步的 Battle.step 耗時、整步（含 AI 規劃）耗時、推送量、事件數、log。"""

    def __init__(self):
        self.step_ms: list[float] = []
        self.zone_ms: list[float] = []
        self.payload: list[int] = []
        self.events: list[int] = []
        self.log: list[str] = []
        self._orig = tsim.Battle.step
        probe = self

        def timed(battle, plans):
            mark = len(battle.log)
            t0 = time.perf_counter()
            probe._orig(battle, plans)
            probe.step_ms.append((time.perf_counter() - t0) * 1000)
            probe.log.extend(battle.log[mark:])
        tsim.Battle.step = timed

    def close(self):
        tsim.Battle.step = self._orig


def _summary(sim, sq, e, t_contact, t_end, probe) -> dict:
    lines = probe.log if probe else []
    return {"win": sq.status == "active" and (not e.alive or e.strength <= 0 or e.order == "withdraw"
                                                or _cleared(e)),
            "lost": sum(1 for m in sq.members if m.lost),
            "hurt": sum(1 for m in sq.members if not m.lost and (m.down or m.hp < 100)),
            "foe_left": e.strength if e.alive else 0,
            "contact_tick": t_contact, "end_tick": t_end,
            "downs": sum(1 for l in lines if "倒地（" in l),
            "aids": sum(1 for l in lines if "已被穩定" in l),
            "hits": sum(1 for l in lines if " 命中 " in l),
            "members": {m.name: [m.hp, bool(m.down), bool(m.lost), list(m.gpos or [])] for m in sq.members}}


def _cleared(e) -> bool:
    from engine.tactical import worldmap
    standing = [sd for sd in e.soldiers if not sd.get("down") and not sd.get("dead") and sd.get("gpos")]
    return not any(worldmap.cell_of(sd["gpos"][0], sd["gpos"][1]) in ("C3", "B3") for sd in standing)


async def run(seed=SEED, foes=FOES, action=ACTION, smoke_at=SMOKE_AT, ticks=TICKS, replays=False, probe=None,
              on_step=None) -> dict:
    """跑一場；replays=True 時存回放。on_step(sim, zone) 在每個戰術步後呼叫（測試用）。"""
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.tac_steps = 2
    sim.enemy_ai_enabled = False
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    if replays:
        if os.path.exists(REPLAYS):
            shutil.rmtree(REPLAYS)
        sim.replays = ReplayStore(REPLAYS)
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sq.pos = "B3"
    sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=foes, route=["C3"], move_every=999, cooldown=999,
                                   tag="garrison", home="C3")
    roster.sync_world(sim.world)
    orig_run = sim._run_zones

    def run_zones():
        t0 = time.perf_counter()
        out = orig_run()
        if probe is not None:
            probe.zone_ms.append((time.perf_counter() - t0) * 1000)
            size, nev = 0, 0
            for z in sim.zones:
                if z.last_frame is not None and sq.pos in z.cells:
                    f = for_viewer(z.last_frame, "1", sim.world)
                    view = views.zone_view(sim, "1")
                    view["frames"] = [f]
                    size += len(json.dumps(view, ensure_ascii=False).encode("utf8"))
                    nev += len(f.get("events", [])) + len(f.get("ev", []))
            probe.payload.append(size)
            probe.events.append(nev)
        if on_step is not None:
            for z in sim.zones:
                on_step(sim, z)
        return out
    sim._run_zones = run_zones
    await sim.tick()
    sq.action = Action(type="move", target="C3", path=["C3"], duration=3, ordered=True)
    t_contact = None
    t = 0
    for t in range(ticks):
        await sim.tick()
        if t_contact is None and sim.in_engagement(sq):
            sq.action = Action(type=action, flank="left" if action == "flank" else None, ordered=True)
            t_contact = t
        if smoke_at and t_contact is not None and t == t_contact + smoke_at:
            await _call_smoke(sim, sq)
        e = sim.world.enemies["x"]
        if sq.status != "active" or _cleared(e) or not e.alive:
            break
    for z in list(sim.zones):
        sim._zone_replay(z)
    res = _summary(sim, sq, sim.world.enemies["x"], t_contact, t, probe)
    if os.path.exists(PATH):
        os.remove(PATH)
    return res


async def _call_smoke(sim, sq):
    """煙幕落在小隊中心與最近敵人的中點（用全圖座標換回格內座標）。"""
    from engine import zone as zmod
    from engine.tactical import worldmap
    c = zmod.squad_center(sq)
    e = sim.world.enemies["x"]
    foes = [sd["gpos"] for sd in e.soldiers if sd.get("gpos") and not sd.get("down") and not sd.get("dead")]
    if not c or not foes:
        return
    f = min(foes, key=lambda p: (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2)
    gx, gy = (c[0] + f[0]) / 2, (c[1] + f[1]) / 2
    cell = worldmap.cell_of(gx, gy)
    ox, oy = worldmap.cell_origin(cell)
    await sim.call_support("1", "smoke", cell, [gx - ox, gy - oy])


def pct(xs, p):
    if not xs:
        return 0
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p))]


async def baseline():
    probe = Probe()
    try:
        res = await run(replays=True, probe=probe)
    finally:
        probe.close()
    idx = ReplayStore(REPLAYS).index
    print(json.dumps(res, ensure_ascii=False))
    print(f"回放：{[e['id'] + '（' + str(e['frames']) + ' 步）' for e in idx]}")
    n = len(probe.step_ms)
    print(f"戰術步 {n}｜Battle.step 平均 {sum(probe.step_ms) / max(1, n):.1f} ms、p95 {pct(probe.step_ms, .95):.1f}、"
          f"最慢 {max(probe.step_ms or [0]):.1f}")
    z = probe.zone_ms
    print(f"整步（AI 規劃＋step＋錄影）平均 {sum(z) / max(1, len(z)):.1f} ms、p95 {pct(z, .95):.1f}、最慢 {max(z or [0]):.1f}")
    pl = [x for x in probe.payload if x]
    print(f"world 推送（單一指揮官）平均 {sum(pl) / max(1, len(pl)) / 1024:.1f} KB、最大 {max(pl or [0]) / 1024:.1f} KB")
    ev = probe.events
    print(f"每步事件（玩家可見）平均 {sum(ev) / max(1, len(ev)):.1f}、最多 {max(ev or [0])}")


async def scan(start, n):
    for s in range(start, start + n):
        probe = Probe()
        try:
            r = await run(seed=s, probe=probe)
        finally:
            probe.close()
        print(s, json.dumps({k: r[k] for k in ("win", "lost", "hurt", "foe_left", "contact_tick", "end_tick",
                                                "downs", "aids", "hits")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        asyncio.run(scan(int(sys.argv[2]), int(sys.argv[3])))
    elif len(sys.argv) > 1 and sys.argv[1] == "json":
        print(json.dumps(asyncio.run(run()), ensure_ascii=False))
    else:
        asyncio.run(baseline())
