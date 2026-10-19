"""應變準則（SOP）：「卡住時通常怎麼改戰術」。

- 每個帶隊者一份（存在 Member.sop；小隊用目前發言人的）。反思時由 LLM 順便寫；沒寫過就依性格數字推一份。
- 只當備援：LLM 呼叫失敗、或「請求新指示／士氣崩潰／隊員倒地／彈藥見底」時 LLM 還沒回應。
- 服從度限制：指揮官明確下令（Action.ordered）時，準則只能改成保命動作（retreat／hide／hold）。
- 敵方依單位類型用固定準則；批次模擬的簡易隊長也用準則。
"""
from __future__ import annotations

SITUATIONS = ("stalled", "downed", "ammo_low", "pinned", "enemy_in_building", "outnumbered")
SITUATION_LABELS = {"stalled": "推進卡住", "downed": "隊員倒地", "ammo_low": "彈藥見底", "pinned": "被壓制",
                    "enemy_in_building": "敵人在建築內", "outnumbered": "兵力劣勢"}
ACTIONS = {"hold", "advance", "supp_advance", "flank", "assault", "hide", "retreat", "breach"}
SAFE = {"hold", "hide", "retreat"}


def derive(traits: dict, persona: float = 0.5) -> dict:
    """依性格數字推一份準則（不需要 LLM）。"""
    a = traits.get("aggr", 0.0)
    c = traits.get("caut", 0.0)
    b = traits.get("bond", 0.0)
    bold = a - c
    return {
        "stalled": "assault" if bold > 0.5 else ("flank" if bold > -0.2 else "supp_advance"),
        "downed": "retreat" if b > 0.2 or c > 0.3 else "hold",
        "ammo_low": "retreat" if c > -0.2 else "hold",
        "pinned": "retreat" if c > 0.3 else "hold",
        "enemy_in_building": "breach" if bold > -0.3 else "supp_advance",
        "outnumbered": "retreat" if c >= 0 or b > 0.3 else "hide",
    }


def normalize(raw) -> dict:
    out = {}
    if isinstance(raw, dict):
        for k in SITUATIONS:
            v = str(raw.get(k, "")).strip().lower()
            if v in ACTIONS:
                out[k] = v
    return out


ENEMY_SOP = {
    "garrison": {"stalled": "hold", "downed": "hold", "ammo_low": "hold", "pinned": "hold",
                 "enemy_in_building": "hold", "outnumbered": "hold"},
    "mobile": {"stalled": "flank", "downed": "hold", "ammo_low": "retreat", "pinned": "hold",
               "enemy_in_building": "breach", "outnumbered": "retreat"},
    "patrol": {"stalled": "supp_advance", "downed": "retreat", "ammo_low": "retreat", "pinned": "retreat",
               "enemy_in_building": "supp_advance", "outnumbered": "retreat"},
    "siege": {"stalled": "assault", "downed": "assault", "ammo_low": "hold", "pinned": "hold",
              "enemy_in_building": "breach", "outnumbered": "supp_advance"},
}


def pick(sop: dict, situation: str, ordered_action: str | None = None) -> str | None:
    """依準則選行動。ordered_action＝指揮官明確下令的行動（有的話只允許保命動作）。"""
    act = sop.get(situation)
    if act is None:
        return None
    if ordered_action and act not in SAFE:
        return None
    return act


def situation_from_text(kind: str, text: str) -> str | None:
    """戰術層事件 → 準則情境。"""
    if kind == "request":
        if "士氣崩潰" in text:
            return "outnumbered"
        if "請求新指示" in text:
            return "stalled"
        return None
    return {"down": "downed", "ammo_out": "ammo_low", "ammo_low": "ammo_low"}.get(kind)
