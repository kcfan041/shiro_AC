"""卡頓量測：把存檔複製到暫存目錄，用網頁版設定（tick 5 秒、2 步）跑 N 拍，印出每拍各部分耗時。
不連網路、不改正式存檔。LLM 用假的：每次呼叫延遲 LLM_DELAY 秒（模擬真實 LLM 回應時間），
可以看出哪些 LLM 呼叫是在 tick 裡同步等待的（會讓整個伺服器卡住）。

用法：python scripts/lag_probe.py [存檔，預設 data/web_world.json] [拍數，預設 40] [--llm-delay=3]
"""
import asyncio
import os
import random
import shutil
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sim import Simulation
from scripts.zone_test import Quiet
from web.app import DEFAULTS, Game
from web.accounts import Accounts


class SlowQuiet(Quiet):
    """假 LLM：延遲 delay 秒；記錄呼叫來源（在 tick 鎖內同步等待的會被標出來）。"""

    def __init__(self, delay: float, sim_ref: list):
        super().__init__()
        self.delay = delay
        self.sim_ref = sim_ref
        self.blocking: dict[str, int] = {}

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        sim = self.sim_ref[0] if self.sim_ref else None
        if sim is not None and sim.lock.locked():
            where = next((f.name for f in reversed(traceback.extract_stack()[:-1])
                          if f.filename.endswith("sim.py") and f.name not in ("tick", "chat_json")), "?")
            self.blocking[where] = self.blocking.get(where, 0) + 1
        await asyncio.sleep(self.delay)
        return await super().chat_json(system, user, temperature, priority)


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = args[0] if args else "./data/web_world.json"
    n = int(args[1]) if len(args) > 1 else 40
    delay = next((float(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--llm-delay=")), 0.0)
    tmp = tempfile.mkdtemp(prefix="lag_")
    save = os.path.join(tmp, "w.json")
    shutil.copyfile(src, save)
    cfg = dict(DEFAULTS, save_path=save, replays=os.path.join(tmp, "replays"), accounts=os.path.join(tmp, "a.json"))
    ref: list = []
    llm = SlowQuiet(delay, ref)
    sim = Simulation(llm, save_path=save, rng=random.Random(1))
    ref.append(sim)
    game = Game(cfg, llm, sim=sim, accounts=Accounts(cfg["accounts"], []))
    rows = []
    for i in range(n):
        t0 = time.perf_counter()
        await game.step_once()
        dt = time.perf_counter() - t0
        rows.append((dt, dict(sim.prof or {})))
        top = "、".join(f"{k.strip()} {v * 1000:.0f}" for k, v in sorted((sim.prof or {}).items(),
                                                                          key=lambda kv: -kv[1])[:4])
        print(f"{i:3} {'作戰' if game.phase == 0 else '戰術'} {dt * 1000:6.0f} ms｜{top}", flush=True)
    await sim.settle()
    dts = sorted(r[0] for r in rows)
    print(f"\n平均 {sum(dts) / len(dts) * 1000:.0f} ms｜p95 {dts[int(len(dts) * .95)] * 1000:.0f}｜最慢 {dts[-1] * 1000:.0f}"
          f"｜超過 1 秒 {sum(1 for d in dts if d > 1)} 拍")
    agg = {}
    for _, p in rows:
        for k, v in p.items():
            k = "區域（合計）" if k.startswith("區域") else k.strip()
            agg[k] = agg.get(k, 0.0) + v
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {k}：合計 {v:.1f} s（每拍 {v / n * 1000:.0f} ms）")
    if llm.blocking:
        print("在 tick 鎖內同步等待 LLM 的呼叫：", llm.blocking)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
