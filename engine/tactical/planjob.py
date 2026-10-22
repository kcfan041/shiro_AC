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
