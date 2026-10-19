"""指揮部支援（網頁版即時微操）：不經過隊長服從判定，直接由指揮部執行；有延遲與散布。

- airdrop 精準空投：落點散布 ±1.5 格；落地後附近（3 格內）我方個體自動補彈藥、投擲物（缺多少補多少，最多 3 個）。
  落地前敵人比我方近 → 可能被搶（敵人補給）。
- smoke 煙幕彈幕：延遲後在指定點周圍落 3 發煙霧（一字排開，垂直於「最近我方 → 指定點」方向）。
- drone 偵察無人機：抵達後持續 DRONE_LIFE 戰術步，半徑 DRONE_R 格內的敵人全部揭露給該格我方（共享情報）。
  每步有機率被看得到它的敵人擊落（被揭露的敵人越多越容易）。
- mark 標定點：集結／包抄／集火，是給小隊的命令（會經過隊長服從判定），由 Simulation.mark_order 處理，不在這裡。

延遲以戰術步計（交戰中每 tick 一步；非交戰格以作戰 tick 計）。第一期不限次數。
"""
from __future__ import annotations

import math

from engine.tactical import rng as trng

KINDS = ("airdrop", "smoke", "drone")
LABELS = {"airdrop": "精準空投", "smoke": "煙幕彈幕", "drone": "偵察無人機", "mark": "標定點"}
DELAY = {"airdrop": 2, "smoke": 1, "drone": 1}
AIRDROP_SPREAD = 1.5
AIRDROP_PICKUP_R = 3
DRONE_R = 8
DRONE_LIFE = 5
DRONE_SHOT_P = 0.08
SMOKE_SPACING = 2.5


def new(kind: str, cell: str, point: list, commander: str, squad: str | None, tick: int, sid: str) -> dict:
    return {"id": sid, "kind": kind, "label": LABELS[kind], "cell": cell, "x": float(point[0]), "y": float(point[1]),
            "commander": commander, "squad": squad, "called": tick, "eta": DELAY[kind], "state": "inbound",
            "life": DRONE_LIFE if kind == "drone" else 0, "result": None}


def public(s: dict) -> dict:
    return {k: s[k] for k in ("id", "kind", "label", "cell", "x", "y", "commander", "eta", "state", "life", "result")}


def _r(seed: int, *parts) -> float:
    return trng.uniform(seed, *parts)


def _g(seed: int, *parts) -> float:
    return trng.gauss(seed, *parts)


def step(eng, s: dict) -> str | None:
    """在交戰格的 b.step 之前推進一個支援。回傳給指揮官的事件文字（或 None）。"""
    b = eng.battle
    m = b.map
    if s["state"] == "inbound":
        s["eta"] -= 1
        if s["eta"] > 0:
            return None
        if s["kind"] == "airdrop":
            return _land_airdrop(eng, s)
        if s["kind"] == "smoke":
            return _land_smoke(eng, s)
        if s["kind"] == "drone":
            s["state"] = "active"
            return f"偵察無人機抵達 ({int(s['x'])},{int(s['y'])}) 上空"
    if s["state"] == "active" and s["kind"] == "drone":
        return _drone(eng, s)
    return None


def _friendly_units(eng, cid: str | None):
    b = eng.battle
    out = []
    for uid, u in b.units.items():
        kind, gid, ref = eng.owner.get(uid, (None, None, None))
        if kind == "s" and u.alive:
            out.append(u)
    return out


def _land_airdrop(eng, s: dict) -> str:
    b, m = eng.battle, eng.battle.map
    seed = b.seed
    x = min(m.w - 1, max(0, s["x"] + _g(seed, "drop", s["id"], "x") * AIRDROP_SPREAD))
    y = min(m.h - 1, max(0, s["y"] + _g(seed, "drop", s["id"], "y") * AIRDROP_SPREAD))
    s["x"], s["y"] = round(x, 2), round(y, 2)
    s["state"] = "landed"
    from engine.tactical.units import LOADOUT, THROW_CAP, WEAPONS
    friends = [u for u in _friendly_units(eng, s["commander"])
               if max(abs(u.pos[0] - x), abs(u.pos[1] - y)) <= AIRDROP_PICKUP_R]
    foes = [u for u in b.units.values() if u.alive and u.side == "red"
            and max(abs(u.pos[0] - x), abs(u.pos[1] - y)) <= AIRDROP_PICKUP_R]
    if foes and (not friends or min(math.hypot(u.pos[0] - x, u.pos[1] - y) for u in foes)
                 < min(math.hypot(u.pos[0] - x, u.pos[1] - y) for u in friends)):
        for u in foes:
            u.ammo = WEAPONS[u.weapon]["ammo"]
        s["result"] = "被敵軍搶走"
        b.log.append(f"T{b.tick} 空投落在敵方附近，被敵軍搶走")
        return f"精準空投落在 ({int(x)},{int(y)})，但敵軍比較近——補給被搶走了"
    if not friends:
        s["result"] = "附近沒有我方人員"
        return f"精準空投落在 ({int(x)},{int(y)})，附近沒有我方人員可以取用"
    names = []
    for u in friends:
        u.ammo = WEAPONS[u.weapon]["ammo"]
        g, sm, fl = LOADOUT.get(u.weapon, (1, 1, 1))
        for item, want in (("grenades", g), ("smokes", sm), ("flashes", fl)):
            while getattr(u, item) < want and u.throwables() < THROW_CAP:
                setattr(u, item, getattr(u, item) + 1)
        kind, gid, ref = eng.owner.get(u.id, (None, None, None))
        names.append(getattr(ref, "name", u.id))
    s["result"] = f"{len(names)} 人補給"
    b.log.append(f"T{b.tick} 精準空投落地，{'、'.join(names)} 補充彈藥")
    return f"精準空投落地：{'、'.join(names)} 補充彈藥與投擲物"


def _land_smoke(eng, s: dict) -> str:
    b, m = eng.battle, eng.battle.map
    from engine.tactical.sim import SMOKE_HEIGHT, SMOKE_LIFE, SMOKE_RADIUS
    friends = _friendly_units(eng, s["commander"])
    if friends:
        f = min(friends, key=lambda u: math.hypot(u.pos[0] - s["x"], u.pos[1] - s["y"]))
        dx, dy = s["x"] - f.pos[0], s["y"] - f.pos[1]
    else:
        dx, dy = 0.0, 1.0
    n = math.hypot(dx, dy) or 1.0
    px, py = -dy / n, dx / n
    for i, k in enumerate((-1, 0, 1)):
        jx = _g(b.seed, "smk", s["id"], i, "x") * 0.5
        jy = _g(b.seed, "smk", s["id"], i, "y") * 0.5
        cx = (s["x"] + px * k * SMOKE_SPACING + jx + 0.5) * m.cell
        cy = (s["y"] + py * k * SMOKE_SPACING + jy + 0.5) * m.cell
        b.smokes.append({"x": cx, "y": cy, "r": SMOKE_RADIUS, "top": SMOKE_HEIGHT, "born": b.tick - 1,
                         "expires": b.tick + SMOKE_LIFE + 1, "by": "support"})
    s["state"] = "done"
    s["result"] = "煙幕展開"
    b.log.append(f"T{b.tick} 煙幕彈幕落地")
    return f"煙幕彈幕在 ({int(s['x'])},{int(s['y'])}) 展開"


def _drone(eng, s: dict) -> str | None:
    b = eng.battle
    s["life"] -= 1
    seen = {u.id for u in b.units.values() if u.alive and u.side == "red"
            and math.hypot(u.pos[0] - s["x"], u.pos[1] - s["y"]) <= DRONE_R}
    for side in list(b.intel):
        if side.startswith("blue"):
            b.reveal[side] = set(seen)
    p = 1 - (1 - DRONE_SHOT_P) ** len(seen)
    if seen and _r(b.seed, "drone_shot", s["id"], b.tick) < p:
        s["state"] = "lost"
        s["result"] = "被擊落"
        _clear_reveal(b)
        b.log.append(f"T{b.tick} 偵察無人機被擊落")
        return "偵察無人機被敵軍擊落"
    if s["life"] <= 0:
        s["state"] = "done"
        s["result"] = f"返航（最後看到 {len(seen)} 名敵人）"
        _clear_reveal(b, keep=True)
        return f"偵察無人機電力耗盡返航（最後看到 {len(seen)} 名敵人）"
    return None


def _clear_reveal(b, keep: bool = False):
    for side in list(b.reveal):
        if side.startswith("blue"):
            b.reveal[side] = set()


def step_idle(sim, s: dict) -> str | None:
    """非交戰格：空投直接給小隊，煙幕與無人機沒有意義（無人機在這格揭露作戰層敵情）。"""
    s["eta"] -= 1
    if s["eta"] > 0:
        return None
    w = sim.world
    if s["kind"] == "airdrop":
        squads = [x for x in w.squads.values() if x.pos == s["cell"] and x.status == "active"
                  and x.commander == s["commander"]]
        s["state"] = "landed"
        if not squads:
            s["result"] = "附近沒有我方人員"
            return f"精準空投落在 {s['cell']}，附近沒有我方小隊"
        from engine.models import refill_loadout
        for sq in squads:
            sq.ammo = 100
            for m in sq.alive_members():
                m.ammo = None
                refill_loadout(m)
        s["result"] = "補給完成"
        return f"精準空投送達 {s['cell']}：{'、'.join(x.callsign for x in squads)} 補滿彈藥與投擲物"
    if s["kind"] == "drone":
        s["state"] = "done"
        e = next((e for e in w.enemies.values() if e.alive and e.pos == s["cell"]), None)
        c = w.commanders.get(s["commander"])
        if c is not None:
            if e:
                c.intel["enemies"][s["cell"]] = {"est": e.strength, "tick": w.tick}
            else:
                c.intel["enemies"].pop(s["cell"], None)
        s["result"] = f"看到約 {e.strength} 名敵人" if e else "沒有敵蹤"
        return f"偵察無人機飛越 {s['cell']}：{s['result']}"
    s["state"] = "done"
    s["result"] = "煙幕展開（附近沒有交戰）"
    return f"煙幕彈幕在 {s['cell']} 展開（附近沒有交戰）"
