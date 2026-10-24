"""戰術地圖實驗頁的資料：把一場本機回放（data/replays）連同全圖地形輸出成 web/tac/fixtures/replay.json。
影格照網頁版的方式依觀看者過濾（回放裡的第一位指揮官；找不到存檔世界就用 GM 視角）。

用法：python scripts/tac_fixture.py [回放 id，預設挑開火最多的一場] [--gm]
之後 cd web/tac && npm run dev，開 http://localhost:5173/static/dist/lab.html?mode=fixture
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import store, views
from engine.replay import ReplayStore, for_viewer
from web.app import load_config

OUT = "web/tac/fixtures/replay.json"


def pick(rs: ReplayStore) -> str | None:
    best, score = None, -1
    for e in rs.index:
        if not e.get("zone"):
            continue
        rp = rs.load(e["id"])
        n = sum(len(f["shots"]) for f in rp["frames"]) if rp else 0
        if n > score:
            best, score = e["id"], n
    return best


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cfg = load_config()
    rs = ReplayStore(cfg["replays"])
    rid = args[0] if args else pick(rs)
    rp = rs.load(rid) if rid else None
    if rp is None:
        print(f"找不到回放（{cfg['replays']}）；先在網頁版打一場，或指定回放 id。")
        return 1
    world = store.load(cfg["save_path"])
    gm = "--gm" in sys.argv or world is None or not rp["commanders"]
    cid = None if gm else rp["commanders"][0]
    frames = [for_viewer(f, cid, world, gm=gm) for f in rp["frames"]]
    if gm:                                    # GM 視角沒有 own／ally／enemy，照陣營補上
        for f in frames:
            for u in f["units"]:
                u["side"] = "enemy" if u.get("kind") not in ("s", "g") else ("own" if u.get("kind") == "s" else "remnant")
    keys = ("id", "cell", "w", "h", "start_tick", "end_tick", "zone", "bbox")
    data = {"terrain": views.world_terrain(), "replay": {k: rp.get(k) for k in keys}, "frames": frames}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False)
    shots = sum(len(f["shots"]) for f in frames)
    print(f"{OUT}：{rid}，{len(frames)} 格影格、{shots} 發、{'GM' if gm else '指揮官 ' + cid} 視角，"
          f"{os.path.getsize(OUT) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
