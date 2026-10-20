"""綠方與營救殘部。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import missions, remnants, reserve, roster
from engine.sim import Simulation
from engine.tactical import runner, scenarios
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit
from lib.llm import LLMClient

PATH = "./data/remnant_test.json"


class Fake(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.radio = 0
        self.voice = 0

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            self.voice += 1
            return {"personality": "撐得住的臨時隊長。", "speech": "斷續短句。", "background": "失聯殘部",
                    "sample_lines": ["還在。"], "beliefs": ["不能再丟下任何人。"], "callsign": "Holdout"}
        if "失聯友軍殘部的臨時隊長" in system:
            self.radio += 1
            return {"report": "這裡還有人……撐不久了。"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


def end_standoff(sim, m):
    sim.world.remnants[m["remnant"]].standoff_until = sim.world.tick


async def fresh(seed=1, cell="C4"):
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = Fake()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    c = sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    spec = missions._side("rescue", cell, random.Random(seed))
    sim._start_mission(c, spec, "side")
    m = c.missions[-1]
    return sim, sq, c, m, llm


# ---------------------------------------------------------------------- 戰術層綠方


def test_green_not_hostile_to_blue():
    m = TacMap(w=12, h=16)
    units = [Unit(id="b0", side="blue", pos=(5, 3, 0)), Unit(id="g0", side="green", pos=(6, 3, 0)),
             Unit(id="r0", side="red", pos=(5, 12, 0), facing=(0, -1))]
    b = Battle(m, units, seed=1)
    b.intel = {"blue": b.intel.get("blue") or type(b.intel["red"])(), "green": type(b.intel["red"])(),
               "red": b.intel["red"]}
    assert not b.hostile("blue", "green") and b.hostile("green", "red") and b.hostile("blue", "red")
    assert {u.id for u in b.enemies_of("green")} == {"r0"}
    assert {u.id for u in b.enemies_of("red")} == {"b0", "g0"}
    b.detect("init")
    assert "g0" not in b.intel["blue"].spotted and "b0" not in b.intel["green"].spotted, "友軍不是發現目標"
    assert b.over() is None
    b.units["r0"].down = True
    assert b.over() in ("blue", "green"), "藍＋綠同陣營 → 結束"


def test_two_side_results_unchanged():
    """只有藍紅兩方時與先前一致（同 seed 結果固定）。"""
    a = runner.run(scenarios.SCENARIOS["assault_suppress"], seed=3, max_ticks=30)
    b = runner.run(scenarios.SCENARIOS["assault_suppress"], seed=3, max_ticks=30)
    assert a["positions"] == b["positions"]


# ---------------------------------------------------------------------- 作戰層


def test_spawn_remnant_mission():
    async def go():
        sim, sq, c, m, _ = await fresh()
        r = sim.world.remnants[m["remnant"]]
        assert 2 <= len(r.members) <= 3 and r.leader in {x.name for x in r.members}
        assert m["deadline"] is None, "無時限"
        lead = next(x for x in r.members if x.name == r.leader)
        assert lead.led_before and lead.persona >= 0.35
        assert all(0.2 <= x.persona <= 0.45 for x in r.members if x is not lead)
        assert all(x.traits["caut"] >= x.innate["caut"] + 0.15 for x in r.members), "被打散 → 謹慎"
        es = [sim.world.enemies[e] for e in m["spawned"]]
        assert es and 4 <= sum(e.strength for e in es) <= 5, "圍攻 4～5 人"
        from engine.tactical import worldmap
        tm = worldmap.tmap(r.pos)
        room = next(x for x in tm.rooms if x["id"] == r.room)
        assert all(tm.room_at(tuple(x.tpos)) is room for x in r.members), "殘部都在建築內"
        x0, y0, x1, y1 = room["rect"]
        for e in es:
            for sd in e.soldiers:
                gx = max(x0 - sd["tpos"][0], 0, sd["tpos"][0] - x1)
                gy = max(y0 - sd["tpos"][1], 0, sd["tpos"][1] - y1)
                assert max(gx, gy) >= 3 and tm.room_at(tuple(sd["tpos"])) is None, "圍攻方在建築外"
        os.remove(PATH)
    asyncio.run(go())


def test_rescue_only_cells_with_buildings():
    rng = random.Random(1)
    for _ in range(60):
        spec = missions.roll_side(rng, set(), kind="rescue")
        assert missions.has_buildings(spec["cell"]), spec["cell"]


def test_low_cap_shrinks_or_skips():
    async def go():
        from engine import enemy_ai
        from engine.models import Enemy
        sim, sq, c, m, _ = await fresh()
        filler = enemy_ai.strength_cap(sim.world) - enemy_ai.total_strength(sim.world) - 3
        sim.world.enemies["fill"] = Enemy(id="fill", pos="E5", strength=filler, route=["E5"], move_every=999,
                                          cooldown=999, tag="garrison", home="E5")
        ok = sim._start_mission(c, missions._side("rescue", "B3", random.Random(2)), "side")
        assert ok and sim.world.enemies[c.missions[-1]["spawned"][0]].strength == 3, "名額不足 → 降到 3 人"
        sim.world.enemies["fill"].strength += 3
        before = len(c.missions)
        ok = sim._start_mission(c, missions._side("rescue", "B4", random.Random(3)), "side")
        assert not ok and len(c.missions) == before, "連 3 人都放不下 → 不開"
        os.remove(PATH)
    asyncio.run(go())


def test_standoff_then_timeout():
    async def go():
        sim, sq, c, m, llm = await fresh()
        r = sim.world.remnants[m["remnant"]]
        for _ in range(5):
            await sim.tick()
        assert "C4" not in sim.engagements, "對峙期間不交戰"
        assert all(not x.damage or x.hp == 70 for x in r.members)
        assert "敵軍集結中" in sim.remnant_status_text(m)
        r.standoff_until = sim.world.tick + 1
        await sim.tick()
        await sim.tick()
        assert r.engaged_reason == "敵軍發起攻擊" and "C4" in sim.engagements
        assert any("敵軍發起攻擊" in e for e in c.events)
        os.remove(PATH)
    asyncio.run(go())


def test_standoff_broken_by_adjacent_squad():
    async def go():
        sim, sq, c, m, llm = await fresh()
        r = sim.world.remnants[m["remnant"]]
        await sim.tick()
        sq.pos = "C3"
        await sim.tick()
        assert r.engaged_reason == "援軍接近" and "C4" in sim.engagements, "我方進入相鄰格 → 開打"
        os.remove(PATH)
    asyncio.run(go())


def test_remnant_fights_and_radios():
    async def go():
        sim, sq, c, m, llm = await fresh()
        r = sim.world.remnants[m["remnant"]]
        end_standoff(sim, m)
        for _ in range(6):
            await sim.tick()
        assert "C4" in sim.engagements or r.status != "holding", "殘部與圍攻敵軍交戰（我方小隊還沒到）"
        assert llm.radio >= 1 and any("📻 失聯友軍" in e for e in c.events), "求救訊號在總部事件欄"
        assert r.last_line
        os.remove(PATH)
    asyncio.run(go())


def test_rescue_forms_new_squad():
    async def go():
        sim, sq, c, m, llm = await fresh()
        r = sim.world.remnants[m["remnant"]]
        for eid in m["spawned"]:
            sim.world.enemies[eid].alive = False
        sq.pos = "C4"
        n_squads = len(sim.world.squads)
        survivors = len([x for x in r.members if not x.lost])
        await sim.tick()
        assert r.status == "rescued" or m["remnant"] not in sim.world.remnants
        new = [s for s in sim.world.squads.values() if s.id != sq.id]
        assert len(sim.world.squads) == n_squads + 1, "2 人以上 → 自成新小隊"
        ns = new[0]
        assert ns.callsign.startswith("Holdout") and ns.members[0].personality
        assert survivors <= len(ns.members) <= survivors + 1, "倖存者（任務達成可能再撥補 1 名新兵）"
        assert any(h["title"] == "營救友軍殘部" and h["success"] for h in c.history)
        os.remove(PATH)
    asyncio.run(go())


def test_rescue_merges_when_single_or_cap():
    async def go():
        sim, sq, c, m, llm = await fresh()
        r = sim.world.remnants[m["remnant"]]
        for x in r.members[1:]:
            x.hp = 0
        r.leader = r.members[0].name
        for eid in m["spawned"]:
            sim.world.enemies[eid].alive = False
        sq.members[1].hp = 0
        sq.pos = "C4"
        await sim.tick()
        assert len(sim.world.squads) == 1, "只剩 1 人 → 併入"
        assert r.members == [] and any(x.name == r.leader for x in sq.members)
        os.remove(PATH)
    asyncio.run(go())


def test_merge_full_goes_to_pool():
    async def go():
        sim, sq, c, m, llm = await fresh()
        r = sim.world.remnants[m["remnant"]]
        for x in r.members[1:]:
            x.hp = 0
        for eid in m["spawned"]:
            sim.world.enemies[eid].alive = False
        reserve.pool(sim.world)[:] = reserve.pool(sim.world)[:3]
        sq.pos = "C4"
        await sim.tick()
        assert reserve.remaining(sim.world) == 4, "滿編 → 回預備兵池"
        os.remove(PATH)
    asyncio.run(go())


def test_destroyed_and_surrender_fail():
    async def go():
        sim, sq, c, m, _ = await fresh()
        r = sim.world.remnants[m["remnant"]]
        for x in r.members:
            x.hp = 0
        await sim.tick()
        assert any(h["reason"] == "殘部全滅" for h in c.history)
        sim2, sq2, c2, m2, _ = await fresh(seed=2)
        r2 = sim2.world.remnants[m2["remnant"]]
        for x in r2.members:
            x.hp = 0
        r2.radio_flags.append(f"surrendered:{r2.members[0].name}")
        await sim2.tick()
        assert any(h["reason"] == "殘部投降" for h in c2.history)
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("remnant_test OK")


if __name__ == "__main__":
    main()
