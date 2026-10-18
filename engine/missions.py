from __future__ import annotations

import random

from engine import scenario

MAIN_LINE = [
    {
        "key": "m1_recon",
        "title": "偵察中繼塔",
        "brief": "派小隊抵達 C2 通訊中繼塔，確認周邊敵情。",
        "type": "reach",
        "cell": "C2",
        "time_limit": 90,
    },
    {
        "key": "m2_hold",
        "title": "奪取並守住中繼塔",
        "brief": "肅清 C2 的敵軍，並由至少一支小隊在 C2 守住一段時間。",
        "type": "hold",
        "cell": "C2",
        "hold_ticks": 18,
        "time_limit": 120,
        "spawn": [{"pos": "C2", "strength": 3, "route": ["C2"], "move_every": 999}],
    },
    {
        "key": "m3_clear",
        "title": "清除工廠區",
        "brief": "C3 工廠區有敵軍據點，將其清除。",
        "type": "clear",
        "cell": "C3",
        "time_limit": 150,
        "spawn": [{"pos": "C3", "strength": 4, "route": ["C3"], "move_every": 999}],
    },
    {
        "key": "m4_extract",
        "title": "撤離",
        "brief": "所有存活小隊撤回 A1 南側集結點。",
        "type": "extract",
        "cell": "A1",
        "time_limit": 90,
    },
]

SIDE_TEMPLATES = ["rescue", "intercept", "crate", "signal"]


def _side(kind: str, cell: str, rng: random.Random) -> dict:
    name = scenario.cell_desc(cell)
    if kind == "rescue":
        return {"key": f"s_rescue_{cell}", "title": "營救友軍殘部", "type": "rescue", "cell": cell,
                "brief": f"{name} 收到微弱友軍訊號，可能是失聯的殘部。派小隊前往接應。", "time_limit": 80}
    if kind == "intercept":
        return {"key": f"s_intercept_{cell}", "title": "攔截敵方巡邏", "type": "clear", "cell": cell,
                "brief": f"敵方巡邏隊正經過 {name}，攔截並擊潰它。", "time_limit": 60,
                "spawn": [{"pos": cell, "strength": rng.randint(2, 3), "route": [cell], "move_every": 999}]}
    if kind == "crate":
        return {"key": f"s_crate_{cell}", "title": "回收補給箱", "type": "crate", "cell": cell,
                "brief": f"一箱補給空投落在 {name}，派小隊回收。", "time_limit": 80}
    return {"key": f"s_signal_{cell}", "title": "偵察可疑訊號", "type": "reach", "cell": cell,
            "brief": f"{name} 出現不明訊號源，派小隊前往查明。", "time_limit": 70}


def roll_side(rng: random.Random, exclude_cells: set[str]) -> dict:
    kind = rng.choice(SIDE_TEMPLATES)
    cells = [c for c in scenario.CELLS if c != scenario.START and c not in exclude_cells]
    cell = rng.choice(cells or [c for c in scenario.CELLS if c != scenario.START])
    return _side(kind, cell, rng)


def describe(m: dict, now: int) -> str:
    left = max(0, m["deadline"] - now)
    extra = ""
    if m["type"] == "hold":
        extra = f"，已守住 {m.get('progress', 0)}/{m['hold_ticks']}"
    return f"{'主線' if m['line'] == 'main' else '支線'}｜{m['title']}：{m['brief']}（剩約 {left} tick{extra}）"
