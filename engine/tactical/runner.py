"""跑一場戰術戰鬥。"""
from __future__ import annotations

from engine.tactical import timeline
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle


def sop_captain(ai, intent: Intent, side: str, sop_table: dict, mark: list) -> Intent:
    """批次用簡易隊長：戰術層回報「請求新指示」或士氣崩潰時，照應變準則換戰術（engine/sop.py）。"""
    new = ai.reports[mark[0]:]
    mark[0] = len(ai.reports)
    sit = None
    for r in new:
        if "士氣崩潰" in r:
            sit = "outnumbered"
        elif "請求新指示" in r:
            sit = "stalled"
    if sit is None:
        return intent
    lk = ai.b.intel[side].last_known
    if sit == "stalled" and any(ai.b.map.room_at(n) for n, _ in lk.values()):
        sit = "enemy_in_building"
    act = sop_table.get(sit)
    if not act or act == intent.kind:
        return intent
    goal = min((n for n, _ in lk.values()), key=lambda n: (abs(n[0]), n)) if lk else intent.goal
    ai.new_intent()
    ai.reports.append(f"T{ai.b.tick} 簡易隊長：依準則（{sit}）改為 {act}")
    if act == "breach":
        return Intent("breach")
    if act == "retreat":
        return Intent("retreat", toward=(0, -1) if side == "blue" else (0, 1))
    if act == "flank":
        return Intent("flank", goal=goal, side="left" if ai.b.tick % 2 else "right")
    return Intent(act, goal=goal)


def run(builder, seed: int = 0, max_ticks: int = 60, order_hook=None, trace: bool = False,
        captain: str | None = None, sop: dict | None = None, keep_busy: bool = False) -> dict:
    """captain：給某一方一個簡易隊長——出現重大事件時改下撤退（模擬「重大事件才給撤退選項」）。
    sop：{side: 準則表}，給該方一個依應變準則換戰術的簡易隊長。
    keep_busy：和網頁版 Engagement 一樣，射擊、投擲、救護做到一半的人不換掉（預設照舊每步全部換，舊批次結果不變）。"""
    tmap, units, blue_intent, red_intent = builder()
    b = Battle(tmap, units, seed=seed)
    b.order_hook = order_hook
    ais = {"blue": TacticalAI(b, "blue"), "red": TacticalAI(b, "red")}
    intents: dict[str, Intent] = {"blue": blue_intent, "red": red_intent}
    b.detect("init")
    first_down = None
    pos_hist: dict[str, list] = {}
    retreat_at = None
    sop_marks: dict[str, list] = {}
    for _ in range(max_ticks):
        if captain and intents[captain].kind != "retreat":
            ev = ais[captain].major_events()
            if ev:
                intents[captain] = Intent("retreat", toward=(0, -1) if captain == "blue" else (0, 1))
                ais[captain].new_intent()
                ais[captain].reports.append(f"T{b.tick} 隊長：{'、'.join(ev)} → 撤退")
                retreat_at = b.tick
        for side, table in (sop or {}).items():
            intents[side] = sop_captain(ais[side], intents[side], side, table, sop_marks.setdefault(side, [0]))
        plans = {}
        for side in ("blue", "red"):
            plans.update(ais[side].plan(intents[side]))
        if keep_busy:
            # 和網頁版 Engagement 一樣：射擊、投擲、救護做到一半的人做完再換（例如探頭射擊打完才蹲回去）
            b.assign({uid: p for uid, p in plans.items() if timeline.idle(b, uid) or not timeline.busy(b, uid)})
            b.advance()
        else:
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
        if any(i.kind == "retreat" for i in intents.values()) and all(
                not u.alive for u in b.units.values() if intents[u.side].kind == "retreat"):
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
        "retreat_at": retreat_at,
        "smokes": sum(1 for l in b.log if "煙霧彈" in l),
        "outcome": outcome,
        "grenades": sum(1 for l in b.log if "投擲手榴彈" in l),
        "flashes": sum(1 for l in b.log if "投擲閃光彈" in l),
        "dazzled": sum(1 for l in b.log if "光學過載" in l),
        "braces": sum(1 for l in b.log if " 架槍" in l and "中斷" not in l),
        "cleared": any("房間已清空" in r for r in ais["blue"].reports),
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
        "sop_changes": sum(1 for r in ais["blue"].reports + ais["red"].reports if "簡易隊長：依準則" in r),
        "positions": pos_hist,
        "battle": b,
    }
