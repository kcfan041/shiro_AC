"""命令生命週期（只給畫面用：戰術圖上小隊旁的命令標籤，方案 §6、§7.5；TODO「命令生命週期顯示」）。

sent 送出 → thinking 隊長思考中（等 LLM）→ 回執：executing 執行中／refused 拒絕／unclear 無法對應／failed 無法執行／
kept 維持原行動／confirm 要求確認／info（不是命令）→ 執行中的命令之後變成 done 完成或 interrupted 中斷（附原因）。
只讀小隊狀態，不影響任何規則。不寫進存檔（重新啟動後從下一道命令開始記）。
"""
from __future__ import annotations

MOVES = ("move", "scout", "retreat")
TACTICS = ("advance", "supp_advance", "flank", "assault", "breach", "attack", "hide", "ambush")
VERDICT = {"comply": "executing", "adapt": "executing", "refuse": "refused", "unclear": "unclear",
           "failed": "failed", "keep": "kept", "confirm": "confirm", "info": "info"}


def snapshot(a) -> tuple:
    return (a.type, a.target, bool(a.ordered), bool(a.auto), a.mark_kind, tuple(a.mark) if a.mark else None)


class OrderTracker:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def sent(self, sid: str, text: str, tick: int):
        self.items[sid] = {"text": text[:60], "status": "sent", "t": tick, "since": tick}

    def thinking(self, sid: str, tick: int):
        o = self.items.get(sid)
        if o and o["status"] == "sent":
            o["status"], o["since"] = "thinking", tick

    def receipt(self, squad, receipt: dict, tick: int):
        """回執：照辦或變通 → 執行中（記下當時的行動，之後比對）；其他 → 對應的結束狀態。"""
        o = self.items.get(squad.id)
        if o is None or o["status"] not in ("sent", "thinking"):      # 標定點、快捷命令沒有經過 sent
            o = self.items[squad.id] = {"text": receipt.get("understanding") or receipt.get("label") or "", "t": tick}
        o.update({"status": VERDICT.get(receipt.get("verdict"), "info"), "since": tick, "label": receipt.get("label"),
                  "understanding": receipt.get("understanding"), "action": receipt.get("action")})
        o.pop("reason", None)
        if o["status"] == "executing":
            o["_act"] = snapshot(squad.action)
            o["_pos"] = squad.pos

    def update(self, squad, tick: int, in_combat: bool, action_text: str = ""):
        """每個作戰 tick：執行中的命令，行動變了 → 判斷是完成還是中斷。"""
        o = self.items.get(squad.id)
        if not o or o["status"] != "executing":
            return
        cur = snapshot(squad.action)
        prev = o["_act"]
        if cur == prev:
            return
        a = squad.action
        if a.auto:
            o["status"], o["reason"] = "interrupted", a.reason or "隊長自己改變行動"
        elif a.type == "hold" and prev[0] in MOVES and prev[1] == squad.pos:
            o["status"], o["reason"] = "done", "抵達"
        elif a.type == "hold" and prev[0] in MOVES and squad.contact:
            o["status"], o["reason"] = "interrupted", "遇敵，改固守"
        elif a.type == "hold" and prev[0] in TACTICS and not in_combat:
            o["status"], o["reason"] = "done", "交戰結束"
        elif a.type == prev[0] and a.target == prev[1]:              # 同一個行動只是標定點、命令旗標變了：照舊執行中
            o["_act"] = cur
            return
        else:
            o["status"], o["reason"] = "interrupted", f"改成：{action_text}" if action_text else "行動改變"
        o["since"] = tick

    def view(self, sid: str) -> dict | None:
        o = self.items.get(sid)
        return {k: v for k, v in o.items() if not k.startswith("_")} if o else None
