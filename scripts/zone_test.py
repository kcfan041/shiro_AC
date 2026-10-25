"""連續地圖（zone 模式）：真實行軍跨格、跨格接敵與射擊、敵軍進出活動區域、兩位玩家區域合併、撤退、偵察、
任務抵達。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, zone as zmod
from engine.models import Action, Enemy
from engine.sim import Simulation
from engine.tactical import worldmap
from lib.llm import LLMClient

PATH = "./data/zone_test.json"


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Kilo"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def setup(seed=3, two=False):
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(Quiet(), save_path=PATH, rng=random.Random(seed))
    sim.zone_mode = True
    sim.tac_steps = 1
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("100", "甲")
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    b = None
    if two:
        sim.ensure_commander("200", "乙")
        b, _ = await sim.deploy_squad(card("bravo"), "200")
    roster.sync_world(sim.world)
    return sim, a, b


def done():
    if os.path.exists(PATH):
        os.remove(PATH)


def test_march_crosses_cells_continuously():
    async def go():
        sim, a, _ = await setup()
        await sim.tick()
        assert all(m.gpos for m in a.members), "部署後有全圖座標"
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        seen_cells, jumps = [], 0
        last = {m.name: tuple(m.gpos) for m in a.members}
        for _ in range(60):
            await sim.tick()
            for m in a.alive_members():
                p = tuple(m.gpos)
                if max(abs(p[0] - last[m.name][0]), abs(p[1] - last[m.name][1])) > 8:
                    jumps += 1
                last[m.name] = p
            if not seen_cells or seen_cells[-1] != a.pos:
                seen_cells.append(a.pos)
            if a.action.type == "hold":
                break
        assert jumps == 0, "沒有瞬移"
        assert seen_cells[:3] == ["A3", "B3", "C3"], seen_cells
        assert a.pos == "C3" and a.action.type == "hold", (a.pos, a.action.type)
        assert any(x["kind"] == "arrive" for x in a.experiences)
        done()
    asyncio.run(go())


def test_cross_cell_contact_and_fire():
    async def go():
        sim, a, _ = await setup()
        a.pos = "B3"
        a.autonomy = "defend"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999, cooldown=999,
                                       tag="garrison", home="C3")
        sim.world.enemies["x"].tcell = "C3"
        oy = worldmap.cell_origin("C3")[1]
        ox = worldmap.cell_origin("C3")[0]
        m0 = worldmap.global_map()
        spots = [zmod.nearest_standable(m0, (ox + 10 + 2 * i, oy + 2, 0)) for i in range(3)]
        roster.sync_world(sim.world)
        for sd, sp in zip(sim.world.enemies["x"].soldiers, spots):
            sd["gpos"] = list(sp)
            sd["facing"] = [0, -1]
        by = worldmap.cell_origin("B3")[1]
        for i, m in enumerate(a.members):
            m.gpos = list(zmod.nearest_standable(m0, (ox + 9 + 2 * i, by + 20, 0)))
            m.facing = [0, 1]
        cross = 0
        for _ in range(16):
            await sim.tick()
            z = sim.engagements.get("B3")
            b = z.battle
            for s in b.last_shots:
                f, t = worldmap.cell_of(s["from"][0] / 2, s["from"][1] / 2), worldmap.cell_of(s["to"][0] / 2, s["to"][1] / 2)
                if f != t:
                    cross += 1
        assert a.pos == "B3", "小隊還在 B3"
        assert sim.in_engagement(a), "隔壁格的敵人也算接敵"
        assert cross > 0, "有跨格射擊"
        assert any(x["kind"] == "contact" for x in a.experiences)
        done()
    asyncio.run(go())


def test_enemy_enters_and_leaves_zone():
    async def go():
        sim, a, _ = await setup()
        sim.enemy_ai_enabled = True
        await sim.tick()
        e = Enemy(id="p9", pos="C3", strength=2, route=["C3", "B3", "C3"], move_every=1, cooldown=1, tag="patrol",
                  home="C3")
        sim.world.enemies["p9"] = e
        roster.sync_world(sim.world)
        z = sim.zones[0]
        assert "C3" not in z.cells and "B3" in z.cells, z.cells
        entered = left = False
        for _ in range(12):
            await sim.tick()
            z = sim.zones[0]
            uids = [u for u, o in z.owner.items() if o[1] == "p9"]
            if e.pos in z.cells:
                entered = entered or bool(uids)
                gs = [sd["gpos"] for sd in e.soldiers if sd.get("gpos")]
                assert not gs or any(worldmap.cell_of(g[0], g[1]) in z.cells for g in gs)
            else:
                if entered:
                    left = True
                assert not uids, "在活動區域外 → 不在戰術層"
        assert entered, "巡邏隊走進活動區域（戰術層）"
        assert left, "走出活動區域"
        done()
    asyncio.run(go())


def test_two_players_zones_merge_and_split():
    async def go():
        sim, a, b = await setup(two=True)
        a.pos, b.pos = "A1", "A5"
        for m in a.members + b.members:
            m.gpos = None
        await sim.tick()
        assert len(sim.zones) == 1 and len(sim.zones[0].regions) == 2, [z.regions for z in sim.zones]
        b.pos = "A3"
        for m in b.members:
            m.gpos = None
        await sim.tick()
        assert len(sim.zones) == 1 and len(sim.zones[0].regions) == 1, "相連 → 同一個活動區域"
        z = sim.zones[0]
        assert {u for u, o in z.owner.items() if o[1] == a.id} and {u for u, o in z.owner.items() if o[1] == b.id}
        done()
    asyncio.run(go())


def test_retreat_moves_to_adjacent_cell():
    async def go():
        sim, a, _ = await setup()
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        a.action = Action(type="retreat", target="A3", ordered=True)
        for _ in range(40):
            await sim.tick()
            if a.action.type == "hold":
                break
        assert a.pos == "A3" and a.action.type == "hold", (a.pos, a.action.type)
        done()
    asyncio.run(go())


def test_scout_stops_short_and_observes():
    async def go():
        sim, a, _ = await setup()
        sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=2, route=["C3"], move_every=999, cooldown=999,
                                       tag="garrison", home="C3")
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        roster.sync_world(sim.world)
        await sim.tick()
        a.action = Action(type="scout", target="C3", duration=2, ordered=True)
        for _ in range(30):
            await sim.tick()
            if a.action.type != "scout":
                break
        assert a.pos == "B3", "偵察不進入目標格"
        assert any(x["kind"] in ("scout", "contact") for x in a.experiences)
        done()
    asyncio.run(go())


def test_rout_at_world_edge_no_exit():
    """連續地圖：在世界邊緣（A3，y 小）士氣崩潰 → 沒有人「撤出戰區」消失；人都還在戰術層、看得到。"""
    async def go():
        sim, a, _ = await setup()
        a.pos = "A3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        m0 = worldmap.global_map()
        ox, oy = worldmap.cell_origin("A3")
        for i, m in enumerate(a.members):
            m.gpos = list(zmod.nearest_standable(m0, (ox + 8 + 2 * i, oy + 2, 0)))
        sim.world.enemies["x"] = Enemy(id="x", pos="B3", strength=3, route=["B3"], move_every=999, cooldown=999,
                                       tag="garrison", home="B3")
        roster.sync_world(sim.world)
        a.morale = 5
        for _ in range(10):
            await sim.tick()
            z = sim.engagements.get(a.pos)
            a.morale = 5
            if z is not None:
                z.battle.morale[z.side_of(a.id)] = 5.0
                mine = [u for u, o in z.owner.items() if o[1] == a.id]
                assert not any(z.battle.units[u].exited for u in mine), "沒有人撤出戰區"
        alive = [m for m in a.members if not m.lost]
        z = sim.engagements.get(a.pos)
        ids = {o[2].name for u, o in z.owner.items() if o[1] == a.id}
        assert all(m.name in ids for m in alive), "活著的人都還在戰術層"
        done()
    asyncio.run(go())


def test_stray_member_does_not_pin_squad_cell():
    """一名隊員卡在遠處（例如舊存檔撤出到世界邊緣）：小隊位置看主體，行軍照常前進到下一格。"""
    async def go():
        sim, a, _ = await setup()
        await sim.tick()
        stray = a.members[1]
        stray.gpos = [75 + 1, 0, 0]
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        cells = []
        for _ in range(60):
            await sim.tick()
            if not cells or cells[-1] != a.pos:
                cells.append(a.pos)
            if "C3" in cells:
                break
        assert "B3" in cells and "C3" in cells, cells
        done()
    asyncio.run(go())


def test_reset_world_drops_old_zones():
    """伺服器運行中重置世界：舊世界的活動區域與個體不留在戰術地圖；新小隊進戰術層、下令會走。"""
    async def go():
        sim, a, _ = await setup()
        await sim.tick()
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        for _ in range(6):
            await sim.tick()
        old_ids = {id(z) for z in sim.zones}
        assert old_ids
        roster_ = sim.reset()
        for sid, cid, card_id in roster_:
            await sim.deploy_squad(card(card_id), cid)
        roster.sync_world(sim.world)
        s = next(x for x in sim.world.squads.values() if x.commander == "100")
        await sim.tick()
        assert not any(id(z) in old_ids for z in sim.zones), "舊活動區域已丟掉"
        assert all(z.world is sim.world for z in sim.zones)
        z = sim.engagements.get(s.pos)
        assert z is not None
        names = {o[2].name for u, o in z.owner.items() if o[1] == s.id}
        assert names == {m.name for m in s.members if not m.lost}, names
        assert all(o[1] in sim.world.squads or o[0] != "s" for o in z.owner.values()), "沒有舊世界的小隊"
        start = {m.name: tuple(m.gpos) for m in s.members}
        s.action = Action(type="move", target="B3", path=["B3"], duration=3, ordered=True)
        for _ in range(4):
            await sim.tick()
        assert any(tuple(m.gpos) != start[m.name] for m in s.members), "新小隊會移動"
        done()
    asyncio.run(go())


def test_resupply_reaches_tactical_layer():
    """連續地圖：在後方據點整補（作戰層）→ 戰術層個體與面板也補滿、傷勢清除（之前會被戰術層舊數值蓋回去）。"""
    async def go():
        from engine import views
        from engine.sim import REST_FULL
        from engine.tactical.units import WEAPONS
        sim, a, _ = await setup()
        await sim.tick()
        z = sim.engagements.get(a.pos)
        assert a.pos == "A3" and z is not None
        mine = {o[2].name: uid for uid, o in z.owner.items() if o[1] == a.id}
        for m in a.members:
            u = z.battle.units[mine[m.name]]
            u.ammo = WEAPONS[u.weapon]["ammo"] // 3
            u.grenades = 0
            u.damage = {"larm": True}
        a.action = Action(type="hold")
        await sim.tick()
        assert all(m.ammo is not None and m.ammo < WEAPONS[m.weapon]["ammo"] for m in a.members), "消耗寫回作戰層"
        for _ in range(REST_FULL + 2):
            await sim.tick()
        z = sim.engagements.get(a.pos)
        live = views.squad_live(sim, a)
        for m in a.members:
            u = z.battle.units[mine[m.name]]
            cap = WEAPONS[u.weapon]["ammo"]
            assert u.ammo == cap, (m.name, u.ammo, cap)
            assert u.grenades > 0 and not any(u.damage.values()), (m.name, u.grenades, u.damage)
        for mv in live["members"]:
            assert mv["ammo"] == mv["ammo_cap"] and not mv["wounds"], mv
        assert all(m.hp == 100 for m in a.members), [m.hp for m in a.members]
        done()
    asyncio.run(go())


def test_ordered_move_continues_through_contact():
    """下令移動途中接敵：命令不取消，邊走邊打，仍抵達目標格。"""
    async def go():
        sim, a, _ = await setup()
        await sim.tick()
        sim.world.enemies["x"] = Enemy(id="x", pos="B4", strength=2, route=["B4"], move_every=999, cooldown=999,
                                       tag="garrison", home="B4")
        roster.sync_world(sim.world)
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        contact = False
        for _ in range(60):
            await sim.tick()
            if sim.in_engagement(a):
                contact = True
            if a.action.type != "move":
                break
        assert contact, "途中有接敵"
        assert a.pos == "C3", (a.pos, a.action.type)
        done()
    asyncio.run(go())


def test_downed_member_counts_toward_roster():
    """倒地（被拖運中）的隊員佔編制名額：回 A3 整補不會多補新人；後送回原隊的人在滿編時不會硬塞（之前會 +1）。"""
    async def go():
        from engine import reserve
        from engine.sim import REST_FULL
        sim, a, _ = await setup()
        await sim.tick()
        m = a.members[1]
        m.down, m.stabilized, m.down_tick, m.hp = True, True, sim.world.tick, 40
        z = sim.engagements.get(a.pos)
        for uid, o in z.owner.items():
            if o[2] is m:
                z.battle.units[uid].down = True
                z.battle.units[uid].stabilized = True
        a.last_recruit = -999
        assert a.roster_size() == 4 and a.full()
        sim._resupply(a, [])
        a.rest_ticks = REST_FULL - 1
        m.down = True
        sim._resupply(a, [])
        assert not sim.pending_recruits, "倒地者算名額 → 不排補兵"
        sim.pending_recruits.clear()
        reserve.pool(sim.world).pop()
        entry = reserve.make_blank(sim.world, sim.rng)
        back = reserve.to_member(entry, "rifle", sim.world.tick)
        assert reserve.give_back(sim.world, back, home=a.id)
        m.down = True
        await sim._recruit(a.id, "後方據點的補充兵")
        assert a.roster_size() == 4, [x.name for x in a.members]
        assert any(r["name"] == back.name for r in reserve.pool(sim.world)), "滿編 → 歸建的人留在池裡"
        done()
    asyncio.run(go())


def test_one_rescuer_and_carry_home():
    """一人倒地：只派一個人救；交戰外穩定後由那個人拖著跟小隊行軍回 A3，傷員進 A3 前不算抵達、不整補；
    回到 A3 整補後傷員歸隊，編制人數不變。"""
    async def go():
        from engine.sim import REST_FULL
        sim, a, _ = await setup()
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        hurt = a.members[2]
        z = sim.engagements.get(a.pos)
        uid_hurt = next(u for u, o in z.owner.items() if o[2] is hurt)
        z.battle.units[uid_hurt].down = True
        z.battle.units[uid_hurt].down_tick = z.battle.tick
        hurt.down = True
        ai = z.ais[z.side_of(a.id)]
        units = [z.battle.units[u] for u, o in z.owner.items() if o[1] == a.id and z.battle.units[u].alive]
        resc = ai.assign_rescuers(units, [])
        assert len(resc) == 1 and list(resc.values()) == [uid_hurt], resc
        a.action = Action(type="move", target="A3", path=["A3"], duration=3, ordered=True)
        arrived_early = False
        for _ in range(80):
            await sim.tick()
            if a.action.type == "hold" and hurt.gpos and worldmap.cell_of(hurt.gpos[0], hurt.gpos[1]) != "A3":
                arrived_early = True
            if a.action.type == "hold" and a.pos == "A3" and hurt.gpos \
                    and worldmap.cell_of(hurt.gpos[0], hurt.gpos[1]) == "A3":
                break
        assert not arrived_early, "傷員還沒進 A3 不算抵達"
        assert hurt.gpos and worldmap.cell_of(hurt.gpos[0], hurt.gpos[1]) == "A3", ("傷員被拖回 A3", hurt.gpos)
        for _ in range(REST_FULL + 3):
            await sim.tick()
        assert not hurt.down and not hurt.lost, "整補後傷員歸隊"
        assert a.roster_size() == 4, [m.name for m in a.members]
        done()
    asyncio.run(go())


def test_op_intel_clears_and_ages():
    """大格地圖敵情：活動區域內依戰術層每步重算（敵人倒了就清、一名敵人只算一格）；區域外的舊標記
    60 秒轉疑似、3 分鐘移除。"""
    async def go():
        from engine.sim import INTEL_DROP, INTEL_SUSPECT
        sim, a, _ = await setup()
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        z = sim.engagements.get(a.pos)
        c = sim.world.commanders["100"]
        # 一名假的敵人：最後已知位置在 C3（中途經過 B4、C4 的舊紀錄不該留下）
        sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=1, route=["C3"], move_every=999, cooldown=999,
                                       tag="garrison", home="C3")
        roster.sync_world(sim.world)
        await sim.tick()
        z = sim.engagements.get(a.pos)
        euid = next((u for u, o in z.owner.items() if o[1] == "x"), None)
        assert euid is not None
        a.known_enemies.update({"B4": {"est": 1, "tick": sim.world.tick}, "C4": {"est": 1, "tick": sim.world.tick}})
        z.battle.intel["blue"].last_known[euid] = (z.battle.units[euid].pos, z.battle.tick)
        await sim.tick()
        inzone = {k for k in a.known_enemies if k in z.cells}
        assert inzone <= {"C3"} and "B4" not in a.known_enemies and "C4" not in a.known_enemies, a.known_enemies
        assert "B4" not in c.intel["enemies"] and "C4" not in c.intel["enemies"], c.intel["enemies"]
        # 敵人倒了 → 下一步清掉
        sim.world.enemies.clear()
        for uid in [u for u, o in z.owner.items() if o[0] == "e"]:
            z.battle.units.pop(uid, None)
            z.owner.pop(uid, None)
        for it in z.battle.intel.values():
            it.last_known.clear()
            it.spotted.clear()
        await sim.tick()
        await sim.tick()
        assert not any(k in z.cells for k in a.known_enemies), a.known_enemies
        assert not any(k in z.cells for k in c.intel["enemies"]), c.intel["enemies"]
        # 區域外的舊標記
        far = "E5"
        t = sim.world.tick
        c.intel["enemies"][far] = {"est": 2, "tick": t}
        a.known_enemies[far] = {"est": 2, "tick": t}
        sim.world.tick += INTEL_SUSPECT // sim.tick_seconds
        sim._age_intel()
        assert c.intel["enemies"][far].get("suspected") and a.known_enemies[far].get("suspected"), "60 秒 → 疑似"
        sim.world.tick = t + INTEL_DROP // sim.tick_seconds
        sim._age_intel()
        assert far not in c.intel["enemies"] and far not in a.known_enemies, "3 分鐘 → 移除"
        done()
    asyncio.run(go())


def test_brace_not_blocked_by_teammate():
    """架槍：射線被前方隊友擋住的人會挪位置或改方向，不會一直白架。"""
    async def go():
        from engine.tactical.ai import TacticalAI
        from engine.tactical.sim import Battle, Plan
        from engine.tactical.units import Unit
        m0 = worldmap.global_map()
        p0 = zmod.nearest_standable(m0, (62, 60, 0))
        front = (p0[0], p0[1] + 2, 0)
        if not m0.standable(front):
            front = zmod.nearest_standable(m0, front)
        us = [Unit(id="a", side="blue", pos=p0), Unit(id="b", side="blue", pos=front)]
        b = Battle(m0, us, seed=1)
        ai = TacticalAI(b, "blue")
        plans = {"a": Plan(mode="brace", watch=(0, 1)), "b": Plan(mode="brace", watch=(1, 0))}
        b.freeze_contact(True)
        ai._unblock_braces(plans)
        b.freeze_contact(False)
        p = plans["a"]
        assert not (p.mode == "brace" and ai.brace_blocked(us[0], p.watch or (0, 1))), p
        assert p.why and "隊友" in p.why, p
    asyncio.run(go())


def test_perf_step():
    async def go():
        sim, a, b = await setup(two=True)
        sim.enemy_ai_enabled = True
        b.pos = "B4"
        for m in b.members:
            m.gpos = None
        for i, c in enumerate(("C3", "C4", "B2", "D3")):
            sim.world.enemies[f"x{i}"] = Enemy(id=f"x{i}", pos=c, strength=3, route=[c], move_every=999,
                                              cooldown=999, tag="garrison", home=c)
        roster.sync_world(sim.world)
        a.action = Action(type="move", target="C3", path=["B3", "C3"], duration=3, ordered=True)
        times = []
        for _ in range(12):
            t = time.time()
            await sim.tick()
            times.append(time.time() - t)
        print(f"  zone 每 tick 平均 {sum(times) / len(times):.2f}s，最慢 {max(times):.2f}s")
        assert sum(times) / len(times) < 1.0
        done()
    asyncio.run(go())


def test_distance_boundaries_in_one_battle():
    """單一 Battle：相距多遠互相影響只看規則，不看是不是同一個活動區域。
    接敵 60 m、射線 80 m、偵測 90 m（切比雪夫）；兩格小格＝2 m，測 58／60／62、78／80／82、88／90／92 m。"""
    from engine.tactical.sim import Battle, Plan
    from engine.tactical.tmap import TacMap
    from engine.tactical.units import Unit

    def pair(dist_m):
        m = TacMap(w=60, h=6)
        a = Unit("a", "blue", (2, 3, 0), posture="stand", facing=(1, 0))
        e = Unit("e", "red", (2 + dist_m // 2, 3, 0), posture="stand", facing=(-1, 0))
        return Battle(m, [a, e], seed=1)

    for d, want in ((58, True), (60, True), (62, False)):
        b = pair(d)
        b.intel["blue"].last_known["e"] = (b.units["e"].pos, 0)
        assert b.unit_in_contact(b.units["a"]) == want, ("接敵距離", d)
    for d, want in ((88, True), (90, True), (92, False)):
        b = pair(d)
        b.intel["blue"].spotted["e"] = 0
        b.detect("t")
        assert ("e" in b.intel["blue"].spotted) == want, ("偵測距離", d)
    for d, want in ((78, True), (82, False)):
        b = pair(d)
        b.units["a"].weapon = "dmr"
        b.intel["blue"].spotted["e"] = 0
        kinds = []
        for seed in range(60):
            b.seed = seed
            shots = b.fire(b.units["a"], Plan(mode="shoot", target="e", fire="aimed"), f"x{seed}")
            assert all(x["len"] <= 80 + 1e-6 for x in shots), "射線最長 80 m"
            kinds += [x["kind"] for x in shots]
        assert ("body" in kinds) == want, ("80 m 內打得到、外面打不到", d, kinds.count("body"))


def test_separate_regions_share_one_battle_and_split_views():
    """不相連的兩個活動區域是同一場 Battle；但每位指揮官的畫面只看到自己區域裡的人（GM 全部）。"""
    async def go():
        from engine import views
        from engine.replay import FrameRecorder, crop_frame
        sim, a, b = await setup(two=True)
        a.pos, b.pos = "A1", "A5"
        for m in a.members + b.members:
            m.gpos = None
        await sim.tick()
        z = sim.zones[0]
        z.recorder = FrameRecorder(z)
        await sim.tick()
        assert len(sim.zones) == 1 and len(z.regions) == 2
        assert {o[1] for o in z.owner.values()} >= {a.id, b.id}, "兩區的人都在同一個 Battle"
        va = views.zone_view(sim, "100")
        assert [sorted(zd["cells"]) for zd in va["zones"]] == [sorted(z.region_of("A1"))]
        assert {u["squad"] for zd in va["zones"] for u in zd["units"]} == {a.id}
        gm = views.zone_view(sim, "100", gm=True)
        assert len(gm["zones"]) == 2
        f = crop_frame(z.last_frame, z.region_of("A5"))
        assert {u["group"] for u in f["units"]} == {b.id}
        done()
    asyncio.run(go())


def test_merge_split_keeps_battle_state():
    """合併 → 分裂 → 再合併：同一場 Battle，個體、架槍、煙霧、AI 狀態都延續（原本換一場 Battle 就遺失）。"""
    async def go():
        sim, a, b = await setup(two=True)
        a.pos, b.pos = "A1", "A5"
        for m in a.members + b.members:
            m.gpos = None
        await sim.tick()
        z = sim.zones[0]
        bt = z.battle
        uid = next(u for u, o in z.owner.items() if o[1] == a.id)
        unit = bt.units[uid]
        unit.braced = (1.0, 0.0)
        bt.smokes.append({"x": unit.pos[0] * 2.0, "y": unit.pos[1] * 2.0, "r": 3.0, "top": 3.5,
                          "born": bt.now, "expires": bt.now + 999, "by": "test"})
        ai = z.ais[z.side_of(a.id)]
        ai.patience = 7
        for pos in ("A3", "A5", "A3"):           # b 靠過來（合併）、離開（分裂）、再靠過來
            b.pos = pos
            for m in b.members:
                m.gpos = None
            await sim.tick()
            assert sim.zones == [z] and z.battle is bt, "一直是同一場 Battle"
            assert len(z.regions) == (1 if pos == "A3" else 2), (pos, z.regions)
            assert bt.units.get(uid) is unit, "個體沒有被重建"
        assert any(s.get("by") == "test" for s in bt.smokes), "煙霧還在"
        assert ai is z.ais[z.side_of(a.id)] and ai.patience >= 7 - 2, "AI 狀態延續"
        done()
    asyncio.run(go())


def test_replay_split_by_fight():
    """回放：整張地圖一份影格，依交火的地方分段（相隔兩格以上 → 兩段），每段只留那一帶的人。"""
    import shutil
    import tempfile
    from types import SimpleNamespace
    from engine.replay import FrameRecorder, ReplayStore, fight_areas

    def unit(uid, group, x, y, fired=False):
        return {"id": uid, "kind": "s", "group": group, "name": uid, "side": "blue", "from": [x, y, 0], "path": [],
                "pos": [x, y, 0], "fired": fired, "dead": False}

    def frame(t, fire_at):
        units = [unit("s:a:1", "a", 12, 12, fired=(12, 12) in fire_at), unit("s:b:1", "b", 112, 12,
                                                                            fired=(112, 12) in fire_at)]
        shots = [{"shooter": "x", "from": list(p), "to": [p[0] + 3, p[1]], "kind": "none"} for p in fire_at]
        return {"t": t, "world_tick": t, "step_seconds": 0.5, "units": units, "shots": shots, "blasts": [],
                "smokes": [], "events": [], "intel": {}, "supports": [], "ev": []}

    frames = [frame(i, [(12, 12)] if i < 3 else [(112, 12)] if i < 6 else []) for i in range(10)]
    areas = fight_areas(frames)
    assert len(areas) == 2 and "A1" in areas[0] and "A5" in areas[1], areas
    assert len(fight_areas([frame(0, [(12, 12), (37, 12)])])) == 1, "相鄰兩格的交火算同一段"
    root = tempfile.mkdtemp()
    try:
        store = ReplayStore(root)
        world = SimpleNamespace(tick=10, squads={})
        eng = SimpleNamespace(zone=True, cells={"A1", "A5"}, battle=SimpleNamespace(map=worldmap.global_map()))
        rec = FrameRecorder.__new__(FrameRecorder)
        rec.frames, rec.started = frames, 0
        out = store.save_fights(eng, rec, world)
        assert len(out) == 2, out
        for entry, gid in zip(out, ("a", "b")):
            data = store.load(entry["id"])
            assert {u["group"] for f in data["frames"] for u in f["units"]} == {gid}, entry
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_replay_saved_during_long_fight():
    """交火一直沒停（從沒安靜 30 秒）也每 ZONE_REPLAY_MAX_S 先存一段：原本上限 1000 秒，實戰打了 9 分多鐘
    伺服器上一份回放都沒有。安靜 30 秒照樣存；還沒開火只留最近 20 秒。"""
    from types import SimpleNamespace
    from engine import sim as simmod
    s = Simulation.__new__(Simulation)
    s.tick_seconds, s.tac_steps = 5, 10            # 一步 0.5 秒（網頁版）
    saved = []
    s._zone_replay = lambda z: (saved.append(len(z.recorder.frames)), z.recorder.frames.clear())
    hot = {"shots": [{"x": 1}], "blasts": [], "units": []}
    quiet = {"shots": [], "blasts": [], "units": []}
    z = SimpleNamespace(recorder=SimpleNamespace(frames=[]), last_fire=0)
    per_seg = int(simmod.ZONE_REPLAY_MAX_S / s.step_seconds)
    for _ in range(per_seg * 3):                 # 一直在打
        z.recorder.frames.append(hot)
        s._zone_replay_check(z)
    assert saved == [per_seg] * 3, saved
    saved.clear()
    for _ in range(10):
        z.recorder.frames.append(hot)
        s._zone_replay_check(z)
    for _ in range(int(simmod.ZONE_REPLAY_QUIET_S / s.step_seconds)):
        z.recorder.frames.append(quiet)
        s._zone_replay_check(z)
    assert len(saved) == 1, "安靜 30 秒 → 存一段"
    for _ in range(200):                         # 沒開火：只留 PREROLL
        z.recorder.frames.append(quiet)
        s._zone_replay_check(z)
    assert len(z.recorder.frames) <= int(simmod.ZONE_REPLAY_PREROLL_S / s.step_seconds) and len(saved) == 1


def test_enemy_leaves_and_reenters_from_world_data():
    """敵軍走出所有活動區域 → 移出 Battle（狀態寫回作戰層）；再走回來 → 依作戰層資料重建，不重複、倒地狀態保留。"""
    async def go():
        sim, a, _ = await setup()
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        e = Enemy(id="g7", pos="B4", strength=3, route=["B4"], move_every=999, cooldown=999, tag="garrison", home="B4")
        sim.world.enemies["g7"] = e
        roster.sync_world(sim.world)
        await sim.tick()
        z = sim.zones[0]
        uids = sorted(u for u, o in z.owner.items() if o[1] == "g7")
        assert len(uids) == len(e.soldiers) == 3, uids
        z.battle.units[uids[0]].down = True
        z.battle.units[uids[0]].down_tick = z.battle.tick
        await sim.tick()
        e.pos = "B5"                              # 在活動區域外（B3 的區域是 A2～C4）
        for sd in e.soldiers:
            sd["gpos"] = None
        await sim.tick()
        assert not any(o[1] == "g7" for o in z.owner.values()), "區域外 → 不在 Battle"
        e.pos = "B4"
        await sim.tick()
        back = sorted(u for u, o in z.owner.items() if o[1] == "g7")
        assert back == uids, back
        assert len([u for u in z.battle.units if u.startswith("e:g7")]) == 3, "沒有分身"
        assert z.battle.units[uids[0]].down or z.battle.units[uids[0]].dead, "倒地狀態從作戰層帶回來"
        done()
    asyncio.run(go())


def test_straggler_outside_zone_not_teleported():
    """敵軍小組多數人在區域內、一人站在區域外的隔壁格：那個人照 Battle 裡的位置繼續，不被同步搬到小組所在格
    （原本 sync_in 用「小組所在格＋他自己那格的格內座標」重算，差一個作戰格，影格上瞬移 25 格再走回來）。"""
    async def go():
        sim, a, _ = await setup()
        a.pos = "B3"
        a.autonomy = "standby"
        for m in a.members:
            m.gpos = None
        await sim.tick()
        e = Enemy(id="x", pos="C4", strength=3, route=["C4"], move_every=999, cooldown=999, tag="garrison", home="C4")
        sim.world.enemies["x"] = e
        e.tcell = "C4"
        roster.sync_world(sim.world)
        m0 = worldmap.global_map()
        cx, cy = worldmap.cell_origin("C4")
        dx, dy = worldmap.cell_origin("D4")
        spots = [zmod.nearest_standable(m0, (cx + 10, cy + 20, 0)), zmod.nearest_standable(m0, (cx + 13, cy + 20, 0)),
                 zmod.nearest_standable(m0, (cx + 11, cy + 22, 0))]
        for sd, sp in zip(e.soldiers, spots):
            sd["gpos"] = list(sp)
            sd["facing"] = [0, -1]
        await sim.tick()                          # 先進 Battle
        z = sim.zones[0]
        uid = next(u for u, o in z.owner.items() if o[1] == "x" and o[2] is e.soldiers[2])
        out = zmod.nearest_standable(m0, (dx + 11, dy + 1, 0))
        assert worldmap.cell_of(out[0], out[1]) == "D4"
        z.battle.units[uid].pos = tuple(out)       # 交戰中自己走過格界、站到區域外的 D4
        prev = tuple(out)
        for _ in range(6):
            await sim.tick()
            z = sim.zones[0]
            assert "D4" not in z.cells, "D4 在活動區域外（B3 的區域是 A2～C4）"
            uid = next(u for u, o in z.owner.items() if o[1] == "x" and o[2] is e.soldiers[2])
            pos = z.battle.units[uid].pos
            if prev is not None:
                step = max(abs(pos[0] - prev[0]), abs(pos[1] - prev[1]))
                assert step <= 6, f"一拍移動 {step} 格（{prev} → {pos}）：被同步搬到別格"
            prev = pos
        assert e.pos == "C4", e.pos
        done()
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}", flush=True)
    print("zone_test OK")


if __name__ == "__main__":
    main()
