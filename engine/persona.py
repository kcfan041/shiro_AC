"""隊員人格（C 模式）：數字決定怎麼打仗，文字由 LLM 在需要時才寫。

- traits：四項性格 -1～+1（0＝一般人）
    aggr 積極：可接受風險、突入排前、撤退較晚、伏擊較早開火
    caut 謹慎：掩體權重、常架槍、不愛暴露
    comp 沉著：較不容易被壓制／釘死
    bond 重情：救人、拖人優先
- persona：人格值 0～1，性格影響戰術的程度（0＝完全照標準戰術 AI）
- innate：天生傾向（出廠抽的），traits＝innate＋經歷造成的變化
- 文字（personality／speech／background／sample_lines）可為空：白紙兵；第一次開口時才寫
"""
from __future__ import annotations

import random

TRAITS = ("aggr", "caut", "comp", "bond")
TRAIT_LABELS = {"aggr": "積極", "caut": "謹慎", "comp": "沉著", "bond": "重情"}
TRAIT_WORDS = {
    "aggr": ("畏縮", "衝勁十足"),
    "caut": ("大意", "謹慎"),
    "comp": ("容易慌亂", "沉著"),
    "bond": ("冷漠", "重視同伴"),
}

ROOKIE_PERSONA = 0.1
PERSONA_GROWTH = 0.05
PERSONA_CAP = 0.8
INNATE_SPREAD = 0.3          # 天生傾向大多在 ±0.3
TRAIT_STEP = 0.05


def clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def roll_innate(rng: random.Random, spread: float = INNATE_SPREAD) -> dict:
    """天生傾向：常態分布截斷，少數人會偏一點。"""
    return {k: round(clamp(rng.gauss(0.0, spread / 1.6), -spread * 1.6, spread * 1.6), 2) for k in TRAITS}


def blank(rng: random.Random, persona: float = ROOKIE_PERSONA, bias: dict | None = None) -> dict:
    """白紙兵的人格欄位（不含名字、武器）。bias：經歷偏移（例如被打散的殘部）。"""
    innate = roll_innate(rng)
    traits = {k: round(clamp(innate[k] + (bias or {}).get(k, 0.0)), 2) for k in TRAITS}
    return {"innate": innate, "traits": traits, "persona": round(persona, 2)}


def default_traits() -> dict:
    return {k: 0.0 for k in TRAITS}


def effective(member_or_unit, key: str) -> float:
    """實際影響＝性格數值 × 人格值。"""
    t = getattr(member_or_unit, "traits", None) or {}
    return float(t.get(key, 0.0)) * float(getattr(member_or_unit, "persona", 0.0) or 0.0)


def describe_short(m) -> str:
    """程式依數字組出的一句簡短描述（白紙兵用）。"""
    t = getattr(m, "traits", None) or {}
    p = float(getattr(m, "persona", 0.0) or 0.0)
    if p < 0.2:
        return "新兵，還看不出什麼個性"
    picks = sorted(((abs(v), k, v) for k, v in t.items() if abs(v) >= 0.25), reverse=True)[:2]
    if not picks:
        return "表現平穩，不太顯露個性"
    return "、".join(TRAIT_WORDS[k][1 if v > 0 else 0] for _, k, v in picks)


def traits_text(m) -> str:
    t = getattr(m, "traits", None) or {}
    return "、".join(f"{TRAIT_LABELS[k]}{t.get(k, 0.0):+.1f}" for k in TRAITS) + \
        f"（人格值 {float(getattr(m, 'persona', 0.0) or 0.0):.2f}）"


def has_voice(m) -> bool:
    return bool(getattr(m, "personality", ""))


def nudge(m, key: str, delta: float):
    t = dict(getattr(m, "traits", None) or default_traits())
    t[key] = round(clamp(t.get(key, 0.0) + delta), 2)
    m.traits = t


def grow(m):
    m.persona = round(min(PERSONA_CAP, float(m.persona or 0.0) + PERSONA_GROWTH), 2)


def from_card_text(text: str) -> dict:
    """從角色卡的一句個性描述粗略對應初始傾向（只用在舊卡沒有數字時）。"""
    out = default_traits()
    rules = {
        "aggr": (("衝", "急", "莽", "好戰", "熱血", "積極", "大膽"), ("膽小", "猶豫", "保守")),
        "caut": (("謹慎", "小心", "穩", "細心", "多疑"), ("大意", "魯莽", "隨便")),
        "comp": (("冷靜", "沉著", "沉穩", "寡言", "冷面"), ("緊張", "慌", "焦躁", "急躁")),
        "bond": (("重視隊員", "護", "照顧", "同伴", "隊友"), ("冷漠", "孤僻", "獨來獨往")),
    }
    for k, (pos, neg) in rules.items():
        if any(w in text for w in pos):
            out[k] += 0.4
        if any(w in text for w in neg):
            out[k] -= 0.4
    return {k: round(clamp(v), 2) for k, v in out.items()}
