"""作戰層的地形摘要（engine/terrain_summary.py，docs/new/terrain.md §7.4）：每格的高地、散兵坑、土堤標籤，
跟著地形變更更新；隊長簡報的地形一行、作戰圖資料、敵方作戰層反攻先搶好守的格子。不需要 LLM。"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import enemy_ai, scenario, terrain_edits as te, terrain_summary as ts
from engine.models import Enemy
from engine.tactical import timeline as tl, worldmap


def fresh():
    w = scenario.new_world()
    te.bind(w)
    return w, worldmap.global_map()


def test_tags_from_terrain():
    """中繼塔緩丘是高地、敵方據點的預挖陣地是散兵坑、北郊農地的田埂是土堤（散兵坑的胸牆不另外算土堤）；平地沒有標籤。"""
    fresh()
    d3, e2, e1 = ts.cell_terrain("D3"), ts.cell_terrain("E2"), ts.cell_terrain("E1")
    assert "▲" in d3["marks"] and d3["tags"][0].startswith("高地"), d3
    assert "◡" in e2["marks"] and "≡" not in e2["marks"], e2
    assert "≡" in e1["marks"], e1
    assert ts.cell_terrain("C3")["tags"] == []
    assert ts.value("D3") > ts.value("C3")


def test_follows_terrain_edits():
    """挖了坑（地圖變更層）摘要跟著變（依地形版本快取）；還原後回去。"""
    w, g = fresh()
    ox, oy = worldmap.cell_origin("C3")
    holes = [(x, y) for x in range(ox + 2, ox + 23) for y in range(oy + 2, oy + 23) if tl.can_dig(g, (x, y, 0))][:4]
    assert len(holes) == 4
    assert "◡" not in ts.cell_terrain("C3")["marks"]
    te.apply(w, {h: tl.dig_ref(g, *h) - 5 for h in holes})
    t = ts.cell_terrain("C3")
    assert "◡" in t["marks"] and t["pits"] >= 4, t
    te.reset_all(w)
    assert "◡" not in ts.cell_terrain("C3")["marks"]


def test_brief_and_map_view():
    """隊長簡報：所在格與相鄰格有標籤的才列；作戰圖每格帶標籤與符號。"""
    fresh()
    b = ts.brief("D2")
    assert "通訊中繼塔（D3）：高地" in b and "西北工廠區（E2）：散兵坑" in b, b
    assert ts.brief("A3") == "", "後方據點附近都是平地"

    from engine.sim import Simulation
    from scripts.zone_test import Quiet
    path = "./data/terrain_summary_test.json"
    if os.path.exists(path):
        os.remove(path)
    sim = Simulation(Quiet(), save_path=path, rng=random.Random(1))
    sim.zone_mode = True
    from engine import views
    worldmap.global_map()
    cells = {c["id"]: c for c in views.op_map(sim, "x")["cells"]}
    assert "▲" in cells["D3"]["marks"] and cells["D3"]["relief"], cells["D3"]
    assert cells["C3"]["marks"] == ""
    if os.path.exists(path):
        os.remove(path)


def test_enemy_counter_prefers_defensible_cell():
    """敵方作戰層：兩個要反攻的格子、只有一支機動隊 → 先去好守的（高地）。"""
    w, _ = fresh()
    w.enemies.clear()
    w.enemies["m1"] = Enemy(id="m1", pos="D2", strength=3, route=["D2"], tag="mobile")
    w.enemy_intel = {"C3": {"est": 2, "tick": 0}, "D3": {"est": 2, "tick": 0}}
    w.enemy_next_think = 0
    w.enemy_next_harass = 10 ** 9
    enemy_ai.think(w, random.Random(1), ["C3", "D3"])
    assert w.enemies["m1"].goal == "D3", (w.enemies["m1"].order, w.enemies["m1"].goal)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("terrain_summary_test OK")


if __name__ == "__main__":
    main()
