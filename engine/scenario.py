from __future__ import annotations

import json
import os

from engine.models import Enemy, Member, Squad, World

SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")
DEFAULT_SCENARIO = "urban"


def _load(name: str) -> dict:
    with open(os.path.join(SCENARIO_DIR, f"{name}.json"), encoding="utf8") as f:
        return json.load(f)


DATA = _load(DEFAULT_SCENARIO)
SCENARIO_ID = DATA["id"]
SCENARIO_NAME = DATA["name"]
ROWS = DATA["rows"]
COLS = DATA["cols"]
CELLS: dict[str, dict] = DATA["cells"]
BASE = DATA["base"]
START = BASE
ENEMY_BASES: list[str] = list(DATA.get("enemy_bases", []))
MAP_NOTE = DATA["map_note"]
MISSION_CELLS: dict[str, str] = DATA.get("missions", {})
PATROL_ROUTE: list[str] = list(DATA["patrols"][0]["route"]) if DATA.get("patrols") else []


def subcells(cell: str) -> dict:
    return CELLS.get(cell, {}).get("subcells", {})


def adjacent(cell: str) -> list[str]:
    r, c = ROWS.index(cell[0]), COLS.index(cell[1])
    out = []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < len(ROWS) and 0 <= nc < len(COLS):
            out.append(ROWS[nr] + COLS[nc])
    return out


def distance(a: str, b: str) -> int:
    return abs(ROWS.index(a[0]) - ROWS.index(b[0])) + abs(COLS.index(a[1]) - COLS.index(b[1]))


def find_path(start: str, goal: str, avoid: set[str] | None = None) -> list[str] | None:
    if start == goal or goal not in CELLS:
        return None
    avoid = (avoid or set()) - {goal}

    def bfs(blocked: set[str]) -> list[str] | None:
        prev = {start: None}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            if cur == goal:
                path = []
                while cur != start:
                    path.append(cur)
                    cur = prev[cur]
                return path[::-1]
            for nxt in adjacent(cur):
                if nxt not in prev and nxt not in blocked:
                    prev[nxt] = cur
                    queue.append(nxt)
        return None

    return bfs(avoid) or bfs(set())


def find_route(start: str, goal: str, via: list[str] | None = None,
               avoid: set[str] | None = None) -> list[str] | None:
    points = [c for c in (via or []) if c in CELLS and c not in (start, goal)] + [goal]
    route: list[str] = []
    cur = start
    for point in points:
        if point == cur:
            continue
        seg = find_path(cur, point, avoid)
        if seg is None:
            return None
        route += seg
        cur = point
    return route or None


def cell_desc(cell: str) -> str:
    info = CELLS.get(cell)
    return f"{cell} {info['name']}" if info else cell


def cover_label(cover: float) -> str:
    if cover >= 0.4:
        return "高"
    if cover >= 0.2:
        return "中"
    return "低"


def make_enemies() -> dict[str, Enemy]:
    enemies = {}
    n = 0
    for p in DATA.get("patrols", []):
        n += 1
        eid = f"p{n}"
        enemies[eid] = Enemy(id=eid, pos=p["pos"], strength=p["strength"], route=list(p["route"]),
                             move_every=p.get("move_every", 8), cooldown=p.get("move_every", 8), tag="patrol")
    for g in DATA.get("garrisons", []):
        n += 1
        eid = f"g{n}"
        enemies[eid] = Enemy(id=eid, pos=g["pos"], strength=g["strength"], route=[g["pos"]],
                             move_every=999, cooldown=999, tag="garrison", home=g["pos"])
    return enemies


def _member(entry) -> Member:
    if isinstance(entry, dict):
        m = Member(name=entry["name"], trait=entry.get("trait", ""))
        if entry.get("weapon"):
            m.weapon = entry["weapon"]
        if entry.get("prof"):
            m.prof = dict(entry["prof"])
        return m
    return Member(name=str(entry))


def squad_from_card(card: dict) -> Squad:
    from engine.obedience import normalize_tendencies
    from engine.roster import assign_weapons
    leader = card["leader"]
    lead = Member(leader["name"], trait="小隊長")
    if leader.get("weapon"):
        lead.weapon = leader["weapon"]
    members = [lead] + [_member(n) for n in card.get("members", [])]
    fixed = {leader["name"]} if leader.get("weapon") else set()
    fixed |= {n["name"] for n in card.get("members", []) if isinstance(n, dict) and n.get("weapon")}
    start = str(card.get("start", START)).upper()
    beliefs = [{"text": b, "origin": "protocol", "t": 0} for b in card.get("initial_beliefs", [])]
    beliefs += [{"text": b, "origin": "experience", "t": 0} for b in card.get("experience_beliefs", [])]
    squad = Squad(
        id=card["id"],
        callsign=card.get("callsign", card["id"]),
        leader=leader,
        members=members,
        pos=start if start in CELLS else START,
        max_size=card.get("max_size", max(4, len(members))),
        morale=card.get("morale", 80),
        memory=list(card.get("memory", [])),
        long_memory=card.get("long_memory", ""),
        beliefs=beliefs,
        tendencies=normalize_tendencies(card.get("tendencies", {})),
    )
    assign_weapons(squad, fixed)
    return squad


def new_world() -> World:
    from engine.roster import sync_world
    w = World(scenario=SCENARIO_ID)
    w.enemies = make_enemies()
    sync_world(w)
    return w
