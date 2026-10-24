"""戰術圖呈現資料（方案 §6，S4）：命令生命週期、我方視野多邊形、瞄準事件、逐格面向、小隊意圖，以及這些資料的迷霧過濾。
這些都只給畫面用：固定遭遇的逐步狀態摘要由 event_test 檢查不變。

用法：python scripts/presentation_test.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import encounter  # noqa: E402
from engine import vision  # noqa: E402
from engine.models import Action  # noqa: E402
from engine.orders import OrderTracker  # noqa: E402
from engine.replay import for_viewer  # noqa: E402
from engine.tactical.tmap import TacMap  # noqa: E402
from engine.tactical.units import Unit  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok  {msg}")


class FakeSquad:
    def __init__(self, sid="s1", pos="B3"):
        self.id, self.pos, self.contact = sid, pos, None
        self.action = Action()


def orders():
    print("1. 命令生命週期")
    tr = OrderTracker()
    s = FakeSquad()
    tr.sent(s.id, "移動到 C3", 10)
    check(tr.view(s.id)["status"] == "sent", "送出")
    tr.thinking(s.id, 10)
    check(tr.view(s.id)["status"] == "thinking", "隊長思考中")
    s.action = Action(type="move", target="C3", ordered=True)
    tr.receipt(s, {"verdict": "comply", "label": "照辦", "understanding": "前往 C3", "action": "移動 → C3"}, 11)
    v = tr.view(s.id)
    check(v["status"] == "executing" and v["understanding"] == "前往 C3" and "_act" not in v, "照辦 → 執行中（內部欄位不外送）")
    tr.update(s, 12, in_combat=False)
    check(tr.view(s.id)["status"] == "executing", "行動沒變：照舊執行中")
    s.pos = "C3"
    s.action = Action(type="hold")
    tr.update(s, 13, in_combat=False)
    check(tr.view(s.id)["status"] == "done" and tr.view(s.id)["reason"] == "抵達", "走到目的地 → 完成（抵達）")

    s2 = FakeSquad("s2")
    s2.action = Action(type="move", target="C3", ordered=True)
    tr.receipt(s2, {"verdict": "adapt", "label": "變通"}, 20)
    s2.contact = "x"
    s2.action = Action(type="hold")
    tr.update(s2, 21, in_combat=True)
    check(tr.view("s2")["status"] == "interrupted" and "遇敵" in tr.view("s2")["reason"], "移動中遇敵改固守 → 中斷（遇敵）")

    s3 = FakeSquad("s3")
    s3.action = Action(type="supp_advance", ordered=True)
    tr.receipt(s3, {"verdict": "comply"}, 30)
    s3.action = Action(type="retreat", target="A3", auto=True, reason="傷亡太重")
    tr.update(s3, 31, in_combat=True)
    check(tr.view("s3")["status"] == "interrupted" and tr.view("s3")["reason"] == "傷亡太重", "隊長自己撤退 → 中斷（附理由）")
    s3.action = Action(type="supp_advance", ordered=True)
    tr.receipt(s3, {"verdict": "comply"}, 40)
    s3.action = Action(type="hold")
    tr.update(s3, 41, in_combat=False)
    check(tr.view("s3")["status"] == "done" and tr.view("s3")["reason"] == "交戰結束", "推進到交戰結束 → 完成")

    tr.receipt(FakeSquad("s4"), {"verdict": "refuse", "label": "拒絕"}, 50)
    check(tr.view("s4")["status"] == "refused", "拒絕（沒有經過送出也記得：標定點、快捷命令）")


def vision_checks():
    print("2. 我方視野多邊形")
    m = TacMap(w=40, h=40)
    for y in range(40):
        m.wall(20, y)                 # x＝20 一整道牆
    m.low_wall(10, 5, "E")            # (10,5) 東邊一道 1 m 矮牆
    m.warm()

    class B:
        def __init__(self, smokes=()):
            self.map, self.smokes = m, list(smokes)

        def active_smokes(self):
            return self.smokes

        @staticmethod
        def smoke_density(s):
            return 1.0

    u = Unit(id="a", side="blue", pos=(15, 20, 0), posture="stand", facing=(1, 0))
    pts = vision.polygon(B(), u)
    check(len(pts) == vision.RAYS, f"每人 {vision.RAYS} 條射線")
    east = pts[0]
    check(19.5 <= east[0] <= 20.1, f"往東碰到 x＝20 的牆就停（{east}）")
    west = pts[vision.RAYS // 2]
    check(west[0] <= 0.1, f"往西沒有東西擋，看到地圖邊（{west}）")
    n0 = len(vision._CACHE)
    vision.polygon(B(), u)
    check(len(vision._CACHE) == n0, "同一格同一眼高：用快取")
    prone = Unit(id="p", side="blue", pos=(10, 5, 0), posture="prone", facing=(1, 0))
    stand = Unit(id="s", side="blue", pos=(10, 5, 0), posture="stand", facing=(1, 0))
    pe, se = vision.polygon(B(), prone)[0], vision.polygon(B(), stand)[0]
    check(pe[0] < 11.1 and se[0] > 19, f"臥姿被 1 m 矮牆擋住（{pe}），站姿看得過去（{se}）")
    smoke = {"x": 17.0 * m.cell, "y": 20.5 * m.cell, "r": 2.0, "top": 4.0}
    sp = vision.polygon(B([smoke]), u)[0]
    check(sp[0] < east[0] - 1, f"煙霧擋住一部分（{sp}，沒有煙時 {east}）")


async def battle_checks():
    print("3. 固定遭遇：瞄準、逐格面向、意圖、視野、過濾")
    got = []

    def collect(sim, z):
        f = z.last_frame
        if f is not None:
            z.recorder.frames = z.recorder.frames[-50:]
            got.append((f, for_viewer(f, "1", sim.world), {uid for uid, o in z.owner.items() if o[0] == "e"}))
    await encounter.run(on_step=collect, replays=True)
    aims = [e for f, _, _ in got for e in f.get("ev", []) if e["type"] == "aim"]
    check(aims and all(e["data"]["fire"] >= e["ts"] for e in aims), f"射擊前先有瞄準事件，開火時間在瞄準之後（{len(aims)} 次）")
    pf_ok = [u for f, _, _ in got for u in f["units"] if u.get("path")]
    check(pf_ok and all(len(u.get("pf", [])) == len(u["path"]) for u in pf_ok), f"有走的人每一格都有面向（{len(pf_ok)} 筆）")
    vis = [f for f, _, _ in got if f.get("vision")]
    check(vis and all(not any(uid in enemies for uid in f["vision"]) for f, _, enemies in got if f.get("vision")),
          f"視野每秒算一次、只算我方（{len(vis)} 次）")
    check(any("alpha" in sid for f, _, _ in got for sid in (f.get("intents") or {})), "記下 Alpha 的戰術意圖")
    leaks = 0
    for f, pv, enemies in got:
        seen = set(f["intel"].get("blue", {}).get("seen", [])) | set(f["intel"].get("blue", {}).get("spotted", []))
        for e in pv["ev"]:
            if e["type"] == "aim":
                if e["actor"] in enemies - seen:
                    leaks += 1
                if e.get("target") is None and (e.get("data") or {}).get("at") is not None:
                    leaks += 1
        if any(uid in enemies for uid in pv.get("vision", {})):
            leaks += 1
    check(leaks == 0, "玩家視圖：看不到的敵人的瞄準不送、瞄準看不到的人不給位置、沒有敵人的視野")
    some = next(pv for _, pv, _ in got if pv.get("intents"))
    sid = next(iter(some["intents"]))
    check("order" in some["intents"][sid], "自己的小隊帶命令狀態")
    # 別的指揮官看不到這個小隊的命令狀態
    f0 = next(f for f, _, _ in got if f.get("intents"))

    class W:
        squads = {}

    other = for_viewer(dict(f0, intents={"sq": {"kind": "hold", "order": {"status": "executing"}}}), "2", W())
    check("order" not in other["intents"]["sq"], "別的指揮官看不到命令狀態")
    print(json.dumps({"aims": len(aims), "vision_frames": len(vis)}, ensure_ascii=False))


def main():
    orders()
    vision_checks()
    asyncio.run(battle_checks())
    print("全部通過")


if __name__ == "__main__":
    main()
