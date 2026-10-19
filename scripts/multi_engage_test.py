"""兩位指揮官：同名角色卡並存、同格各自戰術（不互射、士氣分開、情報共享）、同格救助與後送、原隊員優先歸隊、
殘部歸屬、支線不重疊。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import missions, reserve, roster
from engine.engagement import label_of
from engine.models import Action, Enemy
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/multi_engage_test.json"
CELL = "C3"


class Fake(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Holdout"}
        if "失聯友軍殘部的臨時隊長" in system:
            return {"report": "……"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def two(foes=4, cell=CELL, seed=3):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Fake(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("100", "甲")
    sim.ensure_commander("200", "乙")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    b, _ = await sim.deploy_squad(card("alpha"), "200")
    if foes:
        sim.world.enemies["x"] = Enemy(id="x", pos=cell, strength=foes, route=[cell], move_every=999,
                                       cooldown=999, tag="garrison", home=cell)
    a.pos = b.pos = cell
    roster.sync_world(sim.world)
    sim.save()
    return sim, a, b


def test_same_card_two_commanders():
    async def go():
        sim, a, b = await two(foes=0)
        assert a.id != b.id and a.card_id == b.card_id == "alpha"
        assert a.commander == "100" and b.commander == "200"
        assert label_of(sim.world, a) == "Alpha（甲）" and label_of(sim.world, b) == "Alpha（乙）"
        os.remove(PATH)
    asyncio.run(go())


def test_same_cell_separate_tactics_shared_intel():
    async def go():
        sim, a, b = await two()
        for _ in range(6):
            await sim.tick()
            if sim.engagements.get(CELL):
                break
        eng = sim.engagements[CELL]
        sa, sb = eng.side_of(a.id), eng.side_of(b.id)
        assert sa != sb and sa in eng.ais and sb in eng.ais and eng.ais[sa] is not eng.ais[sb]
        bt = eng.battle
        assert not bt.hostile(sa, sb) and bt.hostile(sa, "red")
        assert bt.intel[sa] is bt.intel[sb], "同格我方共用情報"
        a.action = Action(type="breach", breach={"prep": "flash"})
        b.action = Action(type="hold")
        await sim.tick()
        await sim.tick()
        assert eng.intents[f"s:{a.id}"].kind == "breach" and eng.intents[f"s:{b.id}"].kind == "hold"
        assert eng.ais[sa].breach.room is not None or eng.ais[sa].breach.done
        assert eng.ais[sb].breach.room is None, "破門狀態只在甲的戰術 AI"
        bt.morale[sa] = 20.0
        bt.morale[sb] = 80.0
        assert bt.morale_state(sa) != bt.morale_state(sb), "士氣分開"
        from engine.tactical.ai import TacticalAI
        foes_a = {u.id for u in bt.enemies_of(sa)}
        assert foes_a and all(bt.units[x].side == "red" for x in foes_a), "甲的敵人只有紅方（不互射）"
        plan = TacticalAI(bt, sa).fire_plan(next(u for u in bt.alive(sa)))
        assert plan is None or bt.units[plan.target].side == "red"
        brief_a = eng.brief(a)
        names_b = {m.name for m in b.members}
        mine_line = brief_a.split("我方：")[1].split("\n")[0]
        assert "同格友軍：Alpha（乙）" in brief_a
        assert all(f"{n}（" not in mine_line for n in names_b - {m.name for m in a.members}), "簡報只列自己的人"
        os.remove(PATH)
    asyncio.run(go())


def test_cross_aid_but_no_cross_drag_out():
    from engine.tactical.sim import Plan
    async def go():
        sim, a, b = await two()
        for _ in range(6):
            await sim.tick()
            if sim.engagements.get(CELL):
                break
        eng = sim.engagements[CELL]
        bt = eng.battle
        ua = next(u for uid, u in bt.units.items() if eng.owner[uid][1] == a.id)
        ub = next(u for uid, u in bt.units.items() if eng.owner[uid][1] == b.id)
        ub.down, ub.down_tick, ub.stabilized = True, bt.tick, False
        ub.pos = (ua.pos[0] + 1, ua.pos[1], 0) if bt.map.standable((ua.pos[0] + 1, ua.pos[1], 0)) else ua.pos
        bt.step({ua.id: Plan(mode="aid", target=ub.id)})
        assert ub.stabilized, "跨隊急救可以"
        ua.pos = (0, ua.pos[1], 0) if bt.map.standable((0, ua.pos[1], 0)) else ua.pos
        ub.pos = (1, ua.pos[1], 0) if bt.map.standable((1, ua.pos[1], 0)) else ub.pos
        if bt.on_edge(ua.pos):
            bt.step({ua.id: Plan(mode="drag", target=ub.id, path=[ua.pos], posture="__exit__")})
            assert ua.exited and not ub.exited, "別隊倒地者不能被拖出這一格"
        os.remove(PATH)
    asyncio.run(go())


def test_left_behind_medevac_and_priority_return():
    async def go():
        sim, a, b = await two()
        for _ in range(6):
            await sim.tick()
            if sim.engagements.get(CELL):
                break
        eng = sim.engagements[CELL]
        victim = b.members[1]
        n0 = reserve.remaining(sim.world)
        reserve.pool(sim.world)[:] = reserve.pool(sim.world)[:10]
        n0 = reserve.remaining(sim.world)
        uid = eng._uid("s", b.id, victim.name)
        eng.battle.units[uid].down = True
        eng.battle.units[uid].down_tick = eng.battle.tick
        victim.down = True
        for x in b.members:
            if x is not victim:
                u = eng.battle.units.get(eng._uid("s", b.id, x.name))
                if u:
                    u.exited = True
        out = {"squads": {}}
        eng._handle_exits(out)
        assert victim not in b.members and uid in eng.left_behind, "原隊撤離 → 留給友軍照顧"
        assert any(n == victim.name for _, n in out.get("left_behind", []))
        sim.world.enemies["x"].alive = False
        res = eng.finish()
        assert res and res[0][2] and reserve.remaining(sim.world) == n0 + 1, "交戰結束 → 後送回池"
        entry = next(r for r in reserve.pool(sim.world) if r["name"] == victim.name)
        assert entry["home_squad"] == b.id and entry["traits"] == victim.traits
        b.members[2].hp = 0
        await sim._recruit(b.id, "後方據點的補充兵")
        assert b.members[-1].name == victim.name, "原小隊補人 → 優先抽回自己人"
        assert all(r["name"] != victim.name for r in reserve.pool(sim.world))
        os.remove(PATH)
    asyncio.run(go())


def test_marked_member_can_be_drawn_by_others_and_random_pick():
    async def go():
        sim, a, b = await two(foes=0)
        from engine.models import Member
        p = reserve.pool(sim.world)
        p.clear()
        for nm in ("Z-01", "Z-02"):
            reserve.give_back(sim.world, Member(name=nm), home=b.id)
        reserve.give_back(sim.world, Member(name="Z-03"))
        e = reserve.draw(sim.world, for_squad=a.id)
        assert e["name"] == "Z-01" and "home_squad" not in e, "別人也抽得到被標記的人，抽走後標記清除"
        e2 = reserve.draw(sim.world, for_squad=b.id, rng=random.Random(1))
        assert e2["name"] == "Z-02" and e2.get("returning"), "原小隊優先"
        reserve.give_back(sim.world, Member(name="Z-04"), home=b.id)
        reserve.clear_home(sim.world, b.id)
        assert all("home_squad" not in r for r in p), "原小隊全滅 → 解除標記"
        os.remove(PATH)
    asyncio.run(go())


def test_no_unbounded_stacking():
    async def go():
        sim, a, b = await two(foes=0)
        total0 = reserve.remaining(sim.world) + sum(len([m for m in s.members if not m.lost])
                                                     for s in sim.world.squads.values())
        for _ in range(4):
            m = b.members[1]
            b.members.remove(m)
            reserve.give_back(sim.world, m, home=b.id)
            await sim._recruit(b.id, "後方據點的補充兵")
            await sim._recruit(a.id, "後方據點的補充兵")
        total = reserve.remaining(sim.world) + sum(len([m for m in s.members if not m.lost])
                                                   for s in sim.world.squads.values())
        assert total <= total0, "互救多輪後全區兵源不增加"
        assert len(a.members) <= a.max_size and len(b.members) <= b.max_size
        os.remove(PATH)
    asyncio.run(go())


def test_remnant_ownership():
    async def go():
        for who, expect in (("100", "100"), ("200", "200"), ("both", "100")):
            sim, a, b = await two(foes=0)
            c1 = sim.world.commanders["100"]
            sim._start_mission(c1, missions._side("rescue", "C4", random.Random(2)), "side")
            m = c1.missions[-1]
            for eid in m["spawned"]:
                sim.world.enemies[eid].alive = False
            a.pos = "C4" if who in ("100", "both") else "A3"
            b.pos = "C4" if who in ("200", "both") else "A3"
            await sim.tick()
            new = [s for s in sim.world.squads.values() if s.id not in (a.id, b.id)]
            assert new, "2 人以上的殘部自成小隊"
            assert new[0].commander == expect, (who, new[0].commander)
            assert any(h["title"] == "營救友軍殘部" and h["success"] for h in c1.history), "開任務的人算成功"
            os.remove(PATH)
    asyncio.run(go())


def test_side_missions_not_overlapping():
    async def go():
        sim, a, b = await two(foes=0)
        c1, c2 = sim.world.commanders["100"], sim.world.commanders["200"]
        for _ in range(20):
            c1.missions = [m for m in c1.missions if m["line"] != "side"]
            c2.missions = [m for m in c2.missions if m["line"] != "side"]
            c1.next_side = c2.next_side = sim.world.tick
            sim._ensure_missions(c1)
            sim._ensure_missions(c2)
            s1 = {m["cell"] for m in c1.missions if m["line"] == "side"}
            s2 = {m["cell"] for m in c2.missions if m["line"] == "side"}
            assert not (s1 & s2), (s1, s2)
        os.remove(PATH)
    asyncio.run(go())


def test_squads_tick_concurrently_and_orders_dont_block_on_llm():
    class Slow(Fake):
        async def chat_json(self, system, user, temperature=0.8, priority=1):
            await asyncio.sleep(0.3)
            return {"action": "keep", "report": "收到"}

    async def go():
        import time
        sim, a, b = await two()
        sim.llm = Slow()
        for _ in range(3):
            await sim.tick()
        a.next_chatter = b.next_chatter = 0
        t0 = time.monotonic()
        await asyncio.gather(sim.issue_order(a.id, "回報"), sim.issue_order(b.id, "回報"))
        assert time.monotonic() - t0 < 0.55, "兩位指揮官同時下令不互相排隊等 LLM"
        a.dialogue.clear()
        b.dialogue.clear()
        sim.world.enemies["x"].alive = False
        sim.engagements.clear()
        for s in (a, b):
            s.contact = None
        from engine import prompts
        t0 = time.monotonic()
        await asyncio.gather(sim._react(a, ["x"], {}), sim._react(b, ["x"], {}))
        assert time.monotonic() - t0 < 0.55
        os.remove(PATH)
    asyncio.run(go())


def test_starter_card_prefers_unused_callsign():
    from types import SimpleNamespace
    import cmds.squad as cog
    async def go():
        sim, a, b = await two(foes=0)
        fake = SimpleNamespace(sim=sim, cfg={"starter_cards": ["alpha", "bravo", "charlie"]})
        c = cog.Squad.starter_card(fake, "300")
        assert c["id"] == "bravo", "alpha 已被別人用 → 新指揮官拿 bravo"
        assert cog.Squad.starter_card(fake, "100")["id"] == "bravo", "自己的 alpha 不算佔用（但乙也有 alpha）"
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("multi_engage_test OK")


if __name__ == "__main__":
    main()
