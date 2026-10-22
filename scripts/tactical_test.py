"""戰術層一致性測試（docs/archive/combat.md v0.2 §15.1）。不需要 LLM。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def fingerprint(r: dict) -> str:
    b = r["battle"]
    return json.dumps({"state": b.state(), "log": b.log}, sort_keys=True, default=str)


def test_order_independence():
    for name in ("open_field", "cover_duel", "assault_suppress", "ambush"):
        a = runner.run(scenarios.SCENARIOS[name], seed=3)
        rev = runner.run(scenarios.SCENARIOS[name], seed=3, order_hook=lambda s: list(reversed(s)))
        assert fingerprint(a) == fingerprint(rev), f"{name}：處理順序影響結果"


def test_seed():
    a = runner.run(scenarios.open_field, seed=5)
    b = runner.run(scenarios.open_field, seed=5)
    c = runner.run(scenarios.cover_duel, seed=6)
    d = runner.run(scenarios.cover_duel, seed=7)
    assert fingerprint(a) == fingerprint(b), "同種子結果不同"
    assert fingerprint(c) != fingerprint(d), "不同種子結果相同"


def test_simultaneous_fire():
    for flip in (False, True):
        m = TacMap()
        a = Unit("a", "blue", (4, 2, 0), posture="stand", facing=(0, 1))
        b = Unit("b", "red", (4, 5, 0), posture="stand", facing=(0, -1))
        units = [b, a] if flip else [a, b]
        hits = 0
        for seed in range(40):
            bt = Battle(m, [Unit(**{**u.__dict__, "stats": dict(u.stats), "damage": {}}) for u in units], seed=seed)
            bt.detect("init")
            bt.intel["blue"].spotted["b"] = 0
            bt.intel["red"].spotted["a"] = 0
            bt.step({"a": Plan(mode="shoot", target="b", fire="burst"),
                     "b": Plan(mode="shoot", target="a", fire="burst")})
            hits += bt.units["a"].stats["shots"] + bt.units["b"].stats["shots"]
            assert bt.units["a"].stats["shots"] > 0 and bt.units["b"].stats["shots"] > 0, "同時開火：雙方都要射擊"
        if not flip:
            base = hits
        else:
            assert hits == base


def test_collision():
    m = TacMap()
    for order in (None, lambda s: list(reversed(s))):
        a = Unit("a", "blue", (3, 3, 0))
        b = Unit("b", "blue", (5, 3, 0))
        bt = Battle(m, [a, b], seed=1)
        bt.order_hook = order
        bt.step({"a": Plan(mode="move", path=[(4, 3, 0)]), "b": Plan(mode="move", path=[(4, 3, 0)])})
        positions = sorted(u.pos for u in bt.units.values())
        assert len(set(positions)) == 2 and (4, 3, 0) in positions, positions
        if order is None:
            first = positions
        else:
            assert positions == first, "碰撞解決與順序有關"


def test_wall_blocks():
    m = TacMap()
    m.wall(4, 4)
    shooter = Unit("s", "blue", (4, 1, 0), posture="stand")
    target = Unit("t", "red", (4, 7, 0), posture="stand", facing=(0, -1))
    bt = Battle(m, [shooter, target], seed=2)
    assert bt.visibility(shooter, target) == 0
    pt = target.torso(m)
    for i in range(30):
        bt.tick = i
        shots = bt.fire(shooter, Plan(mode="shoot", point=pt, fire="suppress"), "t")
        for s in shots:
            assert not (s["kind"] == "body" and s["ref"][0] == "t"), "子彈穿牆命中"
            assert "t" not in bt.suppression_from(s, shooter), "牆距目標 > 2 m 時不應壓制"
    m2 = TacMap()
    m2.low_wall(4, 6, "S", height=1.4)
    target2 = Unit("t", "red", (4, 6, 0), posture="crouch", facing=(0, -1))
    bt2 = Battle(m2, [shooter, target2], seed=2)
    pt2 = target2.torso(m2)
    got = 0
    for i in range(30):
        bt2.tick = i
        for s in bt2.fire(shooter, Plan(mode="shoot", point=pt2, fire="suppress"), "t"):
            assert not (s["kind"] == "body" and s["ref"][0] == "t")
            got += bt2.suppression_from(s, shooter).get("t", 0)
    assert got > 0, "緊貼掩體被打應產生壓制"


def test_height_reveals_more():
    m = TacMap()
    m.low_wall(4, 5, "S", height=1.0)
    for x in range(2, 7):
        for y in range(2, 4):
            m.platform(x, y, l=1)
    target = Unit("t", "red", (4, 5, 0), posture="crouch", facing=(0, -1))
    low = Unit("lo", "blue", (4, 3, 0), posture="crouch", facing=(0, 1))
    high = Unit("hi", "blue", (4, 3, 1), posture="stand", facing=(0, 1))
    bt = Battle(m, [target, low, high])
    assert bt.visibility(high, target) > bt.visibility(low, target), "高處應看到更多"


def test_unspotted_not_targeted():
    m = TacMap()
    m.window_side(4, 7, "S")
    watcher = Unit("w", "red", (4, 7, 0), posture="crouch", facing=(0, -1))
    mover = Unit("m", "blue", (4, 2, 0))
    bt = Battle(m, [watcher, mover], seed=4)
    bt.intel["blue"].spotted.clear()
    bt.step({"m": Plan(mode="overwatch"), "w": Plan(mode="shoot", target="m", fire="aimed")})
    assert bt.units["w"].stats["shots"] == 0, "未發現的目標不能被射擊"
    hidden = Unit("h", "red", (4, 8, 0), posture="prone")
    bt2 = Battle(TacMap(), [hidden, Unit("o", "blue", (4, 1, 0))], seed=4)
    bt2.step({"o": Plan(mode="overwatch"), "h": Plan(mode="move", path=[(4, 7, 0)])})
    assert bt2.units["o"].stats["shots"] == 0 or "h" in bt2.intel["blue"].spotted


def test_pinned_no_overwatch():
    m = TacMap()
    watcher = Unit("w", "red", (4, 8, 0), facing=(0, -1))
    mover = Unit("m", "blue", (1, 2, 0))
    fired = {}
    for pinned in (False, True):
        total = 0
        for seed in range(20):
            bt = Battle(m, [Unit(**{**watcher.__dict__, "stats": {"shots": 0, "hits": 0, "kills": 0}}),
                            Unit(**{**mover.__dict__, "stats": {"shots": 0, "hits": 0, "kills": 0}})], seed=seed)
            bt.intel["red"].spotted["m"] = 0
            bt.intel["red"].last_known["m"] = (mover.pos, 0)
            if pinned:
                bt.units["w"].suppression = 95
            bt.step({"w": Plan(mode="overwatch"), "m": Plan(mode="move", path=[(2, 2, 0), (3, 2, 0)])})
            total += bt.units["w"].stats["shots"]
        fired[pinned] = total
    assert fired[False] > 0 and fired[True] == 0, fired


def test_state_roundtrip():
    tmap, units, bi, ri = scenarios.cover_duel()
    from engine.tactical.ai import TacticalAI
    b1 = Battle(tmap, units, seed=9)
    ai = {"blue": TacticalAI(b1, "blue"), "red": TacticalAI(b1, "red")}
    b1.detect("init")
    for _ in range(4):
        plans = {}
        plans.update(ai["blue"].plan(bi))
        plans.update(ai["red"].plan(ri))
        b1.step(plans)
    st = json.loads(json.dumps(b1.state()))
    b2 = Battle(tmap, [], seed=0)
    b2.load_state(st)
    assert json.dumps(b2.state(), sort_keys=True) == json.dumps(b1.state(), sort_keys=True)
    fixed = {"b0": Plan(mode="overwatch"), "r0": Plan(mode="overwatch")}
    for b in (b1, b2):
        for uid in b.units:
            fixed.setdefault(uid, Plan(mode="overwatch"))
        b.step(dict(fixed))
    assert json.dumps(b2.state(), sort_keys=True) == json.dumps(b1.state(), sort_keys=True), "讀檔後結果不同"


def test_detect_once_per_tick():
    m = TacMap()
    m.veg(4, 7)
    for seed in range(60):
        results = []
        for calls in (1, 4):
            t = Unit("t", "red", (4, 7, 0), posture="prone", facing=(0, -1))
            o = Unit("o", "blue", (4, 1, 0), facing=(0, 1))
            b = Battle(m, [t, o], seed=seed)
            b.tick = 1
            for i in range(calls):
                b.detect(f"phase{i}")
            results.append("t" in b.intel["blue"].spotted)
        assert results[0] == results[1], "同一 tick 多次判定不應提高發現機率"


def test_veg_asymmetric():
    m = TacMap()
    m.veg(4, 7)
    hider = Unit("h", "red", (4, 7, 0), posture="prone", facing=(1, 0))
    out = Unit("o", "blue", (4, 2, 0), facing=(0, 1))
    b = Battle(m, [hider, out])
    inside_out = b.visibility(hider, out)
    outside_in = b.visibility(out, hider)
    assert inside_out > 0.5, f"躲在植被中應看得到外面：{inside_out:.2f}"
    assert outside_in < 0.4, f"從外面應較難看到植被中的人：{outside_in:.2f}"
    assert inside_out > outside_in * 3, "植被應不對稱"
    m2 = TacMap()
    m2.veg(4, 5)
    m2.veg(4, 7)
    b2 = Battle(m2, [hider, out])
    assert b2.visibility(hider, out) < inside_out, "前方另一格植被仍會遮擋"


def test_fov():
    m = TacMap()
    t = Unit("t", "red", (4, 7, 0), facing=(0, -1))
    front = Unit("o", "blue", (4, 2, 0), facing=(0, 1))
    side = Unit("o", "blue", (4, 2, 0), facing=(1, 0))
    back = Unit("o", "blue", (4, 2, 0), facing=(0, -1))
    b = Battle(m, [t, front])
    assert b.fov_factor(front, t) == 1.0 and b.fov_factor(side, t) == 0.5 and b.fov_factor(back, t) == 0.2
    watcher = Unit("o", "blue", (4, 2, 0), facing=(0, -1), watch=(0, 1))
    assert b.fov_factor(watcher, t) == 1.0, "警戒扇區優先於面向"
    rates = {}
    for name, f in (("front", (0, 1)), ("back", (0, -1))):
        n = 0
        for seed in range(200):
            bt = Battle(m, [Unit("t", "red", (4, 7, 0), facing=(0, -1), posture="prone"),
                            Unit("o", "blue", (4, 1, 0), facing=f)], seed=seed)
            bt.tick = 1
            bt.detect("x")
            n += "t" in bt.intel["blue"].spotted
        rates[name] = n
    assert rates["front"] > rates["back"] * 2, rates


def test_hearing():
    m = TacMap()
    m.wall(4, 5)
    a = Unit("a", "blue", (4, 4, 0))
    b_ = Unit("b", "red", (4, 6, 0))
    heard = 0
    for seed in range(100):
        bt = Battle(m, [Unit(**{**a.__dict__, "stats": dict(a.stats)}),
                        Unit(**{**b_.__dict__, "stats": dict(b_.stats)})], seed=seed)
        bt.tick = 1
        bt.units["b"].moved = True
        bt.detect("x")
        assert "b" not in bt.intel["blue"].spotted, "聲音不等於發現"
        heard += "b" in bt.intel["blue"].last_known
    assert 20 < heard < 100, f"4 m 內移動應有機率被聽到：{heard}/100"
    far = Battle(m, [Unit("a", "blue", (4, 1, 0)), Unit("b", "red", (4, 8, 0))], seed=1)
    far.tick = 1
    far.units["b"].moved = True
    far.detect("x")
    assert "b" not in far.intel["blue"].last_known


def test_ambush_coordinated():
    from engine.tactical.ai import Intent, TacticalAI
    m = TacMap()
    for y in (4, 5, 6):
        m.veg(1, y)
        m.veg(8, y)
    r0 = Unit("r0", "red", (1, 5, 0), posture="prone", facing=(1, -1))
    r1 = Unit("r1", "red", (8, 5, 0), posture="prone", facing=(-1, -1))
    b0 = Unit("b0", "blue", (2, 3, 0))
    bt = Battle(m, [r0, r1, b0], seed=1)
    bt.intel["red"].spotted["b0"] = 0
    bt.intel["red"].last_known["b0"] = (b0.pos, 0)
    ai = TacticalAI(bt, "red")
    plans = ai.plan(Intent("ambush", ambush_range=6))
    assert plans["r0"].mode == "shoot", plans["r0"]
    assert ai.ambush_state["sprung"], "一人觸發，全組同時發起"
    assert plans["r1"].mode == "shoot" or not bt.visible_parts(r1, b0)


def test_recon_fire():
    from engine.tactical.ai import TacticalAI
    m = TacMap()
    m.veg(4, 6)
    u = Unit("u", "blue", (4, 2, 0))
    e = Unit("e", "red", (4, 6, 0), posture="prone")
    bt = Battle(m, [u, e], seed=1)
    bt.intel["blue"].last_known["e"] = (e.pos, 0)
    ai = TacticalAI(bt, "blue")
    p = ai.recon_fire(u)
    assert p and p.fire == "suppress" and p.point is not None


def test_patience_then_hold():
    import engine.tactical.ai as ai_mod
    from engine.tactical.ai import Intent, TacticalAI
    m = TacMap()
    me = Unit("u", "blue", (4, 1, 0))
    enemies = [Unit(f"e{i}", "red", (x, 8, 0), facing=(0, -1)) for i, x in enumerate((1, 4, 7))]
    bt = Battle(m, [me] + enemies, seed=0)
    for e in enemies:
        bt.intel["blue"].last_known[e.id] = (e.pos, 0)
    ai = TacticalAI(bt, "blue")
    saved = dict(ai_mod.RISK_CAP)
    ai_mod.RISK_CAP["advance"] = -10
    try:
        caps = []
        for i in range(ai_mod.PATIENCE_STEP * (ai_mod.PATIENCE_MAX + 2)):
            bt.tick = i
            ai.plan(Intent("advance", goal=(4, 8, 0)), units=[me])
            caps.append(ai.patience)
            if ai.holding:
                break
        assert caps == sorted(caps), f"耐心應遞增：{caps}"
        assert ai.holding, "到達上限後應固守"
        assert any("請求新指示" in r for r in ai.reports), ai.reports
        plans = ai.plan(Intent("advance", goal=(4, 8, 0)), units=[me])
        assert all(p.mode != "move" for p in plans.values()), "固守後不再移動"
        ai.new_intent()
        assert not ai.holding and ai.patience == 0
    finally:
        ai_mod.RISK_CAP.clear()
        ai_mod.RISK_CAP.update(saved)


def test_grenade_over_wall():
    m = TacMap()
    m.low_wall(4, 6, "S", height=1.2)
    thrower = Unit("t", "blue", (4, 3, 0))
    hider = Unit("h", "red", (4, 6, 0), facing=(0, -1))
    hurt_any = 0
    for seed in range(40):
        b = Battle(m, [Unit(**{**thrower.__dict__, "stats": {"shots": 0, "hits": 0, "kills": 0}}),
                       Unit(**{**hider.__dict__, "stats": {"shots": 0, "hits": 0, "kills": 0}, "damage": {}})],
                   seed=seed)
        b.intel["blue"].last_known["h"] = (hider.pos, 0)
        c = m.hug_base(hider.pos, (0, -1))
        b.step({"t": Plan(mode="grenade", point=(c[0], c[1], 0.1))})
        assert b.units["t"].grenades == 1
        hurt_any += bool(b.units["h"].damage) or b.units["h"].suppression > 0
    assert hurt_any > 20, f"手榴彈應能越過矮牆影響掩體後的人：{hurt_any}/40"
    m2 = TacMap()
    m2.wall(4, 5)
    b2 = Battle(m2, [Unit("t", "blue", (4, 3, 0)), Unit("h", "red", (4, 7, 0))], seed=1)
    hurt, supp = b2.explode({"thrower": "t", "at": (9.0, 8.5, 0.1)})
    assert "h" not in supp, "整面牆後不受爆炸影響"


def test_melee_adjacent_only():
    m = TacMap()
    a = Unit("a", "blue", (4, 4, 0))
    b_ = Unit("b", "red", (4, 5, 0), facing=(0, -1))
    far = Unit("c", "red", (4, 8, 0), facing=(0, -1))
    bt = Battle(m, [a, b_, far], seed=3)
    bt.intel["red"].spotted["a"] = 0
    bt.step({"a": Plan(mode="melee", target="c")})
    assert not any("突擊" in l for l in bt.log), "不相鄰不能近戰"
    bt.step({"a": Plan(mode="melee", target="b")})
    assert any("突擊" in l for l in bt.log)
    wins = {"a": 0, "b": 0}
    for seed in range(200):
        x = Unit("a", "blue", (4, 4, 0), weapon="shotgun")
        y = Unit("b", "red", (4, 5, 0), facing=(0, -1), suppression=90)
        b2 = Battle(m, [x, y], seed=seed)
        b2.intel["red"].spotted["a"] = 0
        res = b2.melee(x, y, "m")
        if res:
            wins[res[0]] += 1
    assert wins["a"] > wins["b"] * 3, f"霰彈槍突擊被釘死的目標應大幅占優：{wins}"


def test_down_timer_and_aid():
    m = TacMap()
    for stabilize in (False, True):
        d = Unit("d", "blue", (4, 4, 0))
        medic = Unit("m", "blue", (4, 3, 0))
        foe = Unit("f", "red", (9, 9, 0))
        b = Battle(m, [d, medic, foe], seed=1)
        b._down(d, foe)
        if stabilize:
            b.step({"m": Plan(mode="aid", target="d")})
            assert b.units["d"].stabilized
        for _ in range(13):
            b.step({})
        assert b.units["d"].dead != stabilize, "未穩定的倒地者 12 tick 後離線；穩定後不會"


def test_downed_shot_dies():
    m = TacMap()
    d = Unit("d", "red", (4, 5, 0), facing=(0, -1))
    s = Unit("s", "blue", (4, 3, 0))
    b = Battle(m, [d, s], seed=1)
    b._down(d, s)
    b.apply([(s, [{"shooter": "s", "from": (0, 0, 0), "to": (0, 0, 0), "kind": "body", "ref": ("d", "torso"),
                   "dir": (0, 1, 0), "len": 1}])])
    assert b.units["d"].dead, "倒地者再被擊中即離線"


def test_morale_link_no_surrender():
    m = TacMap()
    for linked, can in ((True, False), (False, True)):
        surrendered = 0
        for seed in range(80):
            u = Unit("u", "red", (4, 5, 0))
            e = Unit("e", "blue", (9, 0, 0))
            b = Battle(m, [u, e], seed=seed, linked={"red": linked}, morale={"red": 0.0})
            for _ in range(5):
                b.step({})
            surrendered += b.units["u"].surrendered
        assert (surrendered > 0) == can, f"linked={linked} surrendered={surrendered}"
    b = Battle(m, [Unit("u", "red", (4, 5, 0))], linked={"red": True}, morale={"red": 0.0})
    assert b.morale_state("red") == "rout", "連線時有效士氣 +10，不到投降門檻"


def test_rout_retreats():
    from engine.tactical.ai import Intent, TacticalAI
    m = TacMap()
    r0 = Unit("r0", "red", (4, 7, 0), facing=(0, -1))
    r1 = Unit("r1", "red", (5, 7, 0), facing=(0, -1))
    b0 = Unit("b0", "blue", (4, 1, 0))
    bt = Battle(m, [r0, r1, b0], seed=2, morale={"red": 15.0}, linked={"red": False})
    bt.intel["red"].last_known["b0"] = (b0.pos, 0)
    ai = TacticalAI(bt, "red")
    plans = ai.plan(Intent("hold"))
    assert ai.routed and any("士氣崩潰" in r for r in ai.reports)
    movers = [p for p in plans.values() if p.mode == "move"]
    assert movers and all(p.path[-1][1] > 7 for p in movers), "潰退應遠離敵人"
    for _ in range(4):
        bt.step(ai.plan(Intent("hold")))
    assert any(u.exited for u in bt.units.values() if u.side == "red"), "潰退到邊緣應撤出"


def test_proficiency():
    from engine.tactical.units import prof_sigma
    assert prof_sigma(0) == 1.25 and prof_sigma(100) == 1.0 and abs(prof_sigma(200) - 0.85) < 1e-9
    m = TacMap()
    u = Unit("u", "blue", (4, 1, 0), prof={"rifle": 50, "lmg": 150})
    t = Unit("t", "red", (4, 7, 0))
    b = Battle(m, [u, t], seed=1)
    u.stats.update(shots=10, hits=4, kills=1)
    b.used["u"] = {"rifle"}
    b.proficiency_update()
    assert u.prof["rifle"] > 50, "使用中的武器成長"
    assert u.prof["lmg"] == 149, "參戰沒用到：100 以上部分 −1"
    u.prof["lmg"] = 100
    b.proficiency_update()
    assert u.prof["lmg"] == 100, "不低於 100"
    rates = {}
    for p in (0, 200):
        hits = 0
        for seed in range(300):
            s = Unit("s", "blue", (4, 1, 0), prof={"rifle": p})
            tt = Unit("t", "red", (4, 9, 0), facing=(0, -1))
            bt = Battle(m, [s, tt], seed=seed)
            bt.intel["blue"].spotted["t"] = 0
            bt.tick = 1
            hits += sum(1 for r in bt.fire(s, Plan(mode="shoot", target="t", fire="aimed"), "x")
                        if r["kind"] == "body")
        rates[p] = hits
    assert rates[200] > rates[0], rates


def test_smoke_blocks_sight_not_bullets():
    m = TacMap()
    a = Unit("a", "blue", (4, 1, 0), posture="stand")
    t = Unit("t", "red", (4, 8, 0), posture="stand", facing=(0, -1))
    b = Battle(m, [a, t], seed=1)
    clear = b.visibility(a, t)
    b.smokes.append({"x": 9.0, "y": 9.0, "r": 3.0, "top": 3.5, "born": 0, "expires": 10, "by": "a"})
    b.tick = 1
    smoky = b.visibility(a, t)
    assert clear > 0.9 and abs(smoky - clear * 0.3) < 0.05, f"熱感測保留約 30%：{clear:.2f} → {smoky:.2f}"
    assert not b.visible_parts(a, t), "煙霧中看不到可瞄準的部位"
    hits = 0
    for i in range(60):
        b.tick = 1 + (i % 5)
        for s in b.fire(a, Plan(mode="shoot", point=t.torso(m), fire="suppress"), f"s{i}"):
            hits += s["kind"] == "body" and s["ref"][0] == "t"
    assert hits > 0, "子彈可穿過煙霧"
    b.tick = 11
    assert b.visibility(a, t) == clear, "煙霧消散"


def test_smoke_blocks_overwatch():
    fired = {}
    for smoke in (False, True):
        total = 0
        for seed in range(20):
            m = TacMap()
            w = Unit("w", "red", (4, 8, 0), facing=(0, -1))
            r = Unit("r", "blue", (1, 3, 0), facing=(1, 0))
            b = Battle(m, [w, r], seed=seed)
            b.intel["red"].spotted["r"] = 0
            b.intel["red"].last_known["r"] = (r.pos, 0)
            if smoke:
                b.smokes.append({"x": 6.0, "y": 9.0, "r": 3.5, "top": 3.5, "born": -1, "expires": 10, "by": "r"})
            b.step({"w": Plan(mode="overwatch"), "r": Plan(mode="move", path=[(2, 3, 0), (3, 3, 0)])})
            total += b.units["w"].stats["shots"]
        fired[smoke] = total
    assert fired[False] > 0 and fired[True] == 0, fired


def test_smoke_throw_and_carry():
    m = TacMap()
    u = Unit("u", "blue", (4, 1, 0))
    assert u.smokes == 1 and u.grenades == 2, "預設 煙霧 1、破片 2"
    b = Battle(m, [u, Unit("e", "red", (4, 9, 0))], seed=1)
    b.step({"u": Plan(mode="smoke", point=(9.0, 10.0, 0.0))})
    assert u.smokes == 0 and len(b.smokes) == 1
    s = b.smokes[0]
    assert s["born"] == b.tick and s not in b.active_smokes(), "下一 tick 才生效"
    b.step({})
    assert s in b.active_smokes()


def test_rescue_drag_exit():
    r = runner.run(scenarios.rescue, seed=0)
    b = r["battle"]
    assert b.units["b3"].exited, "撤退時拖回倒地隊友並一起撤出"


def test_major_events():
    from engine.tactical.ai import TacticalAI
    m = TacMap()
    a = Unit("a", "blue", (4, 1, 0))
    c = Unit("c", "blue", (5, 1, 0))
    e = Unit("e", "red", (4, 8, 0))
    b = Battle(m, [a, c, e], seed=1)
    ai = TacticalAI(b, "blue")
    assert ai.major_events() == []
    c.down = True
    assert "有隊員倒地" in ai.major_events()
    c.down = False
    a.ammo = c.ammo = 10
    assert "彈藥不足三成" in ai.major_events()
    a.ammo = c.ammo = 200
    for i in range(4):
        b.intel["blue"].last_known[f"x{i}"] = ((0, 9, 0), 0)
        b.units[f"x{i}"] = Unit(f"x{i}", "red", (i, 9, 0))
    assert "兵力劣勢" in ai.major_events()


def test_grenade_rules():
    from engine.tactical.ai import TacticalAI
    m = TacMap()
    m.low_wall(4, 7, "S", height=1.0)
    m.veg(7, 7)
    thrower = Unit("t", "blue", (4, 2, 0))
    covered = Unit("c", "red", (4, 7, 0), facing=(0, -1))
    open_ = Unit("o", "red", (1, 7, 0), facing=(0, -1))
    in_veg = Unit("v", "red", (7, 7, 0), facing=(0, -1), posture="prone")
    b = Battle(m, [thrower, covered, open_, in_veg], seed=1)
    ai = TacticalAI(b, "blue")
    for e in (covered, open_, in_veg):
        b.intel["blue"].last_known[e.id] = (e.pos, 0)
    assert ai.behind_cover(thrower, covered.pos, covered.posture), "矮牆後算掩體"
    assert not ai.behind_cover(thrower, open_.pos, open_.posture), "開闊地不算"
    assert not ai.behind_cover(thrower, in_veg.pos, in_veg.posture), "植被只遮視線，不算實心掩體"
    p = ai.grenade_plan(thrower)
    assert p is not None and abs(p.point[0] - covered.torso(m)[0]) < 1.0, "只對躲在實心掩體後的目標投擲"
    assert ai.grenade_plan(thrower) is None, "個人冷卻 3 tick"
    b.tick += 3
    thrower.grenades = 1
    assert ai.grenade_plan(thrower) is None, "最後一顆：8 m 外不投"
    assert ai.grenade_plan(thrower, mode="assault") is not None, "突擊時可用最後一顆"
    far_only = Battle(m, [Unit("t", "blue", (4, 2, 0)), Unit("o", "red", (1, 7, 0))], seed=1)
    ai2 = TacticalAI(far_only, "blue")
    far_only.intel["blue"].last_known["o"] = ((1, 7, 0), 0)
    assert ai2.grenade_plan(far_only.units["t"]) is None, "沒有掩體的目標不投"


def test_grenade_per_tick():
    from engine.tactical.ai import Intent, TacticalAI
    m = TacMap()
    for x in (3, 5):
        m.low_wall(x, 6, "S", height=1.0)
    blues = [Unit(f"b{i}", "blue", (2 + i, 2, 0)) for i in range(4)]
    reds = [Unit("r0", "red", (3, 6, 0), facing=(0, -1)), Unit("r1", "red", (5, 6, 0), facing=(0, -1))]
    for kind, cap in (("hold", 1), ("assault", 2)):
        b = Battle(m, [Unit(**{**u.__dict__, "stats": {"shots": 0, "hits": 0, "kills": 0}}) for u in blues + reds],
                   seed=1)
        for r in reds:
            b.intel["blue"].last_known[r.id] = (r.pos, 0)
        ai = TacticalAI(b, "blue")
        plans = ai.plan(Intent(kind, goal=(4, 6, 0)))
        n = sum(1 for p in plans.values() if p.mode == "grenade")
        assert n <= cap, f"{kind}：每 tick 最多 {cap} 顆，實際 {n}"


def test_large_map_start_hidden():
    m, units, _, _ = scenarios.district()
    assert (m.w, m.h) == (25, 25)
    b = Battle(m, units)
    blue = [u for u in units if u.side == "blue"]
    red = [u for u in units if u.side == "red"]
    assert all(b.visibility(x, y) == 0 for x in blue for y in red), "50 m 街區開局互相看不見"


def test_detect_range_factor():
    f = Battle.detect_range_factor
    assert f(10) == 1.0 and f(15) == 1.0 and abs(f(100) - 0.05) < 1e-9 and f(200) == 0.05
    assert f(40) > f(60) > f(90)


def test_march_speed():
    m = TacMap(w=25, h=25)
    u = Unit("u", "blue", (2, 2, 0))
    e = Unit("e", "red", (22, 22, 0))
    b = Battle(m, [u, e], seed=1)
    assert b.move_budget(u) == 6, "未接敵：行軍速度"
    path = [(2, y, 0) for y in range(3, 12)]
    b.step({"u": Plan(mode="move", path=path)})
    assert b.units["u"].pos[1] - 2 >= 5, f"一 tick 走 6 格附近：{b.units['u'].pos}"
    b.intel["blue"].last_known["e"] = (e.pos, b.tick)
    assert b.move_budget(u) == 2, "接敵：戰鬥速度"


def test_grid_matches_bruteforce():
    import random
    from engine.tactical.geometry import ray_box, sub
    m, _, _, _ = scenarios.district()
    rnd = random.Random(1)
    for _ in range(400):
        a = (rnd.uniform(0, 50), rnd.uniform(0, 50), rnd.uniform(0.2, 2.0))
        c = (rnd.uniform(0, 50), rnd.uniform(0, 50), rnd.uniform(0.2, 2.0))
        d = sub(c, a)
        brute = any(bx.material == "solid" and (h := ray_box(a, d, bx, 1.0)) and h[1] > 1e-6 and h[0] < 1 - 1e-6
                    for bx in m.boxes)
        assert m.blocked(a, c) == brute, (a, c)


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK {t.__name__}")
    print("tactical_test OK")


if __name__ == "__main__":
    main()
