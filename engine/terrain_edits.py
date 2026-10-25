"""地圖變更層（docs/new/terrain.md §9）：與種子產生的地形不同的部分（挖坑、堆土、爆炸），只做連續地圖的全圖。

- 存在 world.terrain：{"ground": {"x,y": 高度單位}, "berm": {"x,y,E|N": [高度單位, 材質]}, "recover": bool}，
  記的是最終值；和種子的原始地形一樣就刪掉那一筆（所以變更層只會在有改動時存在）。
- 全圖是行程內共用的（worldmap.global_map）：建立、載入、重置世界時 bind(world)，把全圖改回原始地形再套上這個世界的變更；
  全圖還沒建好時不強制建，等建好時（on_built）再套。
- 每次地形改變 VERSION +1：規劃工作行程（planjob）與前端（/api/world_relief）依它判斷要不要更新。
- GM 地形回復（world.terrain["recover"]）：開著時，附近沒人、不在交火格的變更每次往原始地形回一級，回到原樣就刪掉。
"""
from __future__ import annotations

H_BACK = 2          # 地形回復每次地面回幾個高度單位（0.4 m）
BERM_BACK = 1       # 土堤每次回幾個單位（0.2 m）
QUIET_R = 3         # 這麼多格內有人（活著的個體）就不回復

VERSION = 0         # 全圖地形每次改變 +1（行程內）
_BOUND: dict | None = None      # 目前綁定的世界的 world.terrain
_APPLIED: set = set()           # 全圖上目前套用了變更的格子／邊（重新綁定時要先還原）


def empty() -> dict:
    return {"ground": {}, "berm": {}, "recover": False}


def of(world) -> dict:
    """world.terrain（補齊欄位；舊存檔沒有就是空的）。"""
    t = getattr(world, "terrain", None)
    if not isinstance(t, dict):
        t = empty()
        world.terrain = t
    for k, v in empty().items():
        t.setdefault(k, v)
    return t


def _gk(x: int, y: int) -> str:
    return f"{x},{y}"


def _bk(x: int, y: int, side: str) -> str:
    return f"{x},{y},{side}"


def _parse_g(k: str) -> tuple[int, int]:
    x, y = k.split(",")
    return int(x), int(y)


def _parse_b(k: str) -> tuple[int, int, str]:
    x, y, s = k.split(",")
    return int(x), int(y), s


def _global():
    from engine.tactical import worldmap
    return worldmap._GLOBAL.get("map")


def _base():
    from engine.tactical import worldmap
    return worldmap._GLOBAL.get("base")


def base_ground(x: int, y: int) -> int:
    b = _base()
    return b[0].get((x, y), 0) if b else 0


def base_berm(k: tuple) -> tuple[int, str]:
    b = _base()
    if not b:
        return 0, "soil"
    return b[1].get(k, 0), b[2].get(k, "soil")


def _bump():
    global VERSION
    VERSION += 1
    try:
        from engine import vision
        vision._CACHE.clear()               # 我方視野多邊形的快取（依格子與眼高，地形改了要重算）
    except ImportError:
        pass


def _edits_as_map(t: dict) -> tuple[dict, dict]:
    ground = {_parse_g(k): int(u) for k, u in t["ground"].items()}
    berm = {_parse_b(k): (int(v[0]), v[1]) for k, v in t["berm"].items()}
    return ground, berm


def _resync(g):
    """全圖：先把之前套用的變更還原成原始地形，再套上目前綁定的世界的變更。"""
    global _APPLIED
    if _base() is None:
        return
    ground, berm = {}, {}
    for item in _APPLIED:
        if len(item) == 2:
            ground[item] = base_ground(*item)
        else:
            berm[item] = base_berm(item)
    applied: set = set()
    if _BOUND:
        eg, eb = _edits_as_map(_BOUND)
        ground.update(eg)
        berm.update(eb)
        applied = set(eg) | set(eb)
    if g.apply_terrain(ground, berm):
        _bump()
    _APPLIED = applied


def bind(world):
    """這個世界的變更套到全圖上（建立、載入、重置世界時呼叫）。全圖還沒建好就等建好（on_built）再套。"""
    global _BOUND
    _BOUND = of(world)
    g = _global()
    if g is not None:
        _resync(g)


def on_built(g):
    """worldmap.global_map 建好時呼叫：記下原始地形，套上目前綁定的世界的變更。"""
    from engine.tactical import worldmap
    global _APPLIED
    worldmap._GLOBAL["base"] = (dict(g.ground), dict(g.berm), dict(g.berm_mat))
    g.base_ground = worldmap._GLOBAL["base"][0]        # 挖坑深度從原始地面算
    _APPLIED = set()
    if _BOUND and (_BOUND["ground"] or _BOUND["berm"]):
        _resync(g)


def apply(world, ground: dict | None = None, berm: dict | None = None) -> set:
    """改地形（全圖座標的最終值：ground {(x, y): 單位}、berm {(x, y, "E"|"N"): (單位, 材質)}），記進 world.terrain 並套到全圖。
    和原始地形一樣的那一筆從變更層刪掉。回傳改到的格子。"""
    t = of(world)
    for (x, y), u in (ground or {}).items():
        if int(u) == base_ground(x, y):
            t["ground"].pop(_gk(x, y), None)
            _APPLIED.discard((x, y))
        else:
            t["ground"][_gk(x, y)] = int(u)
            _APPLIED.add((x, y))
    for k, (u, mat) in (berm or {}).items():
        if (int(u), mat) == base_berm(k) or (int(u) == 0 and base_berm(k)[0] == 0):
            t["berm"].pop(_bk(*k), None)
            _APPLIED.discard(k)
        else:
            t["berm"][_bk(*k)] = [int(u), mat]
            _APPLIED.add(k)
    g = _global()
    touched = g.apply_terrain(ground, berm) if g is not None and t is _BOUND else set()
    if touched:
        _bump()
    return touched


def changed_cells(world) -> set:
    """變更層裡所有改過的格子（土堤算兩側）。"""
    t = of(world)
    out = {_parse_g(k) for k in t["ground"]}
    for k in t["berm"]:
        x, y, s = _parse_b(k)
        out |= {(x, y), (x + 1, y) if s == "E" else (x, y + 1)}
    return out


def reset_all(world) -> set:
    """GM「立即還原地形」：所有變更回到原始地形。"""
    t = of(world)
    ground = {_parse_g(k): base_ground(*_parse_g(k)) for k in t["ground"]}
    berm = {_parse_b(k): base_berm(_parse_b(k)) for k in t["berm"]}
    return apply(world, ground, berm)


def _toward(cur: int, base: int, step: int) -> int:
    if cur < base:
        return min(base, cur + step)
    return max(base, cur - step)


def recover_step(world, busy: set) -> set:
    """GM 地形回復（world.terrain["recover"] 開著時）：busy 以外的變更往原始地形回一級（地面 0.4 m、土堤 0.2 m），
    依座標排序（結果不受 dict 順序影響）。busy＝不能動的格子（附近有人、交火中的作戰格）。回傳改到的格子。"""
    t = of(world)
    if not t["recover"]:
        return set()
    ground, berm = {}, {}
    for k in sorted(t["ground"]):
        x, y = _parse_g(k)
        if (x, y) not in busy:
            ground[(x, y)] = _toward(int(t["ground"][k]), base_ground(x, y), H_BACK)
    for k in sorted(t["berm"]):
        kk = _parse_b(k)
        x, y, s = kk
        if (x, y) in busy or ((x + 1, y) if s == "E" else (x, y + 1)) in busy:
            continue
        bu, bm = base_berm(kk)
        u, mat = int(t["berm"][k][0]), t["berm"][k][1]
        nu = _toward(u, bu, BERM_BACK)
        berm[kk] = (nu, bm if nu == bu else mat)
    return apply(world, ground, berm)


def busy_cells(positions, hot_cells_global: set) -> set:
    """不能回復的格子：活著的個體 QUIET_R 格內，加上交火中的作戰格涵蓋的格子。"""
    out = set(hot_cells_global)
    for x, y in positions:
        out |= {(x + dx, y + dy) for dx in range(-QUIET_R, QUIET_R + 1) for dy in range(-QUIET_R, QUIET_R + 1)}
    return out


def relief_payload(m) -> dict:
    """地形高低差（只列不是 0 的）：ground [x, y, 高度]、berm [x, y, "E"|"N", 高度, 材質]，高度單位 step（m）。全平時兩個都是空的。
    （/api/world_terrain、/api/world_relief、回放影格共用）"""
    from engine.tactical.tmap import H_STEP
    return {"step": H_STEP,
            "ground": [[x, y, h] for (x, y), h in sorted(m.ground.items())],
            "berm": [[x, y, side, h, m.berm_mat.get((x, y, side), "soil")]
                     for (x, y, side), h in sorted(m.berm.items())]}


def snapshot() -> dict:
    """給規劃工作行程：目前全圖的變更（最終值）與版本。"""
    if not _BOUND:
        return {"v": VERSION, "ground": {}, "berm": {}}
    return {"v": VERSION, "ground": dict(_BOUND["ground"]), "berm": {k: list(v) for k, v in _BOUND["berm"].items()}}


_WORKER_V: int | None = None


def sync_worker(g, snap: dict):
    """工作行程：主行程的地形版本變了就把自己的全圖改成一樣（先還原上次套用的，再套新的）。"""
    global _WORKER_V, _BOUND
    if snap is None or snap.get("v") == _WORKER_V:
        return
    _BOUND = {"ground": snap["ground"], "berm": snap["berm"], "recover": False}
    _resync(g)
    _WORKER_V = snap.get("v")
