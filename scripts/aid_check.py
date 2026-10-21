"""救援診斷（多行程，不需要 LLM）：連續地圖進攻場次中，統計
- 同一個倒地者同時有幾個我方隊員圍在旁邊／往他移動（搶人）；
- 有人連續拖人超過 4 步的次數（原地拖著打轉）。

用法：python scripts/aid_check.py [場數，預設 24] [--root=另一份程式碼目錄]
"""
import asyncio
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _init(root):
    os.chdir(root)
    sys.path.insert(0, root)


def one(seed):
    import scripts.zone_batch as zb
    from engine.tactical import sim as S
    zb.PATH = f"./data/aid_{os.getpid()}.json"
    st = {"downed_steps": 0, "crowd": 0, "drag_long": 0, "drag_steps": 0}
    streak = {}
    orig = S.Battle.step

    def step(b, plans):
        orig(b, plans)
        downed = [d for d in b.units.values() if d.side.startswith("blue") and d.down and not d.dead]
        for d in downed:
            st["downed_steps"] += 1
            near = [uid for uid, p in plans.items() if b.units[uid].side == d.side and not b.units[uid].down
                    and ((p.mode in ("aid", "drag") and p.target == d.id) or
                         (p.mode == "move" and p.path and max(abs(p.path[-1][0] - d.pos[0]),
                                                              abs(p.path[-1][1] - d.pos[1])) <= 1))]
            if len(near) >= 2:
                st["crowd"] += 1
        for uid, p in plans.items():
            if p.mode == "drag":
                st["drag_steps"] += 1
                streak[uid] = streak.get(uid, 0) + 1
                if streak[uid] == 5:
                    st["drag_long"] += 1
            else:
                streak[uid] = 0
    S.Battle.step = step
    try:
        for foes, act in ((3, "supp_advance"), (3, "flank"), (4, "flank")):
            asyncio.run(zb.attack(seed, foes, act, ticks=80))
    finally:
        S.Battle.step = orig
        if os.path.exists(zb.PATH):
            os.remove(zb.PATH)
    return st


def run(root, n, workers):
    tot = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(root,)) as ex:
        for st in ex.map(one, [9300 + i for i in range(n)]):
            for k, v in st.items():
                tot[k] = tot.get(k, 0) + v
    return tot


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 24
    other = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--root=")), None)
    workers = max(1, (os.cpu_count() or 2) - 4)
    for name, root in ([("base", other)] if other else []) + [("new", HERE)]:
        t = run(root, n, workers)
        d = max(1, t.get("downed_steps", 0))
        print(f"{name:4}｜倒地步數 {t['downed_steps']}｜兩人以上搶同一人 {t['crowd']}（{100 * t['crowd'] / d:.0f}%）"
              f"｜拖人步數 {t['drag_steps']}｜連續拖 5 步以上 {t['drag_long']} 次", flush=True)


if __name__ == "__main__":
    main()
