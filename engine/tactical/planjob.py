"""規劃快照（模擬重構第 6 步）：AI 規劃改成「讀快照、輸出計畫」的純函式，之後可以丟到別的行程平行跑。

- battle_snapshot(b)：規劃會讀到的 Battle 狀態（深拷貝；不含地圖、快取、紀錄、事件）。
- ai_state(ai) / restore_ai(...)：TacticalAI 的內部狀態（耐心、固守、破門、行軍路徑、沿用不動…），不含快取。
- run(tmap, snap, side, state, groups)：在快照重建的 Battle 上，用同一個 AI 依序規劃 groups，回傳（計畫, 新 AI 狀態）。
  地圖是靜態的，由呼叫端提供（工作行程啟動時載入一次）。

同一個 AI（同一個 side）內的多組規劃必須依序（紅方所有敵軍小組共用一個 AI，且敵方指揮會讀上一組剛產生的回報）；
不同 side 之間互不影響，可以平行。
"""
from __future__ import annotations

import copy

from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap

# 規劃不讀、或只是快取／紀錄的 Battle 欄位（不放進快照）
SNAP_SKIP = {"map", "_sight_cache", "_contact_cache", "log", "shots", "ev", "morale_log", "order_hook",
             "_seq", "_phase", "_sub", "_aid_n", "_fire_meta", "_why", "reasons", "_ctx", "blasts"}
# AI 的快取與規劃中的暫存（不是狀態）
AI_SKIP = {"b", "_exp_cache", "_scoring", "_threats_override"}


def battle_snapshot(b: Battle) -> dict:
    return copy.deepcopy({k: v for k, v in b.__dict__.items() if k not in SNAP_SKIP})


def battle_from_snapshot(tmap: TacMap, snap: dict) -> Battle:
    b = Battle(tmap, [], seed=snap.get("seed", 0))
    for k, v in copy.deepcopy(snap).items():
        setattr(b, k, v)
    return b


def ai_state(ai: TacticalAI) -> dict:
    return copy.deepcopy({k: v for k, v in ai.__dict__.items() if k not in AI_SKIP})


def restore_ai(ai: TacticalAI, state: dict):
    """把狀態放回既有的 AI（主執行緒提交規劃結果用）；快取保留。"""
    for k, v in copy.deepcopy(state).items():
        setattr(ai, k, v)


def run(tmap: TacMap, snap: dict, side: str, state: dict,
        groups: list[tuple[Intent, list[str]]]) -> tuple[dict[str, Plan], dict]:
    b = battle_from_snapshot(tmap, snap)
    ai = TacticalAI(b, side)
    restore_ai(ai, state)
    plans: dict[str, Plan] = {}
    for intent, ids in groups:
        plans.update(ai.plan(intent, [b.units[i] for i in ids]))
    return plans, ai_state(ai)


# ---------------------------------------------------------------------- 多行程平行規劃（第 7 步）

WORKERS = 0                 # 工作行程數；0＝不平行（測試、Discord 版預設）。網頁版由 setting 的 plan_workers 設定
PARALLEL_MIN_UNITS = 40     # 參與規劃的人數少於這個就依序跑（量測：40 人以下傳輸的固定成本划不來，80 人以上明顯有利）
BUDGET = None               # 每步規劃的時間預算（秒）；None＝等全部算完。超時的工作改用便宜計畫（只射擊不移動）
_POOL = None


def configure(workers: int | None = None, budget: float | None = None, min_units: int | None = None):
    """設定平行規劃；行程數改變時重建工作行程池。"""
    global WORKERS, BUDGET, PARALLEL_MIN_UNITS
    if workers is not None and workers != WORKERS:
        shutdown()
        WORKERS = max(0, int(workers))
    if budget is not None:
        BUDGET = budget if budget > 0 else None
    if min_units is not None:
        PARALLEL_MIN_UNITS = min_units


def _init_worker():
    from engine.tactical import worldmap
    worldmap.global_map()       # 先建好全圖（約 1 秒），第一步就不用等


def _pool():
    global _POOL
    if _POOL is None:
        import atexit
        from concurrent.futures import ProcessPoolExecutor
        _POOL = ProcessPoolExecutor(max_workers=WORKERS, initializer=_init_worker)
        atexit.register(shutdown)
        for f in [_POOL.submit(_ping) for _ in range(WORKERS)]:
            f.result()
    return _POOL


def _ping():
    return True


def warm():
    """先把工作行程池建好、每個行程載好全圖（伺服器啟動時在背景呼叫；不然第一次平行的那一拍會卡一兩秒）。"""
    if WORKERS > 0:
        _pool()


def shutdown():
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None


def _worker_run(map_key: str, snap_bytes: bytes, side: str, state: dict, groups: list) -> tuple[dict, dict]:
    import pickle
    from engine.tactical import worldmap
    return run(worldmap.by_key(map_key), pickle.loads(snap_bytes), side, state, groups)


def parallel_ok(b: Battle, jobs: list) -> bool:
    return (WORKERS > 0 and len(jobs) >= 2 and getattr(b.map, "map_key", None) is not None
            and sum(len(units) for _, _, units in jobs) >= PARALLEL_MIN_UNITS)


def run_parallel(b: Battle, jobs: list) -> tuple[dict[str, Plan], list, int] | None:
    """jobs＝[(AI, 意圖, 單位)]，每個 AI 一份工作送到工作行程。回傳（依 jobs 順序合併的計畫, 每份的新 AI 狀態, 降級份數）。
    超過 BUDGET 的工作改用便宜計畫、AI 狀態不變（新狀態給 None）。工作行程壞掉 → 回 None（呼叫端改依序）。"""
    import pickle
    from concurrent.futures import wait
    try:
        pool = _pool()
        data = pickle.dumps(battle_snapshot(b), protocol=pickle.HIGHEST_PROTOCOL)
        futs = [pool.submit(_worker_run, b.map.map_key, data, ai.side, ai_state(ai), [(intent, [u.id for u in units])])
                for ai, intent, units in jobs]
        done, _ = wait(futs, timeout=BUDGET)
        plans: dict[str, Plan] = {}
        states, degraded = [], 0
        for f, (ai, intent, units) in zip(futs, jobs):
            if f in done:
                p, st = f.result()
                plans.update(p)
                states.append(st)
            else:
                f.cancel()
                degraded += 1
                plans.update(cheap_plans(ai, units))
                states.append(None)
        return plans, states, degraded
    except Exception:
        import logging
        logging.getLogger(__name__).exception("平行規劃失敗，改依序")
        shutdown()
        return None


def cheap_plans(ai: TacticalAI, units: list) -> dict[str, Plan]:
    """超過時間預算的降級計畫：看得到就射擊，否則原地警戒（不移動、不找掩體）。"""
    out = {}
    for u in units:
        p = ai.fire_plan(u) if u.can_shoot() else None
        out[u.id] = p or Plan(mode="overwatch", why="（規劃超時，原地警戒）")
    return out
