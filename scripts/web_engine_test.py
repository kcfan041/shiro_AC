"""網頁版引擎：指揮官畫面（隊員面板、失聯凍結、迷霧）、下令回執、自主等級（例行事件只能提議）、
戰術影格與回放、指揮部支援（空投、煙幕、無人機）、標定點。不需要 LLM。"""
import asyncio
import json
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, views
from engine.models import Action, Enemy
from engine.replay import ReplayStore, for_viewer
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/web_engine_test.json"
REPLAYS = "./data/web_engine_test_replays"
CELL = "C3"


class Fake(LLMClient):
    def __init__(self, react=None, order=None):
        super().__init__(base_url="x", model="x")
        self.react = react or {"action": "keep", "report": None}
        self.order = order or {"kind": "order", "confidence": "high", "action": "hold", "report": "收到"}
        self.users = []

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        self.users.append(user)
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Kilo"}
        if "指揮官剛剛在無線電說" in user:
            return dict(self.order)
        if "剛剛發生" in user:
            return dict(self.react)
        return {"report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def setup(llm=None, foes=0, cell=CELL, replays=False, seed=3):
    for p in (PATH,):
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(REPLAYS):
        shutil.rmtree(REPLAYS)
    sim = Simulation(llm or Fake(), save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    if replays:
        sim.replays = ReplayStore(REPLAYS)
    sim.ensure_commander("100", "甲")
    sim.ensure_commander("200", "乙")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    b, _ = await sim.deploy_squad(card("bravo"), "200")
    if foes:
        sim.world.enemies["x"] = Enemy(id="x", pos=cell, strength=foes, route=[cell], move_every=999,
                                       cooldown=999, tag="garrison", home=cell)
        a.pos = cell
    roster.sync_world(sim.world)
    sim.save()
    return sim, a, b


def cleanup():
    if os.path.exists(PATH):
        os.remove(PATH)
    if os.path.exists(REPLAYS):
        shutil.rmtree(REPLAYS)


def test_commander_state_isolated_and_member_panel():
    async def go():
        sim, a, b = await setup()
        st = views.commander_state(sim, "100")
        assert [s["id"] for s in st["squads"]] == [a.id], "只有自己的小隊"
        m = st["squads"][0]["members"][0]
        for k in ("weapon", "ammo", "ammo_cap", "grenades", "smokes", "flashes", "hp", "wounds", "prof", "battles",
                  "suppression", "posture", "leading"):
            assert k in m, k
        assert m["ammo"] == m["ammo_cap"] > 0 and m["prof"]["level"]
        assert "traits" not in m and "persona" not in m, "不給性格數值"
        txt = json.dumps(st, ensure_ascii=False)
        assert b.id not in txt.replace(f'"label": "{b.callsign}"', ""), "不洩漏別人的小隊 id"
        assert any(x["label"] == b.callsign for x in st["map"]["allies"]), "看得到友軍位置"
        json.dumps(st)
        cleanup()
    asyncio.run(go())


def test_unlinked_freezes_at_last_report():
    async def go():
        sim, a, b = await setup()
        v1 = views.squad_view(sim, a)
        assert not v1["stale"]
        await sim.set_link(a.id, False)
        a.members[1].hp = 20
        a.pos = "B3"
        v2 = views.squad_view(sim, a)
        assert v2["stale"] and v2["pos"] == v1["pos"], "失聯：停在最後回報"
        assert v2["members"][1]["hp"] == v1["members"][1]["hp"]
        await sim.set_link(a.id, True)
        v3 = views.squad_view(sim, a)
        assert not v3["stale"] and v3["pos"] == "B3"
        cleanup()
    asyncio.run(go())


def test_receipt_and_standing_order():
    async def go():
        llm = Fake(order={"kind": "order", "understanding": "原地待命，不准自己出去", "confidence": "high",
                          "action": "hold", "standing": "standby", "report": "收到，待命。"})
        sim, a, b = await setup(llm)
        res = await sim.issue_order(a.id, "在我下令前都待命")
        r = res["receipt"]
        assert r["understanding"] and r["label"] in ("照辦", "變通", "拒絕，改做自己的判斷", "維持原行動")
        assert "comply" not in json.dumps(r).replace('"verdict": "comply"', ""), "不含機率"
        assert a.autonomy == "standby" and r["autonomy"] == "待命"
        kinds = [x["type"] for x in sim.drain()]
        assert "order" in kinds and "receipt" in kinds and "report" in kinds
        cleanup()
    asyncio.run(go())


def test_routine_event_can_only_propose():
    async def go():
        llm = Fake(react={"action": "scout", "target": "B3", "report": "要不要我去看 B3？", "reason": "想偵察",
                          "propose": {"action": "scout", "target": "B3", "text": "要不要我去看 B3？"}})
        sim, a, b = await setup(llm)
        a.autonomy = "free"
        rec = sim._experience(a, "resupply", "x")
        await sim._react(a, ["整補完成"], rec, kinds={"resupply"})
        assert a.action.type == "hold", "例行事件不能自己出門"
        assert a.proposal and a.proposal["action"] == "scout" and a.proposal["target"] == "B3"
        assert "例行狀況" in llm.users[-1] and "- scout" not in llm.users[-1].split("可選行動")[1].split("最近通訊")[0]
        r = await sim.accept_proposal(a.id)
        assert r and a.action.type == "scout" and a.action.ordered and a.proposal is None
        cleanup()
    asyncio.run(go())


def test_threat_respects_autonomy_and_marks_auto():
    async def go():
        llm = Fake(react={"action": "scout", "target": "B3", "report": "去看看", "reason": "敵人在附近"})
        sim, a, b = await setup(llm)
        a.autonomy = "standby"
        rec = sim._experience(a, "spotted", "x")
        await sim._react(a, ["察覺敵軍"], rec, kinds={"spotted"})
        assert a.action.type == "hold", "待命：威脅時也不能自己偵察"
        a.autonomy = "free"
        await sim._react(a, ["察覺敵軍"], rec, kinds={"spotted"})
        assert a.action.type == "scout" and a.action.auto and a.action.reason == "敵人在附近"
        assert any(x["type"] == "auto" for x in sim.drain())
        assert await sim.cancel_auto(a.id) and a.action.type == "hold" and not a.action.auto
        cleanup()
    asyncio.run(go())


def test_frames_replay_and_fog():
    async def go():
        sim, a, b = await setup(foes=4, replays=True)
        for _ in range(4):
            await sim.tick()
        eng = sim.engagements.get(CELL)
        assert eng is not None and eng.recorder and eng.recorder.frames
        f = eng.last_frame
        assert f["units"] and all("from" in u and "path" in u for u in f["units"])
        tv = views.tactical_view(sim, "100", CELL)
        assert tv and len(tv["terrain"]) == 25 and any(u["side"] == "own" for u in tv["units"])
        assert views.tactical_view(sim, "200", CELL) is None, "沒有小隊在場 → 看不到"
        spotted = set(eng.battle.intel["blue"].spotted)
        vis = for_viewer(f, "100", sim.world)
        enemy_ids = {u["id"] for u in vis["units"] if u["side"] == "enemy"}
        assert enemy_ids <= spotted, "迷霧：只畫看得到的敵人"
        hidden = [u for u in f["units"] if u["kind"] == "e" and u["id"] not in spotted and not u["dead"]]
        if hidden:
            assert all(h["id"] not in enemy_ids for h in hidden)
        gm = for_viewer(f, "100", sim.world, gm=True)
        assert len([u for u in gm["units"] if u["kind"] == "e"]) >= len(enemy_ids)
        sim.world.enemies["x"].alive = False
        for sd in sim.world.enemies["x"].soldiers:
            sd["down"] = True
        for uid, u in eng.battle.units.items():
            if u.side == "red":
                u.down = True
        for _ in range(3):
            await sim.tick()
        assert CELL not in sim.engagements
        lst = sim.replays.list_for("100")
        assert lst and lst[0]["cell"] == CELL and not sim.replays.list_for("200")
        rp = sim.replays.load(lst[0]["id"])
        assert rp["frames"] and rp["terrain"]
        assert any(x["type"] == "replay" for x in sim.drain())
        cleanup()
    asyncio.run(go())


def test_supports_airdrop_smoke_drone():
    async def go():
        sim, a, b = await setup(foes=3)
        for _ in range(3):
            await sim.tick()
        eng = sim.engagements[CELL]
        mine = [u for uid, u in eng.battle.units.items() if eng.owner[uid][1] == a.id and u.alive]
        for u in mine:
            u.ammo = 5
            u.grenades = u.smokes = u.flashes = 0
        cx, cy = mine[0].pos[0], mine[0].pos[1]
        ok, _ = await sim.call_support("100", "airdrop", CELL, [cx, cy])
        assert ok
        ok2, msg = await sim.call_support("200", "airdrop", CELL, [cx, cy])
        assert not ok2, "別人在這格沒有小隊 → 不能叫"
        smokes0 = len(eng.battle.smokes)
        ok, _ = await sim.call_support("100", "smoke", CELL, [12, 14])
        ok, _ = await sim.call_support("100", "drone", CELL, [12, 18])
        assert any(s["kind"] == "smoke" for s in views.tactical_view(sim, "100", CELL)["supports"])
        for _ in range(3):
            await sim.tick()
        sp = {s["kind"]: s for s in sim.supports}
        assert sp["airdrop"]["state"] == "landed"
        if sp["airdrop"]["result"].endswith("補給"):
            assert any(u.ammo > 5 for u in mine), "空投補到彈藥"
        assert len(eng.battle.smokes) >= smokes0 + 1 or any(s["by"] == "support" for s in eng.battle.smokes) \
            or sp["smoke"]["state"] == "done"
        assert sp["drone"]["state"] in ("active", "done", "lost")
        if sp["drone"]["state"] == "active":
            reds = {u.id for u in eng.battle.units.values() if u.side == "red" and u.alive
                    and ((u.pos[0] - 12) ** 2 + (u.pos[1] - 18) ** 2) ** 0.5 <= 7}
            assert reds <= set(eng.battle.intel["blue"].spotted), "無人機揭露範圍內的敵人"
        evs = [x for x in sim.drain() if x["type"] == "support"]
        assert evs and all(x["commander"] == "100" for x in evs)
        cleanup()
    asyncio.run(go())


def test_idle_airdrop_and_drone():
    async def go():
        sim, a, b = await setup()
        a.ammo = 10
        for m in a.members:
            m.ammo = 3
        ok, _ = await sim.call_support("100", "airdrop", a.pos, [12, 12])
        assert ok
        sim.world.enemies["y"] = Enemy(id="y", pos="E3", strength=3, route=["E3"], move_every=999, cooldown=999,
                                       tag="garrison", home="E3")
        ok, _ = await sim.call_support("100", "drone", "E3", [12, 12])
        assert ok, "無人機可以飛去沒有小隊的格子"
        for _ in range(3):
            await sim.tick()
        assert a.ammo == 100 and all(m.ammo is None for m in a.alive_members())
        assert sim.world.commanders["100"].intel["enemies"].get("E3", {}).get("est") == 3
        cleanup()
    asyncio.run(go())


def test_mark_point_order():
    async def go():
        llm = Fake(order={"kind": "order", "understanding": "集火標定點", "confidence": "high", "action": "keep",
                          "report": "收到"})
        sim, a, b = await setup(llm, foes=3)
        for _ in range(3):
            await sim.tick()
        sim.rng = random.Random(0)
        a.tendencies["execution"] = "literal"
        res = None
        for _ in range(5):
            res = await sim.mark_order(a.id, "fire", [12, 20])
            if res and res["receipt"]["verdict"] in ("comply", "adapt", "keep"):
                break
        assert res and a.action.mark == [12, 20] and a.action.mark_kind == "fire"
        await sim.tick()
        eng = sim.engagements[CELL]
        assert eng.intents[f"s:{a.id}"].focus == (12, 20, 0)
        cleanup()
    asyncio.run(go())


def test_fast_pace_background_and_quick_order():
    class Slow(Fake):
        async def chat_json(self, system, user, temperature=0.8, priority=1):
            await asyncio.sleep(0.4)
            return await super().chat_json(system, user, temperature, priority)

    async def go():
        import time
        sim, a, b = await setup(Slow(), foes=3)
        sim.tac_steps, sim.react_gap, sim.background, sim.tick_seconds = 2, 2, True, 5
        t0 = time.monotonic()
        for _ in range(3):
            await sim.tick()
        assert time.monotonic() - t0 < 1.0, "背景反應：tick 不等 LLM"
        eng = sim.engagements.get(CELL)
        assert eng is not None and eng.battle.tick >= 5, "一個作戰 tick 跑兩步戰術層"
        assert sim.step_seconds == 2.5
        await sim.settle()
        res = await sim.quick_order(a.id, "hide")
        assert res and res["receipt"]["quick"]
        if res["obedience"] != "refuse":
            assert a.action.type == "hide", "快捷命令當場生效（不等 LLM）"
        assert a.id in sim.typing or sim.bg_tasks, "口頭回覆在背景"
        await sim.settle()
        assert a.dialogue[-1]["who"] == "leader"
        kinds = [x["type"] for x in sim.drain()]
        assert "typing" in kinds and "receipt" in kinds
        bad = await sim.quick_order(a.id, "scout", "E5")
        assert bad.get("error")
        cleanup()
    asyncio.run(go())


def test_retreat_edge_cell_not_stuck():
    async def go():
        sim, a, b = await setup(foes=4, cell="A3")
        a.pos = "A3"
        from engine import roster as r
        r.sync_world(sim.world)
        for _ in range(3):
            await sim.tick()
        eng = sim.engagements.get("A3")
        assert eng is not None
        a.action = Action(type="retreat", ordered=True)
        intent = eng.squad_intent(a)
        assert intent.kind == "retreat" and eng.exit_cell("A3", intent.toward), "邊緣格撤退方向要有相鄰格"
        start = {m.name: tuple(m.tpos) for m in a.alive_members()}
        for _ in range(4):
            await sim.tick()
            if a.pos != "A3":
                break
        moved = a.pos != "A3" or any(tuple(m.tpos) != start.get(m.name) for m in a.alive_members() if m.tpos)
        assert moved, "撤退時會動"
        cleanup()
    asyncio.run(go())


def test_hold_takes_cover_and_auto_push():
    async def go():
        sim, a, b = await setup(foes=3)
        for _ in range(2):
            await sim.tick()
        eng = sim.engagements[CELL]
        ai = eng.ais[eng.side_of(a.id)]
        threats = ai.threats()
        if threats:
            mine = [eng.battle.units[u] for u, o in eng.owner.items() if o[1] == a.id and eng.battle.units[u].alive]
            exposed = [u for u in mine if ai.exposure(u.pos, threats) > 0]
            from engine.tactical.ai import Intent
            plans = ai.plan(Intent("hold"), mine)
            if exposed:
                assert any(plans[u.id].mode == "move" for u in exposed), "固守：暴露的人會找掩體"
        a.autonomy = "free"
        a.action = Action(type="hold")
        import engine.sim as S
        S.AUTO_PUSH_TICKS = 1
        sim.tac_steps = 1
        for _ in range(4):
            await sim.tick()
            if a.action.auto or not sim.in_engagement(a):
                break
        if sim.in_engagement(a):
            assert a.action.auto and a.action.type in ("advance", "supp_advance", "flank", "assault", "breach")
        S.AUTO_PUSH_TICKS = 4
        cleanup()
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("web_engine_test OK")


if __name__ == "__main__":
    main()
