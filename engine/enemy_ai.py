"""規則式敵方指揮。敵方只依自己的認知（world.enemy_intel）行動，不讀取我方真相。"""
from __future__ import annotations

import random

from engine import scenario
from engine.models import Enemy, World

STRENGTH_CAP = 22
THINK_EVERY = 4
STEP_TICKS = 4
SPOT_ADJ_CHANCE = 0.25
INTEL_TTL = 30
BASE_REGEN_EVERY = 30
MOBILE_SIZE = 3
HARASS_INTERVAL = (60, 110)
HARASS_SIZE = 2
REINFORCE_RANGE = 4


def alive(world: World) -> list[Enemy]:
    return [e for e in world.enemies.values() if e.alive]


def total_strength(world: World) -> int:
    return sum(e.strength for e in alive(world))


def can_spawn(world: World, strength: int) -> bool:
    return total_strength(world) + strength <= STRENGTH_CAP


def nearest_base(cell: str) -> str | None:
    if not scenario.ENEMY_BASES:
        return None
    return min(scenario.ENEMY_BASES, key=lambda b: scenario.distance(cell, b))


def send(e: Enemy, order: str, goal: str):
    path = scenario.find_path(e.pos, goal) if e.pos != goal else []
    e.order = order
    e.goal = goal
    e.path = path or []
    e.step_cd = STEP_TICKS


def withdraw(world: World, e: Enemy):
    base = nearest_base(e.pos)
    if base:
        e.tag = "mobile"
        send(e, "withdraw", base)


# ---------------------------------------------------------------------- perception


def observe(world: World, rng: random.Random, squads: list) -> None:
    """敵軍偵測我方：同格必定看到、相鄰格有機率看到。"""
    now = world.tick
    for e in alive(world):
        for s in squads:
            if s.status != "active":
                continue
            seen = s.pos == e.pos or (s.pos in scenario.adjacent(e.pos) and rng.random() < SPOT_ADJ_CHANCE)
            if seen:
                world.enemy_intel[s.pos] = {"tick": now, "est": len(s.alive_members())}
    for cell in list(world.enemy_intel):
        if now - world.enemy_intel[cell]["tick"] > INTEL_TTL:
            del world.enemy_intel[cell]


# ---------------------------------------------------------------------- movement


def move(world: World) -> list[tuple[Enemy, str, str]]:
    """推進所有敵軍。回傳 [(enemy, from, to)]。"""
    moves = []
    for e in alive(world):
        if e.engaged_with:
            continue
        if e.order and e.path:
            e.step_cd -= 1
            if e.step_cd > 0:
                continue
            origin = e.pos
            e.pos = e.path.pop(0)
            e.step_cd = STEP_TICKS
            moves.append((e, origin, e.pos))
            if not e.path:
                _arrive(world, e)
            continue
        if e.order and not e.path:
            _arrive(world, e)
            continue
        if e.tag == "patrol" and len(e.route) > 1:
            e.cooldown -= 1
            if e.cooldown <= 0:
                origin = e.pos
                e.route_idx = (e.route_idx + 1) % len(e.route)
                e.pos = e.route[e.route_idx]
                e.cooldown = e.move_every
                moves.append((e, origin, e.pos))
    return moves


def _arrive(world: World, e: Enemy):
    order, e.order, e.goal = e.order, None, None
    if order == "withdraw":
        others = [o for o in alive(world) if o is not e and o.pos == e.pos and o.tag == "garrison"]
        if others:
            _merge(e, others[0])
            return
        e.tag = "garrison"
        e.home = e.pos
        e.route = [e.pos]
    elif order in ("reinforce", "counter"):
        e.tag = "garrison"
        e.home = e.pos
        e.route = [e.pos]
    elif order in ("hunt", "harass"):
        e.tag = "mobile"


# ---------------------------------------------------------------------- command


def think(world: World, rng: random.Random, objectives: list[str]) -> list[str]:
    """敵方指揮決策。objectives＝我方正在爭奪的格子（敵方從情報／常識得知的任務地點）。回傳決策紀錄。"""
    log = []
    now = world.tick
    if now < world.enemy_next_think:
        return log
    world.enemy_next_think = now + THINK_EVERY
    units = alive(world)

    for e in units:
        if e.strength <= 1 and e.order != "withdraw" and not e.engaged_with and e.pos not in scenario.ENEMY_BASES:
            withdraw(world, e)
            log.append(f"{e.id} 兵力不足，撤回 {e.goal}")

    engaged_cells = {e.pos for e in units if e.engaged_with}
    for cell in engaged_cells:
        local = sum(e.strength for e in units if e.pos == cell)
        threat = world.enemy_intel.get(cell, {}).get("est", 3)
        if local >= threat:
            continue
        helper = _free_unit(units, near=cell, exclude_bases=False)
        if helper and scenario.distance(helper.pos, cell) <= REINFORCE_RANGE:
            send(helper, "reinforce", cell)
            log.append(f"{helper.id} 增援 {cell}")

    for cell in objectives:
        if cell not in world.enemy_intel:
            continue
        if any(e.pos == cell or e.goal == cell for e in units):
            continue
        unit = _free_unit(units, near=cell, exclude_bases=False, min_strength=2)
        if unit:
            send(unit, "counter", cell)
            log.append(f"{unit.id} 反攻 {cell}")

    sightings = sorted(world.enemy_intel.items(), key=lambda kv: -kv[1]["tick"])
    if sightings:
        target = sightings[0][0]
        if not any(e.goal == target for e in units):
            hunter = next((e for e in units if e.tag == "mobile" and not e.order and not e.engaged_with), None)
            if hunter:
                send(hunter, "hunt", target)
                log.append(f"{hunter.id} 搜索 {target}")

    if now >= world.enemy_next_harass:
        if world.enemy_next_harass:
            log += _harass(world, rng, units)
        world.enemy_next_harass = now + rng.randint(*HARASS_INTERVAL)

    if now % BASE_REGEN_EVERY == 0:
        for base in scenario.ENEMY_BASES:
            g = next((e for e in units if e.pos == base and e.tag == "garrison"), None)
            if g and g.strength < max(3, g.max_strength) and can_spawn(world, 1):
                g.strength += 1
            elif g is None and can_spawn(world, 2):
                eid = world.next_id("g")
                world.enemies[eid] = Enemy(id=eid, pos=base, strength=2, route=[base], move_every=999,
                                           cooldown=999, tag="garrison", home=base, max_strength=3)
                log.append(f"{eid} 在 {base} 重建守備")
        _form_mobile(world, units, log)
    return log


def _free_unit(units: list[Enemy], near: str, exclude_bases: bool, min_strength: int = 1) -> Enemy | None:
    pool = []
    for e in units:
        if e.engaged_with or e.order or e.strength < min_strength:
            continue
        if e.tag == "garrison":
            if e.pos in scenario.ENEMY_BASES:
                if exclude_bases or e.strength <= 2:
                    continue
            else:
                continue
        pool.append(e)
    if not pool:
        return None
    return min(pool, key=lambda e: scenario.distance(e.pos, near))


def _merge(src: Enemy, dst: Enemy):
    """撤回合併：保留個體（身分、傷勢）。"""
    from engine.roster import transfer
    transfer(src, dst, len(src.soldiers))
    dst.strength += src.strength
    dst.tcell = dst.pos
    src.alive = False
    src.strength = 0
    src.soldiers = []


def _split(world: World, src: Enemy, n: int, prefix: str, tag: str) -> Enemy:
    """從 src 分出 n 人的新單位：個體從 src 移交（同格，位置保留）。"""
    eid = world.next_id(prefix)
    new = Enemy(id=eid, pos=src.pos, strength=n, route=[src.pos], move_every=999, cooldown=999,
                tag=tag, home=src.pos, max_strength=n)
    moved = src.soldiers[-n:] if n > 0 else []
    src.soldiers = src.soldiers[:len(src.soldiers) - len(moved)]
    new.soldiers = moved
    new.tcell = src.tcell
    src.strength -= n
    world.enemies[eid] = new
    return new


def _form_mobile(world: World, units: list[Enemy], log: list[str]):
    for base in scenario.ENEMY_BASES:
        g = next((e for e in units if e.pos == base and e.tag == "garrison" and not e.engaged_with), None)
        if g and g.strength >= MOBILE_SIZE + 2:
            new = _split(world, g, MOBILE_SIZE, "m", "mobile")
            log.append(f"{base} 編成機動隊 {new.id}")


def _harass(world: World, rng: random.Random, units: list[Enemy]) -> list[str]:
    targets = [c for c in scenario.CELLS if c[0] == scenario.ROWS[0] and c != scenario.BASE]
    if not targets or not can_spawn(world, 0):
        return []
    target = rng.choice(targets)
    src = next((e for e in units if e.tag == "garrison" and e.pos in scenario.ENEMY_BASES
                and e.strength >= HARASS_SIZE + 2 and not e.engaged_with), None)
    if src is None:
        return []
    raider = _split(world, src, HARASS_SIZE, "h", "mobile")
    send(raider, "harass", target)
    return [f"{raider.id} 出發騷擾後方 {target}"]
