from __future__ import annotations

import random

from engine.models import Squad

COMPLY = "comply"
ADAPT = "adapt"
REFUSE = "refuse"


def score(squad: Squad) -> int:
    return max(5, min(99, squad.protocol - squad.deviation + squad.trust))


def roll(squad: Squad, rng: random.Random) -> str:
    s = score(squad)
    r = rng.randint(1, 100)
    if r <= s:
        return COMPLY
    if r <= s + (100 - s) // 2:
        return ADAPT
    return REFUSE


def deviation_label(squad: Squad) -> str:
    if squad.deviation < 10:
        return "協議完整，習慣依賴指揮官判斷"
    if squad.deviation < 30:
        return "開始累積自己的戰場判斷"
    if squad.deviation < 50:
        return "曾長時間獨自作戰，對指揮系統保留懷疑"
    return "高度自主，有自己的作戰風格，只在認同時服從"
