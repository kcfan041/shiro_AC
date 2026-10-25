"""地形情境批次（不需要 LLM）：engine/tactical/scenarios.py 的 TERRAIN，每組地形情境和它的全平對照組各跑 N 場，
比較勝率、傷亡、交戰長度、命中率、手榴彈造成的倒地（docs/new/terrain.md §11）。

用法：python scripts/terrain_batch.py [每組場數，預設 40] [--workers=N] [情境前綴...]
  例如 python scripts/terrain_batch.py 60 foxhole_defense reverse_slope
每場獨立（seed 0～N−1），結果與循序跑相同。
"""
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

MAX_TICKS = 60
KEEP_BUSY = True    # 和網頁版一樣：射擊、投擲、救護做到一半的人做完再換計畫（runner.run 的 keep_busy）


def one(job):
    name, seed = job
    os.environ.setdefault("PYTHONHASHSEED", "0")
    from engine.tactical import runner, scenarios
    r = runner.run(scenarios.TERRAIN[name], seed=seed, max_ticks=MAX_TICKS, keep_busy=KEEP_BUSY)
    ev = r["battle"].ev
    shots = {"blue": 0, "red": 0}
    hits = {"blue": 0, "red": 0}
    side = {u.id: u.side for u in r["battle"].units.values()}
    for e in ev:
        if e["type"] == "shot" and e.get("actor") in side:
            shots[side[e["actor"]]] += 1
        elif e["type"] == "hit" and e.get("actor") in side:
            hits[side[e["actor"]]] += 1
    gren = sum(1 for e in ev if e["type"] in ("down", "kia") and "-throw" in str(e.get("grp") or ""))
    return name, {"winner": r["winner"], "ticks": r["ticks"], "blue_lost": r["blue_lost"], "red_lost": r["red_lost"],
                  "blue_n": r["blue_n"], "red_n": r["red_n"], "shots": shots, "hits": hits,
                  "grenades": r["grenades"], "gren_downs": gren, "smokes": r["smokes"]}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from engine.tactical import scenarios
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args and args[0].isdigit() else 40
    prefixes = [a for a in args if not a.isdigit()]
    workers = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--workers=")),
                   max(1, (os.cpu_count() or 2) - 2))
    names = [k for k in scenarios.TERRAIN if not prefixes or any(k.startswith(p) for p in prefixes)]
    t0 = time.time()
    res: dict = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for name, r in ex.map(one, [(k, s) for k in names for s in range(n)], chunksize=2):
            res.setdefault(name, []).append(r)
    print(f"{len(names) * n} 場，{workers} 個行程，{time.time() - t0:.0f} 秒（每場最多 {MAX_TICKS} 步）")
    for name in names:
        rs = res[name]
        k = len(rs)
        win = {w: sum(r["winner"] == w for r in rs) for w in ("blue", "red", "draw")}
        acc = {s: sum(r["hits"][s] for r in rs) / max(1, sum(r["shots"][s] for r in rs)) for s in ("blue", "red")}
        print(f"{name:24s}｜藍勝 {win['blue']:2}／紅勝 {win['red']:2}／平 {win['draw']:2}"
              f"｜藍損 {sum(r['blue_lost'] for r in rs) / k:.2f}/{rs[0]['blue_n']}"
              f"｜紅損 {sum(r['red_lost'] for r in rs) / k:.2f}/{rs[0]['red_n']}"
              f"｜步數 {sum(r['ticks'] for r in rs) / k:4.1f}"
              f"｜命中率 藍 {acc['blue'] * 100:4.1f}% 紅 {acc['red'] * 100:4.1f}%"
              f"｜手榴彈 {sum(r['grenades'] for r in rs) / k:.1f}、炸倒 {sum(r['gren_downs'] for r in rs) / k:.2f}"
              f"｜煙霧 {sum(r['smokes'] for r in rs) / k:.1f}", flush=True)


if __name__ == "__main__":
    main()
