"""投擲物配置與撿取、架槍、閃光彈、門與房間、破門。不需要 LLM。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.models import Member, refill_loadout
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import THROW_CAP, Unit, loadout


def duel(blue, red, w=12, h=16, m=None):
    m = m or TacMap(w=w, h=h)
    units = [Unit(id=f"b{i}", side="blue", pos=p, facing=(0, 1)) for i, p in enumerate(blue)] + \
            [Unit(id=f"r{i}", side="red", pos=p, facing=(0, -1)) for i, p in enumerate(red)]
    return Battle(m, units, seed=11)


# ---------------------------------------------------------------------- 1 投擲物


def test_loadout_by_weapon():
    assert loadout("rifle") == {"grenades": 1, "smokes": 1, "flashes": 1}
    assert loadout("lmg") == {"grenades": 2, "smokes": 1, "flashes": 0}
    assert loadout("shotgun")["flashes"] == 2
    for w in ("rifle", "lmg", "dmr", "shotgun"):
        assert sum(loadout(w).values()) == THROW_CAP
    m = Member(name="X", weapon="lmg")
    refill_loadout(m)
    assert (m.grenades, m.smokes, m.flashes) == (2, 1, 0)
    m.weapon = "shotgun"
    refill_loadout(m)
    assert (m.grenades, m.smokes, m.flashes) == (1, 0, 2), "換武器後整補換成對應配置"


def test_take_from_downed_any_side_with_cap():
    b = duel([(3, 3, 0)], [(3, 4, 0)])
    me, foe = b.units["b0"], b.units["r0"]
    me.grenades, me.smokes, me.flashes = 1, 0, 0
    foe.grenades, foe.smokes, foe.flashes = 2, 1, 1
    foe.down = True
    n = b.transfer_items(me, Plan(mode="take", target="r0"))
    assert n == 2 and me.throwables() == THROW_CAP, "可撿敵方倒地者，但不超過上限"
    assert foe.throwables() == 2
    foe.down = False
    me.grenades = 0
    assert b.transfer_items(me, Plan(mode="take", target="r0")) == 0, "活著的敵人不能撿"


def test_take_specific_item_and_give_between_allies():
    b = duel([(3, 3, 0), (4, 3, 0)], [(9, 14, 0)])
    a, c = b.units["b0"], b.units["b1"]
    a.grenades, a.smokes, a.flashes = 0, 0, 0
    c.grenades, c.smokes, c.flashes = 1, 1, 1
    assert b.transfer_items(c, Plan(mode="give", target="b0", item="flashes")) == 1
    assert a.flashes == 1 and c.flashes == 0
    c.pos = (8, 3, 0)
    assert b.transfer_items(c, Plan(mode="give", target="b0", item="smokes")) == 0, "不相鄰不能給"


def test_take_via_step_plan():
    b = duel([(3, 3, 0)], [(9, 14, 0)])
    me, foe = b.units["b0"], b.units["r0"]
    me.grenades = me.smokes = me.flashes = 0
    foe.pos = (5, 3, 0)
    foe.down = True
    foe.dead = True
    b.step({"b0": Plan(mode="take", target="r0", path=[(4, 3, 0)])})
    assert me.pos == (4, 3, 0) and me.throwables() > 0, "走一步再撿"


def test_drag_also_takes():
    b = duel([(3, 3, 0)], [(9, 14, 0)])
    b.units["b0"].grenades = b.units["b0"].smokes = b.units["b0"].flashes = 0
    d = Unit(id="b9", side="blue", pos=(3, 4, 0), flashes=1)
    d.down = True
    d.down_tick = 0
    b.units["b9"] = d
    b.step({"b0": Plan(mode="drag", target="b9", path=[(3, 2, 0)])})
    assert b.units["b0"].throwables() > 0, "拖人時順手拿走投擲物"


# ---------------------------------------------------------------------- 2 架槍


def test_brace_action_and_bonus():
    m = TacMap(w=12, h=16)
    m.low_wall(5, 3, "N")
    b = duel([(5, 3, 0), (8, 3, 0)], [(9, 14, 0)], m=m)
    rested, free = b.units["b0"], b.units["b1"]
    b.step({"b0": Plan(mode="brace", watch=(0, 1)), "b1": Plan(mode="brace", watch=(0, 1))})
    assert rested.braced and free.braced
    assert rested.brace_rest and not free.brace_rest, "矮牆後有依託、空地沒有"
    aim = b.units["r0"].torso(b.map)
    base = Unit(id="x", side="blue", pos=(5, 3, 0), facing=(0, 1))
    b.units["x"] = base
    s0 = b.sigma(base, 20, aim=aim)
    s1 = b.sigma(rested, 20, aim=aim)
    assert abs(s1 / s0 - 0.6) < 1e-6, (s0, s1)
    assert b.sigma(free, 20, aim=aim) < b.sigma(Unit(id="y", side="blue", pos=(8, 3, 0)), 20, aim=aim)
    assert b.sigma(rested, 20, overwatch=True, aim=aim) == s1, "架槍取消警戒倉促懲罰"
    behind = (rested.eye(b.map)[0], rested.eye(b.map)[1] - 10, 1.0)
    assert b.sigma(rested, 20, aim=behind) == s0, "扇區外沒有加成"


def test_brace_breaks():
    for why, act in (("移動", lambda b, u: b.step({u.id: Plan(mode="move", path=[(5, 4, 0)])})),
                     ("換姿勢", lambda b, u: b.step({u.id: Plan(mode="posture", posture="prone")})),
                     ("被壓制", lambda b, u: (setattr(u, "suppression", 70), b.step({u.id: Plan(mode="overwatch")}))),
                     ("中彈", None),
                     ("光學過載", lambda b, u: (setattr(u, "dazzled", 2), b.step({u.id: Plan(mode="overwatch")})))):
        b = duel([(5, 3, 0)], [(9, 14, 0)])
        u = b.units["b0"]
        b.step({"b0": Plan(mode="brace", watch=(0, 1))})
        assert u.braced
        if why == "中彈":
            orig = b._update_braces

            def hurt(bracing, pre):
                u.damage["larm"] = 1
                orig(bracing, pre)
            b._update_braces = hurt
            b.step({"b0": Plan(mode="overwatch")})
        else:
            act(b, u)
        assert u.braced is None, f"{why} → 架槍中斷"
        assert any("架槍中斷" in l and why in l for l in b.log), (why, b.log[-3:])


def test_brace_persists_and_braced_overwatch_first():
    b = duel([(5, 3, 0), (6, 3, 0)], [(5, 9, 0)])
    braced, plain = b.units["b0"], b.units["b1"]
    b.step({"b0": Plan(mode="brace", watch=(0, 1)), "b1": Plan(mode="overwatch", watch=(0, 1))})
    b.step({"b0": Plan(mode="overwatch"), "b1": Plan(mode="overwatch", watch=(0, 1))})
    assert braced.braced, "開火或警戒不會消耗架槍狀態"
    b.intel["blue"].spotted["r0"] = b.tick
    b.intel["blue"].last_known["r0"] = (b.units["r0"].pos, b.tick)
    b.step({"b0": Plan(mode="overwatch"), "b1": Plan(mode="overwatch", watch=(0, 1)),
            "r0": Plan(mode="move", path=[(5, 8, 0)])})
    ow = [s["shooter"] for s in b.shots]
    if "b0" in ow and "b1" in ow:
        assert ow.index("b0") < ow.index("b1"), "架槍者先開火"


def test_ai_braces_when_idle():
    from engine.tactical.ai import Intent, TacticalAI
    m = TacMap(w=12, h=16)
    for x in range(12):
        m.wall(x, 8)
    b = duel([(5, 3, 0)], [(9, 14, 0)], m=m)
    ai = TacticalAI(b, "blue")
    p = ai.plan(Intent("hold"))
    assert p["b0"].mode == "brace", p["b0"]
    b.step(p)
    assert b.units["b0"].braced
    p = TacticalAI(b, "blue").plan(Intent("hold"))
    assert p["b0"].mode == "overwatch", "已架槍 → 維持"


# ---------------------------------------------------------------------- 3 閃光彈


def _room_map():
    """5×5 房間（外牆 x=3..7, y=8..12），南牆門在 (5,8)。"""
    m = TacMap(w=12, h=16)
    for x in range(3, 8):
        for y in range(8, 13):
            if (x in (3, 7) or y in (8, 12)) and (x, y) != (5, 8):
                m.wall(x, y)
    return m


def test_flash_blocked_by_wall_and_distance():
    m = _room_map()
    b = duel([(4, 7, 0), (5, 2, 0)], [(5, 10, 0), (6, 11, 0)], m=m)
    b.units["r0"].facing = (0, -1)
    b.units["r1"].facing = (0, -1)
    at = b.map.center((5, 10, 0))
    dazed, supp = b.flash({"at": (at[0], at[1], 0.2), "thrower": "b0"})
    assert dazed.get("r0") == 2, "房內近距、面向 → 2 tick"
    assert "r1" in dazed
    assert "b0" not in dazed, "門外貼牆的友軍被牆擋住，不受光學影響"
    assert "b1" not in dazed, "太遠"
    near = b.flash({"at": b.map.center((5, 9, 0))[:2] + (0.2,), "thrower": "b0"})
    assert near[1].get("b0", 0) > 0 and "b0" not in near[0], "聲響可穿牆（同層 6 m 內），光學不行"


def test_flash_facing_and_partial_cover():
    b = duel([(5, 3, 0)], [(5, 6, 0)])
    foe = b.units["r0"]
    at = b.map.center((5, 5, 0))
    pt = (at[0], at[1], 0.2)
    foe.facing = (0, -1)
    front, _ = b.flash({"at": pt, "thrower": "b0"})
    foe.facing = (0, 1)
    back, _ = b.flash({"at": pt, "thrower": "b0"})
    assert front.get("r0", 0) > back.get("r0", 0), "背對效果較弱"
    m = TacMap(w=12, h=16)
    m.low_wall(5, 6, "S", height=1.4)
    b2 = duel([(5, 3, 0)], [(5, 6, 0)], m=m)
    b2.units["r0"].facing = (0, -1)
    b2.units["r0"].posture = "crouch"
    hidden, _ = b2.flash({"at": (at[0], at[1] - 1.5, 0.2), "thrower": "b0"})
    assert hidden.get("r0", 0) < front.get("r0", 0) or "r0" not in hidden, "矮牆遮蔽 → 效果遞減"


def test_flash_effects_and_brace_penalty():
    m = _room_map()
    b = duel([(4, 7, 0)], [(5, 10, 0), (6, 10, 0)], m=m)
    r0, r1 = b.units["r0"], b.units["r1"]
    r0.braced = (0.0, -1.0)
    r0.facing = r1.facing = (0, -1)
    at = b.map.center((5, 9, 0))
    dazed, _ = b.flash({"at": (at[0], at[1], 0.2), "thrower": "b0"})
    assert dazed["r0"] == dazed.get("r1", 0) + 1 or dazed["r0"] == 3, "架槍中被閃 → 多 1 tick"
    r1.dazzled = 2
    base = Unit(id="c", side="red", pos=(6, 10, 0), facing=(0, -1))
    assert b.sigma(r1, 10) == 2 * b.sigma(base, 10), "過載散布 ×2"


def test_flash_in_step_dazzles_and_breaks_brace_and_decays():
    m = _room_map()
    b = duel([(5, 7, 0)], [(5, 10, 0)], m=m)
    foe = b.units["r0"]
    foe.braced = (0.0, -1.0)
    foe.facing = (0, -1)
    b.units["b0"].flashes = 1
    at = b.map.center((5, 10, 0))
    b.step({"b0": Plan(mode="flash", point=(at[0], at[1], 0.2)), "r0": Plan(mode="overwatch")})
    assert b.units["b0"].flashes == 0
    assert foe.dazzled >= 2 and foe.braced is None, "被閃 → 過載且架槍中斷"
    n = foe.dazzled
    b.step({})
    assert foe.dazzled == n - 1
    b.step({"r0": Plan(mode="overwatch")})
    assert any("投擲閃光彈" in l for l in b.log)


def test_dazzled_detects_less():
    import statistics
    hits = {False: 0, True: 0}
    for dz in (False, True):
        for s in range(60):
            b = duel([(5, 3, 0)], [(5, 9, 0)])
            b.seed = s
            if dz:
                b.units["b0"].dazzled = 1
            b.detect("t")
            hits[dz] += "r0" in b.intel["blue"].spotted
    assert hits[True] < hits[False], hits
    _ = statistics


# ---------------------------------------------------------------------- 4 門與房間


def test_rooms_recorded_in_world_maps():
    from engine import scenario
    from engine.tactical import worldmap
    total = 0
    for cell in scenario.CELLS:
        m = worldmap.tmap(cell)
        for r in m.rooms:
            total += 1
            assert r["doors"], f"{cell} 房間 {r['rect']} 沒有門"
            for d in r["doors"]:
                assert m.standable((d[0], d[1], 0))
                assert m.room_at((d[0], d[1], 0)) is r
                entry = m.door_entry(r, d)
                assert m.room_at(entry) is r, (cell, r["rect"], d, entry)
            c = r["cells"][len(r["cells"]) // 2]
            assert m.room_at((c[0], c[1], 0)) is r
    assert total >= 20, total


def test_stack_cells_hidden_from_doorway():
    from engine.tactical.tmap import FLOOR_H
    from engine.tactical import worldmap
    from engine import scenario
    checked = 0
    for cell in list(scenario.CELLS)[:10]:
        m = worldmap.tmap(cell)
        for r in m.rooms:
            for d in r["doors"]:
                stack = m.door_stack_cells(r, d)
                if not stack:
                    continue
                best = m.stack_exposure(r, stack[0])
                doorway = (d[0] + m.door_outside(r, d)[0], d[1] + m.door_outside(r, d)[1], 0)
                if m.in_bounds(doorway[0], doorway[1]) and m.standable(doorway):
                    assert best <= m.stack_exposure(r, doorway), "待命格比正對門口隱蔽"
                assert best <= max(1, len(r["cells"]) // 3), (cell, r["rect"], d, stack[0], best, len(r["cells"]))
                checked += 1
    assert checked > 10
    _ = FLOOR_H


def test_scenario_block_rooms():
    from engine.tactical import scenarios
    m, *_ = scenarios.district()
    assert len(m.rooms) == 6 and all(r["doors"] for r in m.rooms)


# ---------------------------------------------------------------------- 5 破門


def _run_breach(prep, tempo="stack", entry="one", seed=0, ticks=12):
    from engine.tactical import scenarios
    from engine.tactical.ai import TacticalAI
    m, units, bi, ri = scenarios.house_clear(prep, tempo, entry)
    b = Battle(m, units, seed=seed)
    ais = {"blue": TacticalAI(b, "blue"), "red": TacticalAI(b, "red")}
    b.detect("init")
    for _ in range(ticks):
        plans = {}
        plans.update(ais["blue"].plan(bi))
        plans.update(ais["red"].plan(ri))
        b.step(plans)
        if b.over():
            break
    return b, ais


def test_breach_flash_is_thrown_into_room_then_enter():
    hit = entered = 0
    for s in range(6):
        b, ais = _run_breach("flash", seed=s)
        fl = [bl for bl in b.blasts if bl.get("item") == "flashes"]
        if fl:
            hit += b.map.room_at((int(fl[0]["at"][0] // 2), int(fl[0]["at"][1] // 2), 0)) is b.map.rooms[0]
        entered += ais["blue"].breach.entered
    assert hit >= 4, f"閃光彈落在房內（{hit}/6）"
    assert entered >= 4


def test_breach_params_change_behaviour():
    b1, _ = _run_breach("flash", seed=3)
    b2, _ = _run_breach("none", "quick", seed=3)
    b3, a3 = _run_breach("frag", seed=3)
    assert any("投擲閃光彈" in l for l in b1.log)
    assert not any("投擲閃光彈" in l or "投擲手榴彈" in l and l.split()[1].startswith("b") for l in b2.log)
    assert a3["blue"].breach.prep == "frag"
    bm, am = _run_breach("flash", entry="multi", seed=3)
    assert len(am["blue"].breach.doors) == 2, "兩個入口"


def test_breach_auto_prep_varies_and_room_choice():
    from engine.tactical import scenarios
    from engine.tactical.ai import TacticalAI
    preps = set()
    for s in range(12):
        m, units, bi, ri = scenarios.house_clear()
        b = Battle(m, units, seed=s)
        ai = TacticalAI(b, "blue")
        ai.plan(bi)
        preps.add(ai.breach.prep)
        assert ai.breach.room == 0
    assert len(preps) >= 2, f"未指定 prep 時有變化：{preps}"


def test_breach_no_building_reports():
    from engine.tactical.ai import Intent, TacticalAI
    b = duel([(5, 3, 0)], [(9, 14, 0)])
    ai = TacticalAI(b, "blue")
    ai.plan(Intent("breach"))
    assert any("沒有可破門的建築" in r for r in ai.reports)


def test_breach_cleared_report():
    from engine.tactical import scenarios
    from engine.tactical.ai import TacticalAI
    m, units, bi, ri = scenarios.house_clear("none", "quick")
    units = [u for u in units if u.side == "blue"] + [Unit(id="rx", side="red", pos=(14, 16, 0))]
    b = Battle(m, units, seed=1)
    ai = TacticalAI(b, "blue")
    for _ in range(14):
        b.step(ai.plan(bi))
        if ai.breach.done:
            break
    assert any("房間已清空" in r for r in ai.reports), ai.reports


def test_defender_braces_toward_door_when_enemy_outside():
    from engine.tactical import scenarios
    from engine.tactical.ai import Intent, TacticalAI
    m, units, bi, ri = scenarios.house_clear()
    b = Battle(m, units, seed=0)
    r0 = b.units["r0"]
    r0.braced = (0.0, -1.0)
    b.intel["red"].last_known["b0"] = ((12, 10, 0), b.tick)
    b.units["b0"].pos = (12, 10, 0)
    ai = TacticalAI(b, "red")
    plans = ai.plan(Intent("hold"), [r0])
    p = plans["r0"]
    assert p.mode == "brace" and p.watch is not None and p.watch[0] > 0, ("守方架向東門", p)


# ---------------------------------------------------------------------- 5c 作戰層


def test_captain_breach_menu_and_intent():
    from scripts.engage_test import _engaged, PATH

    async def go():
        sim, sq, e, _ = await _engaged()
        eng = sim.engagements[sq.pos]
        allowed, text = sim._allowed(sq)
        ids = eng.building_ids(sq)
        if not ids:
            os.remove(PATH)
            return
        assert "breach" in allowed and "破門" in text
        a, t, d, v = sim._normalize(sq, {"action": "breach", "target": f"建築{ids[0]}", "prep": "flash",
                                         "entry": "one", "tempo": "quick"}, allowed)
        assert (a, t) == ("breach", str(ids[0]))
        sim._apply(sq, a, t, d, ordered=True, via=v)
        assert sq.action.type == "breach" and sq.action.breach["prep"] == "flash"
        it = eng.squad_intent(sq)
        assert it.kind == "breach" and it.room == ids[0] and it.tempo == "quick"
        assert "破門" in sim._action_text(sq)
        a, t, *_ = sim._normalize(sq, {"action": "breach", "target": None, "prep": "bogus"}, allowed)
        sim._apply(sq, a, t, False, ordered=True, via=[])
        assert sq.action.breach == {"room": None, "prep": None, "entry": None, "tempo": None}
        brief = eng.brief(sq)
        assert "小隊投擲物" in brief
        os.remove(PATH)
    asyncio.run(go())


def test_enemy_commander_breaches_when_enemy_in_building():
    from scripts.enemy_tactics_test import make
    from engine.enemy_tactics import EnemyCommander

    async def go():
        sim, sq, e = await make(foes=4, tag="mobile", seed=4)
        eng = sim.engagements["C3"]
        b = eng.battle
        rooms = [r for r in b.map.rooms if r["doors"]]
        if not rooms:
            return
        r = rooms[0]
        cmd = EnemyCommander(eng)
        units = cmd._units(e)
        blues = [uid for uid, u in b.units.items() if u.side == "blue" and u.alive][:1]
        c = r["cells"][0]
        b.intel["red"].last_known = {blues[0]: ((c[0], c[1], 0), b.tick)}
        b.intel["red"].believed_seen = {u.id: (b.tick, "near_miss") for u in units}
        it, why = cmd._choose(e, units, cmd._known(), len(units), "normal")
        assert it.kind == "breach" and it.room == r["id"], (it, why)
        e.tag = "garrison"
        e.order = None
        it2, _ = cmd._choose(e, units, cmd._known(), len(units), "normal")
        assert it2.kind != "breach", "守軍不破門"
        from scripts.engage_test import PATH
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for k, v in list(globals().items()):
        if k.startswith("test_") and only in k:
            r = v()
            if asyncio.iscoroutine(r):
                asyncio.run(r)
            print(f"OK {k}")
    print("breach_test OK")


if __name__ == "__main__":
    main()
