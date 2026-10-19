"""戰術影格與回放。

每個戰術 tick 記一份影格：每個個體的起點、逐步路徑、終點姿勢／朝向／狀態；子彈線、爆炸、閃光、煙霧、事件。
前端用它把 10 秒內的動作平滑播出來；交戰結束時整場存成回放檔。

影格裡記下「真相」，給玩家時用 `for_viewer` 依該格我方情報過濾（看不到的敵人不畫、只給最後位置）。
"""
from __future__ import annotations

import json
import os
import time

REPLAY_LIMIT = 20
EVENT_SKIP = ("發現", "察覺")


class FrameRecorder:
    """掛在 Engagement 上：step 前抓起點，step 後產生影格。"""

    def __init__(self, eng):
        self.eng = eng
        self.frames: list[dict] = []
        self.started = time.time()
        self._pre: dict = {}
        self._log_mark = 0
        self._blast_mark = 0
        self.supports = lambda: []

    def before(self):
        b = self.eng.battle
        self._pre = {uid: list(u.pos) for uid, u in b.units.items()}
        self._log_mark = len(b.log)
        self._blast_mark = len(b.blasts)

    def after(self, world_tick: int, supports: list | None = None):
        b = self.eng.battle
        m = b.map
        units = []
        for uid, u in b.units.items():
            kind, gid, ref = self.eng.owner.get(uid, (None, None, None))
            name = getattr(ref, "name", None) or (ref.get("id") if isinstance(ref, dict) else uid)
            path = list(getattr(b, "paths", {}).get(uid, []))
            units.append({"id": uid, "kind": kind, "group": gid, "name": name, "side": u.side,
                          "from": self._pre.get(uid, list(u.pos)), "path": [list(p) for p in path],
                          "pos": list(u.pos), "posture": u.posture, "facing": list(u.facing),
                          "down": u.down, "dead": u.dead, "exited": u.exited, "surrendered": u.surrendered,
                          "fired": u.fired, "suppressed": u.suppressed, "pinned": u.pinned,
                          "braced": bool(u.braced), "dazzled": bool(u.dazzled), "stabilized": u.stabilized,
                          "weapon": u.weapon})
        shots = []
        for s in b.shots[b._shot_mark:]:
            shots.append({"shooter": s["shooter"], "from": [round(v / m.cell, 2) for v in s["from"][:2]],
                          "to": [round(v / m.cell, 2) for v in s["to"][:2]], "kind": s["kind"],
                          "hit": s["ref"][0] if s["kind"] == "body" else None})
        blasts = []
        for bl in b.blasts[self._blast_mark:]:
            blasts.append({"thrower": bl.get("thrower"), "item": bl.get("item", "grenades"),
                           "at": [round(bl["at"][0] / m.cell, 2), round(bl["at"][1] / m.cell, 2)]})
        smokes = [{"x": round(s["x"] / m.cell, 2), "y": round(s["y"] / m.cell, 2), "r": round(s["r"] / m.cell, 2),
                   "density": round(b.smoke_density(s), 2), "by": s.get("by")} for s in b.active_smokes()]
        events = []
        for line in b.log[self._log_mark:]:
            text = line.split(" ", 1)[1] if " " in line else line
            if any(k in text for k in EVENT_SKIP):
                continue
            events.append(text)
        intel = {}
        for sides, it in b.intel_groups():
            key = "blue" if any(s.startswith("blue") for s in sides) else sides[0]
            if key in intel:
                continue
            intel[key] = {"spotted": sorted(it.spotted),
                          "last": {uid: [list(n), b.tick - t] for uid, (n, t) in it.last_known.items()}}
        frame = {"t": b.tick, "world_tick": world_tick, "step_seconds": self.eng.step_seconds, "units": units, "shots": shots, "blasts": blasts,
                 "smokes": smokes, "events": events, "intel": intel, "supports": supports or []}
        self.frames.append(frame)
        return frame


def _shooter_visible(uid: str, frame: dict, intel: dict, own_ids: set) -> bool:
    return uid in own_ids or uid in intel.get("spotted", [])


def for_viewer(frame: dict, cid: str, world, gm: bool = False) -> dict:
    """依我方（同格共享）情報過濾影格：我方與友軍全畫；敵人只畫看得到的，看不到的給最後位置與幾秒前。
    看不到的射手：子彈線只畫最後 1/3（看得到彈著、看不到從哪來）。"""
    if gm:
        return dict(frame, viewer="gm")
    intel = frame["intel"].get("blue", {"spotted": [], "last": {}})
    spotted = set(intel.get("spotted", []))
    units, ghosts = [], []
    friendly = set()
    for u in frame["units"]:
        if u["kind"] in ("s", "g"):
            sq = world.squads.get(u["group"]) if u["kind"] == "s" else None
            u = dict(u, side="own" if sq is not None and sq.commander == cid else ("ally" if u["kind"] == "s"
                                                                                  else "remnant"))
            friendly.add(u["id"])
            units.append(u)
        elif u["id"] in spotted and not u["dead"]:
            units.append(dict(u, side="enemy", name="敵兵"))
        elif u["id"] in intel.get("last", {}) and not u["dead"]:
            node, age = intel["last"][u["id"]]
            ghosts.append({"id": u["id"], "pos": node, "age": int(age * frame.get("step_seconds", 10))})
    shots = []
    for s in frame["shots"]:
        if s["shooter"] in friendly or s["shooter"] in spotted:
            shots.append(s)
        else:
            fx, fy = s["from"]
            tx, ty = s["to"]
            shots.append(dict(s, shooter=None, **{"from": [fx + (tx - fx) * 0.66, fy + (ty - fy) * 0.66]},
                              unseen=True))
    events = [e for e in frame["events"] if not _mentions_hidden(e, frame, friendly, spotted)]
    return {"t": frame["t"], "world_tick": frame["world_tick"], "units": units, "ghosts": ghosts, "shots": shots,
            "blasts": frame["blasts"], "smokes": frame["smokes"], "events": [_pretty(e, frame, friendly, world)
                                                                            for e in events],
            "supports": [s for s in frame.get("supports", []) if s.get("commander") == cid or s.get("kind") == "smoke"]}


def _mentions_hidden(text: str, frame: dict, friendly: set, spotted: set) -> bool:
    for u in frame["units"]:
        if u["id"] in text and u["id"] not in friendly and u["id"] not in spotted:
            return text.startswith(u["id"])
    return False


def _pretty(text: str, frame: dict, friendly: set, world) -> str:
    """把個體 id 換成名字（我方用代號，敵人用「敵兵」）。"""
    for u in sorted(frame["units"], key=lambda x: -len(x["id"])):
        if u["id"] in text:
            text = text.replace(u["id"], u["name"] if u["id"] in friendly else "敵兵")
    for side in ("blue", "red", "green"):
        text = text.replace(f"{side} 撤出", "撤出")
    from engine.views import PART_LABELS
    for k, v in sorted(PART_LABELS.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(f" {k}", f" {v}")
    return text


def frame_hot(f: dict) -> bool:
    return bool(f["shots"] or f["blasts"] or any(u.get("fired") for u in f["units"]))


def trim_quiet(frames: list, pad: int = 4) -> list:
    """連續地圖會一直錄：只留第一次交火前 pad 步到最後一次交火後 pad 步。沒有交火 → []。"""
    idx = [i for i, f in enumerate(frames) if frame_hot(f)]
    if not idx:
        return []
    return frames[max(0, idx[0] - pad): idx[-1] + pad + 1]


def _bbox(frames: list, pad: int = 8) -> list:
    xs, ys = [], []
    for f in frames:
        for u in f["units"]:
            if u["kind"] in ("s", "g") or u.get("fired"):
                xs.append(u["pos"][0])
                ys.append(u["pos"][1])
    if not xs:
        return [0, 0, 125, 125]
    return [max(0, min(xs) - pad), max(0, min(ys) - pad), max(xs) + pad, max(ys) + pad]


class ReplayStore:
    """回放檔：data/replays/<id>.json；保留最近 REPLAY_LIMIT 場。索引記在 index.json。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.index_path = os.path.join(root, "index.json")
        self.index: list[dict] = []
        if os.path.exists(self.index_path):
            with open(self.index_path, encoding="utf8") as f:
                self.index = json.load(f)

    def save(self, eng, rec: FrameRecorder, world) -> dict | None:
        if not rec.frames:
            return None
        from engine.views import terrain_grid
        m = eng.battle.map
        zone = getattr(eng, "zone", False)
        frames = rec.frames
        if zone:
            frames = trim_quiet(frames)
            if not frames:
                return None
        cell = eng.label() if zone else eng.cell
        rid = f"{(sorted(eng.cells)[len(eng.cells) // 2] if zone else eng.cell)}-{world.tick}-{int(rec.started)}"
        commanders = sorted({s.commander for f in frames for u in f["units"] if u["kind"] == "s"
                             for s in [world.squads.get(u["group"])] if s is not None and s.commander})
        squads = sorted({u["group"] for f in frames for u in f["units"] if u["kind"] == "s"})
        data = {"id": rid, "cell": cell, "w": m.w, "h": m.h, "zone": zone,
                "terrain": None if zone else terrain_grid(m),
                "rooms": [] if zone else [{"id": r["id"], "rect": list(r["rect"]), "doors": [list(d) for d in r["doors"]]}
                                          for r in m.rooms],
                "start_tick": frames[0]["world_tick"] if zone else eng.started, "end_tick": world.tick,
                "frames": frames, "commanders": commanders, "squads": squads,
                "bbox": _bbox(frames) if zone else None}
        with open(os.path.join(self.root, f"{rid}.json"), "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False)
        entry = {"id": rid, "cell": cell, "start_tick": data["start_tick"], "end_tick": world.tick,
                 "frames": len(frames), "commanders": commanders, "squads": squads, "zone": zone}
        self.index.append(entry)
        while len(self.index) > REPLAY_LIMIT:
            old = self.index.pop(0)
            try:
                os.remove(os.path.join(self.root, f"{old['id']}.json"))
            except OSError:
                pass
        with open(self.index_path, "w", encoding="utf8") as f:
            json.dump(self.index, f, ensure_ascii=False)
        return entry

    def list_for(self, cid: str, gm: bool = False) -> list[dict]:
        return [e for e in reversed(self.index) if gm or cid in e["commanders"]]

    def load(self, rid: str) -> dict | None:
        if not any(e["id"] == rid for e in self.index):
            return None
        path = os.path.join(self.root, f"{rid}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf8") as f:
            return json.load(f)
