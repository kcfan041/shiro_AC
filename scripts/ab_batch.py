"""A/B 批次（多行程平行，不需要 LLM）：連續地圖進攻 C3（scripts/zone_batch.attack），比較某個改動開／關。

用法：python scripts/ab_batch.py [每組場數，預設 16] [--workers N] [--only on|off]
目前比較的開關：brace（架槍射線被隊友擋住時挪位置／改方向，TacticalAI._unblock_braces）。
每場獨立（各自的 seed 與暫存存檔），結果與循序跑相同。
"""
import asyncio
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES = [(foes, action) for foes in (3, 4) for action in ("supp_advance", "flank")]
# 注意：同一個 worker 行程會跑多場，開關必須每場重設（不能只在 off 時改、on 時沿用）。
TICKS = int(os.environ.get("ATTACK_TICKS", "120"))


def one(job):
    flag, foes, action, seed = job
    os.environ.setdefault("PYTHONHASHSEED", "0")
    from engine.tactical import ai as A
    import scripts.zone_batch as zb
    orig = A.__dict__.setdefault("_ORIG_UNBLOCK", A.TacticalAI._unblock_braces)
    A.TacticalAI._unblock_braces = (lambda self, plans: None) if flag == "off" else orig
    zb.PATH = f"./data/ab_{os.getpid()}.json"
    try:
        r = asyncio.run(zb.attack(seed, foes, action, ticks=TICKS))
    finally:
        if os.path.exists(zb.PATH):
            os.remove(zb.PATH)
    return flag, foes, action, seed, r


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 16
    workers = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--workers=")), max(1, (os.cpu_count() or 2) - 2))
    only = next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--only=")), None)
    flags = [only] if only else ["off", "on"]
    jobs = [(f, foes, action, 9000 + s) for f in flags for foes, action in CASES for s in range(n)]
    t0 = time.time()
    res: dict = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for flag, foes, action, seed, r in ex.map(one, jobs, chunksize=1):
            res.setdefault((flag, foes, action), []).append(r)
    print(f"{len(jobs)} 場，{workers} 個行程，{time.time() - t0:.0f} 秒")
    for foes, action in CASES:
        for f in flags:
            rs = res[(f, foes, action)]
            wins = [r for r in rs if r["win"]]
            print(f"守軍 {foes}｜{action:12}｜{f:3}｜拿下 {len(wins):2}/{len(rs)}"
                  f"（平均 {sum(r['secs'] for r in wins) / max(1, len(wins)):.0f} 秒）"
                  f"｜我陣亡 {sum(r['lost'] for r in rs) / len(rs):.2f}｜全滅 {sum(r['wiped'] for r in rs)}"
                  f"｜擊倒敵 {sum(r['killed'] for r in rs) / len(rs):.2f}", flush=True)


if __name__ == "__main__":
    main()
