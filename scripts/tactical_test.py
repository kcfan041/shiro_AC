"""戰術層一致性測試（combat.md v0.2 §15.1）。不需要 LLM。"""
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


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK {t.__name__}")
    print("tactical_test OK")


if __name__ == "__main__":
    main()
