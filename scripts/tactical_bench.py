"""戰術層效能與開局可見性基準。用法：python scripts/tactical_bench.py [N]"""
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios
from engine.tactical.sim import Battle


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    for name, fn in {**scenarios.LARGE, "open_field": scenarios.open_field}.items():
        m, units, _, _ = fn()
        b = Battle(m, units)
        blue = [u for u in units if u.side == "blue"]
        red = [u for u in units if u.side == "red"]
        vis = max(b.visibility(x, y) for x in blue for y in red)
        times, ticks, first = [], [], []
        for seed in range(n):
            t = time.time()
            r = runner.run(fn, seed=seed)
            times.append(time.time() - t)
            ticks.append(r["ticks"])
            spots = [int(re.match(r"T(\d+)", l).group(1)) for l in r["battle"].log if "發現" in l]
            first.append(min(spots) if spots else 99)
        print(f"{name}: {m.w}×{m.h}｜方塊 {len(m.boxes)}｜開局最大可見 {vis:.2f}｜"
              f"首次發現 tick 中位 {statistics.median(first)}｜平均 {statistics.mean(ticks):.1f} tick｜"
              f"每場 {statistics.mean(times):.2f}s｜每 tick {1000 * sum(times) / max(1, sum(ticks)):.0f} ms")


if __name__ == "__main__":
    main()
