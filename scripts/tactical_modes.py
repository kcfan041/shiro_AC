"""射擊模式比較與壓制效果測量（固定站位，不經 AI）。

用法：python scripts/tactical_modes.py [N]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def target_map(cover: bool) -> TacMap:
    m = TacMap()
    if cover:
        m.low_wall(4, 7, "S", height=1.0)
    return m


def modes(n: int):
    print("== 射擊模式：步槍、蹲姿、距離約 12 m")
    for cover in (False, True):
        m = target_map(cover)
        for mode in ("aimed", "burst", "suppress"):
            shots = hits = supp = 0
            for seed in range(n):
                s = Unit("s", "blue", (4, 1, 0), weapon="rifle", facing=(0, 1))
                t = Unit("t", "red", (4, 7, 0), facing=(0, -1))
                b = Battle(m, [s, t], seed=seed)
                b.intel["blue"].spotted["t"] = 0
                b.tick = 1
                res = b.fire(s, Plan(mode="shoot", target="t", fire=mode, point=t.torso(m)), "m")
                shots += len(res)
                hits += sum(1 for r in res if r["kind"] == "body" and r["ref"][0] == "t")
                supp += sum(b.suppression_from(r, s).get("t", 0) for r in res)
            label = "矮牆後（只露頭）" if cover else "開闊地"
            print(f"  {label:10s} {mode:8s} 每次行動：命中 {hits / n:.2f}｜發數 {shots / n:.1f}｜"
                  f"每命中耗彈 {shots / max(hits, 1):.1f}｜壓制 {supp / n:.1f}")


def overwatch_effect(n: int):
    print("== 壓制對警戒的影響：敵方警戒者在窗口，我方 1 人穿越開闊地 4 格")
    for supp in (0, 60, 90):
        hit_runs = shots = 0
        for seed in range(n):
            m = TacMap()
            m.window_side(4, 8, "S")
            w = Unit("w", "red", (4, 8, 0), facing=(0, -1))
            r = Unit("r", "blue", (1, 3, 0), posture="crouch", facing=(1, 0))
            b = Battle(m, [w, r], seed=seed)
            b.intel["red"].spotted["r"] = 0
            b.intel["red"].last_known["r"] = (r.pos, 0)
            b.units["w"].suppression = supp
            b.step({"w": Plan(mode="overwatch"), "r": Plan(mode="move", path=[(2, 3, 0), (3, 3, 0)])})
            shots += b.units["w"].stats["shots"]
            hit_runs += 1 if b.units["r"].damage else 0
        print(f"  警戒者壓制值 {supp:3d}：開火 {shots / n:.2f}｜推進者被命中機率 {hit_runs / n:.0%}")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    modes(n)
    overwatch_effect(n)


if __name__ == "__main__":
    main()
