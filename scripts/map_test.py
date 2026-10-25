import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import missions, scenario, store
from engine.models import World


def main():
    assert len(scenario.CELLS) == 25 and scenario.ROWS == list("ABCDE") and scenario.COLS == list("12345")
    for r in scenario.ROWS:
        for c in scenario.COLS:
            assert r + c in scenario.CELLS, r + c
    assert sorted(scenario.adjacent("A1")) == ["A2", "B1"]
    assert sorted(scenario.adjacent("C3")) == ["B3", "C2", "C4", "D3"]
    assert sorted(scenario.adjacent("E5")) == ["D5", "E4"]
    assert scenario.distance("A1", "E5") == 8

    path = scenario.find_path(scenario.BASE, "E3")
    assert path and path[-1] == "E3" and len(path) == scenario.distance(scenario.BASE, "E3"), path
    avoid = scenario.find_path("A3", "D3", {"C3", "B3"})
    assert avoid and "C3" not in avoid and "B3" not in avoid, avoid
    assert scenario.find_route("A3", "D3", ["A1"])[:2] == ["A2", "A1"]

    assert scenario.BASE in scenario.CELLS and all(b in scenario.CELLS for b in scenario.ENEMY_BASES)
    for m in missions.MAIN_LINE:
        assert m["cell"] in scenario.CELLS, m
    import random
    for i in range(50):
        side = missions.roll_side(random.Random(i), set())
        assert side["cell"] not in {scenario.BASE, *scenario.ENEMY_BASES}

    w = scenario.new_world()
    assert w.scenario == scenario.SCENARIO_ID
    tags = {e.tag for e in w.enemies.values()}
    assert {"patrol", "garrison"} <= tags, tags
    for e in w.enemies.values():
        assert e.pos in scenario.CELLS and all(c in scenario.CELLS for c in e.route)

    scenario.CELLS["B2"]["subcells"] = {"b": {"name": "停車大樓 3F"}}
    assert scenario.subcells("B2") == {"b": {"name": "停車大樓 3F"}}
    del scenario.CELLS["B2"]["subcells"]

    path_file = "./data/map_test.json"
    os.makedirs("./data", exist_ok=True)
    with open(path_file, "w", encoding="utf8") as f:
        json.dump({"scenario": "old_3x3", "tick": 1}, f)
    assert store.load(path_file) is None and os.path.exists(path_file + ".old_scenario")
    os.remove(path_file + ".old_scenario")
    store.save(w, path_file)
    assert isinstance(store.load(path_file), World)
    os.remove(path_file)
    print("map_test OK")


if __name__ == "__main__":
    main()
