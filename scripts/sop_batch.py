"""應變準則批次：district（50 m 街區）在沒有隊長 vs 簡易隊長（雙方各依準則）下的平手率。不需要 LLM。

用法：python scripts/sop_batch.py [每組場數，預設 40]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import sop
from engine.tactical import runner, scenarios

BOLD = sop.derive({"aggr": 0.6, "caut": -0.2, "bond": 0.0})
CAREFUL = sop.derive({"aggr": -0.2, "caut": 0.6, "bond": 0.4})
AVG = sop.derive({})


def row(name, builder, n, sops=None):
    w = {"blue": 0, "red": 0, "draw": 0}
    ticks = bl = rl = 0
    for s in range(n):
        r = runner.run(builder, seed=s, max_ticks=60, sop=sops)
        w[r["winner"] if r["winner"] in w else "draw"] += 1
        ticks += r["ticks"]
        bl += r["blue_lost"] / r["blue_n"]
        rl += r["red_lost"] / r["red_n"]
    print(f"{name:34}｜藍 {100 * w['blue'] // n:3}%／紅 {100 * w['red'] // n:3}%／平 {100 * w['draw'] // n:3}%"
          f"｜{ticks / n:4.1f} tick｜損失 藍 {100 * bl / n:3.0f}% 紅 {100 * rl / n:3.0f}%", flush=True)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    t0 = time.time()
    print("準則：一般", AVG, "\n      積極", BOLD, "\n      謹慎", CAREFUL)
    for name, b in (("district", scenarios.LARGE["district"]), ("district_hold", scenarios.LARGE["district_hold"])):
        row(f"{name}（沒有隊長）", b, n)
        row(f"{name}（雙方一般準則）", b, n, {"blue": AVG, "red": AVG})
        row(f"{name}（藍積極／紅謹慎）", b, n, {"blue": BOLD, "red": CAREFUL})
        row(f"{name}（藍謹慎／紅積極）", b, n, {"blue": CAREFUL, "red": BOLD})
    print(f"耗時 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
