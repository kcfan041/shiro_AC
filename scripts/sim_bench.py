"""模擬效能量測（模擬重構第 2 步）。不需要 LLM；計時用包裝函式，不改引擎程式（event_test 摘要不受影響）。

兩種模式：
- battle：直接在 125×125 全圖建一場 Battle，人數可調，情境：
    idle 待命（雙方相距約 180 m，不接觸）／march 行軍（我方往北推進，不接觸）／contact 初次接敵（我方往北推到敵方面前）／
    firefight 密集交火（雙方相距 16～48 m）／smoke 煙霧壓制（交火＋開局每條戰線三團煙）／two_zones 兩個戰區（兩場 Battle 各一半人）
- sim：完整網頁版模擬（連續地圖、作戰 tick 5 秒、2 個戰術步、友軍、背景 LLM 用假的），1～4 位指揮官，
    另外量同步、迷霧過濾、序列化與推送大小（每位指揮官一份 state＋world）。

用法：
  python scripts/sim_bench.py                       # 預設：battle 40、80 人六種情境＋sim 1、2、4 位指揮官
  python scripts/sim_bench.py battle --sizes 40,60,80,100,120 --steps 12
  python scripts/sim_bench.py battle --scenarios firefight,contact --sizes 80
  python scripts/sim_bench.py sim --commanders 1,2,4 --ticks 10
  python scripts/sim_bench.py --json data/bench/base.json   # 另存結果（之後新舊版本比較用）

統計：每一列是一個（情境, 人數）。總耗時去掉前 --warmup 步（contact 不去掉，第一次接敵就是要量的），
列平均／中位數／p95／p99／最大（ms）；細項是每步平均 ms（含呼叫的子函式，彼此會重疊，不能相加）。
"""
import argparse
import asyncio
import functools
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import worldmap
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle, SideIntel
from engine.tactical.units import Unit

SCENARIOS = ["idle", "march", "contact", "firefight", "smoke", "two_zones"]
COLS = 5
CELL = 25

# ---------------------------------------------------------------------- 計時


class Prof:
    """包住要量的函式：累計每次呼叫的耗時（含子函式）與次數。"""

    def __init__(self):
        self.t: dict[str, float] = {}
        self.n: dict[str, int] = {}
        self._orig: list = []

    def wrap(self, owner, attr: str, label: str):
        orig = getattr(owner, attr)
        prof = self

        @functools.wraps(orig)
        def timed(*a, **kw):
            t0 = time.perf_counter()
            try:
                return orig(*a, **kw)
            finally:
                prof.t[label] = prof.t.get(label, 0.0) + time.perf_counter() - t0
                prof.n[label] = prof.n.get(label, 0) + 1
        setattr(owner, attr, timed)
        self._orig.append((owner, attr, orig))

    def take(self) -> tuple[dict, dict]:
        t, n = self.t, self.n
        self.t, self.n = {}, {}
        return t, n

    def restore(self):
        for owner, attr, orig in reversed(self._orig):
            setattr(owner, attr, orig)
        self._orig = []


TACTICAL_FUNCS = [
    (TacticalAI, "plan", "AI 規劃"),
    (TacticalAI, "route", "  尋路"),
    (Battle, "step", "戰鬥結算"),
    (Battle, "detect", "  偵測"),
    (Battle, "fire", "  射擊"),
    (Battle, "trace", "  彈道"),
    (Battle, "suppression_from", "  壓制"),
    (Battle, "apply", "  套用"),
    (Battle, "reveal_fire", "  開火暴露"),
    (Battle, "_morale_tick", "  士氣"),
    (Battle, "update_beliefs", "  暴露推算"),
    (Battle, "face_threats", "  面向"),
]

# ---------------------------------------------------------------------- battle 模式


def spots(g, x0, x1, y0, y1, n, rng, taken):
    out, tries = [], 0
    while len(out) < n and tries < 20000:
        tries += 1
        node = (rng.randint(x0, x1), rng.randint(y0, y1), 0)
        if node not in taken and g.standable(node):
            out.append(node)
            taken.add(node)
    return out


def place(g, scenario: str, n: int, rng) -> list[Unit]:
    """每條戰線（一行格子）一組我方、一組敵方；人數平均分給 5 條戰線。"""
    half = n // 2
    per = [half // COLS + (1 if i < half % COLS else 0) for i in range(COLS)]
    if scenario in ("idle", "march"):
        blue_y, red_y = (2, 15), (105, 122)          # 相距約 180 m
    elif scenario == "contact":
        blue_y, red_y = (2, 12), (57, 70)            # 相距約 90 m，往北推就接觸
    else:
        blue_y, red_y = (38, 47), (52, 62)           # 16～48 m
    units, taken, k = [], set(), 0
    for c in range(COLS):
        x0, x1 = c * CELL + 3, c * CELL + 21
        for p in spots(g, x0, x1, *blue_y, per[c], rng, taken):
            units.append(Unit(f"b{k}", f"blue|c{c}", p, facing=(0, 1)))
            k += 1
        for p in spots(g, x0, x1, *red_y, per[c], rng, taken):
            units.append(Unit(f"r{c}_{k}", "red", p, facing=(0, -1)))
            k += 1
    return units


def make_battle(g, units, scenario):
    b = Battle(g, units, seed=3)
    shared = SideIntel()
    for s in list(b.intel):
        if s.startswith("blue"):
            b.intel[s] = shared
    if scenario == "smoke":
        for c in range(COLS):
            for i in range(3):
                b.smokes.append({"x": (c * CELL + 6 + i * 6) * 2.0, "y": 50 * 2.0, "r": 3.0, "top": 3.5,
                                 "born": b.tick - 1, "expires": b.tick + 999, "by": "bench"})
    ais = {s: TacticalAI(b, s) for s in b.intel}
    return b, ais


def plan_all(b, ais, scenario):
    plans = {}
    for s, ai in ais.items():
        mine = b.alive(s)
        if not mine:
            continue
        if s.startswith("blue"):
            cx = sum(u.pos[0] for u in mine) // len(mine)
            cy = sum(u.pos[1] for u in mine) // len(mine)
            if scenario == "idle":
                intent = Intent("hold")
            elif scenario == "march":
                intent = Intent("march", goal=(cx, min(cy + 40, 55), 0))     # 停在敵方 90 m 外
            elif scenario == "contact":
                intent = Intent("advance", goal=(cx, cy + 40, 0))
            else:
                lk = b.intel[s].last_known
                near = [nd for nd, _ in lk.values() if abs(nd[0] - cx) <= 15]
                intent = Intent("supp_advance", goal=min(near, key=lambda nd: nd[1])) if near else \
                    Intent("advance", goal=(cx, cy + 15, 0))
            plans.update(ai.plan(intent, mine))
        else:
            for c in range(COLS):        # 敵方一條戰線一組（跟 zone 一樣，同一個 AI 分組規劃）
                grp = [u for u in mine if u.id.startswith(f"r{c}_")]
                if grp:
                    plans.update(ai.plan(Intent("hold"), grp))
    return plans


def run_battle(g, scenario: str, n: int, steps: int, prof: Prof) -> list[dict]:
    rng = random.Random(n * 7 + SCENARIOS.index(scenario))
    if scenario == "two_zones":
        # 兩個戰區：左右各一半人、各自一場 Battle（現行「依序執行」，耗時要相加）
        units = place(g, "firefight", n, rng)
        left = [u for u in units if u.pos[0] < 2 * CELL + 12]
        right = [u for u in units if u.pos[0] >= 2 * CELL + 12]
        battles = [make_battle(g, left, "firefight"), make_battle(g, right, "firefight")]
    else:
        battles = [make_battle(g, place(g, scenario, n, rng), scenario)]
    rows = []
    for i in range(steps):
        prof.take()
        t0 = time.perf_counter()
        for b, ais in battles:
            b.step(plan_all(b, ais, scenario))
        total = time.perf_counter() - t0
        t, c = prof.take()
        b0 = battles[0][0]
        rows.append({"total": total, "parts": t, "calls": c,
                     "shots": sum(len(b.last_shots) for b, _ in battles),
                     "spotted": sum(len(b.intel["red"].spotted) for b, _ in battles if "red" in b.intel),
                     "units": sum(len(b.units) for b, _ in battles), "tick": b0.tick})
    return rows

# ---------------------------------------------------------------------- sim 模式


async def run_sim(k: int, ticks: int, prof: Prof, allies: bool = True) -> list[dict]:
    from engine import replay, roster, views, zone as zmod
    from engine.models import Action, Enemy
    from engine.sim import Simulation
    from web import app as webapp
    from web.accounts import Accounts
    from scripts.zone_test import Quiet, card

    root = "./data/sim_bench"
    os.makedirs(root, exist_ok=True)
    for f in ("world.json", "accounts.json"):
        if os.path.exists(os.path.join(root, f)):
            os.remove(os.path.join(root, f))
    cfg = dict(webapp.DEFAULTS, save_path=f"{root}/world.json", replays=f"{root}/replays",
               accounts=f"{root}/accounts.json", allies=allies)
    sim = Simulation(Quiet(), save_path=cfg["save_path"], rng=random.Random(11), tick_seconds=cfg["tick_seconds"])
    game = webapp.Game(cfg, Quiet(), sim=sim, accounts=Accounts(cfg["accounts"], []))
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    cards = ["alpha", "bravo", "charlie", "delta", "echo"]
    targets = ["C2", "C3", "C4", "C3"]
    cids = []
    for i in range(k):
        cid = str(100 + i)
        sim.ensure_commander(cid, f"指揮官{i}")
        sq, _ = await sim.deploy_squad(card(cards[i]), cid)
        dest = targets[i % len(targets)]
        sq.action = Action(type="move", target=dest, path=["B" + dest[1], dest], ordered=True)
        cids.append(cid)
    for j, cell in enumerate(["C2", "C3", "C4", "D2", "D3", "D4"][: 2 + k]):
        sim.world.enemies[f"x{j}"] = Enemy(id=f"x{j}", pos=cell, strength=3, route=[cell], move_every=999,
                                            cooldown=999, tag="garrison", home=cell)
    roster.sync_world(sim.world)
    funcs = TACTICAL_FUNCS + [
        (zmod.Zone, "sync_in", "同步進"), (zmod.Zone, "sync_out", "同步出"), (zmod.Zone, "step", "區域一步"),
        (replay.FrameRecorder, "after", "錄影"), (Simulation, "save", "存檔"),
        (views, "commander_state", "  state 檢視"), (views, "zone_view", "  world 檢視"),
        (webapp, "for_viewer", "  迷霧過濾"),
    ]
    for owner, attr, label in funcs:
        prof.wrap(owner, attr, label)
    rows = []
    try:
        for _ in range(ticks):
            prof.take()
            t0 = time.perf_counter()
            await sim.tick()
            t_sim = time.perf_counter() - t0
            t1 = time.perf_counter()
            size = 0
            for cid in cids:
                size += len(json.dumps({"type": "state", "state": game.state_for(cid)}, ensure_ascii=False))
                size += len(json.dumps({"type": "world", "world": game.world_for(cid)}, ensure_ascii=False))
            t_push = time.perf_counter() - t1
            t, c = prof.take()
            t["推播組裝＋序列化"] = t_push
            units = sum(len(z.battle.units) for z in sim.zones)
            rows.append({"total": t_sim + t_push, "parts": t, "calls": c, "sim": t_sim, "bytes": size,
                         "units": units, "zones": len(sim.zones), "tick": sim.world.tick})
        await sim.settle()
    finally:
        prof.restore()
    return rows

# ---------------------------------------------------------------------- 統計與輸出


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
    return xs[i]


def summarize(name: str, n, rows: list[dict], warmup: int) -> dict:
    use = rows[warmup:] if len(rows) > warmup else rows
    tot = [r["total"] * 1000 for r in use]
    keys = sorted({k for r in use for k in r["parts"]}, key=lambda k: -sum(r["parts"].get(k, 0) for r in use))
    parts = {k: sum(r["parts"].get(k, 0) for r in use) / len(use) * 1000 for k in keys}
    calls = {k: sum(r["calls"].get(k, 0) for r in use) / len(use) for k in keys}
    out = {"scenario": name, "n": n, "steps": len(use), "mean": statistics.mean(tot), "p50": statistics.median(tot),
           "p95": pct(tot, 95), "p99": pct(tot, 99), "max": max(tot), "parts_ms": parts, "calls": calls,
           "units": statistics.mean(r["units"] for r in use)}
    if "bytes" in use[0]:
        out["push_kb"] = statistics.mean(r["bytes"] for r in use) / 1024
        out["zones"] = statistics.mean(r["zones"] for r in use)
    if "shots" in use[0]:
        out["shots"] = statistics.mean(r["shots"] for r in use)
    return out


def print_row(s: dict):
    extra = f"｜射擊 {s['shots']:.0f}/步" if "shots" in s else ""
    if "push_kb" in s:
        extra = f"｜區域 {s['zones']:.1f}｜推送 {s['push_kb']:.1f} KB／拍（全部指揮官）"
    who = f"{s['n']} 位指揮官（{s['units']:.0f} 人）" if s.get("mode") == "sim" else f"{s['n']:>4} 人（實際 {s['units']:.0f}）"
    print(f"{s['scenario']:<10} {who}｜平均 {s['mean']:7.0f}｜中位 {s['p50']:7.0f}"
          f"｜p95 {s['p95']:7.0f}｜p99 {s['p99']:7.0f}｜最大 {s['max']:7.0f} ms{extra}", flush=True)
    top = [(k, v) for k, v in s["parts_ms"].items() if v >= 0.5]
    print("    " + "、".join(f"{k.strip()} {v:.0f}" for k, v in top[:16]), flush=True)


def env_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = branch = "?"
    return {"python": platform.python_version(), "cpu": platform.processor(), "cores": os.cpu_count(),
            "commit": commit, "branch": branch, "hashseed": os.environ.get("PYTHONHASHSEED", "（未設定）"),
            "time": time.strftime("%Y-%m-%d %H:%M")}


def main():
    ap = argparse.ArgumentParser(description="模擬效能量測")
    ap.add_argument("mode", nargs="?", default="all", choices=["all", "battle", "sim"])
    ap.add_argument("--sizes", default="40,80")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--commanders", default="1,2,4")
    ap.add_argument("--ticks", type=int, default=10)
    ap.add_argument("--no-allies", action="store_true", help="sim 模式不開友方電腦小隊（對照用）")
    ap.add_argument("--json", default=None, help="結果另存成 JSON")
    a = ap.parse_args()
    env = env_info()
    print(f"Python {env['python']}｜{env['cpu']}｜{env['cores']} 核｜{env['branch']} {env['commit']}"
          f"｜PYTHONHASHSEED={env['hashseed']}｜{env['time']}")
    if env["hashseed"] == "（未設定）":
        print("  提醒：固定 PYTHONHASHSEED（例如 0）結果才可比較")
    results = []
    if a.mode in ("all", "battle"):
        t0 = time.perf_counter()
        g = worldmap.global_map()
        print(f"\n== battle（全圖 {g.w}×{g.h}，建圖 {time.perf_counter() - t0:.1f}s；每步 ms）==")
        prof = Prof()
        for owner, attr, label in TACTICAL_FUNCS:
            prof.wrap(owner, attr, label)
        try:
            for sc in a.scenarios.split(","):
                for n in [int(x) for x in a.sizes.split(",")]:
                    rows = run_battle(g, sc, n, a.steps, prof)
                    s = summarize(sc, n, rows, 0 if sc == "contact" else a.warmup)
                    s["mode"] = "battle"
                    print_row(s)
                    results.append(s)
        finally:
            prof.restore()
    if a.mode in ("all", "sim"):
        print("\n== sim（完整網頁版模擬；每個作戰 tick＝2 個戰術步＋推播組裝，ms）==")
        for k in [int(x) for x in a.commanders.split(",")]:
            prof = Prof()
            rows = asyncio.run(run_sim(k, a.ticks, prof, allies=not a.no_allies))
            s = summarize(f"sim_k{k}" + ("_noally" if a.no_allies else ""), k, rows, a.warmup)
            s["mode"] = "sim"
            print_row(s)
            results.append(s)
    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf8") as f:
            json.dump({"env": env, "results": results}, f, ensure_ascii=False, indent=1)
        print(f"\n已存：{a.json}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
