"""預備兵池（C 模式）：只存數字的白紙兵，補員與增援從這裡扣，扣完就沒了。

池放在 World.reserve[陣營] 底下（目前只有 "ally"），將來要擴成多指揮官／戰役兵源不必改格式。
"""
from __future__ import annotations

import random

from engine import persona
from engine.models import Member, World, refill_loadout

POOL_SIZE = 15
FACTION = "ally"
NAME_LETTERS = "BCDFGHJKLMNPRSTVWXZ"


def pool(world: World, faction: str = FACTION) -> list[dict]:
    return world.reserve.setdefault(faction, [])


def taken_names(world: World) -> set[str]:
    names = {m.name for s in world.squads.values() for m in s.members}
    for p in world.reserve.values():
        names |= {r["name"] for r in p}
    return names


def new_name(world: World, rng: random.Random, extra: set[str] | None = None) -> str:
    used = taken_names(world) | (extra or set())
    for _ in range(500):
        name = f"{rng.choice(NAME_LETTERS)}-{rng.randint(10, 99)}"
        if name not in used:
            return name
    n = 100
    while f"N-{n}" in used:
        n += 1
    return f"N-{n}"


def make_blank(world: World, rng: random.Random, persona_value: float = persona.ROOKIE_PERSONA,
               bias: dict | None = None, extra_names: set[str] | None = None) -> dict:
    return {"name": new_name(world, rng, extra_names), **persona.blank(rng, persona_value, bias)}


def fill(world: World, rng: random.Random, faction: str = FACTION, size: int = POOL_SIZE):
    p = pool(world, faction)
    names: set[str] = set()
    while len(p) < size:
        r = make_blank(world, rng, extra_names=names)
        names.add(r["name"])
        p.append(r)


def remaining(world: World, faction: str = FACTION) -> int:
    return len(pool(world, faction))


def to_member(entry: dict, weapon: str, joined: int, hp: int = 100, trait: str = "") -> Member:
    m = Member(name=entry["name"], hp=hp, joined=joined, weapon=weapon, trait=trait,
               prof=dict(entry.get("prof") or {"rifle": 40}))
    m.traits = dict(entry.get("traits") or persona.default_traits())
    m.innate = dict(entry.get("innate") or m.traits)
    m.persona = float(entry.get("persona", persona.ROOKIE_PERSONA))
    for k in ("personality", "speech", "background", "sample_lines", "voice_level", "led_before", "battles"):
        if entry.get(k):
            setattr(m, k, entry[k])
    refill_loadout(m)
    return m


def from_member(m: Member) -> dict:
    """人退回預備兵池（滿編時的遊蕩者／殘部）：保留性格與經歷。"""
    return {"name": m.name, "traits": dict(m.traits), "innate": dict(m.innate), "persona": m.persona,
            "personality": m.personality, "speech": m.speech, "background": m.background,
            "sample_lines": list(m.sample_lines), "voice_level": m.voice_level, "led_before": m.led_before,
            "battles": m.battles, "prof": dict(m.prof)}


def draw(world: World, faction: str = FACTION) -> dict | None:
    p = pool(world, faction)
    return p.pop(0) if p else None


def give_back(world: World, m: Member, faction: str = FACTION) -> bool:
    p = pool(world, faction)
    if len(p) >= POOL_SIZE:
        return False
    p.append(from_member(m))
    return True
