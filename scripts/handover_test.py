"""階段 3、4：換人帶隊（口氣跟著變、白紙第一次開口才寫個性、里程碑重寫、原隊長恢復）與每人一份信念。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import scenario
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/handover_test.json"


class Fake(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.voices = []
        self.systems = []

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            name = user.split("代號：")[1].split("\n")[0]
            self.voices.append(name)
            return {"personality": f"{name} 的個性：火爆、愛罵人。", "speech": f"{name} 式粗口。", "background": "x",
                    "sample_lines": [f"{name}：上啊！"], "beliefs": [f"{name} 不信任指揮部。"], "callsign": ""}
        self.systems.append(system)
        if "獨處整理思緒" in system:
            return {"beliefs": [{"text": "反思後的新信念。", "origin": "experience"}],
                    "tendencies": {"execution": "own"}, "summary": "s", "thought": "t"}
        return {"action": "keep", "report": "回報。"}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def fresh():
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = Fake()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(5))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    sq.pos = "B3"
    return sim, sq, llm


def test_handover_voice_and_beliefs():
    async def go():
        sim, sq, llm = await fresh()
        await sim.tick()
        lead = sq.members[0]
        orig_beliefs = [b["text"] for b in sq.beliefs]
        assert sq.speaker == lead.name
        nxt = sq.members[2]
        nxt.persona = 0.6
        lead.down = True
        await sim._check_handover(sq)
        assert sq.speaker == nxt.name, "人格值最高的接手"
        assert nxt.name in llm.voices, "白紙第一次開口才寫個性"
        assert nxt.personality and nxt.led_before
        assert sq.acting_leader() == nxt.name
        sysmsg = sim._system_prompt(sq)
        assert "火爆" in sysmsg and "接手指揮" in sysmsg, "系統提示改用接手者的人格"
        assert [b["text"] for b in sq.beliefs] == [f"{nxt.name} 不信任指揮部。"], "載入接手者自己的信念"
        assert [b["text"] for b in lead.beliefs] == orig_beliefs, "原隊長信念存回他身上"
        out = sim.drain()
        assert any("接手指揮" in (x.get("text") or "") for x in out)
        os.remove(PATH)
    asyncio.run(go())


def test_reflect_updates_only_speaker():
    async def go():
        sim, sq, llm = await fresh()
        await sim.tick()
        lead, other = sq.members[0], sq.members[1]
        other_before = list(other.beliefs)
        sq.reflect_reasons = ["測試"]
        await sim._reflect(sq)
        assert lead.beliefs[0]["text"] == "反思後的新信念。"
        assert other.beliefs == other_before, "反思只更新目前發言人"
        os.remove(PATH)
    asyncio.run(go())


def test_original_leader_resumes_after_resupply():
    async def go():
        sim, sq, llm = await fresh()
        await sim.tick()
        lead = sq.members[0]
        lead.down = True
        await sim.tick()
        assert sq.speaker != lead.name
        sq.pos = scenario.BASE
        for _ in range(8):
            await sim.tick()
        assert not lead.down
        assert sq.speaker == lead.name, "據點整補後原隊長恢復指揮"
        os.remove(PATH)
    asyncio.run(go())


def test_led_before_has_priority():
    async def go():
        sim, sq, llm = await fresh()
        await sim.tick()
        a, b = sq.members[1], sq.members[2]
        a.persona, b.persona = 0.7, 0.2
        b.led_before = True
        sq.members[0].down = True
        await sim.tick()
        assert sq.speaker == b.name, "曾當過隊長的優先"
        os.remove(PATH)
    asyncio.run(go())


def test_revoice_at_milestone():
    async def go():
        sim, sq, llm = await fresh()
        await sim.tick()
        lead = sq.members[0]
        lead.voice_level = 0.3
        lead.persona = 0.45
        n = len(llm.voices)
        await sim.tick()
        assert len(llm.voices) == n + 1 and lead.voice_level == 0.45, "人格值跨 0.4 → 重寫個性"
        await sim.tick()
        assert len(llm.voices) == n + 1, "不重複寫"
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("handover_test OK")


if __name__ == "__main__":
    main()
