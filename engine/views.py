"""網頁版：每位指揮官看得到的畫面資料（JSON 友善的 dict）。

原則：
- 自己的小隊：通訊正常時即時（數據鏈）；失聯時停在最後一次連線的畫面，標「T幾的資料」。
- 敵情：作戰層照指揮官情報表（可能過時或錯）；戰術層照該格我方共享情報（看得到的即時，看不到的畫最後位置）。
- 不給性格數值與人格值、服從機率。
"""
from __future__ import annotations

from engine import reserve, scenario
from engine.models import Member, Squad
from engine.tactical.units import WEAPONS

WEAPON_LABELS = {"rifle": "步槍", "lmg": "機槍", "dmr": "射手步槍", "shotgun": "霰彈槍"}
PART_LABELS = {"head": "頭部", "torso": "軀幹", "larm": "左臂", "rarm": "右臂", "lleg": "左腿", "rleg": "右腿"}
POSTURE_LABELS = {"stand": "站", "crouch": "蹲", "prone": "臥"}
AUTONOMY_LABELS = {"standby": "待命", "defend": "自衛", "free": "自由"}
NEW_RECRUIT_TICKS = 60


def prof_level(p: float) -> str:
    if p < 70:
        return "新手"
    if p < 110:
        return "熟練"
    if p < 150:
        return "老練"
    return "精英"


def weapon_info(weapon: str) -> dict:
    w = WEAPONS.get(weapon, WEAPONS["rifle"])
    return {"key": weapon, "label": WEAPON_LABELS.get(weapon, weapon), "range": int(w["range"]),
            "accuracy": round(0.02 / w["sigma"], 2), "burst": w["burst"], "cap": w["ammo"],
            "caliber": w["caliber"]}


def suppression_label(v: float) -> str:
    if v >= 80:
        return "被壓制"
    if v >= 50:
        return "受壓制"
    if v >= 20:
        return "緊張"
    return "無"


def member_view(m: Member, squad: Squad, tick: int, unit=None, cell_w: int = 25) -> dict:
    """隊員面板。unit＝交戰中的戰術層個體（有的話用它的即時數值）。"""
    src = unit if unit is not None else m
    cap = WEAPONS.get(m.weapon, WEAPONS["rifle"])["ammo"]
    ammo = src.ammo if (src.ammo is not None and src.ammo >= 0) else cap
    damage = dict(src.damage or {})
    wounds = [PART_LABELS.get(k, k) for k, v in damage.items() if v]
    prof = dict(m.prof or {})
    cur = float((unit.prof if unit is not None else prof).get(m.weapon, 100.0))
    others = {k: {"value": round(v), "level": prof_level(v), "label": WEAPON_LABELS.get(k, k)}
              for k, v in prof.items() if k != m.weapon}
    lead = squad.speaker_member()
    down = bool(src.down) and not getattr(src, "dead", False)
    pos = list(m.tpos) if m.tpos else (list(src.pos) if unit is not None else None)
    state = "陣亡" if m.lost else ("倒地" if down else "可作戰")
    out = {
        "name": m.name,
        "leading": lead is not None and lead.name == m.name,
        "rookie": bool(m.joined and tick - m.joined < NEW_RECRUIT_TICKS),
        "weapon": weapon_info(m.weapon),
        "ammo": int(ammo), "ammo_cap": cap,
        "grenades": src.grenades, "smokes": src.smokes, "flashes": src.flashes,
        "hp": 0 if m.lost else int(m.hp),
        "wounds": wounds,
        "state": state,
        "down": down,
        "stabilized": bool(src.stabilized),
        "suppression": suppression_label(float(src.suppression or 0)),
        "prof": {"value": round(cur), "level": prof_level(cur)},
        "prof_other": others,
        "battles": m.battles,
        "led_before": m.led_before,
        "posture": POSTURE_LABELS.get(src.posture, src.posture),
        "tpos": pos,
    }
    if unit is not None:
        out["stats"] = dict(unit.stats)
        out["braced"] = bool(unit.braced)
        out["dazzled"] = bool(unit.dazzled)
        out["facing"] = list(unit.facing)
    return out


def _squad_gpos(s: Squad):
    pts = [m.gpos for m in s.members if not m.lost and m.gpos]
    if not pts:
        return None
    return [round(sum(p[0] for p in pts) / len(pts), 1), round(sum(p[1] for p in pts) / len(pts), 1)]


def squad_live(sim, s: Squad) -> dict:
    """數據鏈畫面：通訊正常時即時。"""
    eng = sim.engagements.get(s.pos)
    units = {}
    if eng is not None:
        for uid, (kind, gid, ref) in eng.owner.items():
            if kind == "s" and gid == s.id and uid in eng.battle.units:
                units[ref.name] = eng.battle.units[uid]
    t = sim.world.tick
    members = [member_view(m, s, t, units.get(m.name)) for m in s.members if not m.lost]
    a = s.action
    return {
        "id": s.id, "callsign": s.callsign, "card": s.card_id,
        "pos": s.pos, "pos_name": scenario.CELLS[s.pos]["name"],
        "status": s.status, "linked": s.linked,
        "alive": len(s.alive_members()), "total": len([m for m in s.members if not m.lost]),
        "away": [m.name for m in getattr(sim.world, "resting", []) if m.home_squad == s.id] +
                [m.name for a in sim.world.squads.values() if a.ai is not None for m in a.members
                 if m.home_squad == s.id],
        "medevac": any(j["squad"] == s.id for j in getattr(sim.world, "medevac", [])),
        "max": s.max_size,
        "ammo": s.ammo, "morale": s.morale, "morale_label": sim._morale_word(s.morale),
        "action": {"type": a.type, "text": sim._action_text(s), "target": a.target, "path": list(a.path),
                   "progress": a.progress, "duration": a.duration, "ordered": a.ordered, "auto": a.auto,
                   "reason": a.reason, "mark": a.mark, "mark_kind": a.mark_kind},
        "autonomy": s.autonomy, "autonomy_label": AUTONOMY_LABELS.get(s.autonomy, s.autonomy),
        "proposal": s.proposal,
        "rest": sim.rest_progress(s),
        "in_combat": sim.in_engagement(s),
        "contact": bool(s.contact),
        "leader": s.acting_leader(),
        "gpos": _squad_gpos(s),
        "members": members,
        "tick": t,
    }


def squad_view(sim, s: Squad) -> dict:
    """失聯 → 用最後一次連線時的快照（並標出時間）。"""
    store = sim.live_cache
    if s.linked or s.id not in store:
        v = squad_live(sim, s)
        if s.linked:
            store[s.id] = v
        v = dict(v)
        v["stale"] = False
        return v
    v = dict(store[s.id])
    v["stale"] = True
    v["linked"] = False
    return v


def op_map(sim, cid: str) -> dict:
    """作戰圖：地形、我方小隊（數據鏈）、情報表上的敵情、任務、空投、他人小隊（同陣營，位置公開）。"""
    w = sim.world
    c = w.commanders.get(cid)
    cells = []
    for r in scenario.ROWS:
        for col in scenario.COLS:
            key = r + col
            info = scenario.CELLS[key]
            cells.append({"id": key, "row": scenario.ROWS.index(r), "col": scenario.COLS.index(col),
                          "name": info["name"], "terrain": info.get("terrain", ""),
                          "cover": scenario.cover_label(info.get("cover", 0)),
                          "base": key == scenario.BASE, "enemy_base": key in scenario.ENEMY_BASES})
    enemies = []
    if c is not None:
        for cell, e in c.intel["enemies"].items():
            enemies.append({"cell": cell, "est": e["est"], "tick": e["tick"], "suspected": bool(e.get("suspected")),
                            "age": w.tick - e["tick"]})
    missions = [{"cell": m["cell"], "title": m["title"], "line": m["line"]} for m in (c.missions if c else [])]
    drops = [{"cell": o["cell"], "arrive": o["arrive"]} for o in w.objects.values()
             if o["type"] == "airdrop" and o.get("commander") == cid]
    from engine.engagement import label_of
    allies = [{"label": label_of(w, s), "pos": s.pos, "alive": len(s.alive_members())}
              for s in w.squads.values() if s.commander != cid and s.status == "active" and s.linked]
    combat = sorted({s.pos for s in w.squads.values()
                     if s.commander == cid and s.status == "active" and sim.in_engagement(s)})
    return {"rows": len(scenario.ROWS), "cols": len(scenario.COLS), "row_names": scenario.ROWS,
            "col_names": scenario.COLS, "cells": cells, "enemies": enemies, "missions": missions,
            "drops": drops, "allies": allies, "combat": combat, "base": scenario.BASE}


def missions_view(sim, cid: str) -> dict:
    c = sim.world.commanders.get(cid)
    if c is None:
        return {"active": [], "history": []}
    now = sim.world.tick
    active = []
    for m in c.missions:
        item = {"id": m["id"], "title": m["title"], "line": m["line"], "brief": m["brief"], "cell": m["cell"],
                "cell_name": scenario.cell_desc(m["cell"]), "type": m["type"],
                "left": None if m.get("deadline") is None else max(0, m["deadline"] - now)}
        if m["type"] == "hold":
            item["progress"] = [m.get("progress", 0), m["hold_ticks"]]
        if m["type"] == "remnant":
            item["remnant"] = sim.remnant_status_text(m)
        active.append(item)
    return {"active": active, "history": list(reversed(c.history[-8:]))}


def summary(sim, cid: str) -> list[str]:
    """規則產生、每 tick 更新的現況（不經 LLM）。"""
    w = sim.world
    lines = []
    for s in w.squads_of(cid):
        if s.status != "active":
            continue
        if not s.linked:
            since = w.tick - (s.unlinked_since or w.tick)
            lines.append(f"{s.callsign}：📵 失聯 {sim.seconds(since)} 秒（最後在 {s.pos}）")
            continue
        state = "交戰中" if sim.in_engagement(s) else sim._action_text(s)
        hurt = [m.name for m in s.members if not m.lost and (m.down or m.hp < 60)]
        bits = [f"{s.callsign}：{scenario.cell_desc(s.pos)}，{state}",
                f"{len(s.alive_members())}/{len([m for m in s.members if not m.lost])} 人",
                f"彈藥 {sim._ammo_word(s.ammo)}"]
        if hurt:
            bits.append(f"傷員 {'、'.join(hurt)}")
        if s.action.auto:
            bits.append("（自主行動）")
        if s.proposal:
            bits.append(f"提議：{s.proposal.get('text', '')}")
        lines.append("，".join(bits))
    c = w.commanders.get(cid)
    if c and c.missions:
        lines.append("任務：" + "｜".join(m["title"] for m in c.missions))
    return lines


def tactical_view(sim, cid: str, cell: str, gm: bool = False) -> dict | None:
    """戰術畫面（25×25）：地形、我方與同格友軍即時位置、敵人（看得到的即時，看不到的最後位置＋幾秒前）。
    沒有自己的小隊在該格（且非 GM）→ None。"""
    w = sim.world
    mine = [s for s in w.squads.values() if s.commander == cid and s.pos == cell and s.status == "active"]
    if not mine and not gm:
        return None
    if not gm and not any(s.linked for s in mine):
        return None
    from engine.tactical import worldmap
    m = worldmap.tmap(cell)
    eng = sim.engagements.get(cell)
    data = {"cell": cell, "name": scenario.cell_desc(cell), "w": m.w, "h": m.h, "terrain": terrain_grid(m),
            "rooms": [{"id": r["id"], "rect": list(r["rect"]), "doors": [list(d) for d in r["doors"]]}
                      for r in m.rooms],
            "units": [], "ghosts": [], "smokes": [], "tick": eng.battle.tick if eng else 0, "combat": eng is not None,
            "supports": sim.support_view(cell, cid), "marks": []}
    for s in mine:
        if s.action.mark:
            data["marks"].append({"squad": s.id, "pos": list(s.action.mark), "kind": s.action.mark_kind})
    if eng is None:
        for s in w.squads.values():
            if s.pos != cell or s.status != "active":
                continue
            for mem in s.members:
                if mem.lost or not mem.tpos:
                    continue
                data["units"].append({"id": f"s:{s.id}:{mem.name}", "side": "own" if s.commander == cid else "ally",
                                      "squad": s.id, "name": mem.name, "pos": list(mem.tpos),
                                      "posture": mem.posture, "facing": list(mem.facing), "down": mem.down,
                                      "weapon": mem.weapon})
        return data
    b = eng.battle
    intel = b.intel.get("blue")
    for uid, u in b.units.items():
        kind, gid, ref = eng.owner.get(uid, (None, None, None))
        if u.dead or u.exited:
            continue
        if kind in ("s", "g"):
            sq = w.squads.get(gid) if kind == "s" else None
            side = "own" if sq is not None and sq.commander == cid else ("ally" if kind == "s" else "remnant")
            data["units"].append(unit_dict(uid, u, side, gid, ref.name if ref else uid))
        else:
            if gm or (intel is not None and uid in intel.spotted):
                data["units"].append(unit_dict(uid, u, "enemy", gid, "敵兵"))
            elif intel is not None and uid in intel.last_known:
                node, t = intel.last_known[uid]
                data["ghosts"].append({"id": uid, "pos": list(node), "age": int((b.tick - t) * sim.step_seconds)})
    for sm in b.active_smokes():
        data["smokes"].append({"x": sm["x"] / m.cell, "y": sm["y"] / m.cell, "r": sm["r"] / m.cell,
                               "density": b.smoke_density(sm)})
    return data


def world_terrain() -> dict:
    """連續地圖：整張全圖地形（只需要載一次）。座標＝全圖小格。"""
    from engine.tactical import worldmap
    g = worldmap.global_map()
    return {"w": g.w, "h": g.h, "cell": worldmap.SIZE, "terrain": terrain_grid(g),
            "rows": scenario.ROWS, "cols": scenario.COLS,
            "names": {c: scenario.CELLS[c]["name"] for c in scenario.CELLS},
            "rooms": [{"id": r["id"], "rect": list(r["rect"]), "doors": [list(d) for d in r["doors"]]}
                      for r in g.rooms]}


def zone_view(sim, cid: str, gm: bool = False) -> dict:
    """連續地圖：指揮官看得到的所有活動區域（自己小隊所在的 Zone；GM 全部）。全圖座標。
    區域外：只給作戰層情報敵情（格子）。失聯小隊所在的區域 → 不給即時內容（停在最後一刻由前端保留）。"""
    from engine.tactical import worldmap
    w = sim.world
    c = w.commanders.get(cid)
    out = {"zones": [], "intel": [], "tick": w.tick, "step_seconds": sim.step_seconds}
    seen = set()
    for z in sim.zones:
        mine = [s for s in w.squads.values() if s.commander == cid and s.status == "active" and s.pos in z.cells]
        if not gm and not any(s.linked for s in mine):
            continue
        if id(z) in seen:
            continue
        seen.add(id(z))
        b = z.battle
        intel = b.intel.get("blue")
        zd = {"cells": sorted(z.cells), "hot": sorted(z.cells_hot()), "units": [], "ghosts": [], "smokes": [],
              "supports": [], "marks": [], "tick": b.tick}
        for uid, u in b.units.items():
            kind, gid, ref = z.owner.get(uid, (None, None, None))
            if u.dead or u.exited:
                continue
            if kind in ("s", "g"):
                sq = w.squads.get(gid) if kind == "s" else None
                side = "own" if sq is not None and sq.commander == cid else ("ally" if kind == "s" else "remnant")
                zd["units"].append(unit_dict(uid, u, side, gid, ref.name if ref else uid))
            elif gm or (intel is not None and uid in intel.spotted):
                zd["units"].append(unit_dict(uid, u, "enemy", gid, "敵兵"))
            elif intel is not None and uid in intel.last_known:
                node, t = intel.last_known[uid]
                zd["ghosts"].append({"id": uid, "pos": list(node), "age": int((b.tick - t) * sim.step_seconds)})
        for sm in b.active_smokes():
            zd["smokes"].append({"x": sm["x"] / b.map.cell, "y": sm["y"] / b.map.cell, "r": sm["r"] / b.map.cell,
                                 "density": b.smoke_density(sm)})
        for cell in z.cells:
            for sp in sim.support_view(cell, cid if not gm else None):
                ox, oy = worldmap.cell_origin(sp["cell"])
                zd["supports"].append(dict(sp, x=sp["x"] + ox, y=sp["y"] + oy))
        for s in mine:
            n = z.mark_node(s)
            if n:
                zd["marks"].append({"squad": s.id, "callsign": s.callsign, "pos": list(n), "kind": s.action.mark_kind})
        out["zones"].append(zd)
    if c is not None:
        covered = {cell for zd in out["zones"] for cell in zd["cells"]}
        for cell, e in c.intel["enemies"].items():
            if cell in covered:
                continue
            out["intel"].append({"cell": cell, "est": e["est"], "suspected": bool(e.get("suspected")),
                                 "age": int(sim.seconds(w.tick - e["tick"]))})
    return out


def unit_dict(uid: str, u, side: str, group: str, name: str) -> dict:
    return {"id": uid, "side": side, "squad": group, "name": name, "pos": list(u.pos), "posture": u.posture,
            "facing": list(u.facing), "down": u.down, "weapon": u.weapon, "suppressed": u.suppressed,
            "pinned": u.pinned, "braced": bool(u.braced), "dazzled": bool(u.dazzled),
            "stabilized": u.stabilized}


TERRAIN_CODES = {"wall": "#", "window": "W", "wallside": "|", "lowwall": "=", "rubble": "%", "veg": '"'}


def terrain_grid(m) -> list[str]:
    """一列一字串，y=0 在最前面（y 向北）。# 牆 W 窗 | 隔牆 = 矮牆 % 瓦礫 \" 植被 H 樓梯 . 空地。"""
    key = "web_terrain"
    if key in m._cache:
        return m._cache[key]
    rows = []
    for y in range(m.h):
        row = []
        for x in range(m.w):
            tags = {b.tag for b in m._boxes_in_cell(x, y) if b.lo[2] < 3.0}
            if (x, y) in m.stairs:
                row.append("H")
                continue
            for t in ("wall", "window", "wallside", "lowwall", "rubble", "veg"):
                if t in tags:
                    row.append(TERRAIN_CODES[t])
                    break
            else:
                row.append(".")
        rows.append("".join(row))
    m._cache[key] = rows
    return rows


def commander_state(sim, cid: str) -> dict:
    """指揮官整份畫面（初次連線與每 tick 推送）。"""
    w = sim.world
    c = w.commanders.get(cid)
    squads = [squad_view(sim, s) for s in w.squads_of(cid)]
    return {
        "tick": w.tick, "tick_seconds": sim.tick_seconds, "step_seconds": sim.step_seconds, "running": w.running,
        "zone_mode": sim.zone_mode,
        "typing": sorted(s for s in sim.typing if s in {x.id for x in w.squads_of(cid)}),
        "commander": {"id": cid, "name": c.name if c else cid,
                      "supply_wait": max(0, (c.supply_ready - w.tick)) if c else 0},
        "squads": squads,
        "map": op_map(sim, cid),
        "missions": missions_view(sim, cid),
        "summary": summary(sim, cid),
        "events": list(c.events[-20:]) if c else [],
        "adjutant": dict(c.adjutant) if c else {},
        "reserve": reserve.remaining(w),
    }


def radio_log(s: Squad, n: int = 40) -> list[dict]:
    return [{"t": d["t"], "who": d["who"], "text": d["text"]} for d in s.dialogue[-n:]]
