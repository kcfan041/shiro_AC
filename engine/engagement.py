"""C2：作戰層交戰改由戰術層結算。

同一格同時有我方小隊與敵軍時，該格建立（或沿用）一場戰術戰鬥 `Battle`：
- 個體從作戰層名冊建立（位置、傷勢、彈藥、投擲物、熟練度、壓制、倒地狀態）。
- 每個作戰層 tick 推進一個戰術 tick（皆 10 秒）。
- 結束後把個體狀態寫回作戰層，並產生給小隊長的事件。
- 戰鬥進行中（有接觸）作戰層移動暫停；撤退意圖會在戰術層交替撤退，撤出邊緣後才移到相鄰格。

作戰層小隊行動 → 戰術意圖：
  hold → hold；attack／move → advance（朝敵軍最後位置）；scout → hold；hide → ambush；retreat → retreat。
敵軍：garrison → hold；patrol／mobile → advance；withdraw → retreat。
"""
from __future__ import annotations

from engine import scenario
from engine.models import Enemy, Squad, World
from engine.tactical import worldmap
from engine.tactical.ai import Intent, TacticalAI
from engine.tactical.sim import Battle, SideIntel
from engine.tactical.units import WEAPONS, Unit

SQUAD_SIDE = "blue"
ENEMY_SIDE = "red"
IDLE_END = 6           # 雙方互相不知道對方位置連續 N tick → 結束戰鬥（脫離接觸）
MORALE_TO_TAC = 0.875  # 作戰層士氣 80 ≈ 戰術層 70
WEAPON_LABELS = {"rifle": "步槍", "lmg": "機槍", "dmr": "射手步槍", "shotgun": "霰彈槍"}


class Engagement:
    def __init__(self, world: World, cell: str, seed: int):
        self.world = world
        self.cell = cell
        self.battle = Battle(worldmap.tmap(cell), [], seed=seed)
        self.battle.intel = {SQUAD_SIDE: SideIntel(), ENEMY_SIDE: SideIntel()}
        self.battle.linked = {SQUAD_SIDE: True, ENEMY_SIDE: True}
        self.battle.morale = {SQUAD_SIDE: 70.0, ENEMY_SIDE: 70.0}
        self.ais = {SQUAD_SIDE: TacticalAI(self.battle, SQUAD_SIDE), ENEMY_SIDE: TacticalAI(self.battle, ENEMY_SIDE)}
        self.owner: dict[str, tuple[str, str, object]] = {}   # unit id -> (kind, group id, ref)
        self.quiet = IDLE_END
        self.started = world.tick
        self.reported: set[str] = set()
        self.events: dict[str, list[tuple[str, str, str]]] = {}
        self.intents: dict[str, Intent] = {}
        self.report_mark = 0

    # ------------------------------------------------------------------ roster ↔ units

    def _uid(self, kind: str, gid: str, key: str) -> str:
        return f"{kind}:{gid}:{key}"

    def sync_in(self):
        """把作戰層個體同步進戰鬥（新加入的建立 Unit，離開的移除）。"""
        present = set()
        for s in self.world.squads.values():
            if s.status != "active" or s.pos != self.cell:
                continue
            for i, m in enumerate(s.members):
                if m.lost:
                    continue
                uid = self._uid("s", s.id, m.name)
                present.add(uid)
                if uid not in self.battle.units:
                    self.battle.units[uid] = self._unit_from_member(uid, m, i == 0 or m.trait == "小隊長")
                self.owner[uid] = ("s", s.id, m)
            self.battle.linked[SQUAD_SIDE] = s.linked
        for e in self.world.enemies.values():
            if not e.alive or e.pos != self.cell:
                continue
            for sd in e.soldiers:
                uid = self._uid("e", e.id, sd["id"])
                present.add(uid)
                if uid not in self.battle.units:
                    self.battle.units[uid] = self._unit_from_soldier(uid, sd)
                self.owner[uid] = ("e", e.id, sd)
        for uid in list(self.battle.units):
            if uid not in present:
                u = self.battle.units.pop(uid)
                for intel in self.battle.intel.values():
                    intel.spotted.pop(uid, None)
                    intel.last_known.pop(uid, None)
                self.owner.pop(uid, None)
                _ = u

    def _unit_from_member(self, uid: str, m, leader: bool) -> Unit:
        pos = tuple(m.tpos) if m.tpos else (12, 1, 0)
        u = Unit(uid, SQUAD_SIDE, pos, weapon=m.weapon, posture=m.posture, facing=tuple(m.facing),
                 ammo=m.ammo if m.ammo is not None else -1, damage=dict(m.damage), leader=leader,
                 grenades=m.grenades, smokes=m.smokes, prof=dict(m.prof))
        u.torso_hits = m.torso_hits
        u.suppression = m.suppression
        u.down = m.down
        u.down_tick = m.down_tick
        u.stabilized = m.stabilized
        return u

    def _unit_from_soldier(self, uid: str, sd: dict) -> Unit:
        pos = tuple(sd["tpos"]) if sd.get("tpos") else (12, 23, 0)
        u = Unit(uid, ENEMY_SIDE, pos, weapon=sd.get("weapon", "rifle"), posture=sd.get("posture", "crouch"),
                 facing=tuple(sd.get("facing") or (0, -1)), ammo=sd["ammo"] if sd.get("ammo") is not None else -1,
                 damage=dict(sd.get("damage", {})), grenades=sd.get("grenades", 2), smokes=sd.get("smokes", 1),
                 prof=dict(sd.get("prof", {})))
        u.torso_hits = sd.get("torso_hits", 0)
        u.suppression = sd.get("suppression", 0.0)
        u.down = sd.get("down", False)
        u.down_tick = sd.get("down_tick")
        u.stabilized = sd.get("stabilized", False)
        return u

    def sync_out(self):
        """把戰鬥中的個體狀態寫回作戰層。陣亡（離線）者從名冊移除。"""
        for uid, u in self.battle.units.items():
            kind, gid, ref = self.owner.get(uid, (None, None, None))
            if ref is None:
                continue
            if kind == "s":
                m = ref
                m.tpos = list(u.pos)
                m.posture, m.facing = u.posture, list(u.facing)
                m.damage, m.torso_hits = dict(u.damage), u.torso_hits
                m.ammo, m.grenades, m.smokes = u.ammo, u.grenades, u.smokes
                m.prof = dict(u.prof)
                m.suppression = u.suppression
                m.down, m.down_tick, m.stabilized = u.down and not u.dead, u.down_tick, u.stabilized
                m.hp = 0 if u.dead else max(10, 100 - 30 * len([k for k, v in u.damage.items() if v]))
            else:
                sd = ref
                sd.update(tpos=list(u.pos), posture=u.posture, facing=list(u.facing), damage=dict(u.damage),
                          torso_hits=u.torso_hits, ammo=u.ammo, grenades=u.grenades, smokes=u.smokes,
                          prof=dict(u.prof), suppression=u.suppression, down=u.down and not u.dead,
                          down_tick=u.down_tick, stabilized=u.stabilized, dead=u.dead)
        for e in self.world.enemies.values():
            if e.pos != self.cell or not e.alive:
                continue
            e.soldiers = [sd for sd in e.soldiers if not sd.get("dead")]
            fighting = [sd for sd in e.soldiers if not sd.get("down") and not sd.get("surrendered")]
            e.strength = len(fighting)
            if e.strength <= 0:
                e.alive = False
                e.engaged_with = None
        for s in self.world.squads.values():
            if s.pos != self.cell or s.status != "active":
                continue
            ammo = [u.ammo / WEAPONS[u.weapon]["ammo"] for uid, u in self.battle.units.items()
                    if self.owner.get(uid, ("",))[1] == s.id and u.alive]
            if ammo:
                s.ammo = int(round(100 * sum(ammo) / len(ammo)))

    # ------------------------------------------------------------------ intents

    def squad_intent(self, s: Squad) -> Intent:
        a = s.action.type
        lk = self.battle.intel[SQUAD_SIDE].last_known
        goal = None
        if lk:
            goal = min((n for n, _ in lk.values()), key=lambda n: (n[1], n[0]))
        if a == "retreat":
            toward = self._toward(s.pos, s.action.target) if s.action.target else self._rear(s.pos)
            return Intent("retreat", toward=toward)
        if a in ("attack", "assault"):
            return Intent("assault" if goal else "advance", goal=goal or self._center())
        if a in ("advance", "supp_advance"):
            return Intent(a if goal else "advance", goal=goal or self._center())
        if a == "flank":
            if not goal:
                return Intent("advance", goal=self._center())
            return Intent("flank", goal=goal, side=s.action.flank or "left")
        if a == "move" and s.action.path:
            return Intent("retreat", toward=self._toward(s.pos, s.action.path[0]))
        if a == "hide":
            return Intent("ambush", ambush_range=12)
        return Intent("hold")

    def enemy_intent(self, e: Enemy) -> Intent:
        if e.order == "withdraw":
            return Intent("retreat", toward=self._toward(e.pos, e.goal) if e.goal else (0, 1))
        if e.tag == "garrison":
            return Intent("hold")
        lk = self.battle.intel[ENEMY_SIDE].last_known
        if lk:
            goal = min((n for n, _ in lk.values()), key=lambda n: (-n[1], n[0]))
            return Intent("advance", goal=goal)
        return Intent("hold")

    def _center(self) -> tuple[int, int, int]:
        return (self.battle.map.w // 2, self.battle.map.h // 2, 0)

    def _rear(self, cell: str) -> tuple[int, int]:
        """我方後方：往後方據點的方向；在據點本身則往南。"""
        if cell == scenario.BASE:
            return (0, -1)
        return self._toward(cell, scenario.BASE)

    @staticmethod
    def _toward(frm: str, to: str | None) -> tuple[int, int]:
        if not to or to == frm:
            return (0, -1)
        dr = scenario.ROWS.index(to[0]) - scenario.ROWS.index(frm[0])
        dc = scenario.COLS.index(to[1]) - scenario.COLS.index(frm[1])
        if abs(dr) >= abs(dc):
            return (0, 1 if dr > 0 else -1)
        return (1 if dc > 0 else -1, 0)

    def exit_cell(self, frm: str, toward: tuple[int, int]) -> str | None:
        r = scenario.ROWS.index(frm[0]) + toward[1]
        c = scenario.COLS.index(frm[1]) + toward[0]
        if 0 <= r < len(scenario.ROWS) and 0 <= c < len(scenario.COLS):
            return scenario.ROWS[r] + scenario.COLS[c]
        return None

    # ------------------------------------------------------------------ step

    def step(self) -> dict:
        self.sync_in()
        b = self.battle
        if b.tick == 0:
            b.detect("init")
        plans = {}
        for s in self.world.squads.values():
            if s.pos != self.cell or s.status != "active":
                continue
            units = [b.units[u] for u in b.units if self.owner[u][1] == s.id and b.units[u].alive]
            if not units:
                continue
            intent = self.squad_intent(s)
            key = f"s:{s.id}"
            if self.intents.get(key) is None or self.intents[key].kind != intent.kind:
                self.ais[SQUAD_SIDE].new_intent()
            self.intents[key] = intent
            b.morale[SQUAD_SIDE] = s.morale * MORALE_TO_TAC
            plans.update(self.ais[SQUAD_SIDE].plan(intent, units))
        for e in self.world.enemies.values():
            if e.pos != self.cell or not e.alive:
                continue
            units = [b.units[u] for u in b.units if self.owner[u][1] == e.id and b.units[u].alive]
            if units:
                plans.update(self.ais[ENEMY_SIDE].plan(self.enemy_intent(e), units))
        before = {uid: (u.down, u.dead, dict(u.damage)) for uid, u in b.units.items()}
        ammo_before = {uid: u.ammo for uid, u in b.units.items()}
        b.step(plans)
        out = self._collect(before, ammo_before)
        ai = self.ais[SQUAD_SIDE]
        new_reports = ai.reports[self.report_mark:]
        self.report_mark = len(ai.reports)
        for r in new_reports:
            text = r.split(" ", 1)[1] if " " in r else r
            if "請求新指示" in text or "士氣崩潰" in text:
                for s in self.world.squads.values():
                    if s.pos == self.cell and s.status == "active":
                        out["squads"].setdefault(s.id, []).append(("request", text))
        for s in self.world.squads.values():
            if s.pos == self.cell and s.status == "active":
                s.morale = int(round(b.morale.get(SQUAD_SIDE, 70) / MORALE_TO_TAC))
        self.sync_out()
        self._handle_exits(out)
        active = any(u.fired for u in b.units.values()) or any(
            l.startswith(f"T{b.tick} ") and ("發現" in l or "察覺" in l) for l in b.log)
        seen = any(b.units[u].alive for side in b.intel for u in b.intel[side].spotted if u in b.units)
        was_hot = self.hot() and self.quiet < IDLE_END and "contact" in self.reported
        self.quiet = 0 if (active or seen) else self.quiet + 1
        if was_hot and not self.hot():
            self.reported.discard("contact")
            for s in self.world.squads.values():
                if s.pos == self.cell and s.status == "active":
                    out["squads"].setdefault(s.id, []).append(("disengage", "與敵軍脫離接觸"))
        return out

    def _collect(self, before, ammo_before) -> dict:
        """整理本 tick 對每個作戰層單位的事件。"""
        b = self.battle
        out: dict[str, list] = {"squads": {}, "enemies": {}, "exits": []}
        for uid, u in b.units.items():
            kind, gid, ref = self.owner.get(uid, (None, None, None))
            if kind != "s":
                continue
            ev = out["squads"].setdefault(gid, [])
            was_down, was_dead, dmg = before.get(uid, (False, False, {}))
            name = ref.name
            if u.dead and not was_dead:
                ev.append(("casualty", f"{name} 陣亡"))
            elif u.down and not was_down:
                ev.append(("down", f"{name} 倒地"))
            elif any(v and not dmg.get(k) for k, v in u.damage.items()):
                ev.append(("wounded", f"{name} 負傷"))
            if u.exited:
                out["exits"].append((gid, uid))
        for gid, ev in out["squads"].items():
            mine = {uid for uid, o in self.owner.items() if o[1] == gid}
            kills = [l for l in b.log if l.startswith(f"T{b.tick} ") and "倒地（" in l
                     and l.split("倒地（")[1].rstrip("）") in mine
                     and self.owner.get(l.split()[1], ("",))[0] == "e"]
            for _ in kills:
                ev.append(("kill", "擊倒一名敵軍"))
            spotted_now = [l for l in b.log if l.startswith(f"T{b.tick} {SQUAD_SIDE} 發現 ")]
            under_fire = any(self.owner.get(s["shooter"], ("",))[0] == "e" for s in b.shots[b._shot_mark:]) or any(
                e for e in ev if e[0] in ("down", "casualty", "wounded"))
            if (spotted_now or under_fire or kills) and "contact" not in self.reported:
                self.reported.add("contact")
                n = sum(1 for u in b.intel[SQUAD_SIDE].last_known if b.units.get(u) and b.units[u].alive)
                ev.insert(0, ("contact", f"在 {self.cell} 與敵軍接觸（{'約 ' + str(n) if n else '位置不明'}）"))
            smokes = [l for l in b.log if l.startswith(f"T{b.tick} ") and "煙霧彈" in l and l.split()[1] in mine]
            if smokes:
                ev.append(("smoke", "投擲煙霧彈"))
            gren = [l for l in b.log if l.startswith(f"T{b.tick} ") and "投擲手榴彈" in l and l.split()[1] in mine]
            if gren:
                ev.append(("grenade", "投擲手榴彈"))
        return out

    def _handle_exits(self, out: dict):
        """撤出戰區的小隊：全員（含拖回的倒地者）撤出後移到相鄰格。"""
        b = self.battle
        for s in list(self.world.squads.values()):
            if s.pos != self.cell or s.status != "active":
                continue
            mine = [uid for uid, o in self.owner.items() if o[1] == s.id]
            if not mine:
                continue
            active = [uid for uid in mine if b.units[uid].alive]
            exited = [uid for uid in mine if b.units[uid].exited]
            if exited and not active:
                toward = self.intents.get(f"s:{s.id}", Intent("retreat", toward=self._rear(s.pos))).toward \
                    or self._rear(s.pos)
                dest = self.exit_cell(s.pos, toward)
                if dest:
                    left_behind = [self.owner[uid][2] for uid in mine
                                   if b.units[uid].down and not b.units[uid].exited and not b.units[uid].dead]
                    for m in left_behind:
                        m.hp = 0
                        m.down = False
                    for uid in exited:
                        b.units[uid].exited = False
                    from engine.models import Action
                    frm = s.pos
                    s.pos = dest
                    s.contact = None
                    if s.action.type == "move" and s.action.path and s.action.path[0] == dest:
                        s.action.path.pop(0)
                        s.action.progress = 0
                        if not s.action.path:
                            s.action = Action(type="hold")
                    else:
                        s.action = Action(type="hold")
                    out.setdefault("moved", []).append((s.id, frm, dest, len(left_behind)))

    def brief(self, s: Squad) -> str:
        """給隊長的戰術層簡報：只含小隊看得到的資訊。"""
        b = self.battle
        mine = [(uid, b.units[uid]) for uid, o in self.owner.items() if o[1] == s.id and uid in b.units]
        if not mine:
            return ""
        alive = [u for _, u in mine if u.alive]
        cx = sum(u.pos[0] for u in alive) / len(alive) if alive else 12
        cy = sum(u.pos[1] for u in alive) / len(alive) if alive else 12
        lines = []
        for uid, u in mine:
            name = self.owner[uid][2].name
            if u.dead:
                continue
            state = "倒地" if u.down else ("被壓制" if u.pinned else ("受壓制" if u.suppression > 0 else "可作戰"))
            ammo = u.ammo / WEAPONS[u.weapon]["ammo"]
            ammo_w = "彈藥充足" if ammo > 0.6 else ("彈藥過半" if ammo > 0.3 else ("彈藥見底" if ammo > 0 else "沒子彈"))
            lines.append(f"{name}（{WEAPON_LABELS.get(u.weapon, u.weapon)}，{state}，{ammo_w}，"
                         f"手榴彈{u.grenades} 煙霧{u.smokes}）")
        enemies = []
        for uid, (node, tick) in sorted(b.intel[SQUAD_SIDE].last_known.items()):
            u = b.units.get(uid)
            if not u or not u.alive:
                continue
            dx, dy = node[0] - cx, node[1] - cy
            dist = int(round(2 * (dx * dx + dy * dy) ** 0.5))
            seen = "看得到" if uid in b.intel[SQUAD_SIDE].spotted else f"{(b.tick - tick) * 10} 秒前的位置"
            enemies.append(f"{_dir(dx, dy)}約 {dist} 公尺（{seen}{'，在樓上' if node[2] > 0 else ''}）")
        return ("戰術狀況（本格 50 公尺見方）：\n"
                f"  我方：{'；'.join(lines)}\n"
                f"  已知敵人：{'；'.join(enemies) if enemies else '目前看不到，位置不明'}\n"
                f"  士氣狀態：{ {'normal': '穩定', 'shaken': '動搖', 'rout': '崩潰', 'surrender': '崩潰'}.get(b.morale_state(SQUAD_SIDE), '穩定') }")

    def over(self) -> bool:
        """同格內有一方已無可作戰個體 → 結束（戰鬥狀態持續存在於同格雙方都在時）。"""
        b = self.battle
        sides = {u.side for u in b.units.values() if u.alive}
        return len(sides) <= 1

    def hot(self) -> bool:
        """近期有接觸（射擊、發現、可見敵人）。安靜 IDLE_END tick 後視為脫離接觸。"""
        return self.quiet < IDLE_END

    def finish(self):
        """結束：寫回並清理戰鬥暫存狀態（倒地計時延續到作戰層由整補處理）。"""
        self.sync_out()
        for e in self.world.enemies.values():
            if e.pos == self.cell:
                e.engaged_with = None


def _dir(dx: float, dy: float) -> str:
    """戰術地圖 y 向北。"""
    if abs(dx) < 2 and abs(dy) < 2:
        return "很近，"
    ns = "北" if dy > 0 else "南"
    ew = "東" if dx > 0 else "西"
    if abs(dy) >= 2 * abs(dx):
        return ns + "方"
    if abs(dx) >= 2 * abs(dy):
        return ew + "方"
    return ew + ns + "方"


def cells_in_contact(world: World) -> list[str]:
    cells = set()
    for s in world.squads.values():
        if s.status != "active":
            continue
        if any(e.alive and e.pos == s.pos for e in world.enemies.values()):
            cells.add(s.pos)
    return sorted(cells)
