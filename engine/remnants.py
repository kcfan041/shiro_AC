"""失聯友軍殘部（綠方）。

- 由友方 AI 帶出的白紙兵，斷線後殘存原地抵抗：斷線前沒有小隊長；斷線時隨機（偏人格值高、沉著者）選一人為臨時隊長。
- 戰術層是綠方（與我方同陣營不互射，情報、士氣、AI 各自獨立），躲在該格門最多、最大的建築內固守。
- 對峙：任務開始後 STANDOFF tick 內雙方不交戰（敵軍在建築外 6～10 格集結）；我方小隊進入相鄰格或同一格、
  或時限到 → 開打，圍攻方直接強攻／破門。
- 結局：我方小隊在該格且該格沒有活著的敵軍 → 救援成功；全員投降 → 投降；全員倒地／陣亡 → 被殲滅。
- 救回：≥2 人且小隊數未達上限 → 自成新小隊（臨時隊長的白紙人格此時由 LLM 寫好，給新呼號）；
  否則併入最先抵達的那支小隊，滿編多出的人回預備兵池（池滿就沒了）。
"""
from __future__ import annotations

import random

from engine import persona, reserve
from engine.models import Remnant, World
from engine.tactical.units import WEAPONS

SCATTER_BIAS = {"caut": 0.2, "comp": 0.1}   # 被打散的經歷
RADIO_GAP = 3
STANDOFF = 20                                # 對峙（敵軍集結）tick 數


def spawn(world: World, rng: random.Random, cell: str, commander: str | None, mission: str | None,
          n: int | None = None) -> Remnant:
    rid = world.next_id("r")
    n = n or rng.randint(2, 3)
    names: set[str] = set()
    members = []
    from engine.roster import balanced_weapons
    weapons = balanced_weapons(n)
    rng.shuffle(weapons)
    for i in range(n):
        entry = reserve.make_blank(world, rng, persona_value=round(rng.uniform(0.2, 0.4), 2), bias=SCATTER_BIAS,
                                   extra_names=names)
        names.add(entry["name"])
        m = reserve.to_member(entry, weapons[i], world.tick)
        m.ammo = int(WEAPONS[m.weapon]["ammo"] * rng.uniform(0.3, 0.6))
        m.grenades, m.smokes, m.flashes = rng.choice(((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0)))
        if rng.random() < 0.5:
            m.damage = {rng.choice(("larm", "rarm", "lleg", "rleg")): 1}
            m.hp = 70
        members.append(m)
    weights = [max(0.05, m.persona + 0.5 * m.traits.get("comp", 0.0) + 0.5) for m in members]
    leader = rng.choices(members, weights=weights)[0]
    leader.persona = round(max(leader.persona, 0.35), 2)
    persona.nudge(leader, "comp", 0.1)
    persona.nudge(leader, "caut", 0.1)
    leader.led_before = True
    r = Remnant(id=rid, pos=cell, commander=commander, mission=mission, members=members, leader=leader.name,
                standoff_until=world.tick + STANDOFF)
    from engine.tactical import worldmap
    m = worldmap.tmap(cell)
    room = worldmap.best_room(m)
    if room is not None:
        r.room = room["id"]
        worldmap.place_in_room(world, cell, members, room)
        r.tcell = cell
        from engine.roster import new_enemy_soldiers
        for e in world.enemies.values():
            if e.alive and e.pos == cell and e.tag == "siege":
                if len(e.soldiers) < e.strength:
                    e.soldiers.extend(new_enemy_soldiers(world, e, e.strength - len(e.soldiers)))
                worldmap.place_besiegers(world, cell, e.soldiers, room)
                e.tcell = cell
    world.remnants[rid] = r
    return r


def in_standoff(world: World, r: Remnant) -> bool:
    return r.status == "holding" and r.standoff_until is not None and world.tick < r.standoff_until


def check_standoff(world: World, r: Remnant) -> str | None:
    """對峙中：我方任一小隊進入相鄰格或同一格 → 「援軍接近」；時限到 → 「敵軍發起攻擊」。回傳開打原因。"""
    from engine import scenario
    if r.status != "holding" or r.standoff_until is None:
        return None
    near = {r.pos, *scenario.adjacent(r.pos)}
    if any(s.status == "active" and s.pos in near for s in world.squads.values()):
        reason = "援軍接近"
    elif world.tick >= r.standoff_until:
        reason = "敵軍發起攻擊"
    else:
        return None
    r.standoff_until = None
    r.engaged_reason = reason
    return reason


def leader_of(r: Remnant):
    lead = next((m for m in r.members if m.name == r.leader and m.alive), None)
    if lead is None:
        alive = r.alive_members()
        if alive:
            lead = max(alive, key=lambda m: (m.led_before, m.persona, m.traits.get("comp", 0.0)))
            r.leader = lead.name
            lead.led_before = True
    return lead


def outcome(world: World, r: Remnant) -> str | None:
    """回傳新結局（rescued／surrendered／destroyed）或 None。"""
    if r.status != "holding":
        return None
    standing = [m for m in r.members if not m.lost and not m.down]
    if not standing:
        surrendered = any(f.startswith("surrendered:") for f in r.radio_flags)
        return "surrendered" if surrendered else "destroyed"
    foes = any(e.alive and e.pos == r.pos for e in world.enemies.values())
    ours = any(s.status == "active" and s.pos == r.pos for s in world.squads.values())
    if ours and not foes:
        return "rescued"
    return None


def survivors(r: Remnant) -> list:
    return [m for m in r.members if not m.lost]
