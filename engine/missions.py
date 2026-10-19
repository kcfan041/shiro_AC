from __future__ import annotations

import random

from engine import scenario

_M = scenario.MISSION_CELLS
_RECON = _M.get("recon", scenario.BASE)
_HOLD = _M.get("hold", _RECON)
_CLEAR = _M.get("clear", _HOLD)
_EXTRACT = _M.get("extract", scenario.BASE)

MAIN_LINE = [
    {
        "key": "m1_recon",
        "title": "偵察中繼塔",
        "brief": f"派小隊抵達 {scenario.cell_desc(_RECON)}，確認周邊敵情。",
        "type": "reach",
        "cell": _RECON,
        "time_limit": 120,
    },
    {
        "key": "m2_hold",
        "title": "奪取並守住中繼塔",
        "brief": f"肅清 {scenario.cell_desc(_HOLD)} 的敵軍，並由至少一支小隊守住一段時間。敵方可能反攻。",
        "type": "hold",
        "cell": _HOLD,
        "hold_ticks": 18,
        "time_limit": 150,
        "spawn": [{"pos": _HOLD, "strength": 3, "route": [_HOLD], "move_every": 999}],
    },
    {
        "key": "m3_clear",
        "title": "攻擊敵方據點",
        "brief": f"{scenario.cell_desc(_CLEAR)} 是敵方據點，將其守軍清除。",
        "type": "clear",
        "cell": _CLEAR,
        "time_limit": 200,
        "clear_all": True,
    },
    {
        "key": "m4_extract",
        "title": "撤離",
        "brief": f"所有存活小隊撤回 {scenario.cell_desc(_EXTRACT)}。",
        "type": "extract",
        "cell": _EXTRACT,
        "time_limit": 120,
    },
]

SIDE_TEMPLATES = ["rescue", "intercept", "crate", "signal"]


def _side(kind: str, cell: str, rng: random.Random) -> dict:
    name = scenario.cell_desc(cell)
    if kind == "rescue":
        return {"key": f"s_rescue_{cell}", "title": "營救友軍殘部", "type": "remnant", "cell": cell,
                "brief": f"{name} 有一支失聯的友軍殘部被敵軍圍困，正在固守。清除那裡的敵軍、接應他們。",
                "time_limit": None,
                "spawn": [{"pos": cell, "strength": rng.randint(3, 4), "route": [cell], "move_every": 999}]}
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
    blocked = {scenario.BASE, *scenario.ENEMY_BASES}
    cells = [c for c in scenario.CELLS if c not in blocked and c not in exclude_cells]
    cell = rng.choice(cells or [c for c in scenario.CELLS if c not in blocked])
    return _side(kind, cell, rng)


def describe(m: dict, now: int) -> str:
    extra = ""
    if m["type"] == "hold":
        extra = f"，已守住 {m.get('progress', 0)}/{m['hold_ticks']}"
    if m.get("deadline") is None:
        return f"{'主線' if m['line'] == 'main' else '支線'}｜{m['title']}：{m['brief']}（無時限{extra}）"
    left = max(0, m["deadline"] - now)
    return f"{'主線' if m['line'] == 'main' else '支線'}｜{m['title']}：{m['brief']}（剩約 {left} tick{extra}）"
