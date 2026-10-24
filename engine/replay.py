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
VISION_S = 1.0          # 視野多邊形多久算一次（戰鬥秒）

# 彈著材質（只給畫面用：彈著特效與聲音）：wall 牆／窗台／樓板、low 矮牆、rubble 瓦礫、ground 地面、body 人、air 沒打到東西
_MAT = {"wall": "wall", "wallside": "wall", "window": "wall", "floor": "wall", "lowwall": "low", "rubble": "rubble"}


def impact_material(shot: dict) -> str:
    kind = shot.get("kind")
    if kind == "cover":
        return _MAT.get(getattr(shot.get("ref"), "tag", ""), "wall")
    return {"ground": "ground", "body": "body"}.get(kind, "air")


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
        self.orders = lambda sid: None          # 命令狀態（engine/orders.py；模擬那邊接上）
        self._vision_t = -1e9

    def _intents(self, b) -> dict:
        return _intents_of(self, b)

    def before(self):
        b = self.eng.battle
        self._pre = {uid: list(u.pos) for uid, u in b.units.items()}
        self._log_mark = len(b.log)
        self._blast_mark = len(b.blasts)
        self._seq0 = getattr(b, "_seq", 0)
        self._spot0 = {}
        for sides, it in b.intel_groups():
            key = "blue" if any(s.startswith("blue") for s in sides) else sides[0]
            self._spot0.setdefault(key, set()).update(it.spotted)

    def after(self, world_tick: int, supports: list | None = None):
        b = self.eng.battle
        m = b.map
        units = []
        reasons = getattr(b, "reasons", {})
        subs = getattr(b, "path_sub", {})
        times = getattr(b, "path_t", {})
        starts = getattr(b, "path_st", {})
        faces = getattr(b, "path_f", {})
        acts = getattr(b, "acts", {})
        for uid, u in b.units.items():
            kind, gid, ref = self.eng.owner.get(uid, (None, None, None))
            name = getattr(ref, "name", None) or (ref.get("id") if isinstance(ref, dict) else uid)
            path = list(getattr(b, "paths", {}).get(uid, []))
            row = {"id": uid, "kind": kind, "group": gid, "name": name, "side": u.side,
                   "from": self._pre.get(uid, list(u.pos)), "path": [list(p) for p in path],
                   "pos": list(u.pos), "posture": u.posture, "facing": list(u.facing),
                   "down": u.down, "dead": u.dead, "exited": u.exited, "surrendered": u.surrendered,
                   "fired": u.fired, "suppressed": u.suppressed, "pinned": u.pinned,
                   "braced": bool(u.braced), "bracing": bool(u.bracing), "dazzled": bool(u.dazzled),
                   "stabilized": u.stabilized, "weapon": u.weapon, "supp": round(u.suppression)}
            if uid in reasons:
                row["why"] = reasons[uid]
            if uid in subs and path:
                row["psub"] = list(subs[uid])
            if uid in times and path:
                row["pt"] = [round(x, 3) for x in times[uid]]
                if uid in starts and len(starts[uid]) == len(path):
                    row["pst"] = [round(x, 3) for x in starts[uid]]
                if uid in faces and len(faces[uid]) == len(path):
                    row["pf"] = faces[uid]             # 走每一格時的面向
            a = acts.get(uid)
            if a is not None and a.get("path") and a.get("next_move") is not None and a.get("seg_t") is not None:
                # 正在走向下一格（還沒到）：下一格、起步與抵達時間，前端跨影格連續內插
                row["nx"] = list(a["path"][0])
                row["nst"] = round(a["seg_t"], 3)
                row["nt"] = round(a["next_move"], 3)
            units.append(row)
        shots = []
        for s in b.shots[b._shot_mark:]:
            shots.append({"shooter": s["shooter"], "from": [round(v / m.cell, 2) for v in s["from"][:2]],
                          "to": [round(v / m.cell, 2) for v in s["to"][:2]], "kind": s["kind"],
                          "hit": s["ref"][0] if s["kind"] == "body" else None, "aid": s.get("aid"),
                          "ow": bool(s.get("ow")), "mode": s.get("mode"), "ts": s.get("ts"), "mat": impact_material(s)})
            if s.get("r"):
                shots[-1]["r"] = s["r"]                  # 第幾發：前端依射速錯開（規則上同時結算）
        blasts = []
        for bl in b.blasts[self._blast_mark:]:
            blasts.append({"thrower": bl.get("thrower"), "item": bl.get("item", "grenades"), "ts": bl.get("ts"),
                           "at": [round(bl["at"][0] / m.cell, 2), round(bl["at"][1] / m.cell, 2)]})
        smokes = [{"x": round(s["x"] / m.cell, 2), "y": round(s["y"] / m.cell, 2), "r": round(s["r"] / m.cell, 2),
                   "density": round(b.smoke_density(s), 2), "by": s.get("by")} for s in b.active_smokes()]
        events = []
        for line in b.log[self._log_mark:]:
            text = line.split(" ", 1)[1] if " " in line else line
            if any(k in text for k in EVENT_SKIP):
                continue
            events.append(text)
        ev = [dict(e) for e in getattr(b, "ev", []) if e["seq"] > getattr(self, "_seq0", 0)]
        # 這一格期間看到過的（步首、步末，或步中移動時發現又失去的）
        mid: dict[str, set] = {}
        for e in ev:
            if e["type"] == "spot":
                side = str((e.get("data") or {}).get("side", ""))
                mid.setdefault("blue" if side.startswith("blue") else side, set()).add(e["target"])
        intel = {}
        for sides, it in b.intel_groups():
            key = "blue" if any(s.startswith("blue") for s in sides) else sides[0]
            if key in intel:
                continue
            intel[key] = {"spotted": sorted(it.spotted),
                          "seen": sorted(set(it.spotted) | getattr(self, "_spot0", {}).get(key, set()) | mid.get(key, set())),
                          "last": {uid: [list(n), b.tick - t] for uid, (n, t) in it.last_known.items()}}
        # ts0～ts1：這一格影格涵蓋的戰鬥時間（秒）；路徑、子彈、爆炸、事件都帶自己的時間，前端照實播放
        frame = {"t": b.tick, "world_tick": world_tick, "step_seconds": self.eng.step_seconds,
                 "ts0": round(b.now - b.dt, 3), "ts1": round(b.now, 3), "units": units, "shots": shots, "blasts": blasts,
                 "smokes": smokes, "events": events, "intel": intel, "supports": supports or [], "ev": ev,
                 "intents": self._intents(b)}
        # 我方（小隊、殘部）的視野多邊形，每秒一次（engine/vision.py；只給畫面用）
        if b.now - self._vision_t >= VISION_S - 1e-6:
            self._vision_t = b.now
            from engine import vision
            frame["vision"] = {uid: vision.polygon(b, u) for uid, u in sorted(b.units.items())
                               if self.eng.owner.get(uid, (None,))[0] in ("s", "g") and u.alive and not u.down and not u.exited}
        self.frames.append(frame)
        return frame


def _intents_of(rec, b) -> dict:
    """每個小隊目前的戰術意圖（畫面用）：種類、目標、包抄方向、集火點、破門房間、撤退方向；
    這一刻誰在移動、誰在掩護（射擊、警戒、架槍）；命令狀態。敵方的意圖不記。"""
    eng = rec.eng
    world = getattr(eng, "world", None)
    out = {}
    for key, it in sorted(getattr(eng, "intents", {}).items()):
        if not key.startswith("s:") or world is None:
            continue
        sid = key[2:]
        sq = world.squads.get(sid)
        if sq is None or sq.status != "active":
            continue
        ids = sorted(uid for uid, o in eng.owner.items() if o[0] == "s" and o[1] == sid and uid in b.units and b.units[uid].alive)
        if not ids:
            continue
        movers, cover = [], []
        for uid in ids:
            a = b.acts.get(uid)
            mode = a["plan"].mode if a else None
            if mode in ("move", "drag") and a.get("path"):
                movers.append(uid)
            elif mode in ("shoot", "overwatch", "brace"):
                cover.append(uid)
        out[sid] = {"callsign": sq.callsign, "kind": it.kind, "goal": list(it.goal[:2]) if it.goal else None,
                    "side": it.side, "focus": list(it.focus[:2]) if it.focus else None, "room": it.room,
                    "toward": list(it.toward) if it.toward else None, "movers": movers, "cover": cover if movers else [],
                    "order": rec.orders(sid)}
    return out


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
    visible = friendly | spotted | set(intel.get("seen", []))
    for s in frame["shots"]:
        if s.get("hit") and s["hit"] not in visible:
            # 打中看不到的人：命中的對象與「打在人身上」都會洩漏他的位置 → 只說彈著不明
            s = dict(s, hit=None, kind="unknown", mat="unk")
        if s["shooter"] in friendly or s["shooter"] in spotted:
            shots.append(s)
        else:
            fx, fy = s["from"]
            tx, ty = s["to"]
            shots.append(dict(s, shooter=None, **{"from": [fx + (tx - fx) * 0.66, fy + (ty - fy) * 0.66]},
                              unseen=True))
    events = [e for e in frame["events"] if not _mentions_hidden(e, frame, friendly, spotted)]
    units = [u if u["side"] != "enemy" else {k: v for k, v in u.items() if k not in ("why", "supp")}
             for u in units]
    return {"t": frame["t"], "world_tick": frame["world_tick"], "ts0": frame.get("ts0"), "ts1": frame.get("ts1"),
            "units": units, "ghosts": ghosts, "shots": shots,
            "blasts": frame["blasts"], "smokes": frame["smokes"], "events": [_pretty(e, frame, friendly, world)
                                                                            for e in events],
            "supports": [s for s in frame.get("supports", []) if s.get("commander") == cid or s.get("kind") == "smoke"],
            # 視野只給我方與友軍；意圖只給我方與友軍小隊，命令狀態只給下令的指揮官
            **({"vision": {k: v for k, v in frame["vision"].items() if k in friendly}} if frame.get("vision") is not None else {}),
            "intents": {sid: (r if _commander_of(world, sid) == cid else {k: v for k, v in r.items() if k != "order"})
                        for sid, r in (frame.get("intents") or {}).items()},
            "ev": filter_events(frame.get("ev", []), friendly, spotted | set(intel.get("seen", [])), cid,
                                set(intel.get("last", {})))}


def _commander_of(world, sid: str):
    sq = world.squads.get(sid) if world is not None else None
    return sq.commander if sq is not None else None


OWN_SIDE = "blue"
ACTOR_ONLY = ("posture", "brace", "exit", "drag", "aid", "transfer", "surrender")


def filter_events(evs: list, friendly: set, spotted: set, cid: str, tracked: set | None = None) -> list:
    """結構化事件的玩家視圖（伺服器端過濾，前端拿不到真相）：
    - 看不到的敵人：ID 一律拿掉；射擊只留彈道後段（看得到彈著、看不到從哪來），不給武器。
    - 玩家不知道的命中、倒地、陣亡、急救、投降不送。
    - 發現／聽到／失去目標只送我方（blue 情報組）的；聲音只給情報裡記下的大概位置（引擎寫入的誤差位置），
      目標 ID 只在已有最後已知位置（ghost 本來就帶 ID）時保留。
    - 壓制只送我方與看得到的人；來源只留看得到的。
    - 支援只送自己叫的（煙幕大家都看得到）。"""
    seen = set(spotted) | {e["target"] for e in evs if e["type"] == "spot"
                           and str((e.get("data") or {}).get("side", "")).startswith(OWN_SIDE)}
    known = friendly | seen

    def vis(uid):
        return uid is None or uid in known

    def mask(uid):
        return uid if uid in known else None

    out = []
    for e in evs:
        k = e["type"]
        d = dict(e.get("data") or {})
        a, t = e.get("actor"), e.get("target")
        if k == "shot":
            if not vis(a):
                to = d.get("to") or []
                fr = d.get("frm")
                if fr and to:
                    d["frm"] = [fr[0] + (to[0][0] - fr[0]) * 0.66, fr[1] + (to[0][1] - fr[1]) * 0.66]
                d.pop("weapon", None)
                d["unseen"] = True
            if t is not None and t not in known and "body" in (d.get("impact") or []):
                d["impact"] = ["unknown" if x == "body" else x for x in d["impact"]]   # 打中看不到的人會洩漏他的位置
            out.append(dict(e, actor=mask(a), target=mask(t), data=d))
        elif k in ("hit", "down", "kia", "dazzled"):
            if t not in known:
                continue
            out.append(dict(e, actor=mask(a), data=d))
        elif k == "suppress":
            if t not in known:
                continue
            if "sources" in d:
                d["sources"] = [s for s in d["sources"] if s in known]
            out.append(dict(e, actor=mask(a), data=d))
        elif k in ("spot", "hear", "lost"):
            if not str(d.get("side", "")).startswith(OWN_SIDE):
                continue
            d.pop("side", None)
            if k == "spot" and t not in known:
                continue
            out.append(dict(e, target=t if (k == "lost" or t in (tracked or ())) else mask(t), data=d))
        elif k == "aim":                        # 舉槍瞄準：看不到的人不送；瞄準看不到的人不給位置
            if a not in known:
                continue
            if t is not None and t not in known:
                d.pop("at", None)
            out.append(dict(e, target=mask(t), data=d))
        elif k in ("throw", "blast"):
            if not vis(a):
                d.pop("frm", None)
                d.pop("aimed", None)
            out.append(dict(e, actor=mask(a), data=d))
        elif k == "melee":
            if a not in known and t not in known:
                continue
            w = d.get("winner")
            if w is not None and w not in known:
                d["winner"] = None
            out.append(dict(e, actor=mask(a), target=mask(t), data=d))
        elif k == "flanked":
            if t not in friendly:
                continue
            out.append(dict(e, actor=mask(a), data={}))
        elif k == "hold_fire":
            if a not in friendly:
                continue
            out.append(dict(e, target=mask(t)))
        elif k == "support":
            if d.get("commander") != cid and d.get("what") != "smoke":
                continue
            out.append(e)
        elif k in ACTOR_ONLY:
            if a not in known:
                continue
            out.append(dict(e, target=mask(t)))
    return out


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


def _cell(x, y) -> str | None:
    from engine.tactical import worldmap
    return worldmap.cell_of(x, y)


def crop_frame(frame: dict, cells: set[str]) -> dict:
    """單一 Battle 的影格只留 cells 這一帶（某位指揮官的活動區域、或一場交火的範圍）：
    人（起點或終點在範圍內）、子彈（起點或落點在範圍內）、爆炸、煙霧、支援、事件（行動者或目標留著的，或位置在範圍內）。"""
    units = [u for u in frame["units"] if _cell(u["pos"][0], u["pos"][1]) in cells
             or _cell(u["from"][0], u["from"][1]) in cells]
    keep = {u["id"] for u in units}
    others = {u["id"] for u in frame["units"]} - keep

    def at_in(v):
        return v is not None and len(v) >= 2 and _cell(v[0], v[1]) in cells

    def ev_ok(e):
        a, t = e.get("actor"), e.get("target")
        if a in keep or t in keep:
            return True
        if a in others or t in others:
            return False
        d = e.get("data") or {}
        pos = d.get("at") or d.get("frm")
        return pos is None or at_in(pos)

    shots = [x for x in frame["shots"] if at_in(x["from"]) or at_in(x["to"])]
    texts = [t for t in frame["events"] if any(k in t for k in keep) or not any(o in t for o in others)]
    return dict(frame, units=units, shots=shots, blasts=[b for b in frame["blasts"] if at_in(b["at"])],
                smokes=[m for m in frame["smokes"] if _cell(m["x"], m["y"]) in cells],
                supports=[x for x in frame.get("supports", []) if _cell(x.get("x", -1), x.get("y", -1)) in cells],
                events=texts, ev=[e for e in frame.get("ev", []) if ev_ok(e)])


def fight_areas(frames: list) -> list[set[str]]:
    """這段影格裡有交火的格子（開火、爆炸的地方），依相鄰（兩格內）分群；每群往外擴一格就是一場交火的範圍。"""
    from engine import scenario
    from engine.tactical import worldmap
    hot: set[str] = set()
    for f in frames:
        for x in f["shots"]:                 # 只看開火的地方（落點可能飛到 80 m 外，不算交火範圍）
            c = _cell(x["from"][0], x["from"][1])
            if c:
                hot.add(c)
        for b in f["blasts"]:
            c = _cell(b["at"][0], b["at"][1])
            if c:
                hot.add(c)
        for u in f["units"]:
            if u.get("fired"):
                c = _cell(u["pos"][0], u["pos"][1])
                if c:
                    hot.add(c)
    groups: list[set[str]] = []
    for c in sorted(hot):
        near = [g for g in groups if any(abs(scenario.ROWS.index(c[0]) - scenario.ROWS.index(o[0])) <= 2
                                         and abs(scenario.COLS.index(c[1:]) - scenario.COLS.index(o[1:])) <= 2
                                         for o in g)]
        merged = {c}.union(*near) if near else {c}
        groups = [g for g in groups if g not in near] + [merged]
    return [set().union(*(worldmap.zone_cells(c, 1) for c in g)) for g in sorted(groups, key=lambda g: sorted(g))]


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

    def save_fights(self, eng, rec: FrameRecorder, world) -> list[dict]:
        """連續地圖（單一 Battle）：依交火的地方切成幾段，各存一份（只留那一帶）。"""
        out = []
        for i, area in enumerate(fight_areas(rec.frames)):
            entry = self.save(eng, rec, world, frames=[crop_frame(f, area) for f in rec.frames], cells=area, part=i)
            if entry:
                out.append(entry)
        return out

    def save(self, eng, rec: FrameRecorder, world, frames: list | None = None, cells: set | None = None,
             part: int = 0) -> dict | None:
        frames = rec.frames if frames is None else frames
        if not frames:
            return None
        from engine.views import terrain_grid
        m = eng.battle.map
        zone = getattr(eng, "zone", False)
        if zone:
            frames = trim_quiet(frames)
            if not frames:
                return None
        area = sorted(cells if cells is not None else (eng.cells if zone else {eng.cell}))
        cell = "、".join(area) if zone else eng.cell
        rid = f"{(area[len(area) // 2] if zone else eng.cell)}-{world.tick}-{int(rec.started)}" + (f"-{part}" if part else "")
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
