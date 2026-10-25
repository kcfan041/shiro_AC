"""A/B 批次（多行程平行，不需要 LLM）：連續地圖進攻 C3（scripts/zone_batch.attack）＋途經敵人的行軍。

用法：python scripts/ab_batch.py [每組場數，預設 16] [--workers=N] [--root=另一份程式碼目錄] [--fail-p=0.05]
  --root：用另一份程式碼（例如 git worktree 的舊版）跑同一組場次，當作對照組。
  成功率附 95% 信賴區間（Wilson）；有對照組時每組做兩比例檢定，p < 0.05 標「＊」。
  --fail-p：有任何一組成功率差異的 p 值小於這個值就以結束碼 1 結束（給自動檢查用）。
  --base-env=名稱=值：對照組在這個環境變數下跑（可重複；沒給 --root 時對照組用同一份程式碼），
    例如 --base-env=SHIRO_RELIEF=0 比較有沒有地形。
  環境變數 TICK_SECONDS、TAC_STEPS：新版的步長（網頁版設定 5、10）。
每場獨立（各自的 seed 與暫存存檔），結果與循序跑相同。
"""
import asyncio
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = [("attack", foes, action) for foes in (3, 4) for action in ("supp_advance", "flank")] + \
        [("march", 2, "move"), ("march", 3, "move")]
TICKS = int(os.environ.get("ATTACK_TICKS", "120"))


def _init(root, env=None):
    os.chdir(root)
    sys.path.insert(0, root)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.update(env or {})               # 在載入引擎之前設定（例如 SHIRO_RELIEF）


def battle_secs(sim) -> float:
    """一個作戰 tick 推進多少秒的戰鬥時間（對照組的舊版程式也適用）。"""
    return min(2.5, sim.step_seconds) * sim.tac_steps


async def march(seed, foes):
    """A3 下令移動到 C3（經 B3），B4 有守軍 foes 人（路線旁、會開火）：抵達率、耗時、傷亡。"""
    import random
    import scripts.zone_batch as zb
    from engine import roster
    from engine.models import Action, Enemy
    from engine.sim import Simulation
    from scripts.zone_test import Quiet, card
    if os.path.exists(zb.PATH):
        os.remove(zb.PATH)
    sim = Simulation(Quiet(), save_path=zb.PATH, rng=random.Random(seed),
                     tick_seconds=int(os.environ.get("TICK_SECONDS", "10")))
    sim.zone_mode = True
    sim.tac_steps = int(os.environ.get("TAC_STEPS", "2"))
    sim.enemy_ai_enabled = False
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sim.world.enemies["x"] = Enemy(id="x", pos="B4", strength=foes, route=["B4"], move_every=999, cooldown=999,
                                   tag="garrison", home="B4")
    roster.sync_world(sim.world)
    await sim.tick()
    sq.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
    t = 0
    for t in range(80):
        await sim.tick()
        if sq.status != "active" or sq.action.type != "move":
            break
    return {"win": sq.pos == "C3" and sq.status == "active", "lost": sum(1 for m in sq.members if m.lost),
            "secs": t * battle_secs(sim), "wiped": sq.status != "active", "killed": foes - (sim.world.enemies["x"].strength
                                                                             if sim.world.enemies["x"].alive else 0)}


def one(job):
    kind, foes, action, seed = job
    import scripts.zone_batch as zb
    zb.PATH = f"./data/ab_{os.getpid()}.json"
    try:
        if kind == "march":
            r = asyncio.run(march(seed, foes))
        else:
            r = asyncio.run(zb.attack(seed, foes, action, ticks=TICKS))
    finally:
        if os.path.exists(zb.PATH):
            os.remove(zb.PATH)
    return kind, foes, action, seed, r


def run(root, n, workers, env=None):
    jobs = [(k, foes, action, 9000 + s) for k, foes, action in CASES for s in range(n)]
    res: dict = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(root, env)) as ex:
        for kind, foes, action, seed, r in ex.map(one, jobs, chunksize=1):
            res.setdefault((kind, foes, action), []).append(r)
    return res


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """成功率的 95% 信賴區間（Wilson，樣本少時比常態近似可靠）。"""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def two_prop_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """兩組成功率相同的雙尾 p 值（合併比例的 z 檢定）。"""
    if not n1 or not n2:
        return 1.0
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(k1 / n1 - k2 / n2) / se
    return math.erfc(z / math.sqrt(2))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 16
    workers = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--workers=")),
                   max(1, (os.cpu_count() or 2) - 2))
    other = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--root=")), None)
    fail_p = next((float(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--fail-p=")), None)
    base_env = dict(a.split("=", 1)[1].split("=", 1) for a in sys.argv[1:] if a.startswith("--base-env="))
    if base_env and not other:
        other = HERE                              # 同一份程式碼、不同環境變數
    roots = [("new", HERE)] + ([("base", other)] if other else [])
    t0 = time.time()
    out = {name: run(root, n, workers, base_env if name == "base" else None) for name, root in roots}
    print(f"{len(CASES) * n * len(roots)} 場，{workers} 個行程，{time.time() - t0:.0f} 秒")
    worst = 1.0
    for key in [(k, f, a) for k, f, a in CASES]:
        for name, _ in roots[::-1]:
            rs = out[name][key]
            wins = [r for r in rs if r["win"]]
            lo, hi = wilson(len(wins), len(rs))
            label = f"{'行軍途經守軍' if key[0] == 'march' else '守軍'} {key[1]}｜{key[2]:12}"
            mark = ""
            if name == "new" and other:
                b = out["base"][key]
                pv = two_prop_p(len(wins), len(rs), sum(r["win"] for r in b), len(b))
                worst = min(worst, pv)
                mark = f"｜p={pv:.2f}{'＊' if pv < 0.05 else ''}"
            print(f"{label}｜{name:4}｜成功 {len(wins):2}/{len(rs)}（{lo * 100:.0f}～{hi * 100:.0f}%）"
                  f"（平均 {sum(r['secs'] or 0 for r in wins) / max(1, len(wins)):.0f} 秒）"
                  f"｜我陣亡 {sum(r['lost'] for r in rs) / len(rs):.2f}｜全滅 {sum(r['wiped'] for r in rs)}"
                  f"｜擊倒敵 {sum(r['killed'] for r in rs) / len(rs):.2f}{mark}", flush=True)
    if fail_p is not None and other and worst < fail_p:
        print(f"有成功率差異顯著（最小 p={worst:.3f} < {fail_p}）")
        sys.exit(1)


if __name__ == "__main__":
    main()
