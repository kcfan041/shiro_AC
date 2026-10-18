import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import obedience, scenario
from engine.models import Action, World
from engine.sim import REFLECT_GAP, SUPPLY_COOLDOWN, SUPPLY_DELAY, Simulation
from lib.llm import LLMClient

PATH = "./data/belief_test.json"


class FakeLLM(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.order_reply = {"kind": "order", "confidence": "high", "risky": False, "action": "keep", "report": "收到"}
        self.reflects = 0

    async def chat_json(self, system, user, temperature=0.8):
        if "獨處整理思緒" in system:
            self.reflects += 1
            return {"beliefs": [{"text": "服從指揮官是小隊存活的最佳方式。", "origin": "protocol"},
                                {"text": "指揮官的命令有時來源可疑。", "origin": "experience"}],
                    "tendencies": {"consult": "often", "initiative": "normal", "execution": "intent", "verify": True},
                    "summary": "收到過奇怪的命令。", "thought": "得小心。"}
        if "新出廠的機器人步兵小隊" in system:
            return {"callsign": "Kilo", "leader": {"name": "K-55「灰燼」", "personality": "沉穩", "speech": "短",
                                                   "background": "新編", "sample_lines": ["一", "二"]},
                    "members": [{"name": "K-56", "trait": "愛哼歌"}, {"name": "K-57", "trait": "多疑"}],
                    "initial_beliefs": ["任務第一。"], "tendencies": {"execution": "literal"}}
        if "新兵" in system:
            return {"name": "N-01", "trait": "x"}
        if "副官" in system:
            return {"summary": "s", "suggestions": []}
        if "指揮官剛剛在無線電說" in user:
            return dict(self.order_reply)
        return {"action": "keep", "report": None, "interp": "ok"}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def run(sim, n):
    for _ in range(n):
        await sim.tick()


async def main():
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = FakeLLM()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(4))
    sim.world.enemies.clear()
    c = sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")

    print("== 角色卡轉信念 ==")
    assert [b["origin"] for b in sq.beliefs] == ["protocol", "protocol"], sq.beliefs
    assert sq.tendencies["execution"] == "literal" and obedience.score(sq) > obedience.score(sq, True)
    bravo, _ = await sim.deploy_squad(card("bravo"), "1")
    assert obedience.score(bravo) < obedience.score(sq), "老兵服從較低"
    assert obedience.self_ratio(bravo) > 0.5 and bravo.long_memory
    echo, _ = await sim.deploy_squad(card("echo"), "1")
    assert echo.tendencies["verify"] is True

    print("== 無法對應的命令觸發反思 ==")
    llm.order_reply = {"kind": "order", "confidence": "none", "action": "keep", "report": "?"}
    await sim.issue_order("alpha", "發動古代魔法")
    assert sq.reflect_reasons, "要排入反思"
    sq.last_reflect = -99
    await run(sim, 1)
    assert llm.reflects >= 1 and not sq.reflect_reasons
    assert any(b["origin"] == "experience" for b in sq.beliefs)
    assert sq.tendencies == {"consult": "often", "initiative": "normal", "execution": "intent", "verify": True}
    assert "奇怪的命令" in sq.long_memory
    ref = [e for e in sq.experiences if e["kind"] == "reflect"][-1]
    assert ref["added"] == ["指揮官的命令有時來源可疑。"] and ref["tendencies"].get("verify") == (False, True)
    assert not any("反思" in m for m in sq.memory), "反思不進短期記憶"

    print("== 反思冷卻 ==")
    n = llm.reflects
    sim._want_reflect(sq, "測試")
    await run(sim, 1)
    assert llm.reflects == n, "冷卻中不反思"
    await run(sim, REFLECT_GAP)
    assert llm.reflects == n + 1

    print("== 驗證傾向：危險命令要求確認 ==")
    llm.order_reply = {"kind": "order", "confidence": "high", "risky": True, "confirm_request": True,
                       "action": "move", "target": "C2", "report": "請確認身分"}
    r = await sim.issue_order("alpha", "直衝 C2")
    assert r["outcome"] == "要求確認命令來源" and sq.pending_confirm == "直衝 C2" and sq.action.type != "move"
    llm.order_reply = {"kind": "order", "confidence": "high", "risky": True, "confirm_request": False,
                       "action": "move", "target": "C2", "report": "確認，出發"}
    r = await sim.issue_order("alpha", "確認，是我，執行")
    assert sq.pending_confirm is None and sq.action.type == "move", r

    print("== 補給空投 ==")
    sq.action = Action(type="hold")
    sq.pos = "B2"
    sq.ammo = 10
    ok, msg = await sim.request_supply("alpha")
    assert ok, msg
    ok2, msg2 = await sim.request_supply("alpha")
    assert not ok2 and "在途" in msg2
    await run(sim, SUPPLY_DELAY)
    assert sq.ammo >= 70, sq.ammo
    assert any("送達" in e for e in c.events), c.events
    ok3, msg3 = await sim.request_supply("alpha")
    assert not ok3 and "冷卻" in msg3

    print("== 空投被攔截 / 小隊離開 ==")
    c.supply_ready = 0
    sim.rng = random.Random(0)
    from engine.models import Enemy
    sim.world.enemies["ex"] = Enemy(id="ex", pos="B3", strength=2, route=["B3"], move_every=999, cooldown=999)
    sq.ammo = 10
    await sim.request_supply("alpha")
    sim.world.objects[next(k for k, v in sim.world.objects.items() if v["type"] == "airdrop")]["arrive"] = 0
    sim.rng = random.Random(1)
    drops = sim._airdrops()
    assert drops and ("攔截" in drops[0][1] or "落地" in drops[0][1])
    del sim.world.enemies["ex"]
    c.supply_ready = 0
    await sim.request_supply("alpha")
    sq.pos = "A1"
    oid = next(k for k, v in sim.world.objects.items() if v["type"] == "airdrop")
    sim.world.objects[oid]["arrive"] = 0
    sim._airdrops()
    assert any(o["type"] == "crate" and o["cell"] == "B2" for o in sim.world.objects.values()), "留下補給箱"

    print("== 口頭請求補給 ==")
    c.supply_ready = 0
    sim.world.objects.clear()
    llm.order_reply = {"kind": "order", "confidence": "high", "supply": True, "action": "keep", "report": "請求補給"}
    await sim.issue_order("alpha", "我幫你們叫空投")
    assert any(o["type"] == "airdrop" for o in sim.world.objects.values())

    print("== 指揮部自動增援 ==")
    sim.world.objects.clear()
    for s in (sq, bravo, echo):
        for m in s.members:
            m.hp = 0
    await run(sim, 2)
    kilo = sim.world.squads.get("kilo")
    assert kilo and kilo.commander == "1" and kilo.pos == scenario.BASE, list(sim.world.squads)
    assert kilo.beliefs and kilo.beliefs[0]["origin"] == "protocol"
    assert sim.new_cards and sim.new_cards[0]["id"] == "kilo"
    out = [x for x in sim.drain() if x["type"] == "reinforce"]
    assert out and out[0]["squad"] == "kilo"
    n_squads = len(sim.world.squads)
    await run(sim, 3)
    assert len(sim.world.squads) == n_squads, "增援冷卻"

    print("== 戰情圖與兵力 ==")
    c.intel["enemies"]["C2"] = {"est": 3, "tick": sim.world.tick}
    m = sim.intel_map("1")
    print(m)
    assert "C2!3" in m and "A1K" in m.replace("基", "")
    print(sim.force_summary("1"))

    print("== 舊存檔相容 ==")
    with open(PATH, encoding="utf8") as f:
        d = json.load(f)
    d["squads"]["kilo"]["protocol"] = 90
    d["squads"]["kilo"]["deviation"] = 5
    del d["squads"]["kilo"]["beliefs"]
    World.from_dict(d)

    os.remove(PATH)
    print("belief_test OK")


if __name__ == "__main__":
    asyncio.run(main())
