"""隊員人格（C 模式）與預備兵池、遊蕩生化人。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import persona, reserve, scenario
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/reserve_test.json"


class Fake(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.voice_calls = 0
        self.recruit_calls = 0

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            self.voice_calls += 1
            return {"personality": "沉默但可靠的老手。", "speech": "短句。", "background": "從預備兵力抽調。",
                    "sample_lines": ["收到。"], "beliefs": ["活下來。"], "callsign": "Ember"}
        if "新兵" in system:
            self.recruit_calls += 1
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def fresh(seed=1):
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = Fake()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    return sim, sq, llm


def test_persona_blank_and_effective():
    rng = random.Random(3)
    vals = [persona.blank(rng) for _ in range(200)]
    for v in vals:
        assert set(v["traits"]) == set(persona.TRAITS)
        assert all(-1 <= x <= 1 for x in v["traits"].values())
        assert v["persona"] == persona.ROOKIE_PERSONA
    spread = max(abs(x) for v in vals for x in v["innate"].values())
    assert 0.2 < spread <= 0.5, spread
    from engine.models import Member
    m = Member(name="X")
    m.traits = {"aggr": 0.8, "caut": 0, "comp": 0, "bond": 0}
    m.persona = 0.5
    assert abs(persona.effective(m, "aggr") - 0.4) < 1e-9
    assert persona.describe_short(m) == "衝勁十足"


def test_card_leader_has_voice_members_blank():
    async def go():
        sim, sq, _ = await fresh()
        lead = sq.members[0]
        assert lead.personality and lead.speech and lead.led_before
        assert lead.traits["caut"] > 0 and lead.traits["comp"] > 0, "從角色卡個性對應出謹慎、冷靜"
        for m in sq.members[1:]:
            assert not m.personality, "開局小隊其他人是白紙"
        os.remove(PATH)
    asyncio.run(go())


def test_world_starts_with_pool():
    w = scenario.new_world()
    assert reserve.remaining(w) == reserve.POOL_SIZE
    names = [r["name"] for r in reserve.pool(w)]
    assert len(set(names)) == len(names)
    assert all(not r.get("personality") for r in reserve.pool(w)), "池裡只存數字"


def test_recruit_draws_from_pool_without_llm():
    async def go():
        sim, sq, llm = await fresh()
        n0 = reserve.remaining(sim.world)
        sq.members[1].hp = 0
        await sim._recruit(sq.id, "後方據點的補充兵")
        assert reserve.remaining(sim.world) == n0 - 1
        assert llm.recruit_calls == 0, "補員不再即時呼叫 LLM"
        newbie = sq.members[-1]
        assert newbie.persona == persona.ROOKIE_PERSONA and not newbie.personality
        os.remove(PATH)
    asyncio.run(go())


def test_pool_exhausted_stops_and_notifies():
    async def go():
        sim, sq, _ = await fresh()
        reserve.pool(sim.world).clear()
        sq.members[1].hp = 0
        n = len(sq.members)
        await sim._recruit(sq.id, "後方據點的補充兵")
        assert len(sq.members) == n, "池空了不補"
        c = sim.world.commanders["1"]
        assert any("預備兵力已耗盡" in e for e in c.events)
        assert await sim.card_from_reserve(3, "x") is None
        os.remove(PATH)
    asyncio.run(go())


def test_reinforce_from_pool_leader_voice_only():
    async def go():
        sim, sq, llm = await fresh()
        n0 = reserve.remaining(sim.world)
        c = sim.world.commanders["1"]
        new = await sim._reinforce(c)
        assert new is not None
        used = n0 - reserve.remaining(sim.world)
        assert used == len(new.members) and 3 <= used <= 4
        assert llm.voice_calls == 1, "只為隊長寫一次人格（含呼號）"
        assert new.callsign.startswith("Ember")
        assert new.members[0].personality and all(not m.personality for m in new.members[1:])
        reserve.pool(sim.world)[:] = reserve.pool(sim.world)[:1]
        c.reinforce_ready = 0
        assert await sim._reinforce(c) is None, "池裡少於 2 人不派增援"
        os.remove(PATH)
    asyncio.run(go())


def test_wanderer_pickup_and_full_goes_to_pool():
    async def go():
        sim, sq, llm = await fresh()
        oid = sim.world.next_id("o")
        sim.world.objects[oid] = {"type": "wanderer", "cell": sq.pos, "mission": None, "until": None}
        sq.members[1].hp = 0
        n0 = reserve.remaining(sim.world)
        await sim.tick()
        assert oid not in sim.world.objects
        w = sq.members[-1]
        assert w.hp == 60 and w.persona == 0.15
        assert reserve.remaining(sim.world) == n0, "遊蕩生化人不扣池"
        assert any("遊蕩" in x["text"] for x in sq.experiences)
        reserve.pool(sim.world)[:] = reserve.pool(sim.world)[:5]
        oid2 = sim.world.next_id("o")
        sim.world.objects[oid2] = {"type": "wanderer", "cell": sq.pos, "mission": None, "until": None}
        await sim.tick()
        assert reserve.remaining(sim.world) == 6, "滿編時進預備兵池"
        os.remove(PATH)
    asyncio.run(go())


def test_wanderer_expires_and_enemy_takes():
    async def go():
        sim, sq, _ = await fresh()
        from engine.models import Enemy
        w = sim.world
        w.objects["oa"] = {"type": "wanderer", "cell": "C4", "mission": None, "until": w.tick + 1}
        w.objects["ob"] = {"type": "wanderer", "cell": "D4", "mission": None, "until": None}
        w.enemies["x"] = Enemy(id="x", pos="D4", strength=2, route=["D4"], move_every=999, cooldown=999,
                               tag="garrison", home="D4")
        await sim.tick()
        await sim.tick()
        assert "oa" not in w.objects, "時間到離開"
        assert "ob" not in w.objects, "敵軍經過帶走"
        os.remove(PATH)
    asyncio.run(go())


def test_wanderers_spawn_randomly():
    async def go():
        sim, sq, _ = await fresh()
        w = sim.world
        w.next_wanderer = 1
        for _ in range(3):
            await sim.tick()
        assert any(o["type"] == "wanderer" for o in w.objects.values())
        os.remove(PATH)
    asyncio.run(go())


def test_force_summary_shows_reserve():
    async def go():
        sim, sq, _ = await fresh()
        assert f"預備兵力：剩 {reserve.POOL_SIZE} 人" in sim.force_summary("1")
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("reserve_test OK")


if __name__ == "__main__":
    main()
