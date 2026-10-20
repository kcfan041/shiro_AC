"""連續地圖（zone 模式）：玩家小隊周圍 3×3 格合成一場持續的戰術模擬。

- 全圖 125×125（engine/tactical/worldmap.global_map）。每支玩家小隊產生活動區域＝所在格＋周圍一圈；
  相連（重疊或相鄰）的活動區域合成同一個 Zone（同一場 Battle）。
- 活動區域內：所有單位（我方、敵方、殘部）都在戰術層，連續移動、跨格交火；單位所在格＝座標換算。
- 活動區域外：AI 單位照舊在作戰層用格子移動；走進區域時從進入的邊出現，離開時收回並保留格內座標。
- 玩家小隊行軍：作戰層的 move／scout／retreat 變成戰術層 march（照格子路線一格一格走過去，中心點為目標）。
- 敵軍：作戰層給下一格（Enemy.dest），戰術層照路線走；接敵時交給敵方戰術指揮。
- 我方小隊永遠在戰術層（status active 且所在格屬於某個 Zone）。

座標：Member.gpos／敵兵 sd["gpos"]＝全圖座標；tpos 仍保存格內座標（給舊程式與作戰層用）。
"""
from __future__ import annotations

from engine import scenario
from engine.engagement import ENEMY_SIDE, REMNANT_SIDE, SQUAD_SIDE, Engagement
from engine.models import Squad
from engine.tactical import worldmap
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.units import Unit

ZONE_RADIUS = 1
ARRIVE_R = 4            # 小隊平均位置離目標格中心 N 格內 → 抵達
SCOUT_STOP = 6          # 偵察：走到目標格邊緣內 N 格就停下觀察


def squad_cell(s: Squad) -> str | None:
    pts = [m.gpos for m in s.members if not m.lost and m.gpos]
    if not pts:
        return None
    x = sum(p[0] for p in pts) / len(pts)
    y = sum(p[1] for p in pts) / len(pts)
    return worldmap.cell_of(x, y)


def squad_center(s: Squad):
    pts = [m.gpos for m in s.members if not m.lost and m.gpos and not m.down] or \
          [m.gpos for m in s.members if not m.lost and m.gpos]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def cell_center(cell: str) -> tuple[int, int, int]:
    ox, oy = worldmap.cell_origin(cell)
    return (ox + worldmap.SIZE // 2, oy + worldmap.SIZE // 2, 0)


def nearest_standable(m, node, r: int = 6):
    x, y = int(node[0]), int(node[1])
    for k in range(0, r + 1):
        for dx in range(-k, k + 1):
            for dy in range(-k, k + 1):
                if max(abs(dx), abs(dy)) != k:
                    continue
                n = (x + dx, y + dy, 0)
                if m.in_bounds(n[0], n[1]) and m.standable(n):
                    return n
    return (x, y, 0)


class Zone(Engagement):
    """一個（或多個相連）活動區域的持續模擬。self.cells＝目前納入模擬的格子。"""
    zone = True

    def __init__(self, world, cells: set[str], seed: int):
        super().__init__(world, sorted(cells)[0], seed, tmap=worldmap.global_map())
        self.cells = set(cells)
        self.quiet = 99
        self.hot_cells: set[str] = set()
        self.reported_hot: set[str] = set()

    # ------------------------------------------------------------------ 範圍

    def has_group(self, pos: str) -> bool:
        return pos in self.cells

    def where(self, gid: str) -> str:
        s = self.world.squads.get(gid)
        return s.pos if s else self.cell

    def _near_units(self, gid: str, r: float = 30):
        b = self.battle
        mine = [b.units[u] for u, o in self.owner.items() if o[1] == gid and u in b.units and b.units[u].alive]
        if not mine:
            return [], None
        cx = sum(u.pos[0] for u in mine) / len(mine)
        cy = sum(u.pos[1] for u in mine) / len(mine)
        return mine, (cx, cy)

    def known_count(self, gid: str) -> int:
        b = self.battle
        _, c = self._near_units(gid)
        if c is None:
            return 0
        r = 60 / b.map.cell
        return sum(1 for uid, (n, _) in b.intel[SQUAD_SIDE].last_known.items() if b.units.get(uid)
                   and b.units[uid].alive and max(abs(n[0] - c[0]), abs(n[1] - c[1])) <= r)

    def label(self) -> str:
        return "、".join(sorted(self.cells))

    def cells_hot(self) -> set[str]:
        return set(self.hot_cells)

    # ------------------------------------------------------------------ 進出

    def _unit_from_member(self, uid, m, leader):
        u = super()._unit_from_member(uid, m, leader)
        if m.gpos:
            u.pos = tuple(m.gpos)
        return u

    def _unit_from_soldier(self, uid, sd):
        u = super()._unit_from_soldier(uid, sd)
        if sd.get("gpos"):
            u.pos = tuple(sd["gpos"])
        return u

    def sync_in(self):
        """把落在 self.cells 的個體放進戰鬥；落在外面的移除（敵軍收回作戰層，保留座標）。"""
        b = self.battle
        present = set()
        for s in self.world.squads.values():
            if s.status != "active":
                continue
            if not any(m.gpos and worldmap.cell_of(m.gpos[0], m.gpos[1]) in self.cells for m in s.members
                       if not m.lost):
                continue
            side = self.ensure_side(s)
            for i, m in enumerate(s.members):
                if m.lost:
                    continue
                uid = self._uid("s", s.id, m.name)
                present.add(uid)
                if uid not in b.units:
                    u = self._unit_from_member(uid, m, i == 0 or m.trait == "小隊長")
                    u.side = side
                    b.units[uid] = u
                elif m.gpos and tuple(m.gpos) != b.units[uid].pos:
                    b.units[uid].pos = tuple(m.gpos)
                self.owner[uid] = ("s", s.id, m)
        present |= self._left_behind_present()
        for e in self.world.enemies.values():
            if not e.alive or e.pos not in self.cells:
                continue
            for sd in e.soldiers:
                uid = self._uid("e", e.id, sd["id"])
                present.add(uid)
                if not sd.get("gpos") or worldmap.cell_of(sd["gpos"][0], sd["gpos"][1]) not in self.cells:
                    sd["gpos"] = list(worldmap.to_global(e.pos, sd["tpos"])) if sd.get("tpos") else \
                        list(nearest_standable(b.map, cell_center(e.pos)))
                    if uid in b.units:
                        b.units[uid].pos = tuple(sd["gpos"])
                if uid not in b.units:
                    b.units[uid] = self._unit_from_soldier(uid, sd)
                self.owner[uid] = ("e", e.id, sd)
        for r in getattr(self.world, "remnants", {}).values():
            if r.status != "holding" or r.pos not in self.cells:
                continue
            for m in r.members:
                if m.lost:
                    continue
                uid = self._uid("g", r.id, m.name)
                present.add(uid)
                if uid not in b.units:
                    if not m.gpos and m.tpos:
                        m.gpos = list(worldmap.to_global(r.pos, m.tpos))
                    u = self._unit_from_member(uid, m, m.name == r.leader)
                    u.side = REMNANT_SIDE
                    b.units[uid] = u
                b.units[uid].leader = m.name == r.leader
                self.owner[uid] = ("g", r.id, m)
        leaving = [uid for uid in b.units if uid not in present]
        if leaving:
            self.evolve(only=set(leaving))
        for uid in leaving:
            b.units.pop(uid)
            for intel in b.intel.values():
                intel.spotted.pop(uid, None)
                intel.last_known.pop(uid, None)
            self.owner.pop(uid, None)

    def sync_out(self):
        """寫回：gpos、格內 tpos、作戰層所在格（敵軍與殘部依多數人位置；我方依平均位置）。"""
        super().sync_out()
        b = self.battle
        for uid, u in b.units.items():
            kind, gid, ref = self.owner.get(uid, (None, None, None))
            if ref is None:
                continue
            cell, local = worldmap.to_local(u.pos)
            if kind in ("s", "g"):
                ref.gpos = list(u.pos)
                ref.tpos = list(local)
            else:
                ref["gpos"] = list(u.pos)
                ref["tpos"] = list(local)
        for s in self.world.squads.values():
            if s.status != "active":
                continue
            c = squad_cell(s)
            if c and c in self.cells and c != s.pos:
                s.pos = c
                s.tcell = c
        for e in self.world.enemies.values():
            if not e.alive or e.pos not in self.cells:
                continue
            cells = [worldmap.cell_of(sd["gpos"][0], sd["gpos"][1]) for sd in e.soldiers
                     if sd.get("gpos") and not sd.get("down")]
            if cells:
                c = max(set(cells), key=cells.count)
                if c and c != e.pos:
                    e.pos = c
                    e.tcell = c
                    if e.path and e.path[0] == c:
                        e.path.pop(0)
                    if e.dest == c:
                        e.dest = None
                    if e.order and not e.path:
                        from engine import enemy_ai
                        enemy_ai._arrive(self.world, e)

    # ------------------------------------------------------------------ 意圖

    def _center(self, cell: str | None = None):
        return nearest_standable(self.battle.map, cell_center(cell or self.cell))

    def _toward_cell(self, frm, cell: str):
        return nearest_standable(self.battle.map, cell_center(cell))

    def mark_node(self, s: Squad):
        """標定點存的是「標定時的格」＋格內座標（action.mark_cell）；沒有就用小隊所在格。"""
        mark = s.action.mark
        if not mark:
            return None
        cell = getattr(s.action, "mark_cell", None) or s.pos
        return worldmap.to_global(cell, (int(mark[0]), int(mark[1]), 0))

    def _squad_intent(self, s: Squad) -> Intent:
        a = s.action.type
        b = self.battle
        mine = [b.units[u] for u, o in self.owner.items() if o[1] == s.id and u in b.units and b.units[u].alive]
        lk = b.intel[SQUAD_SIDE].last_known
        goal = None
        if lk and mine:
            cx = sum(u.pos[0] for u in mine) / len(mine)
            cy = sum(u.pos[1] for u in mine) / len(mine)
            near = [n for n, _ in lk.values() if max(abs(n[0] - cx), abs(n[1] - cy)) <= 40]
            if near:
                goal = min(near, key=lambda n: (abs(n[0] - cx) + abs(n[1] - cy), n))
        if a in ("move", "scout") and s.action.path:
            nxt = s.action.path[0]
            return Intent("march", goal=self.march_goal(s, nxt))
        if a == "scout" and s.action.target:
            return Intent("march", goal=self.march_goal(s, s.action.target, scout=True))
        if a == "retreat":
            tgt = s.action.target
            if not tgt or tgt not in scenario.CELLS:
                tgt = self._retreat_cell(s)
            if tgt and tgt != s.pos:
                return Intent("march", goal=self.march_goal(s, tgt), sprint=True)
            return Intent("retreat", toward=self._away_vec(mine))
        sweep = self._sweep_point(s, mine)
        if a in ("attack", "assault"):
            return Intent("assault" if goal else "advance", goal=goal or sweep)
        if a in ("advance", "supp_advance"):
            return Intent(a if goal else "advance", goal=goal or sweep)
        if a == "flank":
            if not goal:
                return Intent("advance", goal=sweep)
            return Intent("flank", goal=goal, side=s.action.flank or "left")
        if a == "breach":
            p = s.action.breach or {}
            return Intent("breach", room=p.get("room"), prep=p.get("prep"), entry=p.get("entry"),
                          tempo=p.get("tempo"))
        if a == "hide":
            return Intent("ambush", ambush_range=12)
        return Intent("hold")

    def _sweep_point(self, s: Squad, mine):
        """看不到敵人時的進攻目標：作戰層情報裡有敵情、離小隊最近的相鄰／所在格中心；沒有就往小隊所在格中心。"""
        cands = [s.pos, *scenario.adjacent(s.pos)]
        known = [c for c in cands if c in s.known_enemies]
        cell = s.pos
        if known and mine:
            cx = sum(u.pos[0] for u in mine) / len(mine)
            cy = sum(u.pos[1] for u in mine) / len(mine)
            cell = min(known, key=lambda c: (abs(cell_center(c)[0] - cx) + abs(cell_center(c)[1] - cy), c))
        return self.march_goal(s, cell)

    def march_goal(self, s: Squad, cell: str, scout: bool = False):
        """行軍目標點：目標格中心（偵察：停在目標格靠我方這一側的邊內 SCOUT_STOP 格）。"""
        m = self.battle.map
        c = cell_center(cell)
        if scout:
            sc = squad_center(s)
            if sc:
                dx, dy = c[0] - sc[0], c[1] - sc[1]
                n = max(abs(dx), abs(dy)) or 1
                back = worldmap.SIZE // 2 - SCOUT_STOP
                c = (int(c[0] - dx / n * back), int(c[1] - dy / n * back), 0)
        key = ("mg", s.id, cell, scout)
        hit = self.__dict__.setdefault("_mg", {}).get(key)
        if hit is None:
            hit = nearest_standable(m, c)
            self._mg[key] = hit
        return hit

    def _retreat_cell(self, s: Squad) -> str | None:
        """撤退目標格：往後方據點方向的相鄰格；在據點（或該方向出了地圖）→ 離已知敵人最遠的相鄰格。"""
        d = (0, -1) if s.pos == scenario.BASE else self._toward(s.pos, scenario.BASE)
        c = self.exit_cell(s.pos, d)
        if c:
            return c
        lk = self.battle.intel[SQUAD_SIDE].last_known
        if not lk:
            adj = scenario.adjacent(s.pos)
            return adj[0] if adj else None
        ex = sum(n[0] for n, _ in lk.values()) / len(lk)
        ey = sum(n[1] for n, _ in lk.values()) / len(lk)
        return max(scenario.adjacent(s.pos), key=lambda c: (abs(cell_center(c)[0] - ex) + abs(cell_center(c)[1] - ey), c))

    def _away_vec(self, units):
        lk = self.battle.intel[SQUAD_SIDE].last_known
        if not lk or not units:
            return (0, -1)
        ex = sum(n[0] for n, _ in lk.values()) / len(lk)
        ey = sum(n[1] for n, _ in lk.values()) / len(lk)
        cx = sum(u.pos[0] for u in units) / len(units)
        cy = sum(u.pos[1] for u in units) / len(units)
        return (cx - ex, cy - ey)

    def enemy_intent(self, e) -> Intent:
        """作戰層給了下一格（行軍、增援、反攻、撤退）且附近沒有已知我方 → 行軍過去；否則交給敵方戰術指揮。"""
        b = self.battle
        units = [b.units[u] for u, o in self.owner.items() if o[1] == e.id and u in b.units and b.units[u].alive]
        nxt = e.path[0] if e.path else e.dest
        if nxt and nxt in scenario.CELLS and nxt != e.pos and units:
            lk = b.intel[ENEMY_SIDE].last_known
            cx = sum(u.pos[0] for u in units) / len(units)
            cy = sum(u.pos[1] for u in units) / len(units)
            close = any(max(abs(n[0] - cx), abs(n[1] - cy)) <= 20 for uid, (n, _) in lk.items()
                        if b.units.get(uid) is not None and b.units[uid].alive)
            if not close or e.order == "withdraw":
                intent = Intent("march", goal=nearest_standable(b.map, cell_center(nxt)),
                                sprint=e.order == "withdraw")
                self.intents[f"e:{e.id}"] = intent
                return intent
        return super().enemy_intent(e)

    # ------------------------------------------------------------------ 步進

    def step(self) -> dict:
        b = self.battle
        if len(b.log) > 3000:
            del b.log[:-500]
        if len(b.shots) > 3000:
            keep = len(b.shots) - 500
            del b.shots[:keep]
            b._shot_mark = max(0, b._shot_mark - keep)
        if len(b.blasts) > 400:
            del b.blasts[:-100]
        out = super().step()
        gone = [uid for uid, (kind, gid, ref) in self.owner.items()
                if kind == "e" and (gid not in self.world.enemies or not self.world.enemies[gid].alive
                                    or self.world.enemies[gid].pos not in self.cells)]
        for uid in gone:
            b.units.pop(uid, None)
            for intel in b.intel.values():
                intel.spotted.pop(uid, None)
                intel.last_known.pop(uid, None)
            self.owner.pop(uid, None)
        hot = set()
        for uid in b.intel[SQUAD_SIDE].spotted:
            u = b.units.get(uid)
            if u is not None and u.alive:
                c = worldmap.cell_of(u.pos[0], u.pos[1])
                if c:
                    hot.add(c)
        for u in b.units.values():
            if u.fired or (u.alive and u.suppression > 0):
                c = worldmap.cell_of(u.pos[0], u.pos[1])
                if c:
                    hot.add(c)
        self.hot_cells = hot
        return out

    def _handle_exits(self, out: dict):
        """連續地圖沒有「撤出戰區」：撤退就是走到別格。"""
        return

    def over(self) -> bool:
        return False

    def squad_hot(self, s: Squad) -> bool:
        """這支小隊附近（CONTACT_R 內）有已知敵人或正在交火。"""
        b = self.battle
        mine = [b.units[u] for u, o in self.owner.items() if o[1] == s.id and u in b.units and b.units[u].alive]
        return any(b.unit_in_contact(u) or u.fired for u in mine)

    def friends_text(self, s: Squad) -> str:
        out = []
        sc = squad_center(s)
        for o in self.world.squads.values():
            if o.id == s.id or o.status != "active":
                continue
            oc = squad_center(o)
            if sc is None or oc is None or max(abs(oc[0] - sc[0]), abs(oc[1] - sc[1])) > 30:
                continue
            from engine.engagement import label_of
            out.append(f"{label_of(self.world, o)} {len(o.alive_members())} 人")
        return "；".join(out)


def build_zones(world) -> list[set[str]]:
    """玩家小隊（有指揮官的 active 小隊）的活動區域，相連（重疊或相鄰）就合併。回傳每個區域的格子集合。"""
    regions: list[set[str]] = []
    for s in world.squads.values():
        if s.status != "active" or not s.commander:
            continue
        regions.append(worldmap.zone_cells(s.pos, ZONE_RADIUS))
    merged = True
    while merged:
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                if regions[i] & regions[j] or any(b in scenario.adjacent(a) for a in regions[i] for b in regions[j]):
                    regions[i] |= regions[j]
                    del regions[j]
                    merged = True
                    break
            if merged:
                break
    return regions


def place_squad_global(world, s: Squad):
    """小隊（新部署、增援、殘部編成）還沒有全圖座標：用格內座標換算；沒有就放在所在格中心附近。"""
    m = worldmap.global_map()
    taken = set()
    for o in world.squads.values():
        for mem in o.members:
            if mem.gpos and not mem.lost:
                taken.add(tuple(mem.gpos))
    base = cell_center(s.pos)
    spots = []
    for mem in s.members:
        if mem.lost:
            continue
        if mem.gpos and worldmap.cell_of(mem.gpos[0], mem.gpos[1]) == s.pos:
            continue
        if mem.tpos:
            g = worldmap.to_global(s.pos, mem.tpos)
            if m.standable(g) and g not in taken:
                mem.gpos = list(g)
                taken.add(g)
                continue
        for r in range(0, 10):
            found = None
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    n = (base[0] + dx * 2, base[1] + dy * 2, 0)
                    if m.in_bounds(n[0], n[1]) and m.standable(n) and n not in taken \
                            and worldmap.cell_of(n[0], n[1]) == s.pos:
                        found = n
                        break
                if found:
                    break
            if found:
                mem.gpos = list(found)
                taken.add(found)
                spots.append(found)
                break
    _ = spots


def place_enemy_entry(world, e, frm: str | None):
    """敵軍從作戰層走進活動區域：依進場邊放在 e.pos 的邊上（全圖座標）。"""
    m = worldmap.global_map()
    ox, oy = worldmap.cell_origin(e.pos)
    side = worldmap.entry_side(frm, e.pos)
    local = worldmap.tmap(e.pos)
    nodes = worldmap.edge_nodes(local, side, depth=3 if side else 25)
    taken = set()
    for o in world.squads.values():
        for mem in o.members:
            if mem.gpos and not mem.lost:
                taken.add(tuple(mem.gpos))
    out = []
    for n in nodes:
        g = (n[0] + ox, n[1] + oy, n[2])
        if g in taken or not m.standable(g):
            continue
        if all(max(abs(g[0] - p[0]), abs(g[1] - p[1])) >= 2 for p in out):
            out.append(g)
        if len(out) >= len(e.soldiers):
            break
    for sd, g in zip(e.soldiers, out):
        sd["gpos"] = list(g)
        sd["tpos"] = [g[0] - ox, g[1] - oy, g[2]]


__all__ = ["Zone", "build_zones", "squad_cell", "squad_center", "cell_center", "place_squad_global",
           "place_enemy_entry", "Unit", "TacticalAI"]
