"""跑一場戰術戰鬥。"""
from __future__ import annotations

from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle


def run(builder, seed: int = 0, max_ticks: int = 60, order_hook=None, trace: bool = False) -> dict:
    tmap, units, blue_intent, red_intent = builder()
    b = Battle(tmap, units, seed=seed)
    b.order_hook = order_hook
    ais = {"blue": TacticalAI(b, "blue"), "red": TacticalAI(b, "red")}
    intents: dict[str, Intent] = {"blue": blue_intent, "red": red_intent}
    b.detect("init")
    first_down = None
    pos_hist: dict[str, list] = {}
    for _ in range(max_ticks):
        plans = {}
        for side in ("blue", "red"):
            plans.update(ais[side].plan(intents[side]))
        b.step(plans)
        for u in b.units.values():
            pos_hist.setdefault(u.id, []).append(u.pos)
        if first_down is None and any(not u.alive for u in b.units.values()):
            first_down = b.tick
        if trace:
            print(f"--- T{b.tick}")
            print(tmap.render(b.units.values()))
        if b.over():
            break
    winner = b.over() or "draw"
    b.proficiency_update()
    outcome = {}
    for side in ("blue", "red"):
        us = [u for u in b.units.values() if u.side == side]
        outcome[side] = {
            "dead": sum(u.dead for u in us),
            "down": sum(u.down and not u.dead for u in us),
            "exited": sum(u.exited and not u.down for u in us),
            "surrendered": sum(u.surrendered for u in us),
            "morale": round(b.morale.get(side, 0)),
        }
    return {
        "outcome": outcome,
        "grenades": sum(1 for l in b.log if "投擲手榴彈" in l),
        "melee": sum(1 for l in b.log if "突擊" in l),
        "winner": winner,
        "ticks": b.tick,
        "first_down": first_down,
        "blue_lost": sum(1 for u in b.units.values() if u.side == "blue" and not u.alive),
        "red_lost": sum(1 for u in b.units.values() if u.side == "red" and not u.alive),
        "blue_n": sum(1 for u in b.units.values() if u.side == "blue"),
        "red_n": sum(1 for u in b.units.values() if u.side == "red"),
        "stats": {u.id: dict(u.stats, weapon=u.weapon, side=u.side) for u in b.units.values()},
        "reports": ais["blue"].reports + ais["red"].reports,
        "positions": pos_hist,
        "battle": b,
    }
