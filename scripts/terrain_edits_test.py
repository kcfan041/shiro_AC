"""地圖變更層（engine/terrain_edits.py，docs/new/terrain.md §9）：記錄與種子不同的地形、綁定世界、存讀、局部快取失效、
GM 地形回復、規劃工作行程同步、回放影格。不需要 LLM。"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import scenario, terrain_edits as te
from engine.models import World
from engine.tactical import worldmap

CELL = (60, 60)                # D3 附近（中繼塔緩丘上）


def fresh():
    w = scenario.new_world()
    te.bind(w)
    return w, worldmap.global_map()


def test_apply_and_bind():
    """改地形記進 world.terrain 並套到全圖；換世界時全圖回到原始地形，換回來變更又出現；改回原樣就從變更層刪掉。"""
    w1, g = fresh()
    base = g.ground.get(CELL, 0)
    v0 = te.VERSION
    touched = te.apply(w1, {CELL: base - 5}, {CELL + ("N",): (2, "soil")})
    assert CELL in touched and te.VERSION > v0
    assert g.ground.get(CELL) == base - 5 and g.berm.get(CELL + ("N",)) == 2
    assert w1.terrain["ground"] == {"60,60": base - 5} and w1.terrain["berm"] == {"60,60,N": [2, "soil"]}
    w2, _ = fresh()
    assert g.ground.get(CELL, 0) == base and CELL + ("N",) not in g.berm, "換世界：回到原始地形"
    te.bind(w1)
    assert g.ground.get(CELL) == base - 5 and g.berm.get(CELL + ("N",)) == 2, "換回來：變更又出現"
    te.apply(w1, {CELL: base}, {CELL + ("N",): (0, "soil")})
    assert w1.terrain["ground"] == {} and w1.terrain["berm"] == {}, "改回原樣：從變更層刪掉"
    assert g.ground.get(CELL, 0) == base and CELL + ("N",) not in g.berm


def test_save_load_roundtrip():
    """存檔只存變更層；讀回來、綁定後全圖一樣；舊存檔沒有 terrain 欄位就是空的。"""
    w1, g = fresh()
    base = g.ground.get(CELL, 0)
    te.apply(w1, {CELL: base - 5}, {CELL + ("E",): (3, "soil")})
    w2 = World.from_dict(w1.to_dict())
    assert w2.terrain == w1.terrain
    fresh()
    te.bind(w2)
    assert g.ground.get(CELL) == base - 5 and g.berm.get(CELL + ("E",)) == 3
    old = w1.to_dict()
    old.pop("terrain")
    w3 = World.from_dict(old)
    assert te.of(w3) == te.empty()
    te.bind(w3)
    assert g.ground.get(CELL, 0) == base


def test_local_invalidation_matches_full_rebuild():
    """局部快取失效的結果和整個清掉快取重算一樣：改地形前後附近的通行成本、可站立、視線、貼掩體。"""
    w, g = fresh()
    x0, y0 = CELL
    nodes = [(x, y, 0) for x in range(x0 - 4, x0 + 5) for y in range(y0 - 4, y0 + 5)]
    for a in nodes:                                   # 先把快取填滿
        for b in [(a[0] + dx, a[1] + dy, 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]:
            g.step_cost(a, b)
        g.neighbors(a)
        g.hug_base(a, (0, 1))
    eye = (g.center((x0, y0 - 6, 0))[0], g.center((x0, y0 - 6, 0))[1], g.floor_z((x0, y0 - 6, 0)) + 1.0)
    pts = [(g.center(n)[0], g.center(n)[1], g.floor_z(n) + 0.5) for n in nodes]
    for p in pts:
        g.sight(eye, p)
    te.apply(w, {CELL: g.ground.get(CELL, 0) - 5, (x0 + 1, y0): g.ground.get((x0 + 1, y0), 0) + 6},
             {CELL + ("N",): (3, "soil")})
    got = ([g.step_cost(a, (a[0] + 1, a[1], 0)) for a in nodes], [g.step_cost(a, (a[0] + 1, a[1] + 1, 0)) for a in nodes],
           [sorted(g.neighbors(a)) for a in nodes], [g.hug_base(a, (0, 1)) for a in nodes], [g.sight(eye, p) for p in pts])
    g._cache.clear()
    g.__dict__.pop("_pair_sight", None)
    want = ([g.step_cost(a, (a[0] + 1, a[1], 0)) for a in nodes], [g.step_cost(a, (a[0] + 1, a[1] + 1, 0)) for a in nodes],
            [sorted(g.neighbors(a)) for a in nodes], [g.hug_base(a, (0, 1)) for a in nodes], [g.sight(eye, p) for p in pts])
    assert got == want
    te.reset_all(w)


def test_recover_and_reset():
    """GM 地形回復：關著不動；開著每次往原始地形回一級（地面 0.4 m、土堤 0.2 m），附近有人的不動；立即還原全部回去。"""
    w, g = fresh()
    base = g.ground.get(CELL, 0)
    other = (CELL[0] + 10, CELL[1])
    ob = g.ground.get(other, 0)
    te.apply(w, {CELL: base - 5, other: ob - 5}, {CELL + ("N",): (2, "soil")})
    assert not te.recover_step(w, set()), "關著不動"
    w.terrain["recover"] = True
    busy = te.busy_cells([other], set())
    te.recover_step(w, busy)
    assert g.ground[CELL] == base - 3 and g.berm[CELL + ("N",)] == 1
    assert g.ground[other] == ob - 5, "附近有人：不動"
    te.recover_step(w, busy)
    te.recover_step(w, busy)
    assert g.ground.get(CELL, 0) == base and CELL + ("N",) not in g.berm and "60,60" not in w.terrain["ground"]
    te.reset_all(w)
    assert g.ground.get(other, 0) == ob and not w.terrain["ground"] and not w.terrain["berm"]


def _worker_ground(snap, cell):
    from engine import terrain_edits
    from engine.tactical import worldmap as wm
    g = wm.by_key("global")
    terrain_edits.sync_worker(g, snap)
    return g.ground.get(cell, 0), g.berm.get(cell + ("N",))


def test_worker_sync():
    """規劃工作行程：主行程的地形版本變了，工作行程的全圖跟著改（先還原上次的，再套新的）。"""
    w, g = fresh()
    base = g.ground.get(CELL, 0)
    with ProcessPoolExecutor(max_workers=1) as ex:
        te.apply(w, {CELL: base - 5}, {CELL + ("N",): (2, "soil")})
        assert ex.submit(_worker_ground, te.snapshot(), CELL).result() == (base - 5, 2)
        te.reset_all(w)
        assert ex.submit(_worker_ground, te.snapshot(), CELL).result() == (base, None), "還原也同步"


def test_replay_frames_carry_relief():
    """回放：地形變了的那一格影格帶 relief（每格都有版本 rv）；存片段時第一格補上起始地形。"""
    import asyncio
    import random
    from engine import roster
    from engine.replay import ReplayStore
    from engine.sim import Simulation
    from scripts.zone_test import Quiet, card
    path = "./data/terrain_edits_test.json"
    rp_dir = "./data/terrain_edits_replays"

    async def go():
        if os.path.exists(path):
            os.remove(path)
        sim = Simulation(Quiet(), save_path=path, rng=random.Random(1))
        sim.zone_mode = True
        sim.tac_steps = 1
        sim.enemy_ai_enabled = False
        sim.world.enemies.clear()
        sim.replays = ReplayStore(rp_dir)
        sim.ensure_commander("1", "甲")
        a, _ = await sim.deploy_squad(card("alpha"), "1")
        a.pos = "B3"
        roster.sync_world(sim.world)
        await sim.tick()
        await sim.tick()
        z = sim.zones[0]
        rec = z.recorder
        assert rec.frames and "rv" in rec.frames[-1]
        x, y = worldmap.cell_origin("B3")
        te.apply(sim.world, {(x + 5, y + 5): -5})
        await sim.tick()
        last = rec.frames[-1]
        assert last.get("relief") and [x + 5, y + 5, -5] in last["relief"]["ground"], "地形變了那一格帶 relief"
        seg = [{k: v for k, v in last.items() if k != "relief"}]          # 片段從沒有 relief 的一格開始
        import engine.replay as rpm
        orig_trim, rpm.trim_quiet = rpm.trim_quiet, (lambda frames: frames)  # 測試的影格沒有開火，不要被當成安靜剪掉
        try:
            entry = sim.replays.save(z, rec, sim.world, frames=seg, cells={"B3"})
        finally:
            rpm.trim_quiet = orig_trim
        assert entry is not None
        saved = sim.replays.load(entry["id"])
        assert saved["frames"][0].get("relief") == last["relief"], "片段第一格補上起始地形"
        te.reset_all(sim.world)
        if os.path.exists(path):
            os.remove(path)
    asyncio.run(go())


def test_views_relief_version():
    """前端：區域畫面帶 relief_v；/api/world_relief 回傳目前的地形與版本。"""
    from engine import views
    w, g = fresh()
    r0 = views.world_relief()
    te.apply(w, {CELL: g.ground.get(CELL, 0) - 5})
    r1 = views.world_relief()
    assert r1["v"] > r0["v"] and [CELL[0], CELL[1], g.ground[CELL]] in r1["relief"]["ground"]
    assert views.world_terrain()["relief_v"] == r1["v"]
    te.reset_all(w)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("terrain_edits_test OK")


if __name__ == "__main__":
    main()
