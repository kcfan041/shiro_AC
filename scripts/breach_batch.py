"""破門批次：同一棟房子，比較破門參數組合與突擊、壓制推進。不需要 LLM。

用法：python scripts/breach_batch.py [每組場數，預設 60] [情境名稱過濾]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    flt = sys.argv[2] if len(sys.argv) > 2 else ""
    t0 = time.time()
    for name, builder in scenarios.BREACH.items():
        if flt not in name:
            continue
        w = {"blue": 0, "red": 0, "draw": 0}
        bl = rl = ticks = fl = dz = cl = br = 0
        for s in range(n):
            r = runner.run(builder, seed=s, max_ticks=40)
            w[r["winner"] if r["winner"] in w else "draw"] += 1
            bl += r["blue_lost"] / r["blue_n"]
            rl += r["red_lost"] / r["red_n"]
            ticks += r["ticks"]
            fl += r["flashes"]
            dz += r["dazzled"]
            cl += r["cleared"]
            br += r["braces"]
        print(f"{name:26}｜藍 {100 * w['blue'] // n:3}%／紅 {100 * w['red'] // n:3}%／平 {100 * w['draw'] // n:3}%"
              f"｜損失 藍 {100 * bl / n:3.0f}% 紅 {100 * rl / n:3.0f}%｜{ticks / n:4.1f} tick"
              f"｜閃光 {fl / n:.1f} 過載 {dz / n:.1f}｜架槍 {br / n:.1f}｜清空回報 {100 * cl // n}%", flush=True)
    print(f"耗時 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
