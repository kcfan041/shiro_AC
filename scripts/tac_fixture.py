"""戰術地圖實驗頁的資料：把一場本機回放（data/replays）連同全圖地形輸出成 web/tac/fixtures/replay.json。
影格照網頁版的方式依觀看者過濾（回放裡的第一位指揮官；找不到存檔世界就用 GM 視角）。

用法：python scripts/tac_fixture.py [回放 id，預設挑開火最多的一場] [--gm]
      python scripts/tac_fixture.py --demo=40   離線跑 web_demo 的場景（Alpha 從 B3 推進 C3）40 拍、錄下影格（帶最新的欄位，例如彈著材質）
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


async def demo(ticks: int) -> tuple[list, dict, str, object]:
    """離線跑 web_demo 的場景，錄下戰術影格（區域的 FrameRecorder），回傳（影格、回放資訊、指揮官、世界）。"""
    import web_demo
    game, tokens = await web_demo.build(False)
    sim = game.sim
    got: dict[int, int] = {}
    frames: list = []
    for _ in range(ticks):
        await game.tick_once()
        for z in sim.zones:
            rec = getattr(z, "recorder", None)
            if rec is None:
                continue
            k = id(rec)
            frames.extend(rec.frames[got.get(k, 0):])
            got[k] = len(rec.frames)
    frames.sort(key=lambda f: (f.get("ts1") or 0))
    meta = {"id": f"demo-{ticks}", "cell": "demo", "w": 125, "h": 125, "start_tick": 0, "end_tick": sim.world.tick, "zone": True, "bbox": None}
    return frames, meta, tokens["甲"][0], sim.world


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    demo_ticks = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--demo=")), 0)
    if demo_ticks:
        import asyncio
        raw, meta, cid, world = asyncio.run(demo(demo_ticks))
        frames = [for_viewer(f, cid, world) for f in raw]
        return write({"terrain": views.world_terrain(), "replay": meta, "frames": frames}, meta["id"], f"指揮官 {cid}")
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
    return write(data, rid, "GM" if gm else "指揮官 " + cid)


def write(data: dict, rid: str, who: str) -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False)
    frames = data["frames"]
    shots = sum(len(f["shots"]) for f in frames)
    mats = {}
    for f in frames:
        for s in f["shots"]:
            mats[s.get("mat")] = mats.get(s.get("mat"), 0) + 1
    print(f"{OUT}：{rid}，{len(frames)} 格影格、{shots} 發（彈著 {mats}）、{who} 視角，{os.path.getsize(OUT) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
