"""網頁版（background）：作戰 tick 不能在鎖內同步等 LLM。用一個「卡住、放行才回答」的假 LLM，
確認增援、殘部無線電、殘部自成小隊在 LLM 沒回來時 tick 照樣跑完、鎖已放開；放行後結果才寫進世界。不需要 LLM。"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import reserve
from engine.sim import Simulation
from lib.llm import LLMClient
from scripts.remnant_test import card, end_standoff, fresh

PATH = "./data/lock_test.json"
TICK_LIMIT = 5.0          # 一拍最多等這麼久（真的卡在 LLM 就會超過）


class Gate(LLMClient):
    """呼叫後一直等到 open 才回答。"""

    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.open = asyncio.Event()
        self.calls = 0

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        self.calls += 1
        await self.open.wait()
        if "個性與說話方式" in system:
            return {"personality": "沉穩的新任隊長。", "speech": "短句。", "background": "預備兵力",
                    "sample_lines": ["收到。"], "beliefs": ["先活下來。"], "callsign": "Gate"}
        if "失聯友軍殘部的臨時隊長" in system:
            return {"report": "這裡還有人……撐不久了。"}
        return {"action": "keep", "report": None}


async def tick(sim):
    await asyncio.wait_for(sim.tick(), TICK_LIMIT)
    assert not sim.lock.locked(), "tick 結束鎖要放開"


def test_reinforce_llm_outside_lock():
    async def go():
        if os.path.exists(PATH):
            os.remove(PATH)
        llm = Gate()
        sim = Simulation(llm, save_path=PATH, rng=random.Random(2))
        sim.background = True
        sim.enemy_ai_enabled = False
        sim.world.enemies.clear()
        sim._ensure_missions = lambda c: None
        sim._wanderers = lambda: None
        c = sim.ensure_commander("1", "甲")
        llm.open.set()
        await sim.deploy_squad(card("alpha"), "1")
        llm.open.clear()
        asked = {"n": 0}

        def need(cc):
            asked["n"] += 1
            return asked["n"] == 1
        sim._need_reinforce = need
        n_squads, pool = len(sim.world.squads), reserve.remaining(sim.world)
        await tick(sim)
        assert llm.calls >= 1, "增援有呼叫 LLM（寫隊長人格）"
        assert len(sim.world.squads) == n_squads, "LLM 還沒回來 → 還沒編成"
        assert reserve.remaining(sim.world) < pool, "人已經從預備兵池抽出（不會被別人抽走）"
        await tick(sim)
        llm.open.set()
        await sim.settle()
        new = [s for s in sim.world.squads.values() if s.callsign.startswith("Gate")]
        assert len(new) == 1 and new[0].commander == c.id, [s.callsign for s in sim.world.squads.values()]
        assert any("增援" in e for e in c.events)
        os.remove(PATH)
    asyncio.run(go())


def test_remnant_radio_outside_lock():
    async def go():
        sim, sq, c, m, _ = await fresh()
        llm = Gate()
        sim.llm = llm
        sim.background = True
        r = sim.world.remnants[m["remnant"]]
        end_standoff(sim, m)
        for _ in range(6):
            await tick(sim)
        assert llm.calls >= 1, "殘部無線電有呼叫 LLM"
        assert not any("📻 失聯友軍" in e for e in c.events), "LLM 還沒回來 → 還沒有無線電"
        llm.open.set()
        await sim.settle()
        assert r.last_line and any("📻 失聯友軍" in e for e in c.events)
        os.remove("./data/remnant_test.json")
    asyncio.run(go())


def test_remnant_new_squad_outside_lock():
    async def go():
        sim, sq, c, m, _ = await fresh()
        llm = Gate()
        sim.llm = llm
        sim.background = True
        r = sim.world.remnants[m["remnant"]]
        for eid in m["spawned"]:
            sim.world.enemies[eid].alive = False
        sq.pos = "C4"
        n_squads = len(sim.world.squads)
        await tick(sim)
        assert r.status == "rescued" or m["remnant"] not in sim.world.remnants
        assert len(sim.world.squads) == n_squads, "臨時隊長的人格還沒寫好 → 還沒編成"
        llm.open.set()
        await sim.settle()
        new = [s for s in sim.world.squads.values() if s.id != sq.id]
        assert len(new) == 1 and new[0].callsign.startswith("Gate"), [s.callsign for s in new]
        assert new[0].members[0].personality
        os.remove("./data/remnant_test.json")
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("lock_test OK")


if __name__ == "__main__":
    main()
