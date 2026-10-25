"""地形高低差（docs/new/terrain.md 第 1 步：高度圖底層）：每格地面高度、格子邊緣土堤，
視線、彈道、爆炸、通行、站位接上地形；全平的地圖結果不變（固定遭遇摘要見 event_test）。不需要 LLM。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical.sim import Battle, Plan
from engine.tactical.tmap import FLOOR_H, H_STEP, TacMap
from engine.tactical.units import Unit


def flat(w=20, h=20):
    return TacMap(w=w, h=h)


def eye_at(m, node, height=1.0):
    c = m.center(node)
    return (c[0], c[1], c[2] + height)


def test_flat_map_unchanged():
    """全平的地圖：沒有高低差、地板高度＝樓層 × 3 m、地形檢查一律沒碰到。"""
    m = flat()
    assert not m.has_relief
    assert m.floor_z((3, 4, 0)) == 0.0 and m.floor_z((3, 4, 1)) == FLOOR_H
    assert m.terrain_hit((1.0, 1.0, 1.0), (30.0, 30.0, -5.0)) is None, "全平時地面 z＝0 由呼叫端照舊處理"
    assert m.terrain_t((1.0, 1.0, 1.0), (1.0, 0.0, 0.0), 20.0) is None
    assert m.step_cost((2, 2, 0), (3, 2, 0)) == 1


def test_ground_height_moves_everything_up():
    """地面墊高：地板、格中心、眼睛跟著升高；設回 0 就沒有高低差。"""
    m = flat()
    m.set_ground(5, 5, 8)                         # 1.6 m
    assert m.has_relief
    assert abs(m.floor_z((5, 5, 0)) - 8 * H_STEP) < 1e-9
    assert abs(m.center((5, 5, 0))[2] - 1.6) < 1e-9
    u = Unit("a", "blue", (5, 5, 0), posture="crouch")
    assert abs(u.eye(m)[2] - 2.6) < 1e-9
    assert m.standable((5, 5, 0))
    m.set_ground(5, 5, 0)
    assert not m.has_relief


def test_crest_blocks_sight_reverse_slope():
    """中間一道 2 m 高的土丘：兩邊蹲著互相看不到（反斜面）；站到丘頂就看得到另一邊。"""
    m = flat()
    for y in range(20):
        m.set_ground(10, y, 10)                   # x＝10 整排 2.0 m
    a, b = (5, 10, 0), (15, 10, 0)
    assert m.sight(eye_at(m, a), eye_at(m, b)) == 0.0, "丘後蹲著看不到"
    assert m.blocked(eye_at(m, a), eye_at(m, b))
    top = (10, 10, 0)
    assert m.sight(eye_at(m, top, 1.6), eye_at(m, b, 1.0)) > 0.0, "站在丘頂看得到下面"
    assert not m.blocked(eye_at(m, a), eye_at(m, b), terrain=False), "只看方塊時不擋"


def test_berm_on_edge():
    """格界上 1.0 m 的土堤：蹲著（眼高 1.0）的人隔著它被擋，站著（1.6）看得過去；只擋那一條邊。"""
    m = flat()
    m.set_berm(10, 10, "E", 5)                    # (10,10) 與 (11,10) 之間
    assert abs(m.berm_h((10, 10), (11, 10)) - 1.0) < 1e-9
    assert abs(m.berm_h((11, 10), (10, 10)) - 1.0) < 1e-9
    assert m.berm_h((10, 10), (10, 11)) == 0.0
    a, b = (8, 10, 0), (14, 10, 0)
    assert m.sight(eye_at(m, a, 0.9), eye_at(m, b, 0.9)) == 0.0
    assert m.sight(eye_at(m, a, 1.6), eye_at(m, b, 1.6)) > 0.0
    assert m.sight(eye_at(m, (8, 12, 0), 0.9), eye_at(m, (14, 12, 0), 0.9)) > 0.0, "旁邊一排沒有土堤"
    m.set_berm(11, 10, "W", 0)                    # 西邊＝鄰格的東邊，同一條邊
    assert not m.berm


def test_foxhole_protects_from_bullets_and_blast():
    """散兵坑（地面 −1.0 m、前緣 0.4 m 胸牆）：遠處平射打不到蹲著的人的軀幹；坑外的爆炸被擋，掉進坑裡就擋不住。"""
    m = flat(30, 30)
    hole = (10, 10)
    m.set_ground(*hole, -5)
    m.set_berm(10, 10, "N", 2)
    u = Unit("d", "blue", (10, 10, 0), posture="crouch", facing=(0, 1))
    shooter = Unit("s", "red", (10, 25, 0), posture="crouch", facing=(0, -1))
    bt = Battle(m, [u, shooter], seed=1)
    assert bt.map.blocked(shooter.eye(m), u.torso(m)), "坑裡蹲著，平射的線被坑緣與胸牆擋住"
    hit = bt.trace(shooter, shooter.eye(m), u.torso(m), 0.0, "t", 0)
    assert hit["kind"] == "ground", hit["kind"]
    out_blast = (10 * 2 + 1.0, 12 * 2 + 1.0, 0.0)
    assert m.blocked(out_blast, u.torso(m)), "坑外 2 格的爆炸被擋"
    in_blast = (10 * 2 + 1.5, 10 * 2 + 1.0, -1.0)
    assert not m.blocked(in_blast, u.torso(m)), "手榴彈落進坑裡沒有保護"


def test_step_costs():
    """高差 ≤0.4 m 正常走、0.4～1.2 翻越、超過過不去；土堤要從兩側較高的地面算；跳進坑容易、爬出來要翻越。"""
    m = flat()
    m.set_ground(5, 5, 2)                         # 0.4
    m.set_ground(6, 5, 5)                         # 1.0
    m.set_ground(7, 5, 12)                        # 2.4
    assert m.step_cost((4, 5, 0), (5, 5, 0)) == 1, "高 0.4：正常走"
    assert m.step_cost((5, 5, 0), (6, 5, 0)) == 2, "0.4 → 1.0 高 0.6：翻越"
    assert m.step_cost((6, 5, 0), (7, 5, 0)) is None, "1.0 → 2.4 高 1.4：過不去"
    m2 = flat()
    m2.set_ground(5, 5, 5)                        # 1.0
    assert m2.step_cost((4, 5, 0), (5, 5, 0)) == 2
    assert m2.step_cost((5, 5, 0), (4, 5, 0)) == 1, "往下 1.0 m 走得下來"
    m2.set_ground(8, 8, 7)                        # 1.4
    assert m2.step_cost((7, 8, 0), (8, 8, 0)) is None and m2.step_cost((8, 8, 0), (7, 8, 0)) is None
    m3 = flat()
    m3.set_ground(5, 5, -5)                       # 坑 −1.0
    assert m3.step_cost((4, 5, 0), (5, 5, 0)) == 1, "跳進坑"
    assert m3.step_cost((5, 5, 0), (4, 5, 0)) == 2, "爬出坑要翻越"
    m3.set_berm(5, 5, "W", 2)                     # 坑西緣 0.4 m 胸牆：從外面要跨 0.4、從坑裡要爬 1.4
    assert m3.step_cost((4, 5, 0), (5, 5, 0)) is None, "坑裡往上 1.4 m：兩邊都算，過不去"
    m4 = flat()
    m4.set_berm(5, 5, "E", 4)                     # 0.8 m 土堤
    assert m4.step_cost((5, 5, 0), (6, 5, 0)) == 2
    assert m4.step_cost((5, 6, 0), (6, 5, 0)) is None, "斜走繞過 (5,5) 時要跨土堤那條邊"
    assert m4.step_cost((5, 4, 0), (6, 5, 0)) is None, "斜走經過土堤那條邊"
    assert m4.step_cost((5, 6, 0), (6, 7, 0)) == 1, "旁邊沒有土堤的斜走照常"


def test_trace_ground_on_slope():
    """彈道往下打：全平時打在 z＝0；有高地時先打在墊高的地面上。"""
    m = flat(40, 40)
    s = Unit("s", "red", (5, 10, 0), posture="stand", facing=(1, 0))
    t = Unit("t", "blue", (30, 10, 0), posture="prone", facing=(-1, 0))
    bt = Battle(m, [s, t], seed=2)
    aim = (s.eye(m)[0] + 20.0, s.eye(m)[1], 0.0)
    r0 = bt.trace(s, s.eye(m), aim, 0.0, "t", 0)
    assert r0["kind"] == "ground" and abs(r0["to"][2]) < 1e-6
    m.set_ground(10, 10, 5)                       # 路上一格 1.0 m 高
    r1 = bt.trace(s, s.eye(m), aim, 0.0, "t", 0)
    assert r1["kind"] == "ground" and r1["to"][0] < r0["to"][0], (r0["to"], r1["to"])


def test_hearing_uses_height():
    """聽聲音比實際高度差（≤ 一層樓 3 m）：全平時一樓與二樓差 3 m（和原本「樓層差 ≤ 1」相同）、與三樓差 6 m；
    隔壁格但地面高 4 m 的人超過門檻。"""
    m = flat()
    assert abs(m.floor_z((5, 5, 1)) - m.floor_z((5, 5, 0))) <= FLOOR_H
    assert abs(m.floor_z((5, 5, 2)) - m.floor_z((5, 5, 0))) > FLOOR_H
    m.set_ground(6, 5, 20)                        # 4 m 高的平台
    assert abs(m.floor_z((6, 5, 0)) - m.floor_z((5, 5, 0))) > FLOOR_H


def test_hug_and_rest_on_berm():
    """面向那一邊有夠高的土堤：貼著格界站、算依託；面向別邊不算。"""
    m = flat()
    m.set_berm(5, 5, "N", 5)                      # 1.0 m
    c = m.center((5, 5, 0))
    hug = m.hug_base((5, 5, 0), (0, 1))
    assert hug[1] > c[1], "往北貼過去"
    assert m.hug_base((5, 5, 0), (1, 0)) == c, "面向東沒有土堤"
    u = Unit("u", "blue", (5, 5, 0), posture="crouch", facing=(0, 1))
    bt = Battle(m, [u], seed=0)
    assert bt.has_rest(u, (0.0, 1.0))
    assert not bt.has_rest(u, (1.0, 0.0))


def test_world_terrain_relief_payload():
    """協定：relief 只列不是 0 的；全平時是空的。"""
    from engine.views import relief_payload, unit_dict
    m = flat()
    p = relief_payload(m)
    assert p["ground"] == [] and p["berm"] == [] and p["step"] == H_STEP
    m.set_ground(3, 4, -5)
    m.set_berm(3, 4, "S", 2)
    p = relief_payload(m)
    assert p["ground"] == [[3, 4, -5]]
    assert p["berm"] == [[3, 3, "N", 2, "soil"]]
    u = Unit("u", "blue", (3, 4, 0))
    assert "z" not in unit_dict("u", u, "own", "g", "u")
    assert unit_dict("u", u, "own", "g", "u", z=m.floor_z(u.pos))["z"] == -1.0


def test_relief_copied_into_global_map():
    """全圖由各格拼起來時，地面高度與土堤平移過去。"""
    from engine.tactical import worldmap
    worldmap.clear_cache()
    m = worldmap.tmap("B2")
    m.set_ground(3, 3, 4)
    m.set_berm(3, 3, "E", 2)
    try:
        worldmap._GLOBAL.clear()
        g = worldmap.global_map()
        ox, oy = worldmap.cell_origin("B2")
        assert g.ground.get((3 + ox, 3 + oy)) == 4
        assert g.berm.get((3 + ox, 3 + oy, "E")) == 2
    finally:
        worldmap.clear_cache()


def test_vision_polygon_stops_at_crest():
    """我方視野多邊形：往高地的方向在丘前停下。"""
    from engine import vision
    m = flat(40, 40)
    for y in range(40):
        m.set_ground(20, y, 10)
    u = Unit("u", "blue", (10, 20, 0), posture="crouch", facing=(1, 0))
    bt = Battle(m, [u], seed=0)
    vision._CACHE.clear()
    rays = vision._base(m, u.pos, u.eye(m))
    east = max(rays, key=lambda r: r[0])
    assert east[2] < 25.0, east


def test_generated_relief():
    """自然地形（relief.py）：每格與全圖都連通、作戰格交界沒有超過 0.4 m 的高差、同一張圖每次一樣；
    中繼塔在緩丘上、敵方據點有預挖散兵坑；開關地形時物件的平面位置相同。"""
    from engine import scenario
    from engine.tactical import mapgen, relief, worldmap

    def gen(cell):
        return mapgen.generate(scenario.SCENARIO_ID, cell, scenario.CELLS[cell], scenario.BASE, scenario.ENEMY_BASES)

    def layout(m):
        return sorted((round(b.lo[0], 3), round(b.lo[1], 3), round(b.hi[0], 3), round(b.hi[1], 3), b.tag)
                      for b in m.boxes if b.tag != "floor")

    assert relief.ENABLED, "測試假設地形是開的（沒設 SHIRO_RELIEF=0）"
    worldmap.clear_cache()
    try:
        g = worldmap.global_map()
        assert g.has_relief
        assert len(mapgen._components(g)) == 1, "全圖連通"
        for x in range(g.w):
            for y in range(g.h):
                for dx, dy in ((1, 0), (0, 1)):
                    nx, ny = x + dx, y + dy
                    if g.in_bounds(nx, ny) and (x // 25 != nx // 25 or y // 25 != ny // 25):
                        assert abs(g.ground_h(x, y) - g.ground_h(nx, ny)) <= 0.41, ((x, y), (nx, ny))
        for cell in scenario.CELLS:
            assert len(mapgen._components(worldmap.tmap(cell))) == 1, cell
        tower = next(c for c in scenario.CELLS if worldmap.template(c) == "tower")
        assert max(worldmap.tmap(tower).ground.values()) * H_STEP >= 1.4, "中繼塔在緩丘上"
        for eb in scenario.ENEMY_BASES:
            m = worldmap.tmap(eb)
            holes = [k for k in m.berm if k[2] == "N" and m.ground.get((k[0], k[1] + 1), 0) < m.ground.get((k[0], k[1]), 0)]
            assert holes, f"{eb} 有面向南的散兵坑"
        a, _ = gen("C3")
        b, _ = gen("C3")
        assert a.ground == b.ground and a.berm == b.berm, "同一格每次一樣"
        relief.ENABLED = False
        try:
            flat_m, _ = gen("C3")
        finally:
            relief.ENABLED = True
        assert not flat_m.has_relief and layout(flat_m) == layout(a), "開關地形時物件位置相同"
    finally:
        worldmap.clear_cache()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("terrain_test OK")


if __name__ == "__main__":
    main()
