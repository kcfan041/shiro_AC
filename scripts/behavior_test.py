"""原本固定遭遇缺的三種行為（TODO P1）：engine/tactical/scenarios.py 的 BEHAVIORS 跑固定 seed，確認行為真的會發生。
急救／拖人、警戒射擊、看不到的射手。不需要 LLM；結果固定（同一個 seed 每次一樣）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios

SEEDS = range(3)


def _runs(name):
    return [runner.run(scenarios.BEHAVIORS[name], seed=s, max_ticks=60, keep_busy=True) for s in SEEDS]


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


def test_lone_holder_settles():
    """單人固守（草叢裡的射手）：剛找過射擊位置的 5 秒內沒被壓制就不馬上又找掩體（只擋來回；剛找過掩體、仍暴露的照樣再找）；
    被壓制照樣換；冷卻過了照樣換。整場：射手有開槍，潰退前換格不多（原本在暴露、掩體、看不到之間來回）。"""
    from engine.tactical.ai import REPOS_COOLDOWN_S, Intent, TacticalAI
    from engine.tactical.sim import Battle
    m, units, _, _ = scenarios.unseen_shooter_grass()
    b = Battle(m, units, seed=0)
    b.dt = 0.5
    b.detect("init")
    r = b.units["r0"]
    r.posture = "crouch"
    b.intel["red"].last_known.update({u.id: (u.pos, 0) for u in units if u.side == "blue"})
    ai = TacticalAI(b, "red")
    assert ai.plan(Intent("hold"))["r0"].mode == "move", "暴露：換到掩體"
    b.tick = 2
    ai.repos_t["r0"] = (b.tick, "cover")
    assert ai.plan(Intent("hold"))["r0"].mode == "move", "剛找過掩體、仍暴露：照樣再找"
    ai.repos_t["r0"] = (b.tick, "fire_pos")
    assert ai.plan(Intent("hold"))["r0"].mode != "move", "剛找過射擊位置、沒被壓制：不馬上又找掩體"
    r.suppression = 60.0
    assert ai.plan(Intent("hold"))["r0"].mode in ("move", "posture"), "被壓制：照樣處理"
    r.suppression = r.supp_peak = 0.0
    b.tick += int(REPOS_COOLDOWN_S / b.dt) + 1
    assert ai.plan(Intent("hold"))["r0"].mode == "move", "冷卻過了照樣換"
    from engine.tactical import timeline as tl
    stats = []
    for seed in SEEDS:                                   # 網頁版的節奏：0.5 秒一步、做到一半的射擊不換掉
        m, units, bi, ri = scenarios.unseen_shooter_grass()
        b = Battle(m, units, seed=seed)
        b.dt = 0.5
        ais = {"blue": TacticalAI(b, "blue"), "red": TacticalAI(b, "red")}
        b.detect("init")
        r, last, moves = b.units["r0"], b.units["r0"].pos, 0
        for _ in range(120):
            plans = {**ais["blue"].plan(bi), **ais["red"].plan(ri)}
            b.assign({k: p for k, p in plans.items() if tl.idle(b, k) or not tl.busy(b, k)})
            b.advance()
            if b.morale_state("red") in ("rout", "surrender") or not r.alive:
                break
            moves += r.pos != last
            last = r.pos
        stats.append((sum(1 for e in b.ev if e["type"] == "shot" and e.get("actor") == "r0"), moves))
    assert all(s >= 3 for s, _ in stats) and sum(mv for _, mv in stats) / len(stats) <= 6, stats


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
