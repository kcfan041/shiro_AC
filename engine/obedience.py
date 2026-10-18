from __future__ import annotations

import random

from engine.models import Squad

COMPLY = "comply"
ADAPT = "adapt"
REFUSE = "refuse"

TENDENCY_VALUES = {
    "consult": ("often", "normal", "rarely"),
    "initiative": ("low", "normal", "high"),
    "execution": ("literal", "intent", "own"),
}
TENDENCY_LABELS = {
    "consult": {"often": "遇到不確定的事會先請示", "normal": "必要時請示", "rarely": "很少請示，自己判斷"},
    "initiative": {"low": "只做被交代的事，不主動回報", "normal": "會回報重要的事", "high": "主動補位、主動回報"},
    "execution": {"literal": "照命令字面執行", "intent": "理解命令意圖後彈性執行", "own": "用自己的方式達成目標"},
}
BASE_COMPLY = {"literal": 92, "intent": 78, "own": 55}


def normalize_tendencies(t: dict) -> dict:
    out = {}
    for key, values in TENDENCY_VALUES.items():
        v = str(t.get(key, "")).strip().lower()
        out[key] = v if v in values else ("normal" if "normal" in values else values[0])
    out["verify"] = bool(t.get("verify", False))
    return out


def tendency_text(squad: Squad) -> str:
    t = squad.tendencies
    parts = [TENDENCY_LABELS[k][t.get(k, "normal" if k != "execution" else "literal")] for k in TENDENCY_VALUES]
    if t.get("verify"):
        parts.append("對指揮來源存疑，危險命令會要求確認")
    return "；".join(parts)


def beliefs_text(squad: Squad, n: int = 6) -> str:
    if not squad.beliefs:
        return "（還沒有形成自己的看法）"
    return "\n".join(f"- {b['text']}{'（出廠設定）' if b.get('origin') == 'protocol' else ''}"
                     for b in squad.beliefs[-n:])


def self_ratio(squad: Squad) -> float:
    if not squad.beliefs:
        return 0.0
    own = sum(1 for b in squad.beliefs if b.get("origin") != "protocol")
    return own / len(squad.beliefs)


def score(squad: Squad, risky: bool = False) -> int:
    s = BASE_COMPLY.get(squad.tendencies.get("execution", "literal"), 80)
    if squad.morale < 45:
        s -= 10
    if squad.morale < 20:
        s -= 15
    if not squad.alive_members() or len(squad.alive_members()) <= max(1, len(squad.members) // 2):
        s -= 5
    if risky:
        s -= 15 if squad.tendencies.get("execution") != "literal" else 5
    return max(5, min(97, s))


def roll(squad: Squad, rng: random.Random, risky: bool = False) -> str:
    s = score(squad, risky)
    r = rng.randint(1, 100)
    if r <= s:
        return COMPLY
    if r <= s + (100 - s) * 2 // 3:
        return ADAPT
    return REFUSE
