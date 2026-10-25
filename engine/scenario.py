from __future__ import annotations

import json
import os
import re

from engine import cells
from engine.models import Enemy, Member, Squad, World

SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")
DEFAULT_SCENARIO = "urban"


def _load(name: str) -> dict:
    with open(os.path.join(SCENARIO_DIR, f"{name}.json"), encoding="utf8") as f:
        return json.load(f)


DATA = _load(DEFAULT_SCENARIO)
SCENARIO_ID = DATA["id"]
SCENARIO_NAME = DATA["name"]
N_ROWS = len(DATA["rows"])                 # 場景檔的 rows／cols 只用來決定大小，名稱一律由 engine/cells.py 產生
N_COLS = len(DATA["cols"])
ROWS: list[str] = [cells.row_label(r) for r in range(N_ROWS)]   # 由南到北
COLS: list[str] = [str(c + 1) for c in range(N_COLS)]           # 由西到東
CELLS: dict[str, dict] = DATA["cells"]
BASE = DATA["base"]
START = BASE
ENEMY_BASES: list[str] = list(DATA.get("enemy_bases", []))
MAP_NOTE = DATA["map_note"]
MISSION_CELLS: dict[str, str] = DATA.get("missions", {})
PATROL_ROUTE: list[str] = list(DATA["patrols"][0]["route"]) if DATA.get("patrols") else []


def subcells(cell: str) -> dict:
    return CELLS.get(cell, {}).get("subcells", {})


def cell_at(r: int, c: int) -> str | None:
    """（列, 欄）→ 格子 ID；在地圖外回傳 None。"""
    if 0 <= r < N_ROWS and 0 <= c < N_COLS:
        return cells.label(r, c)
    return None


def in_bounds(cell: str) -> bool:
    rc = cells.try_parse(cell)
    return rc is not None and 0 <= rc[0] < N_ROWS and 0 <= rc[1] < N_COLS


def adjacent(cell: str) -> list[str]:
    r, c = cells.parse(cell)
    out = []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        n = cell_at(r + dr, c + dc)
        if n is not None:
            out.append(n)
    return out


def distance(a: str, b: str) -> int:
    return cells.manhattan(a, b)


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
    """給人與隊長看的地點：地名為主、格子代號備用，例如「通訊中繼塔（D3）」。"""
    info = CELLS.get(cell)
    return f"{info['name']}（{cell}）" if info else cell


def places_text(center: str | None = None, radius: int | None = None) -> str:
    """隊長簡報用的地點清單：「地名（代號）」，由北到南、由西到東。給了 center、radius 就只列那個範圍內的
    （切比雪夫距離，大地圖用；現在 5×5 全部列出）。"""
    out = []
    for r in reversed(range(N_ROWS)):
        for c in range(N_COLS):
            cell = cells.label(r, c)
            if cell not in CELLS:
                continue
            if center is not None and radius is not None and cells.chebyshev(center, cell) > radius:
                continue
            out.append(cell_desc(cell))
    return "、".join(out)


def resolve_place(text) -> str | None:
    """隊長或指揮官說的地點 → 格子 ID。接受格子代號（D3）、地名（通訊中繼塔、只說中繼塔也行，要唯一）、
    或兩者混寫（「通訊中繼塔（D3）」，以代號為準）；認不出來回傳 None。"""
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    up = s.upper()
    if up in CELLS:
        return up
    for m in re.finditer(r"[A-Z]+\d+", up):
        if m.group() in CELLS:
            return m.group()
    exact = sorted(c for c, info in CELLS.items() if info.get("name") == s)
    if exact:
        return exact[0]
    hits = sorted(c for c, info in CELLS.items()
                  if info.get("name") and (info["name"] in s or (len(s) >= 2 and s in info["name"])))
    return hits[0] if len(hits) == 1 else None


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


PERSONA_KEYS = ("personality", "speech", "background", "sample_lines")


def _member(entry) -> Member:
    """角色卡隊員：字串（白紙）或 dict（可帶 trait／weapon／prof／traits／persona／個性文字）。"""
    from engine import persona
    if isinstance(entry, dict):
        m = Member(name=entry["name"], trait=entry.get("trait", ""))
        if entry.get("weapon"):
            m.weapon = entry["weapon"]
        if entry.get("prof"):
            m.prof = dict(entry["prof"])
        if isinstance(entry.get("traits"), dict):
            m.traits = {k: persona.clamp(float(entry["traits"].get(k, 0.0))) for k in persona.TRAITS}
        elif m.trait:
            m.traits = persona.from_card_text(m.trait)
        m.innate = dict(m.traits)
        if entry.get("persona") is not None:
            m.persona = persona.clamp(float(entry["persona"]), 0.0, 1.0)
        for k in PERSONA_KEYS:
            if entry.get(k):
                setattr(m, k, entry[k])
        return m
    return Member(name=str(entry))


def leader_member(leader: dict) -> Member:
    """角色卡隊長：完整人格（說話人格就是他自己的）。"""
    from engine import persona
    lead = Member(leader["name"], trait="小隊長")
    for k in PERSONA_KEYS:
        if leader.get(k):
            setattr(lead, k, leader[k])
    if isinstance(leader.get("traits"), dict):
        lead.traits = {k: persona.clamp(float(leader["traits"].get(k, 0.0))) for k in persona.TRAITS}
    else:
        lead.traits = persona.from_card_text(leader.get("personality", ""))
    lead.innate = dict(lead.traits)
    lead.persona = persona.clamp(float(leader.get("persona", 0.5)), 0.0, 1.0)
    lead.voice_level = lead.persona
    lead.led_before = True
    return lead


def squad_from_card(card: dict) -> Squad:
    from engine.obedience import normalize_tendencies
    from engine.roster import assign_weapons
    leader = card["leader"]
    lead = leader_member(leader)
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
    lead.beliefs = [dict(b) for b in beliefs]
    lead.tendencies = dict(squad.tendencies)
    assign_weapons(squad, fixed)
    return squad


def new_world() -> World:
    import random
    import zlib
    from engine import reserve
    from engine.roster import sync_world
    w = World(scenario=SCENARIO_ID)
    w.enemies = make_enemies()
    reserve.fill(w, random.Random(zlib.crc32(f"{SCENARIO_ID}:reserve".encode())))
    sync_world(w)
    return w
