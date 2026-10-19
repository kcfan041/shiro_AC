"""營救殘部批次：沒人救 vs 我方小隊在不同時間抵達。不需要 LLM。

用法：python scripts/remnant_batch.py [每組場數，預設 20]
"""
import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import missions, remnants, scenario
from engine.models import Action
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/remnant_batch.json"
CELL = "C4"
NEAR = "C3"
MAX_T = 70


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Holdout"}
        if "失聯友軍殘部的臨時隊長" in system:
            return {"report": "……"}
        return {"action": "keep", "report": None}


async def one(seed: int, arrive: str | None):
    """arrive：None＝沒人救；"standoff"＝對峙中進入相鄰格；"start"＝開打當下抵達；"late"＝開打 5 tick 後抵達。"""
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    c = sim.ensure_commander("1", "甲")
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        sq, _ = await sim.deploy_squad(json.load(f), "1")
    sq.pos = scenario.BASE
    sim._start_mission(c, missions._side("rescue", CELL, random.Random(seed)), "side")
    m = c.missions[-1]
    r = sim.world.remnants[m["remnant"]]
    n0 = len(r.members)
    when = {"standoff": 8, "start": remnants.STANDOFF, "late": remnants.STANDOFF + 5}.get(arrive)
    engaged_at = None
    sent = False
    lost0 = sum(1 for x in sq.members if x.lost)
    for _ in range(MAX_T):
        if when is not None and not sent and sim.world.tick >= when:
            sq.pos = NEAR
            sq.action = Action(type="move", target=CELL, path=[CELL], duration=1, ordered=True)
            sent = True
        if sent and sq.pos == CELL and sim.in_engagement(sq) and sq.action.type not in ("assault", "breach"):
            sq.action = Action(type="breach", breach={})
        await sim.tick()
        if engaged_at is None and r.engaged_reason:
            engaged_at = sim.world.tick
        if r.status != "holding":
            break
    saved = len([x for x in r.members if not x.lost]) if r.status == "rescued" else 0
    if r.status == "rescued":
        new = [s for s in sim.world.squads.values() if s.id != "alpha"]
        saved = sum(len([x for x in s.members if not x.lost]) for s in new) or saved
        merged = [x for x in sq.members if x.joined and x.joined > 0 and x.name not in {"R-12", "R-15", "M-03"}]
        saved = max(saved, len(merged))
    last = sim.world.tick - engaged_at if engaged_at else None
    return {"status": r.status, "n0": n0, "saved": saved, "lost": sum(1 for x in sq.members if x.lost) - lost0,
            "last": last, "reason": r.engaged_reason}


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    t0 = time.time()
    for arrive, label in ((None, "沒人救"), ("standoff", "對峙中抵達相鄰格（T8）"),
                          ("start", "開打當下抵達（T20）"), ("late", "開打 5 tick 後抵達（T25）")):
        res = [await one(100 + s, arrive) for s in range(n)]
        st = {}
        for x in res:
            st[x["status"]] = st.get(x["status"], 0) + 1
        lasts = [x["last"] for x in res if x["last"] is not None and x["status"] != "holding"]
        print(f"{label:24}｜" + " ".join(f"{k}{v}" for k, v in sorted(st.items()))
              + f"｜救回 {sum(x['saved'] for x in res) / n:.1f}/{sum(x['n0'] for x in res) / n:.1f} 人"
              + f"｜我方陣亡 {sum(x['lost'] for x in res) / n:.2f}"
              + (f"｜開打後撐 {sum(lasts) / len(lasts):.1f} tick" if lasts else ""), flush=True)
    if os.path.exists(PATH):
        os.remove(PATH)
    print(f"耗時 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
