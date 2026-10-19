"""連續地圖效能測試：拼 125×125 全圖，放兩群（我方 4＋敵 6）×2 區域＋散布敵人，共約 40 人，量每步耗時。

用法：python scripts/zone_bench.py [步數，預設 20]
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import worldmap
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle, SideIntel
from engine.tactical.units import Unit


def spots(g, cell, n, rng, region=None):
    ox, oy = worldmap.cell_origin(cell)
    out = []
    while len(out) < n:
        x, y = ox + rng.randint(2, 22), oy + rng.randint(2, 22)
        if region:
            x, y = ox + rng.randint(*region[0]), oy + rng.randint(*region[1])
        n0 = (x, y, 0)
        if g.standable(n0) and n0 not in out:
            out.append(n0)
    return out


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    t0 = time.time()
    g = worldmap.global_map()
    print(f"拼圖 {g.w}×{g.h}：{time.time() - t0:.1f}s，方塊 {len(g.boxes)}，房間 {len(g.rooms)}", flush=True)
    rng = random.Random(1)
    units = []
    k = 0
    for cell, ecell in (("B2", "C2"), ("B4", "C4")):
        for p in spots(g, cell, 4, rng, region=((8, 16), (16, 23))):
            units.append(Unit(f"b{k}", f"blue|{cell}", p, facing=(0, 1)))
            k += 1
        for p in spots(g, ecell, 6, rng, region=((5, 20), (2, 12))):
            units.append(Unit(f"r{k}", "red", p, facing=(0, -1)))
            k += 1
    for cell in ("D1", "D3", "D5", "E2", "E4", "C3", "B3", "A5"):
        for p in spots(g, cell, 2, rng):
            units.append(Unit(f"r{k}", "red", p, facing=(0, -1)))
            k += 1
    print(f"個體 {len(units)}", flush=True)
    b = Battle(g, units, seed=3)
    shared = SideIntel()
    for s in list(b.intel):
        if s.startswith("blue"):
            b.intel[s] = shared
    ais = {s: TacticalAI(b, s) for s in b.intel}
    times = []
    for i in range(steps):
        t = time.time()
        plans = {}
        for s, ai in ais.items():
            mine = [u for u in b.alive(s)]
            if not mine:
                continue
            if s.startswith("blue"):
                lk = b.intel[s].last_known
                goal = min((n for n, _ in lk.values()), key=lambda n: n[1]) if lk else None
                intent = Intent("supp_advance", goal=goal) if goal else Intent("advance", goal=(mine[0].pos[0], mine[0].pos[1] + 15, 0))
            else:
                intent = Intent("hold")
            plans.update(ai.plan(intent, mine))
        b.step(plans)
        times.append(time.time() - t)
        print(f"  步 {i + 1}: {times[-1]:.2f}s｜射擊 {len(b.last_shots)}｜倒地 {sum(1 for u in b.units.values() if u.down)}",
              flush=True)
    print(f"平均 {sum(times) / len(times):.2f}s，最慢 {max(times):.2f}s")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
