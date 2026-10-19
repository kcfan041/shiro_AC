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
REMNANT_SIDE = "green"   # 失聯友軍殘部：與我方同陣營（不互射），情報、士氣、AI 各自獨立
IDLE_END = 6           # 雙方互相不知道對方位置連續 N tick → 結束戰鬥（脫離接觸）
MORALE_TO_TAC = 0.875  # 作戰層士氣 80 ≈ 戰術層 70
WEAPON_LABELS = {"rifle": "步槍", "lmg": "機槍", "dmr": "射手步槍", "shotgun": "霰彈槍"}
EXPOSE_LABELS = {"hit": "中彈", "near_miss": "子彈掠過", "fired": "開火暴露", "watched": "被盯上"}


class Engagement:
    def __init__(self, world: World, cell: str, seed: int):
        self.world = world
        self.cell = cell
        self.battle = Battle(worldmap.tmap(cell), [], seed=seed)
        self.battle.intel = {SQUAD_SIDE: SideIntel(), ENEMY_SIDE: SideIntel(), REMNANT_SIDE: SideIntel()}
        self.battle.linked = {SQUAD_SIDE: True, ENEMY_SIDE: True, REMNANT_SIDE: False}
        self.battle.morale = {SQUAD_SIDE: 70.0, ENEMY_SIDE: 70.0, REMNANT_SIDE: 55.0}
        self.ais = {SQUAD_SIDE: TacticalAI(self.battle, SQUAD_SIDE), ENEMY_SIDE: TacticalAI(self.battle, ENEMY_SIDE),
                    REMNANT_SIDE: TacticalAI(self.battle, REMNANT_SIDE)}
        self.owner: dict[str, tuple[str, str, object]] = {}   # unit id -> (kind, group id, ref)
        self.quiet = IDLE_END
        self.started = world.tick
        self.reported: set[str] = set()
        self.events: dict[str, list[tuple[str, str, str]]] = {}
        self.intents: dict[str, Intent] = {}
        self.report_marks: dict[str, int] = {}
        self.exp: dict[str, set] = {}
        self.left_behind: dict[str, tuple[str, object]] = {}   # unit id -> (原小隊 id, Member)：原隊撤離後留在本格的倒地者
        self._seen_surrender: set[str] = set()
        from engine.enemy_tactics import EnemyCommander
        self.commander = EnemyCommander(self)
        self.recorder = None          # engine.replay.FrameRecorder（網頁版才開）
        self.last_frame: dict | None = None
        self.pre_step = None          # 每步 b.step 前的掛勾（指揮部支援用）

    # ------------------------------------------------------------------ roster ↔ units

    def _uid(self, kind: str, gid: str, key: str) -> str:
        return f"{kind}:{gid}:{key}"

    @staticmethod
    def side_of(squad_id: str) -> str:
        """每支小隊在戰術層一個 side（blue|<小隊 id>）：AI、士氣、連線各自獨立；同格我方共用一份情報。"""
        return f"{SQUAD_SIDE}|{squad_id}"

    def ensure_side(self, s: Squad) -> str:
        side = self.side_of(s.id)
        b = self.battle
        if side not in b.intel:
            b.intel[side] = b.intel[SQUAD_SIDE]
            b.morale[side] = s.morale * MORALE_TO_TAC
            self.ais[side] = TacticalAI(b, side)
        b.linked[side] = s.linked
        return side

    def intel_of(self, s: Squad | None = None):
        return self.battle.intel[SQUAD_SIDE]

    def sync_in(self):
        """把作戰層個體同步進戰鬥（新加入的建立 Unit，離開的移除）。"""
        present = set()
        for s in self.world.squads.values():
            if s.status != "active" or s.pos != self.cell:
                continue
            side = self.ensure_side(s)
            for i, m in enumerate(s.members):
                if m.lost:
                    continue
                uid = self._uid("s", s.id, m.name)
                present.add(uid)
                if uid not in self.battle.units:
                    u = self._unit_from_member(uid, m, i == 0 or m.trait == "小隊長")
                    u.side = side
                    self.battle.units[uid] = u
                self.owner[uid] = ("s", s.id, m)
        present |= self._left_behind_present()
        for e in self.world.enemies.values():
            if not e.alive or e.pos != self.cell:
                continue
            for sd in e.soldiers:
                uid = self._uid("e", e.id, sd["id"])
                present.add(uid)
                if uid not in self.battle.units:
                    self.battle.units[uid] = self._unit_from_soldier(uid, sd)
                self.owner[uid] = ("e", e.id, sd)
        for r in getattr(self.world, "remnants", {}).values():
            if r.status != "holding" or r.pos != self.cell:
                continue
            for m in r.members:
                if m.lost:
                    continue
                uid = self._uid("g", r.id, m.name)
                present.add(uid)
                if uid not in self.battle.units:
                    u = self._unit_from_member(uid, m, m.name == r.leader)
                    u.side = REMNANT_SIDE
                    self.battle.units[uid] = u
                self.battle.units[uid].leader = m.name == r.leader
                self.owner[uid] = ("g", r.id, m)
        leaving = [uid for uid in self.battle.units if uid not in present]
        if leaving:
            self.evolve(only=set(leaving))
        for uid in leaving:
            self.battle.units.pop(uid)
            for intel in self.battle.intel.values():
                intel.spotted.pop(uid, None)
                intel.last_known.pop(uid, None)
            self.owner.pop(uid, None)

    # ------------------------------------------------------------------ 同格救助與後送

    def _friendly_present(self, exclude: str | None = None) -> bool:
        """本格還有其他我方小隊（可作戰）或固守中的殘部。"""
        if any(o.id != exclude and o.pos == self.cell and o.status == "active" and o.alive_members()
               for o in self.world.squads.values()):
            return True
        return any(r.status == "holding" and r.pos == self.cell and r.alive_members()
                   for r in getattr(self.world, "remnants", {}).values())

    def _left_behind_present(self) -> set:
        """原小隊撤離後留在本格、由友軍照顧的倒地者：仍在戰鬥中（可被急救、被敵人補槍）。"""
        keep = set()
        for uid, (sid, m) in list(self.left_behind.items()):
            if m.lost:
                self.left_behind.pop(uid, None)
                continue
            keep.add(uid)
            if uid not in self.battle.units:
                u = self._unit_from_member(uid, m, False)
                u.side = self.side_of(sid)
                self.battle.units[uid] = u
            self.owner[uid] = ("s", sid, m)
        return keep

    def medevac(self, reason: str) -> list[tuple[str, object, bool]]:
        """交戰結束（或友軍全離開）：留在本格、還活著的倒地者後送回預備兵池，標記原小隊。
        回傳 [(原小隊 id, Member, 是否進池)]。友軍都不在了 → 陣亡。"""
        from engine import reserve
        out = []
        cared = self._friendly_present()
        for uid, (sid, m) in list(self.left_behind.items()):
            self.left_behind.pop(uid, None)
            self.battle.units.pop(uid, None)
            self.owner.pop(uid, None)
            if m.lost:
                continue
            if not cared:
                m.hp, m.down = 0, False
                out.append((sid, m, False))
                continue
            m.down, m.down_tick, m.stabilized = False, None, False
            m.tpos = None
            ok = reserve.give_back(self.world, m, home=sid)
            out.append((sid, m, ok))
        return out

    def _unit_from_member(self, uid: str, m, leader: bool) -> Unit:
        pos = tuple(m.tpos) if m.tpos else (12, 1, 0)
        u = Unit(uid, SQUAD_SIDE, pos, weapon=m.weapon, posture=m.posture, facing=tuple(m.facing),
                 ammo=m.ammo if m.ammo is not None else -1, damage=dict(m.damage), leader=leader,
                 grenades=m.grenades, smokes=m.smokes, flashes=m.flashes, prof=dict(m.prof),
                 traits=dict(m.traits), persona=m.persona)
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
                 flashes=sd.get("flashes", 0), prof=dict(sd.get("prof", {})),
                 traits=dict(sd.get("traits", {})), persona=sd.get("persona", 0.0))
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
            if kind in ("s", "g"):
                m = ref
                m.tpos = list(u.pos)
                m.posture, m.facing = u.posture, list(u.facing)
                m.damage, m.torso_hits = dict(u.damage), u.torso_hits
                m.ammo, m.grenades, m.smokes, m.flashes = u.ammo, u.grenades, u.smokes, u.flashes
                m.prof = dict(u.prof)
                m.suppression = u.suppression
                m.down, m.down_tick, m.stabilized = u.down and not u.dead, u.down_tick, u.stabilized
                m.hp = 0 if u.dead else max(10, 100 - 30 * len([k for k, v in u.damage.items() if v]))
                if kind == "g" and u.surrendered:
                    m.hp = 0
            else:
                sd = ref
                sd.update(tpos=list(u.pos), posture=u.posture, facing=list(u.facing), damage=dict(u.damage),
                          torso_hits=u.torso_hits, ammo=u.ammo, grenades=u.grenades, smokes=u.smokes,
                          flashes=u.flashes,
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
        intent = self._squad_intent(s)
        mark = s.action.mark
        if mark:
            node = (int(mark[0]), int(mark[1]), 0)
            kind = s.action.mark_kind
            if kind == "fire":
                intent.focus = node
            elif kind == "rally" and intent.kind not in ("retreat",):
                intent = Intent("advance", goal=node)
            elif kind == "flank" and intent.kind not in ("retreat",):
                intent = Intent("flank", goal=node, side=s.action.flank or "left")
        return intent

    def _squad_intent(self, s: Squad) -> Intent:
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
        if a == "breach":
            p = s.action.breach or {}
            return Intent("breach", room=p.get("room"), prep=p.get("prep"), entry=p.get("entry"),
                          tempo=p.get("tempo"))
        if a == "move" and s.action.path:
            return Intent("retreat", toward=self._toward(s.pos, s.action.path[0]))
        if a == "hide":
            return Intent("ambush", ambush_range=12)
        return Intent("hold")

    def enemy_intent(self, e: Enemy) -> Intent:
        intent = self.commander.decide(e)
        self.intents[f"e:{e.id}"] = intent
        return intent

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
            side = self.ensure_side(s)
            intent = self.squad_intent(s)
            key = f"s:{s.id}"
            prev = self.intents.get(key)
            if prev is None or prev.kind != intent.kind or (s.action.mark and prev.goal != intent.goal) or (
                    intent.kind == "breach" and (prev.room, prev.prep, prev.entry, prev.tempo)
                    != (intent.room, intent.prep, intent.entry, intent.tempo)):
                self.ais[side].new_intent()
            self.intents[key] = intent
            b.morale[side] = s.morale * MORALE_TO_TAC
            plans.update(self.ais[side].plan(intent, units))
        for e in self.world.enemies.values():
            if e.pos != self.cell or not e.alive:
                continue
            units = [b.units[u] for u in b.units if self.owner[u][1] == e.id and b.units[u].alive]
            if units:
                plans.update(self.ais[ENEMY_SIDE].plan(self.enemy_intent(e), units))
        for r in getattr(self.world, "remnants", {}).values():
            if r.status != "holding" or r.pos != self.cell:
                continue
            units = [b.units[u] for u in b.units if self.owner[u][1] == r.id and b.units[u].alive]
            if units:
                b.morale[REMNANT_SIDE] = float(r.morale)
                plans.update(self.ais[REMNANT_SIDE].plan(Intent("hold"), units))
        before = {uid: (u.down, u.dead, dict(u.damage)) for uid, u in b.units.items()}
        ammo_before = {uid: u.ammo for uid, u in b.units.items()}
        if self.recorder is not None:
            self.recorder.before()
        if self.pre_step is not None:
            self.pre_step(self, plans)
        b.step(plans)
        if self.recorder is not None:
            self.last_frame = self.recorder.after(self.world.tick, self.recorder.supports())
        self._track_experience(before)
        out = self._collect(before, ammo_before)
        for s in self.world.squads.values():
            if s.pos != self.cell or s.status != "active":
                continue
            side = self.side_of(s.id)
            ai = self.ais.get(side)
            if ai is None:
                continue
            mark = self.report_marks.get(side, 0)
            for r in ai.reports[mark:]:
                text = r.split(" ", 1)[1] if " " in r else r
                if "請求新指示" in text or "士氣崩潰" in text or "架向門口" in text:
                    out["squads"].setdefault(s.id, []).append(("request", text))
            self.report_marks[side] = len(ai.reports)
            s.morale = int(round(b.morale.get(side, 70) / MORALE_TO_TAC))
        for r in getattr(self.world, "remnants", {}).values():
            if r.status == "holding" and r.pos == self.cell:
                r.morale = int(round(b.morale.get(REMNANT_SIDE, r.morale)))
        self.sync_out()
        self._remnant_events(out, before)
        self._handle_exits(out)
        if self.left_behind and not self._friendly_present():
            out.setdefault("medevac", []).extend(self.medevac("abandoned"))
        active = any(u.fired for u in b.units.values()) or any(
            l.startswith(f"T{b.tick} ") and ("發現" in l or "察覺" in l) for l in b.log)
        seen = any(b.units[u].alive for side in b.intel for u in b.intel[side].spotted if u in b.units)
        was_hot = self.hot() and self.quiet < IDLE_END and "contact" in self.reported
        self.quiet = 0 if (active or seen) else self.quiet + 1
        if was_hot and not self.hot():
            self.reported = {x for x in self.reported if not x.startswith("contact")}
            self.evolve()
            for s in self.world.squads.values():
                if s.pos == self.cell and s.status == "active":
                    out["squads"].setdefault(s.id, []).append(("disengage", "與敵軍脫離接觸"))
        return out

    def _remnant_events(self, out: dict, before: dict):
        """殘部本 tick 的重大事件（給無線電）：有人倒地／陣亡、投降。"""
        b = self.battle
        for uid, u in b.units.items():
            kind, gid, ref = self.owner.get(uid, (None, None, None))
            if kind != "g":
                continue
            was_down, was_dead, _ = before.get(uid, (False, False, {}))
            ev = out.setdefault("remnants", {}).setdefault(gid, [])
            if u.dead and not was_dead:
                ev.append(("casualty", ref.name))
            elif u.down and not was_down:
                ev.append(("down", ref.name))
            if u.surrendered and f"surr:{ref.name}" not in self._seen_surrender:
                self._seen_surrender.add(f"surr:{ref.name}")
                ev.append(("surrender", ref.name))
                r = self.world.remnants.get(gid)
                if r is not None:
                    r.radio_flags.append(f"surrendered:{ref.name}")

    def _track_experience(self, before):
        """記錄每名我方個體本場的經歷（交戰結束時用來演化性格）。"""
        b = self.battle
        tick_log = [l for l in b.log if l.startswith(f"T{b.tick} ")]
        downs = [(l.split()[1], l.split("倒地（")[1].rstrip("）")) for l in tick_log if "倒地（" in l]
        for uid, u in b.units.items():
            kind = self.owner.get(uid, ("",))[0]
            if kind != "s":
                continue
            x = self.exp.setdefault(uid, set())
            if u.fired:
                x.add("fought")
            if u.pinned or u.suppression >= 50:
                x.add("pressed")
            was_down, _, _ = before.get(uid, (False, False, {}))
            if u.down and not was_down:
                x.add("downed")
            if u.damage:
                x.add("hit")
        mates_down = {t for t, _ in downs if self.owner.get(t, ("",))[0] == "s"}
        for uid in list(self.exp):
            u = b.units.get(uid)
            if u is None:
                continue
            if mates_down - {uid}:
                self.exp[uid].add("saw_loss")
            for target, shooter in downs:
                if shooter == uid and self.owner.get(target, ("",))[0] == "e":
                    intent = self.intents.get(f"s:{self.owner[uid][1]}")
                    if intent and intent.kind in ("assault", "breach"):
                        self.exp[uid].add("assault_kill")
        for uid, tid in getattr(b, "last_drags", {}).items():
            if self.owner.get(uid, ("",))[0] == "s":
                self.exp.setdefault(uid, set()).add("dragged")

    def evolve(self, only: set | None = None):
        """交戰結束（或個體離開這場戰鬥）：依經歷小幅調整性格，活下來的人人格值成長（engine/persona.py）。
        只有實際接觸過（本場有開火、被壓制、受傷或看到傷亡）的人才算一場。"""
        from engine import persona
        for uid in list(self.exp):
            if only is not None and uid not in only:
                continue
            flags = self.exp.pop(uid)
            kind, gid, ref = self.owner.get(uid, (None, None, None))
            if kind != "s" or ref is None or ref.lost:
                continue
            m = ref
            innate = m.innate or {}
            if "pressed" in flags and not m.down:
                persona.nudge(m, "comp", persona.TRAIT_STEP)
            if "saw_loss" in flags:
                key = "bond" if innate.get("bond", 0) >= innate.get("caut", 0) else "caut"
                persona.nudge(m, key, persona.TRAIT_STEP)
            if "assault_kill" in flags:
                persona.nudge(m, "aggr", persona.TRAIT_STEP)
            if "downed" in flags or "hit" in flags:
                persona.nudge(m, "caut", 2 * persona.TRAIT_STEP)
            if "dragged" in flags:
                persona.nudge(m, "bond", persona.TRAIT_STEP)
            if flags:
                persona.grow(m)
                m.battles += 1

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
            spotted_now = [l for l in b.log if l.startswith(f"T{b.tick} {SQUAD_SIDE}") and " 發現 " in l]
            under_fire = any(self.owner.get(s["shooter"], ("",))[0] == "e" for s in b.shots[b._shot_mark:]) or any(
                e for e in ev if e[0] in ("down", "casualty", "wounded"))
            ckey = f"contact:{gid}"
            if (spotted_now or under_fire or kills) and ckey not in self.reported:
                self.reported.add(ckey)
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
                    left_uids = [uid for uid in mine
                                 if b.units[uid].down and not b.units[uid].exited and not b.units[uid].dead]
                    left_behind = [self.owner[uid][2] for uid in left_uids]
                    cared = self._friendly_present(exclude=s.id)
                    for uid in left_uids:
                        m = self.owner[uid][2]
                        if cared:
                            s.members.remove(m)
                            self.left_behind[uid] = (s.id, m)
                            out.setdefault("left_behind", []).append((s.id, m.name))
                        else:
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
        for e in list(self.world.enemies.values()):
            if e.pos != self.cell or not e.alive:
                continue
            mine = [uid for uid, o in self.owner.items() if o[1] == e.id and uid in b.units]
            active = [uid for uid in mine if b.units[uid].alive]
            exited = [uid for uid in mine if b.units[uid].exited]
            if not exited or active:
                continue
            intent = self.intents.get(f"e:{e.id}")
            toward = intent.toward if intent and intent.toward else (0, 1)
            dest = self.exit_cell(e.pos, toward)
            if not dest:
                continue
            exited_ids = {self.owner[uid][2]["id"] for uid in exited}
            e.soldiers = [sd for sd in e.soldiers if sd["id"] in exited_ids]
            for uid in exited:
                b.units[uid].exited = False
            frm = e.pos
            e.pos = dest
            e.engaged_with = None
            e.strength = len([sd for sd in e.soldiers if not sd.get("down")])
            if e.strength <= 0:
                e.alive = False
            if e.path and e.path[0] == dest:
                e.path.pop(0)
            elif e.order == "withdraw" and e.goal:
                from engine.enemy_ai import send
                send(e, "withdraw", e.goal)
            elif e.order != "withdraw":
                from engine.enemy_ai import withdraw
                withdraw(self.world, e)
            out.setdefault("enemy_moved", []).append((e.id, frm, dest))

    @staticmethod
    def _aim_text(u: Unit) -> str:
        """架槍與閃光狀態（給隊長看）。"""
        parts = []
        if getattr(u, "braced", None):
            parts.append(f"已架槍，朝{_dir(u.braced[0], u.braced[1]).rstrip('方，')}")
        if getattr(u, "dazzled", 0):
            parts.append("光學過載")
        return "，".join(parts)

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
        exposed = []
        for uid, u in mine:
            name = self.owner[uid][2].name
            if u.dead:
                continue
            state = "倒地" if u.down else ("被壓制" if u.pinned else ("受壓制" if u.suppression > 0 else "可作戰"))
            ammo = u.ammo / WEAPONS[u.weapon]["ammo"]
            ammo_w = "彈藥充足" if ammo > 0.6 else ("彈藥過半" if ammo > 0.3 else ("彈藥見底" if ammo > 0 else "沒子彈"))
            why = b.believes_seen(u) if u.alive else None
            if why:
                exposed.append(name)
            lines.append(f"{name}（{WEAPON_LABELS.get(u.weapon, u.weapon)}，{state}，{ammo_w}，"
                         f"破片{u.grenades} 煙霧{u.smokes} 閃光{u.flashes}"
                         f"{'，' + aim if (aim := self._aim_text(u)) else ''}"
                         f"{'，⚠' + EXPOSE_LABELS[why] if why else ''}）")
        if exposed:
            awareness = f"敵方應已發現 {'、'.join(exposed)} 的位置"
        elif any(u.alive for _, u in mine):
            awareness = "敵方似乎還沒察覺我們（沒有中彈、沒有子彈掠過、沒有被盯上）"
        else:
            awareness = "—"
        enemies = []
        for uid, (node, tick) in sorted(b.intel[SQUAD_SIDE].last_known.items()):
            u = b.units.get(uid)
            if not u or not u.alive:
                continue
            dx, dy = node[0] - cx, node[1] - cy
            dist = int(round(2 * (dx * dx + dy * dy) ** 0.5))
            seen = "看得到" if uid in b.intel[SQUAD_SIDE].spotted else f"{(b.tick - tick) * 10} 秒前的位置"
            enemies.append(f"{_dir(dx, dy)}約 {dist} 公尺（{seen}{'，在樓上' if node[2] > 0 else ''}）")
        friends = self.friends_text(s)
        return ("戰術狀況（本格 50 公尺見方）：\n"
                f"  我方：{'；'.join(lines)}\n"
                f"{'  同格友軍：' + friends + chr(10) if friends else ''}"
                f"  已知敵人（與同格友軍共享情報）：{'；'.join(enemies) if enemies else '目前看不到，位置不明'}\n"
                f"  暴露判斷：{awareness}\n"
                f"{'  附近建築：' + bt + chr(10) if (bt := self.buildings_text(s)) else ''}"
                f"  小隊投擲物：破片 {sum(u.grenades for _, u in mine if u.alive)}、"
                f"煙霧 {sum(u.smokes for _, u in mine if u.alive)}、閃光 {sum(u.flashes for _, u in mine if u.alive)}\n"
                f"  士氣狀態：{ {'normal': '穩定', 'shaken': '動搖', 'rout': '崩潰', 'surrender': '崩潰'}.get(b.morale_state(self.side_of(s.id)), '穩定') }")

    def friends_text(self, s: Squad) -> str:
        """同格其他我方小隊與殘部（顯示呼號；同名呼號加指揮官名）。"""
        out = []
        for o in self.world.squads.values():
            if o.id == s.id or o.pos != self.cell or o.status != "active":
                continue
            n = len([m for m in o.members if m.alive])
            out.append(f"{label_of(self.world, o)} {n} 人")
        for r in getattr(self.world, "remnants", {}).values():
            if r.status == "holding" and r.pos == self.cell:
                out.append(f"失聯友軍殘部 {len(r.alive_members())} 人")
        return "；".join(out)

    def building_ids(self, s: Squad) -> list[int]:
        """可破門的建築（有門、小隊不在裡面）。"""
        b = self.battle
        mine = [b.units[uid] for uid, o in self.owner.items() if o[1] == s.id and uid in b.units and b.units[uid].alive]
        return [r["id"] for r in b.map.rooms
                if r["doors"] and not any(b.map.room_at(u.pos) is r for u in mine)]

    def buildings_text(self, s: Squad) -> str:
        b = self.battle
        mine = [b.units[uid] for uid, o in self.owner.items() if o[1] == s.id and uid in b.units and b.units[uid].alive]
        if not mine:
            return ""
        cx = sum(u.pos[0] for u in mine) / len(mine)
        cy = sum(u.pos[1] for u in mine) / len(mine)
        lk = b.intel[SQUAD_SIDE].last_known
        rows = []
        for rid in self.building_ids(s):
            r = b.map.rooms[rid]
            x0, y0, x1, y1 = r["rect"]
            dx, dy = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
            dist = int(round(2 * (dx * dx + dy * dy) ** 0.5))
            if dist > 40:
                continue
            known = sum(1 for uid, (node, _) in lk.items() if b.units.get(uid) and b.units[uid].alive
                        and b.map.room_at(node) is r)
            rows.append((dist, f"建築{rid}（{_dir(dx, dy)}約 {dist} 公尺，{len(r['doors'])} 扇門"
                               f"{f'，已知 {known} 名敵人在內' if known else ''}）"))
        rows.sort()
        return "；".join(t for _, t in rows[:4])

    def over(self) -> bool:
        """同格內有一方已無可作戰個體 → 結束（戰鬥狀態持續存在於同格雙方都在時）。"""
        b = self.battle
        sides = {u.side for u in b.units.values() if u.alive}
        return not any(b.hostile(x, y) for x in sides for y in sides)

    def hot(self) -> bool:
        """近期有接觸（射擊、發現、可見敵人）。安靜 IDLE_END tick 後視為脫離接觸。"""
        return self.quiet < IDLE_END

    def finish(self) -> list:
        """結束：寫回並清理戰鬥暫存狀態（倒地計時延續到作戰層由整補處理），演化性格，後送留下的倒地者。"""
        self.sync_out()
        self.evolve()
        for e in self.world.enemies.values():
            if e.pos == self.cell:
                e.engaged_with = None
        return self.medevac("end")


def label_of(world: World, s: Squad) -> str:
    """小隊在共用畫面的名稱：呼號；有其他指揮官的同名呼號時加上指揮官名，例如「Alpha（甲）」。"""
    dup = any(o.id != s.id and o.callsign == s.callsign and o.commander != s.commander for o in world.squads.values())
    if not dup:
        return s.callsign
    c = world.commanders.get(s.commander or "")
    return f"{s.callsign}（{c.name if c else s.commander}）"


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
    from engine import remnants
    standoff = set()
    for r in getattr(world, "remnants", {}).values():
        if remnants.in_standoff(world, r):
            standoff.add(r.pos)
            continue
        if r.status == "holding" and any(e.alive and e.pos == r.pos for e in world.enemies.values()):
            cells.add(r.pos)
    return sorted(c for c in cells if c not in standoff)
