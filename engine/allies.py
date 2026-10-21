"""友方電腦小隊（規則式，不用 LLM、沒有指揮官）：平常守我方據點周邊；玩家小隊附近交火就派一支去支援，
打完回家；彈藥或兵力不足回據點整補。傷亡不進預備兵力池、也不從池裡補人（補充由本模組自己生）。

- 小隊資料仍是一般 Squad（commander=None，ai＝{"home", "task", "goal", "since"}），作戰層移動照 move／hold 規則，
  在玩家的活動區域內時自動進戰術層（zone 會把落在區域內的小隊放進去），在區域外只在作戰層移動。
- 任務：guard（在 home 固守）／support（去 goal 支援，接敵後壓制推進）／return（回 home）／refit（回據點整補）。
"""
from __future__ import annotations

import random

from engine import scenario
from engine.models import Action, Member, Squad, World

ALLY_CARDS = [
    {"id": "ai_charlie", "callsign": "Charlie", "home": None,
     "names": ["C-21", "C-22", "C-23", "C-24"], "weapons": ["lmg", "rifle", "rifle", "dmr"]},
    {"id": "ai_delta", "callsign": "Delta", "home": None,
     "names": ["D-31", "D-32", "D-33", "D-34"], "weapons": ["rifle", "shotgun", "rifle", "dmr"]},
]
SUPPORT_RANGE = 2           # 玩家小隊在交戰、而且離友軍家 2 格內 → 派一支去
REFIT_AMMO = 35             # 小隊彈藥（%）低於這個 → 回據點整補
REFIT_ALIVE = 2             # 能作戰的人少於這個 → 回據點整補
REBUILD_EVERY = 120         # 全滅後多久（作戰 tick）在據點重新編成
SUPPORT_TIMEOUT = 60        # 支援出發後最多這麼久（作戰 tick）就回家


def homes() -> list[str]:
    """友軍駐守的格：據點旁邊、往敵方方向的兩格（沒有就據點本身）。"""
    adj = sorted(scenario.adjacent(scenario.BASE),
                 key=lambda c: (-scenario.ROWS.index(c[0]), abs(scenario.COLS.index(c[1]) -
                                                                  scenario.COLS.index(scenario.BASE[1])), c))
    out = adj[:2]
    while len(out) < 2:
        out.append(scenario.BASE)
    return out


def ally_squads(world: World) -> list[Squad]:
    return [s for s in world.squads.values() if s.ai is not None]


def _make(card: dict, home: str) -> Squad:
    from engine import persona
    from engine.roster import assign_weapons
    members = []
    for i, (n, w) in enumerate(zip(card["names"], card["weapons"])):
        m = Member(name=n, weapon=w, trait="小隊長" if i == 0 else "")
        m.traits = {"aggr": 0.0, "caut": 0.0, "comp": 0.3, "bond": 0.3}
        m.persona = 0.2
        members.append(m)
    s = Squad(id=card["id"], callsign=card["callsign"], leader={"name": members[0].name}, members=members,
              pos=home, max_size=len(members), morale=75)
    s.card_id = card["id"]
    s.autonomy = "defend"
    s.ai = {"home": home, "task": "guard", "goal": None, "since": 0}
    assign_weapons(s, {m.name for m in members})
    _ = persona
    return s


def ensure(world: World) -> list[str]:
    """沒有就建；全滅的過 REBUILD_EVERY 再重建。回傳事件文字。"""
    log = []
    hs = homes()
    for card, home in zip(ALLY_CARDS, hs):
        s = world.squads.get(card["id"])
        if s is None:
            s = _make(card, home)
            world.squads[s.id] = s
            log.append(f"友軍 {s.callsign} 進駐 {scenario.cell_desc(home)}")
            continue
        if s.status != "active" and world.tick - (s.ai or {}).get("since", 0) >= REBUILD_EVERY:
            new = _make(card, scenario.BASE)
            new.ai["task"] = "return"
            new.ai["since"] = world.tick
            new.action = Action(type="move", target=home, path=scenario.find_path(scenario.BASE, home) or [home],
                                ordered=True)
            world.squads[s.id] = new
            log.append(f"友軍 {new.callsign} 在 {scenario.BASE} 重新編成")
    return log


def _send(s: Squad, cell: str):
    path = scenario.find_path(s.pos, cell) if s.pos != cell else []
    if path:
        s.action = Action(type="move", target=cell, path=path, ordered=True)
    else:
        s.action = Action(type="hold")


PICKUP_R = 3                # 友軍走到傷員 3 格內就接手
MEDEVAC_TIMEOUT = 120       # 後送任務最多這麼久（作戰 tick），接不到就放棄


def request_medevac(sim, squad: Squad) -> tuple[bool, str]:
    """玩家請求後送：派一支在家、有空的友軍去接這支小隊的已穩定倒地者，拖回據點。"""
    w = sim.world
    hurt = [m for m in squad.members if m.down and not m.lost]
    if not hurt:
        return False, "小隊沒有倒地的隊員。"
    if any(job["squad"] == squad.id for job in w.medevac):
        return False, "已經有友軍在路上了。"
    free = [s for s in ally_squads(w) if s.status == "active" and s.ai["task"] in ("guard", "return")
            and len(s.alive_members()) >= 2 and not sim.in_engagement(s)]
    if not free:
        return False, "目前沒有友軍可以後送。"
    a = min(free, key=lambda s: (scenario.distance(s.pos, squad.pos), s.id))
    w.medevac.append({"squad": squad.id, "ally": a.id, "names": [m.name for m in hurt], "since": w.tick,
                      "state": "inbound"})
    a.ai.update(task="medevac", goal=squad.pos, since=w.tick)
    _send(a, squad.pos)
    return True, f"友軍 {a.callsign} 出發後送 {'、'.join(m.name for m in hurt)}（從 {scenario.cell_desc(a.pos)}）"


def _medevac_step(sim, a: Squad, job: dict, log: list):
    """後送任務的一步：前往 → 接手（把傷員從原小隊移到友軍名下）→ 帶回據點 → 在據點休養。"""
    from engine import zone as zmod
    w = sim.world
    src = w.squads.get(job["squad"])
    if job["state"] == "inbound":
        if src is None or src.status != "active":
            job["state"] = "done"
            return
        hurt = [m for m in src.members if m.name in job["names"] and m.down and not m.lost]
        if not hurt:
            job["state"] = "done"
            return
        target = hurt[0]
        if target.gpos and any(m.gpos and max(abs(m.gpos[0] - target.gpos[0]), abs(m.gpos[1] - target.gpos[1]))
                               <= PICKUP_R for m in a.alive_members()):
            for m in hurt:
                src.members.remove(m)
                m.home_squad = src.id
                m.stabilized = True
                a.members.append(m)
                src.away += 1
            job["state"] = "carrying"
            a.ai.update(goal=scenario.BASE)
            _send(a, scenario.BASE)
            log.append(f"友軍 {a.callsign} 接到 {'、'.join(m.name for m in hurt)}，送回 {scenario.BASE}")
            return
        cell = zmod.squad_cell(src) or src.pos
        c = None
        if target.gpos:
            from engine.tactical import worldmap
            c = worldmap.cell_of(target.gpos[0], target.gpos[1])
        goal = c or cell
        if a.pos != goal and (a.action.type != "move" or a.action.target != goal):
            _send(a, goal)
        elif a.pos == goal and target.gpos:
            # 到了那一格：直接走到傷員旁邊（標定集結點＝傷員位置）
            from engine.tactical import worldmap
            gcell, local = worldmap.to_local(tuple(target.gpos))
            if a.action.type != "advance" or a.action.mark != [local[0], local[1]]:
                a.action = Action(type="advance", ordered=True, mark=[local[0], local[1]], mark_kind="rally",
                                  reason="前往後送傷員")
                a.action.mark_cell = gcell
        return
    if job["state"] == "carrying":
        patients = [m for m in a.members if m.home_squad == job["squad"]]
        if not patients:
            job["state"] = "done"
            return
        from engine.tactical import worldmap
        home = all(m.gpos and worldmap.cell_of(m.gpos[0], m.gpos[1]) == scenario.BASE for m in patients) \
            and a.pos == scenario.BASE
        if home:
            for m in patients:
                a.members.remove(m)
                if m.lost:
                    if src is not None:
                        src.away = max(0, src.away - 1)
                    continue
                m.down, m.down_tick, m.stabilized = False, None, False
                m.gpos, m.tpos = None, None
                w.resting.append(m)
            job["state"] = "done"
            log.append(f"友軍 {a.callsign} 把傷員送回 {scenario.BASE}，等原小隊回來整補時歸隊")
            return
        if a.action.type != "move" and not sim.in_engagement(a):
            _send(a, scenario.BASE) if a.pos != scenario.BASE else sim._regroup(a)


def rejoin_resting(sim, squad: Squad) -> list[str]:
    """原小隊在據點整補完成：在據點休養的原隊員歸隊。"""
    w = sim.world
    back = [m for m in w.resting if m.home_squad == squad.id]
    for m in back:
        w.resting.remove(m)
        m.home_squad = None
        m.hp = max(m.hp, 60)
        squad.members.append(m)
        squad.away = max(0, squad.away - 1)
    return [m.name for m in back]


def think(sim, rng: random.Random) -> list[str]:
    """每個作戰 tick：決定每支友軍的任務與行動。回傳事件文字（給指揮官的事件欄）。"""
    w = sim.world
    log = ensure(w)
    allies = [s for s in ally_squads(w) if s.status == "active"]
    players = [s for s in w.squads.values() if s.status == "active" and s.ai is None and s.commander]
    hot = [s for s in players if sim.in_engagement(s)]
    busy = {s.ai.get("goal") for s in allies if s.ai.get("task") == "support"}
    for job in list(w.medevac):
        a = w.squads.get(job["ally"])
        if a is None or a.status != "active" or w.tick - job["since"] >= MEDEVAC_TIMEOUT:
            job["state"] = "done"
            if a is not None and a.status == "active":
                for m in [m for m in a.members if m.home_squad == job["squad"]]:
                    a.members.remove(m)
                    src = w.squads.get(job["squad"])
                    if src is not None and src.status == "active":
                        m.home_squad = None
                        src.members.append(m)
                        src.away = max(0, src.away - 1)
            log.append("後送任務取消（友軍無法抵達）")
        else:
            _medevac_step(sim, a, job, log)
        if job["state"] == "done":
            w.medevac.remove(job)
            if a is not None and a.status == "active" and a.ai.get("task") == "medevac":
                a.ai.update(task="return", goal=a.ai["home"], since=w.tick)
                _send(a, a.ai["home"])
    for s in allies:
        st = s.ai
        if st["task"] == "medevac":
            continue
        alive = len(s.alive_members())
        in_fight = sim.in_engagement(s)
        if st["task"] != "refit" and not in_fight and (s.ammo < REFIT_AMMO or alive < REFIT_ALIVE):
            st.update(task="refit", goal=scenario.BASE, since=w.tick)
            _send(s, scenario.BASE)
            log.append(f"友軍 {s.callsign} 回 {scenario.BASE} 整補")
            continue
        if st["task"] == "refit":
            if s.pos == scenario.BASE and s.ammo >= 100 and alive >= min(REFIT_ALIVE + 1, s.max_size):
                st.update(task="return", goal=st["home"], since=w.tick)
                _send(s, st["home"])
            elif s.pos == scenario.BASE and s.action.type != "hold":
                s.action = Action(type="hold")
            continue
        if st["task"] == "support":
            target = next((p for p in hot if p.pos == st["goal"] or scenario.distance(p.pos, st["goal"]) <= 1), None)
            if (target is None and not in_fight) or w.tick - st["since"] >= SUPPORT_TIMEOUT:
                st.update(task="return", goal=st["home"], since=w.tick)
                _send(s, st["home"])
                log.append(f"友軍 {s.callsign} 結束支援，返回 {scenario.cell_desc(st['home'])}")
                continue
            if target is not None and target.pos != st["goal"]:
                st["goal"] = target.pos
            if in_fight:
                if s.action.type not in ("supp_advance", "advance", "flank", "assault") or s.action.ordered:
                    s.action = Action(type="supp_advance", ordered=False, auto=True, reason="支援友軍交戰")
            elif s.pos != st["goal"] and (s.action.type != "move" or s.action.target != st["goal"]):
                _send(s, st["goal"])
            continue
        if st["task"] == "return":
            if s.pos == st["home"] or (s.action.type != "move" and not in_fight):
                if s.pos != st["home"]:
                    _send(s, st["home"])
                else:
                    st.update(task="guard", goal=None, since=w.tick)
                    s.action = Action(type="hold")
            continue
        # guard：在家固守；附近玩家交戰 → 去支援（一場交戰只派一支）
        if s.pos != st["home"] and s.action.type != "move" and not in_fight:
            _send(s, st["home"])
            continue
        need = [p for p in hot if p.pos not in busy and scenario.distance(p.pos, st["home"]) <= SUPPORT_RANGE]
        if need and not in_fight:
            p = min(need, key=lambda x: (scenario.distance(x.pos, st["home"]), x.id))
            st.update(task="support", goal=p.pos, since=w.tick)
            busy.add(p.pos)
            _send(s, p.pos)
            log.append(f"友軍 {s.callsign} 前往 {scenario.cell_desc(p.pos)} 支援 {p.callsign}")
    return log


def tick_squad(sim, s: Squad):
    """友軍的作戰層一拍（沒有 LLM）：移動進度、交戰外穩定傷員、整補、交戰事件只更新狀態。"""
    from engine import reserve
    major: list = []
    minor: list = []
    before_lost = {m.name for m in s.members if m.lost}
    if not sim.in_engagement(s):
        sim._progress_action(s, major, minor)
        sim._downed_outside_combat(s, major)
    elif sim.zone_mode and s.action.type == "move" and s.action.ordered:
        sim._progress_action(s, major, minor)
    sim._apply_tac_events(s, major, minor, before_lost)
    if not any(not m.lost for m in s.members) or (not s.alive_members() and not sim.in_engagement(s)):
        s.status = "destroyed"
        s.contact = None
        s.ai["since"] = sim.world.tick
        reserve.clear_home(sim.world, s.id)
        return [f"友軍 {s.callsign} 全滅"]
    sim._resupply(s, major)
    if s.pos == scenario.BASE and s.ai.get("task") == "refit" and s.rest_ticks >= 1:
        _refill_members(sim, s)
    for kind, fact, _ in major:
        sim._experience(s, kind, fact)
    return []


def _refill_members(sim, s: Squad):
    """整補完成時補回缺額（不動用預備兵力池）。"""
    from engine.sim import REST_FULL
    if s.rest_ticks < REST_FULL:
        return
    card = next((c for c in ALLY_CARDS if c["id"] == s.id), None)
    if card is None:
        return
    have = {m.name for m in s.members if not m.lost}
    s.members = [m for m in s.members if not m.lost]
    for n, wpn in zip(card["names"], card["weapons"]):
        if len(s.members) >= s.max_size:
            break
        if n in have:
            continue
        m = Member(name=n, weapon=wpn)
        m.traits = {"aggr": 0.0, "caut": 0.0, "comp": 0.3, "bond": 0.3}
        m.persona = 0.2
        m.joined = sim.world.tick
        s.members.append(m)
