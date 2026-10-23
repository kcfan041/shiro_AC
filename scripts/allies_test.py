"""友方電腦小隊（engine/allies.py）：建立、守家、支援玩家交戰、打完回家、不建活動區域、不影響玩家行軍、
傷亡不進預備兵力池。不需要 LLM。"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import allies, reserve, roster, scenario, zone as zmod
from engine.models import Action, Enemy
from engine.sim import Simulation
from scripts.zone_test import Quiet, card

PATH = "./data/allies_test.json"


async def setup(seed=3):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.allies_enabled = True
    sim.tac_steps = 1
    sim.enemy_ai_enabled = False
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    sim.ensure_commander("100", "甲")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    roster.sync_world(sim.world)
    return sim, a


def done():
    if os.path.exists(PATH):
        os.remove(PATH)


def test_created_guard_no_zone():
    async def go():
        sim, a = await setup()
        await sim.tick()
        al = allies.ally_squads(sim.world)
        assert len(al) == 2, [s.id for s in al]
        assert all(s.commander is None and s.ai["task"] == "guard" for s in al)
        assert {s.pos for s in al} <= set(allies.homes()) | {scenario.BASE}
        regions = zmod.build_zones(sim.world)
        assert len(regions) == 1 and a.pos in regions[0], "友軍不自己建活動區域"
        pool = reserve.remaining(sim.world)
        for _ in range(4):
            await sim.tick()
        assert reserve.remaining(sim.world) == pool, "友軍不動用預備兵力池"
        done()
    asyncio.run(go())


def test_support_then_return():
    async def go():
        sim, a = await setup()
        await sim.tick()
        home_cells = allies.homes()
        target = next(c for c in scenario.CELLS if scenario.distance(c, home_cells[0]) == 1 and c != scenario.BASE
                      and c not in home_cells)
        a.pos = target
        for m in a.members:
            m.gpos = None
        foe_cell = next(c for c in scenario.adjacent(target) if c not in home_cells and c != scenario.BASE)
        sim.world.enemies["x"] = Enemy(id="x", pos=foe_cell, strength=2, route=[foe_cell], move_every=999,
                                       cooldown=999, tag="garrison", home=foe_cell)
        roster.sync_world(sim.world)
        sent = None
        for _ in range(40):
            await sim.tick()
            sup = [s for s in allies.ally_squads(sim.world) if s.ai["task"] == "support"]
            if sup:
                sent = sup[0]
                break
        assert sent is not None, "附近玩家交戰 → 派一支支援"
        radio = [it for it in sim.drain() if it.get("type") == "ally" and it.get("squad") == a.id]
        assert any("支援" in it["text"] and "秒" in it["text"] for it in radio), "友軍出發時用無線電告訴被支援的小隊"
        assert len([s for s in allies.ally_squads(sim.world) if s.ai["task"] == "support"]) == 1, "一場只派一支"
        sim.world.enemies.clear()
        z = sim.engagements.get(a.pos)
        if z is not None:
            for uid in [u for u, o in z.owner.items() if o[0] == "e"]:
                z.battle.units.pop(uid, None)
                z.owner.pop(uid, None)
            for it in z.battle.intel.values():
                it.spotted.clear()
                it.last_known.clear()
        back = False
        for _ in range(120):
            await sim.tick()
            if sent.ai["task"] in ("return", "guard"):
                back = True
            if sent.ai["task"] == "guard" and sent.pos == sent.ai["home"]:
                break
        assert back, ("交戰結束 → 回家", sent.ai)
        assert sent.pos == sent.ai["home"], (sent.pos, sent.ai)
        done()
    asyncio.run(go())


def test_player_march_unaffected():
    async def go():
        sim, a = await setup()
        await sim.tick()
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        for _ in range(60):
            await sim.tick()
            if a.action.type == "hold":
                break
        assert a.pos == "C3", a.pos
        c = zmod.squad_center(a)
        assert c is not None
        done()
    asyncio.run(go())


def test_destroyed_rebuilds_without_pool():
    async def go():
        sim, a = await setup()
        await sim.tick()
        s = allies.ally_squads(sim.world)[0]
        for m in s.members:
            m.hp = 0
        pool = reserve.remaining(sim.world)
        await sim.tick()
        assert s.status == "destroyed"
        sim.world.tick += allies.REBUILD_EVERY
        await sim.tick()
        new = sim.world.squads[s.id]
        assert new.status == "active" and len(new.alive_members()) == 4, "過一陣子在據點重新編成"
        assert reserve.remaining(sim.world) == pool, "傷亡不進／不取預備兵力池"
        done()
    asyncio.run(go())


def test_medevac_to_base_and_rejoin():
    """請求後送：友軍出發、接手倒地的隊員、拖回 A3；原小隊繼續（編制仍算）；原小隊回 A3 整補時歸隊。"""
    async def go():
        from engine.sim import REST_FULL
        from engine.tactical import worldmap
        sim, a = await setup()
        await sim.tick()
        a.pos = "B2"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        hurt = a.members[1]
        z = sim.engagements.get(a.pos)
        uid = next(u for u, o in z.owner.items() if o[2] is hurt)
        z.battle.units[uid].down = True
        z.battle.units[uid].stabilized = True
        hurt.down = hurt.stabilized = True
        a.action = Action(type="hold")
        ok, msg = await sim.request_medevac(a.id)
        assert ok, msg
        ally = sim.world.squads[sim.world.medevac[0]["ally"]]
        picked = False
        for _ in range(150):
            await sim.tick()
            if hurt in ally.members:
                picked = True
                assert hurt not in a.members and a.roster_size() == 4, "接手後原小隊編制仍算"
            if hurt in sim.world.resting:
                break
        assert picked, "友軍接到傷員"
        assert hurt in sim.world.resting, ("送回 A3 休養", ally.pos, ally.ai, hurt.gpos)
        assert a.roster_size() == 4 and not sim.pending_recruits
        assert not any(m.name == hurt.name for s in sim.world.squads.values() for m in s.members), "不會有分身"
        a.action = Action(type="move", target="A3", path=["A3"], duration=3, ordered=True)
        for _ in range(60 + REST_FULL):
            await sim.tick()
            if hurt in a.members:
                break
        assert hurt in a.members and not hurt.down, "原小隊回 A3 整補時歸隊"
        assert a.roster_size() == 4 and a.away == 0
        done()
    asyncio.run(go())


def test_medevac_meets_enemy_fights_then_carries():
    """後送途中遇敵：不照行軍命令丟下傷員往前走，停下來打；打完回頭扛起傷員，照樣送回 A3。"""
    async def go():
        sim, a = await setup()
        await sim.tick()
        a.pos = "B2"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        hurt = a.members[1]
        z = sim.engagements.get(a.pos)
        uid = next(u for u, o in z.owner.items() if o[2] is hurt)
        z.battle.units[uid].down = True
        z.battle.units[uid].stabilized = True
        hurt.down = hurt.stabilized = True
        a.action = Action(type="hold")
        ok, msg = await sim.request_medevac(a.id)
        assert ok, msg
        ally = sim.world.squads[sim.world.medevac[0]["ally"]]
        spawned = hot = False
        for _ in range(200):
            await sim.tick()
            job = sim.world.medevac[0] if sim.world.medevac else None
            if job and job["state"] == "carrying" and not spawned:
                cell = (ally.action.path or [ally.pos])[0]
                sim.world.enemies["x"] = Enemy(id="x", pos=cell, strength=2, route=[cell], move_every=999,
                                               cooldown=999, tag="garrison", home=cell)
                roster.sync_world(sim.world)
                spawned = True
            elif spawned and job and sim.in_engagement(ally):
                hot = True
                assert ally.action.type != "move", "遇敵不照行軍命令往前走（會丟下傷員）"
            if hurt in sim.world.resting or hurt.lost:
                break
        assert spawned and hot, "帶回途中遇到敵人"
        assert not hurt.lost and hurt in sim.world.resting, ("打完繼續送回 A3", ally.pos, ally.ai, hurt.gpos)
        done()
    asyncio.run(go())


def _radio(sim, got: list):
    got += [it for it in sim.drain() if it.get("type") == "ally"]


def test_request_support_cell_stays_then_returns():
    """指揮官指定一格請友軍：最近的空閒友軍過去，那格沒交戰也守到時限；同時只能請一支；有無線電（含預估秒數）。"""
    async def go():
        sim, a = await setup()
        await sim.tick()
        sim.drain()
        ok, msg = await sim.request_ally_support("100", "C2")
        assert ok, msg
        sent = next(s for s in allies.ally_squads(sim.world) if s.ai.get("requested") == "100")
        assert sent.ai["task"] == "support" and sent.ai["goal"] == "C2"
        ok2, msg2 = await sim.request_ally_support("100", "C4")
        assert not ok2 and "已經" in msg2, msg2
        got = []
        _radio(sim, got)
        assert got and got[0]["commander"] == "100" and "C2" in got[0]["text"] and "秒" in got[0]["text"], got
        for _ in range(60):
            await sim.tick()
            _radio(sim, got)
            if sent.ai.get("arrived"):
                break
        assert sent.pos == "C2" and sent.ai["task"] == "support", ("到了守在那裡", sent.pos, sent.ai)
        assert any("已到 C2" in it["text"] for it in got), [it["text"] for it in got]
        for _ in range(4):
            await sim.tick()
        assert sent.ai["task"] == "support", "那格沒交戰也不馬上回家"
        sim.world.tick = sent.ai["since"] + allies.SUPPORT_TIMEOUT
        await sim.tick()
        _radio(sim, got)
        assert sent.ai["task"] in ("return", "guard") and not sent.ai.get("requested"), sent.ai
        assert any("返回" in it["text"] for it in got)
        ok3, msg3 = await sim.request_ally_support("100", "C4")
        assert ok3 or "空閒" in msg3, msg3
        done()
    asyncio.run(go())


def test_ally_radio_to_supported_and_medevac_squad():
    """友軍無線電：自動支援時給被支援的小隊（進隊長對話紀錄），後送出發與接到傷員時給原小隊。"""
    async def go():
        sim, a = await setup()
        await sim.tick()
        sim.drain()
        a.pos = "B2"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        hurt = a.members[1]
        z = sim.engagements.get(a.pos)
        uid = next(u for u, o in z.owner.items() if o[2] is hurt)
        z.battle.units[uid].down = True
        z.battle.units[uid].stabilized = True
        hurt.down = hurt.stabilized = True
        a.action = Action(type="hold")
        got = []
        _radio(sim, got)
        ok, msg = await sim.request_medevac(a.id)
        assert ok, msg
        for _ in range(60):
            await sim.tick()
            _radio(sim, got)
            if any("接到" in it["text"] for it in got):
                break
        texts = [it["text"] for it in got if it["squad"] == a.id]
        assert any("後送" in t and "秒" in t for t in texts), texts
        assert any("接到" in t for t in texts), texts
        assert any(d["who"] == "ally" for d in a.dialogue), "隊長看得到友軍無線電"
        assert "友軍無線電" in sim.dialogue_text(a, n=20)
        done()
    asyncio.run(go())


async def _down(sim, a, idx):
    z = sim.engagements.get(a.pos)
    hurt = a.members[idx]
    uid = next(u for u, o in z.owner.items() if o[2] is hurt)
    z.battle.units[uid].down = True
    z.battle.units[uid].stabilized = True
    hurt.down = hurt.stabilized = True
    return hurt, uid, z


def test_medevac_timeout_per_leg_not_total():
    """後送逾時只算這一趟、沒交火的時間：已經接到人在送回途中，不會因為從請求起算的總時間到了就取消
    （原本 T93 請求、交火拖到 T213，離 A3 不到一格時取消，把傷員丟在地上）；真的一直到不了才取消，
    傷員交還原小隊並說明位置。"""
    async def go():
        sim, a = await setup()
        await sim.tick()
        a.pos = "B2"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        hurt, _, _ = await _down(sim, a, 1)
        a.action = Action(type="hold")
        ok, msg = await sim.request_medevac(a.id)
        assert ok, msg
        for _ in range(80):
            await sim.tick()
            if sim.world.medevac and sim.world.medevac[0]["state"] == "carrying":
                break
        job = sim.world.medevac[0]
        assert job["state"] == "carrying"
        sim.world.tick = job["since"] + allies.MEDEVAC_TIMEOUT + 5      # 從請求起算早就超過
        await sim.tick()
        assert sim.world.medevac and sim.world.medevac[0]["state"] == "carrying", "送回途中不因總時間取消"
        got = []
        _radio(sim, got)
        sim.world.medevac[0]["clock"] = allies.MEDEVAC_TIMEOUT - 1      # 這一趟真的太久
        await sim.tick()
        _radio(sim, got)
        assert not sim.world.medevac, "這一趟太久 → 取消"
        assert hurt in a.members and hurt.home_squad is None, "傷員交還原小隊"
        assert any("送不回" in it["text"] and hurt.name in it["text"] for it in got), [it["text"] for it in got]
        done()
    asyncio.run(go())


def test_medevac_scattered_takes_near_first():
    """傷員分散：只接走到身邊的（原本靠近第一個就把兩人都算接到），先送回去，再回頭接遠的那個；
    無線電講清楚哪個還沒接到。"""
    async def go():
        sim, a = await setup()
        await sim.tick()
        a.pos = "B2"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        near, _, z = await _down(sim, a, 1)
        far, fuid, _ = await _down(sim, a, 2)
        b = z.battle
        ox, oy = b.units[fuid].pos[0], b.units[fuid].pos[1]
        spot = None
        for dy in range(12, 22):                 # 往南（離 A3 更遠）找一格站得住、沒人佔的位置
            for dx in range(-3, 4):
                n = (ox + dx, oy + dy, 0)
                if any(u.pos == n for u in b.units.values()):
                    continue
                if any(b.map.step_cost(m, n) is not None for m, _ in b.map.neighbors(n)):
                    spot = n
                    break
            if spot:
                break
        assert spot, "找得到放遠處傷員的位置"
        b.units[fuid].pos = spot
        for u in b.units.values():               # 其他人不要守在遠處傷員旁邊（不然會被當成同時接到）
            assert max(abs(u.pos[0] - spot[0]), abs(u.pos[1] - spot[1])) > allies.PICKUP_R or u.id == fuid
        a.action = Action(type="hold")
        got = []
        _radio(sim, got)
        ok, msg = await sim.request_medevac(a.id)
        assert ok, msg
        ally = sim.world.squads[sim.world.medevac[0]["ally"]]
        first = None
        for _ in range(400):
            await sim.tick()
            _radio(sim, got)
            if first is None and (near in ally.members or far in ally.members):
                first = [m.name for m in ally.members if m.home_squad == a.id]
            if near in sim.world.resting and far in sim.world.resting:
                break
        texts = [it["text"] for it in got]
        assert first and len(first) == 1, ("只接走到身邊的", first, texts)
        assert any("離得遠" in t for t in texts), texts
        assert near in sim.world.resting and far in sim.world.resting, ("兩人都送回", texts, ally.pos, far.gpos)
        assert any("回頭去接" in t for t in texts), texts
        done()
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("allies_test OK")


if __name__ == "__main__":
    main()
