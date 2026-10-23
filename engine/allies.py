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


MARCH_STEPS_PER_CELL = 14   # 連續地圖：一格 50 m、行軍約 1.5 m/s（2 AP／2.5 秒）再加縱隊等待，抓 14 個 2.5 秒


def eta_seconds(sim, frm: str, to: str) -> int:
    """友軍從 frm 走到 to 的粗估秒數（無線電報用，四捨五入到 10 秒）。"""
    from engine.sim import STEP_TICKS
    n = len(scenario.find_path(frm, to) or []) if frm != to else 0
    from engine.tactical.sim import TICK_S
    per = MARCH_STEPS_PER_CELL * TICK_S if sim.zone_mode else STEP_TICKS["move"] * sim.tick_seconds
    return int(round(n * per / 10.0)) * 10


def _eta_text(sim, frm: str, to: str) -> str:
    t = eta_seconds(sim, frm, to)
    return "馬上到" if t <= 0 else f"約 {t} 秒到"


def _free(sim, w: World) -> list[Squad]:
    """在家或回家途中、≥2 人能作戰、沒在交戰的友軍。"""
    return [s for s in ally_squads(w) if s.status == "active" and s.ai["task"] in ("guard", "return")
            and len(s.alive_members()) >= 2 and not sim.in_engagement(s)]


def request_support(sim, commander_id: str, cell: str) -> tuple[bool, str]:
    """指揮官指定一格請友軍支援：派最近的空閒友軍過去，到了之後守在那裡（有交戰就壓制推進），
    SUPPORT_TIMEOUT 後回家。每位指揮官同時只能請一支。"""
    w = sim.world
    if cell not in scenario.CELLS:
        return False, "沒有這一格。"
    mine = next((s for s in ally_squads(w) if s.status == "active" and s.ai.get("requested") == commander_id
                 and s.ai["task"] == "support"), None)
    if mine is not None:
        return False, f"友軍 {mine.callsign} 已經在支援 {mine.ai['goal']}。"
    free = _free(sim, w)
    if not free:
        return False, "目前沒有空閒的友軍。"
    a = min(free, key=lambda s: (scenario.distance(s.pos, cell), s.id))
    a.ai.update(task="support", goal=cell, since=w.tick, requested=commander_id, arrived=False, hot_said=False,
                **{"for": None})
    _send(a, cell)
    sim.ally_radio(a, f"收到，{len(a.alive_members())} 人從 {a.pos} 前往 {cell}，{_eta_text(sim, a.pos, cell)}。",
                   commander_id=commander_id, cell=cell)
    return True, f"友軍 {a.callsign} 前往 {scenario.cell_desc(cell)} 支援"


def _support_radio(sim, s: Squad, text: str):
    """支援任務的無線電：自動支援 → 被支援的小隊；指揮官請求的 → 那位指揮官（與他在那附近的小隊）。"""
    st = s.ai
    sim.ally_radio(s, text, squad_id=st.get("for"), commander_id=st.get("requested"), cell=st.get("goal"))


PICKUP_R = 3                # 友軍走到傷員 3 格內就接手
MEDEVAC_TIMEOUT = 120       # 後送任務每一趟（去接／送回）最多這麼久（作戰 tick，交火中不算），到不了就放棄


def request_medevac(sim, squad: Squad) -> tuple[bool, str]:
    """玩家請求後送：派一支在家、有空的友軍去接這支小隊的已穩定倒地者，拖回據點。"""
    w = sim.world
    hurt = [m for m in squad.members if m.down and not m.lost]
    if not hurt:
        return False, "小隊沒有倒地的隊員。"
    if any(job["squad"] == squad.id for job in w.medevac):
        return False, "已經有友軍在路上了。"
    free = _free(sim, w)
    if not free:
        return False, "目前沒有友軍可以後送。"
    a = min(free, key=lambda s: (scenario.distance(s.pos, squad.pos), s.id))
    w.medevac.append({"squad": squad.id, "ally": a.id, "names": [m.name for m in hurt], "since": w.tick,
                      "state": "inbound"})
    a.ai.update(task="medevac", goal=squad.pos, since=w.tick)
    _send(a, squad.pos)
    sim.ally_radio(a, f"收到後送請求，從 {a.pos} 過去接 {'、'.join(m.name for m in hurt)}，"
                      f"{_eta_text(sim, a.pos, squad.pos)}。", squad_id=squad.id)
    return True, f"友軍 {a.callsign} 出發後送 {'、'.join(m.name for m in hurt)}（從 {scenario.cell_desc(a.pos)}）"


def _gap(a: Squad, m: Member) -> int:
    """友軍離傷員最近的人有幾格（沒有位置當成很遠）。"""
    if not m.gpos:
        return 10 ** 6
    return min((max(abs(u.gpos[0] - m.gpos[0]), abs(u.gpos[1] - m.gpos[1])) for u in a.alive_members() if u.gpos),
               default=10 ** 6)


def _near(a: Squad, m: Member) -> bool:
    """友軍有人在傷員 PICKUP_R 格內。"""
    return _gap(a, m) <= PICKUP_R


def _where(m: Member, fallback: str) -> str:
    from engine.tactical import worldmap
    return (worldmap.cell_of(m.gpos[0], m.gpos[1]) if m.gpos else None) or fallback


def _go_to(a: Squad, m: Member, fallback: str):
    """走到傷員那一格；到了就以傷員位置為集結點走過去。"""
    from engine.tactical import worldmap
    goal = worldmap.cell_of(m.gpos[0], m.gpos[1]) if m.gpos else fallback
    if a.pos != goal and (a.action.type != "move" or a.action.target != goal):
        _send(a, goal)
    elif a.pos == goal and m.gpos:
        gcell, local = worldmap.to_local(tuple(m.gpos))
        if a.action.type != "advance" or a.action.mark != [local[0], local[1]]:
            a.action = Action(type="advance", ordered=True, mark=[local[0], local[1]], mark_kind="rally",
                              reason="前往後送傷員")
            a.action.mark_cell = gcell


def _medevac_step(sim, a: Squad, job: dict, log: list):
    """後送任務的一步：前往 → 接手（把走到身邊的傷員從原小隊移到友軍名下）→ 帶回據點 → 在據點休養。
    傷員分散時先接最近的、只接走到身邊的，先送回去，再回頭接剩下的（不拖著已接到的人往別處繞）。
    帶回途中遇敵：停下來打（拖的人照一般規則放下傷員、最多拖到掩護），打完回頭去扛、再繼續走。"""
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
        got = [m for m in hurt if _near(a, m)]
        if got:
            for m in got:
                src.members.remove(m)
                m.home_squad = src.id
                m.stabilized = True
                a.members.append(m)
                src.away += 1
            job.update(state="carrying", clock=0)
            a.ai.update(goal=scenario.BASE)
            _send(a, scenario.BASE)
            names = "、".join(m.name for m in got)
            rest = [m for m in hurt if m not in got]
            log.append(f"友軍 {a.callsign} 接到 {names}，送回 {scenario.BASE}")
            text = f"接到 {names}，送回 {scenario.BASE}，{_eta_text(sim, a.pos, scenario.BASE)}。"
            if rest:
                where = "、".join(f"{m.name}（{_where(m, src.pos)}）" for m in rest)
                text += f"{where} 離得遠，送完再回來接。"
            sim.ally_radio(a, text, squad_id=src.id)
            return
        target = min(hurt, key=lambda m: (_gap(a, m), m.name))
        _go_to(a, target, zmod.squad_cell(src) or src.pos)
        return
    if job["state"] == "carrying":
        patients = [m for m in a.members if m.home_squad == job["squad"]]
        if not patients:
            job["state"] = "done"
            return
        from engine.tactical import worldmap
        home = all(m.gpos and worldmap.cell_of(m.gpos[0], m.gpos[1]) == scenario.BASE for m in patients)             and a.pos == scenario.BASE
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
            log.append(f"友軍 {a.callsign} 把傷員送回 {scenario.BASE}，等原小隊回來整補時歸隊")
            names = [m.name for m in patients if not m.lost]
            rest = [m for m in src.members if m.name in job["names"] and m.down and not m.lost]                 if src is not None and src.status == "active" else []
            text = f"{'、'.join(names)} 已送回 {scenario.BASE} 休養，你們回來整補時歸隊。" if names else ""
            if rest:                     # 還有沒接到的：再跑一趟
                job.update(state="inbound", clock=0, since=w.tick, hot_said=False)
                a.ai.update(goal=src.pos)
                _go_to(a, min(rest, key=lambda m: (_gap(a, m), m.name)), zmod.squad_cell(src) or src.pos)
                text += f"回頭去接 {'、'.join(m.name for m in rest)}，{_eta_text(sim, a.pos, _where(rest[0], src.pos))}。"
            else:
                job["state"] = "done"
            if text:
                sim.ally_radio(a, text, squad_id=job["squad"])
            return
        if sim.in_engagement(a):
            # 不照行軍命令丟下傷員往前走：原地打，傷員交給一般戰鬥規則（急救、拖到掩護）
            if a.action.type == "move":
                a.action = Action(type="hold", reason="後送途中遇敵，放下傷員還擊")
            if not job.get("hot_said"):
                job["hot_said"] = True
                sim.ally_radio(a, "途中接敵，先放下傷員還擊，打完再走。", squad_id=job["squad"])
            return
        job["hot_said"] = False
        left = next((m for m in patients if not m.lost and m.gpos and not _near(a, m)), None)
        if left is not None:
            _go_to(a, left, a.pos)
            return
        if a.action.type != "move":
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
        if a is not None and a.status == "active" and not sim.in_engagement(a):
            job["clock"] = job.get("clock", 0) + 1       # 這一趟花了幾拍（交火中不算：打完會繼續）
        if a is None or a.status != "active" or job.get("clock", 0) >= MEDEVAC_TIMEOUT:
            job["state"] = "done"
            dropped = []
            if a is not None and a.status == "active":
                for m in [m for m in a.members if m.home_squad == job["squad"]]:
                    a.members.remove(m)
                    src = w.squads.get(job["squad"])
                    if src is not None and src.status == "active":
                        m.home_squad = None
                        src.members.append(m)
                        src.away = max(0, src.away - 1)
                        dropped.append(m)
            log.append("後送任務取消（友軍無法抵達）")
            if a is not None and a.status == "active":
                if dropped:
                    where = "、".join(f"{m.name}（{_where(m, a.pos)}）" for m in dropped)
                    sim.ally_radio(a, f"送不回 {scenario.BASE}，後送取消，{where} 交還給你們。", squad_id=job["squad"])
                else:
                    sim.ally_radio(a, "無法抵達，後送取消。", squad_id=job["squad"])
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
            # 指揮官請求的支援：那格沒在交戰也守到時限；自動支援：交戰結束就回家
            if (target is None and not in_fight and not st.get("requested")) or w.tick - st["since"] >= SUPPORT_TIMEOUT:
                _support_radio(sim, s, f"這邊結束，返回 {st['home']}。")
                st.update(task="return", goal=st["home"], since=w.tick, requested=None, **{"for": None})
                _send(s, st["home"])
                log.append(f"友軍 {s.callsign} 結束支援，返回 {scenario.cell_desc(st['home'])}")
                continue
            if target is not None and target.pos != st["goal"]:
                st["goal"] = target.pos
            if in_fight:
                if not st.get("hot_said"):
                    st["hot_said"] = True
                    _support_radio(sim, s, "接敵，開始壓制推進。")
                if s.action.type not in ("supp_advance", "advance", "flank", "assault") or s.action.ordered:
                    s.action = Action(type="supp_advance", ordered=False, auto=True, reason="支援友軍交戰")
            elif s.pos != st["goal"]:
                if s.action.type != "move" or s.action.target != st["goal"]:
                    _send(s, st["goal"])
            else:
                if not st.get("arrived"):
                    st["arrived"] = True
                    _support_radio(sim, s, f"已到 {st['goal']}，就位警戒。")
                if s.action.type == "move":
                    s.action = Action(type="hold")
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
            st.update(task="support", goal=p.pos, since=w.tick, requested=None, arrived=False, hot_said=False,
                      **{"for": p.id})
            busy.add(p.pos)
            sim.ally_radio(s, f"聽到你們那邊交火，{len(s.alive_members())} 人從 {s.pos} 過去支援，"
                              f"{_eta_text(sim, s.pos, p.pos)}。", squad_id=p.id)
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
