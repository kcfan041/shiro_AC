"""戰術層批次模擬：每個情境跑 N 場，輸出統計（combat.md v0.2 §15.2）。

用法：python scripts/tactical_batch.py [N] [情境...]
"""
import os
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios


def stuck_ticks(positions: dict) -> int:
    worst = 0
    for hist in positions.values():
        run = best = 0
        for a, b in zip(hist, hist[1:]):
            run = run + 1 if a == b else 0
            best = max(best, run)
        worst = max(worst, best)
    return worst


def summarize(name: str, results: list[dict]) -> str:
    n = len(results)
    wins = defaultdict(int)
    for r in results:
        wins[r["winner"]] += 1
    ticks = [r["ticks"] for r in results]
    firsts = [r["first_down"] for r in results if r["first_down"]]
    blue_loss = [r["blue_lost"] / r["blue_n"] for r in results]
    red_loss = [r["red_lost"] / r["red_n"] for r in results]
    by_weapon = defaultdict(lambda: [0, 0, 0])
    for r in results:
        for s in r["stats"].values():
            w = by_weapon[f"{s['side']}:{s['weapon']}"]
            w[0] += s["shots"]
            w[1] += s["hits"]
            w[2] += s["kills"]
    reports = sum(len(r["reports"]) for r in results)
    for side in ("blue", "red"):
        o = [r["outcome"][side] for r in results]
        lines_extra = (f"  {side} 結局：陣亡 {sum(x['dead'] for x in o) / n:.2f}｜倒地未死 {sum(x['down'] for x in o) / n:.2f}｜"
                       f"撤出 {sum(x['exited'] for x in o) / n:.2f}｜投降 {sum(x['surrendered'] for x in o) / n:.2f}｜"
                       f"結束士氣 {statistics.mean(x['morale'] for x in o):.0f}")
        by_weapon.setdefault(f"~{side}", lines_extra)
    extra = [by_weapon.pop(f"~{s}") for s in ("blue", "red")]
    extra.append(f"  手榴彈 {sum(r['grenades'] for r in results) / n:.1f}/場｜近戰 {sum(r['melee'] for r in results) / n:.2f}/場")
    lines = [
        f"== {name}（{n} 場）",
        f"  勝負：藍 {wins['blue'] / n:.0%}｜紅 {wins['red'] / n:.0%}｜平手（60 tick 未分） {wins['draw'] / n:.0%}",
        f"  長度：平均 {statistics.mean(ticks):.1f} tick｜中位 {statistics.median(ticks)}｜"
        f"首個倒地 平均 {statistics.mean(firsts):.1f}" if firsts else
        f"  長度：平均 {statistics.mean(ticks):.1f} tick｜無人倒地",
        f"  損失比例：藍 {statistics.mean(blue_loss):.0%}｜紅 {statistics.mean(red_loss):.0%}",
    ]
    for key in sorted(by_weapon):
        shots, hits, kills = by_weapon[key]
        if shots:
            lines.append(f"  {key}: 發 {shots / n:.1f}/場｜命中率 {hits / shots:.1%}｜"
                         f"每命中耗彈 {shots / max(hits, 1):.1f}｜擊倒 {kills / n:.2f}/場")
    lines += extra
    lines.append(f"  最長原地不動：{max(stuck_ticks(r['positions']) for r in results)} tick｜"
                 f"不可行回報 {reports / n:.1f}/場")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    n = int(args[0]) if args and args[0].isdigit() else 200
    names = [a for a in args if not a.isdigit()] or list(scenarios.SCENARIOS)
    start = time.time()
    for name in names:
        results = []
        for seed in range(n):
            r = runner.run(scenarios.SCENARIOS[name], seed=seed)
            r.pop("battle", None)
            results.append(r)
        print(summarize(name, results), flush=True)
    print(f"\n總耗時 {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
