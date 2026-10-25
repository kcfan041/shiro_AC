"""原本固定遭遇缺的三種行為（TODO P1）：engine/tactical/scenarios.py 的 BEHAVIORS 跑固定 seed，確認行為真的會發生。
急救／拖人、警戒射擊、看不到的射手。不需要 LLM；結果固定（同一個 seed 每次一樣）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios

SEEDS = range(3)


def _runs(name):
    return [runner.run(scenarios.BEHAVIORS[name], seed=s, max_ticks=60) for s in SEEDS]


def test_aid_drag():
    """倒在牆前的隊友：有人去止血，也有人拖往掩護（三個 seed 合計）。"""
    runs = _runs("aid_drag")
    aid = sum(1 for r in runs for e in r["battle"].ev if e["type"] == "aid")
    drag = sum(1 for r in runs for e in r["battle"].ev if e["type"] == "drag")
    assert aid >= 1 and drag >= 1, (aid, drag)


def test_overwatch_fire():
    """從牆後走出來穿越開闊地的敵人：等待中的藍方會警戒射擊（射擊事件帶 ow）。"""
    runs = _runs("overwatch_crossing")
    ow = sum(1 for r in runs for e in r["battle"].ev
             if e["type"] == "shot" and e.get("data", {}).get("ow") and str(e.get("actor", "")).startswith("b"))
    assert ow >= 1, ow


def test_unseen_shooter():
    """窗戶後的射手：每場都有開火；大多數場次開第一槍時藍方還沒發現他；藍方會因槍聲得知他的大概位置
    （開火暴露是機率性的，50 m 外單發約 46%，所以看三個 seed 合計）。"""
    unseen_first, gunfire = 0, 0
    for r in _runs("unseen_shooter"):
        ev = r["battle"].ev
        first_shot = next((e["seq"] for e in ev if e["type"] == "shot" and e.get("actor") == "r0"), None)
        assert first_shot is not None, "射手有開火"
        first_spot = next((e["seq"] for e in ev if e["type"] == "spot" and e.get("target") == "r0"), None)
        unseen_first += first_spot is None or first_spot > first_shot
        gunfire += sum(1 for e in ev if e["type"] == "hear" and e.get("target") == "r0"
                       and e.get("data", {}).get("how") == "gunfire")
    assert unseen_first >= 2, unseen_first
    assert gunfire >= 1, gunfire


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("behavior_test OK")


if __name__ == "__main__":
    main()
