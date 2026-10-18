from __future__ import annotations

from engine.models import Enemy, Member, Squad, World

SCENARIO_NAME = "廢棄城區"
OBJECTIVE = "偵察 C2 通訊中繼塔，確認敵軍動向並回報。"
START = "A1"
ROWS = "ABC"
COLS = "123"

CELLS = {
    "A1": {"name": "南側集結點", "terrain": "瓦礫空地", "cover": 0.1},
    "A2": {"name": "倒塌公寓", "terrain": "建築廢墟", "cover": 0.4},
    "A3": {"name": "東側高架橋下", "terrain": "高架道路", "cover": 0.3},
    "B1": {"name": "地下鐵入口", "terrain": "地鐵站", "cover": 0.5},
    "B2": {"name": "中央十字路口", "terrain": "開闊路口", "cover": 0.05},
    "B3": {"name": "燒毀的商場", "terrain": "大型建築", "cover": 0.4},
    "C1": {"name": "北側公園", "terrain": "枯樹林", "cover": 0.25},
    "C2": {"name": "通訊中繼塔", "terrain": "高塔基座", "cover": 0.2},
    "C3": {"name": "工廠區", "terrain": "工業廠房", "cover": 0.45},
}

MAP_NOTE = "A 列在南（靠近我方後方），C 列在北；1 行在西，3 行在東。移動時沿上下左右相鄰格逐格前進，可指定任一格為目的地。"


def adjacent(cell: str) -> list[str]:
    r, c = ROWS.index(cell[0]), COLS.index(cell[1])
    out = []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < len(ROWS) and 0 <= nc < len(COLS):
            out.append(ROWS[nr] + COLS[nc])
    return out


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
    info = CELLS[cell]
    return f"{cell} {info['name']}"


def cover_label(cover: float) -> str:
    if cover >= 0.4:
        return "高"
    if cover >= 0.2:
        return "中"
    return "低"


BASE = START
PATROL_ROUTE = ["B3", "B2", "C2", "C3"]


def make_enemies() -> dict[str, Enemy]:
    return {
        "e1": Enemy(id="e1", pos="B3", strength=3, route=PATROL_ROUTE, move_every=8, cooldown=8, tag="patrol"),
    }


def _member(entry) -> Member:
    if isinstance(entry, dict):
        return Member(name=entry["name"], trait=entry.get("trait", ""))
    return Member(name=str(entry))


def squad_from_card(card: dict) -> Squad:
    from engine.obedience import normalize_tendencies
    leader = card["leader"]
    members = [Member(leader["name"], trait="小隊長")] + [_member(n) for n in card.get("members", [])]
    start = str(card.get("start", START)).upper()
    beliefs = [{"text": b, "origin": "protocol", "t": 0} for b in card.get("initial_beliefs", [])]
    beliefs += [{"text": b, "origin": "experience", "t": 0} for b in card.get("experience_beliefs", [])]
    return Squad(
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


def new_world() -> World:
    w = World(scenario=SCENARIO_NAME)
    w.enemies = make_enemies()
    return w
