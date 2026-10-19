from __future__ import annotations

import asyncio
import logging
import random
import re

from engine import enemy_ai, missions, obedience, prompts, roster, scenario, store, units
from engine.models import Action, Commander, Enemy, Member, Squad, World
from lib.llm import LLMClient

log = logging.getLogger(__name__)

TICK_SECONDS_DEFAULT = 10
STEP_TICKS = {"move": 3, "scout": 2, "retreat": 1}
ACTION_LABELS = {
    "keep": "維持目前行動",
    "hold": "原地固守",
    "move": "移動（可指定任一格，自動規劃路線）",
    "scout": "偵察相鄰區塊（不移動）",
    "hide": "隱蔽待機",
    "attack": "攻擊接觸中的敵軍",
    "retreat": "撤退到相鄰區塊（脫離接觸）",
}
SHORT_LABELS = {"hold": "固守", "move": "移動", "scout": "偵察", "hide": "隱蔽", "attack": "攻擊", "retreat": "撤退"}
TARGET_ACTIONS = {"move", "scout", "retreat"}
MEMORY_LIMIT = 12
EXPERIENCE_LIMIT = 60
DIALOGUE_LIMIT = 20
LOG_LIMIT = 12
MINOR_GAP = 3
REST_FULL = 6
SIDE_INTERVAL = (40, 70)
SIDE_MAX = 1
IDLE_CHATTER = (30, 60)
RECRUIT_GAP = 12
MISSION_OUTBOX = "mission"
REFLECT_GAP = 6
BELIEF_LIMIT = 6
SUPPLY_DELAY = 6
SUPPLY_COOLDOWN = 60
SUPPLY_INTERCEPT = 0.6
REINFORCE_COOLDOWN = 120
ACTIVE_SQUAD_CAP = 4
EVENT_LOG_LIMIT = 8
ACK_PREFIX = re.compile(r"^\s*(收到|了解|明白|是的?|Roger|Copy)[\s，,。.!！、…]*", re.IGNORECASE)


class Simulation:
    def __init__(self, llm: LLMClient, save_path: str = "./data/world.json",
                 tick_seconds: int = TICK_SECONDS_DEFAULT, rng: random.Random | None = None):
        self.llm = llm
        self.save_path = save_path
        self.tick_seconds = tick_seconds
        self.rng = rng or random.Random()
        self.lock = asyncio.Lock()
        self.outbox: list[dict] = []
        self.dirty: set[str] = set()
        self.pending_recruits: list[tuple[str, str]] = []
        self.new_cards: list[dict] = []
        self.enemy_log: list[str] = []
        self.enemy_ai_enabled = True
        self.engagements: dict = {}
        self.tac_events: dict = {}
        self.world: World = store.load(save_path) or scenario.new_world()

    def save(self):
        roster.sync_world(self.world)
        store.save(self.world, self.save_path)

    def reset(self, keep_squads: bool = True) -> list[tuple[str, str | None]]:
        roster = [(s.id, s.commander) for s in self.world.squads.values()] if keep_squads else []
        old = self.world
        self.world = scenario.new_world()
        self.world.discord = old.discord
        self.world.running = old.running
        for cid, c in old.commanders.items():
            c.reset_knowledge()
            self.world.commanders[cid] = c
        self.outbox.clear()
        self.dirty = set(self.world.commanders)
        self.save()
        return roster

    def backfill_beliefs(self, load_card) -> list[str]:
        filled = []
        for squad in self.world.squads.values():
            if squad.beliefs:
                continue
            card = load_card(squad.id)
            if not card:
                continue
            fresh = scenario.squad_from_card(card)
            squad.beliefs = fresh.beliefs
            squad.tendencies = fresh.tendencies
            if not squad.long_memory:
                squad.long_memory = fresh.long_memory
            filled.append(squad.id)
        if filled:
            self.save()
        return filled

    def wipe(self):
        import os
        import shutil
        if os.path.exists(self.save_path):
            shutil.copyfile(self.save_path, f"{self.save_path}.bak")
        self.world = scenario.new_world()
        self.outbox.clear()
        self.dirty.clear()
        self.pending_recruits.clear()
        self.save()

    def drain(self) -> list[dict]:
        items, self.outbox = self.outbox, []
        return items

    def seconds(self, ticks: int) -> int:
        return ticks * self.tick_seconds

    def _dirty(self, squad: Squad):
        if squad.commander:
            self.dirty.add(squad.commander)

    def ensure_commander(self, commander_id: str, name: str) -> Commander:
        c = self.world.commanders.get(commander_id)
        if c is None:
            c = Commander(id=commander_id, name=name)
            self.world.commanders[commander_id] = c
            self.save()
        return c

    # ------------------------------------------------------------------ missions

    def _start_mission(self, c: Commander, spec: dict, line: str):
        m = {**spec, "line": line, "id": self.world.next_id("m"), "started": self.world.tick,
             "deadline": self.world.tick + spec["time_limit"], "progress": 0, "spawned": []}
        for e in spec.get("spawn", []):
            eid = self.world.next_id("e")
            if not enemy_ai.can_spawn(self.world, e["strength"]):
                continue
            self.world.enemies[eid] = Enemy(id=eid, pos=e["pos"], strength=e["strength"], route=list(e["route"]),
                                            move_every=e.get("move_every", 999), cooldown=e.get("move_every", 999),
                                            tag="garrison", home=e["pos"], max_strength=e["strength"])
            m["spawned"].append(eid)
        if m["type"] == "crate":
            oid = self.world.next_id("o")
            self.world.objects[oid] = {"type": "crate", "cell": m["cell"], "mission": m["id"]}
            m["object"] = oid
        if m["type"] == "rescue":
            oid = self.world.next_id("o")
            self.world.objects[oid] = {"type": "remnant", "cell": m["cell"], "mission": m["id"]}
            m["object"] = oid
        c.missions.append(m)
        self.outbox.append({"type": MISSION_OUTBOX, "commander": c.id, "event": "start", "mission": dict(m)})
        self.dirty.add(c.id)

    def _ensure_missions(self, c: Commander):
        if not any(s.status == "active" for s in self.world.squads_of(c.id)):
            return
        if not any(m["line"] == "main" for m in c.missions):
            spec = missions.MAIN_LINE[c.main_idx % len(missions.MAIN_LINE)]
            self._start_mission(c, spec, "main")
        if c.next_side == 0:
            c.next_side = self.world.tick + self.rng.randint(*SIDE_INTERVAL)
        sides = [m for m in c.missions if m["line"] == "side"]
        if len(sides) < SIDE_MAX and self.world.tick >= c.next_side:
            busy = {m["cell"] for m in c.missions}
            self._start_mission(c, missions.roll_side(self.rng, busy), "side")
            c.next_side = self.world.tick + self.rng.randint(*SIDE_INTERVAL)

    def _end_mission(self, c: Commander, m: dict, success: bool, reason: str = ""):
        c.missions.remove(m)
        for eid in m.get("spawned", []):
            e = self.world.enemies.get(eid)
            if e and e.alive and not e.engaged_with:
                enemy_ai.withdraw(self.world, e)
        if m.get("object"):
            self.world.objects.pop(m["object"], None)
        c.history.append({"t": self.world.tick, "title": m["title"], "line": m["line"],
                          "success": success, "reason": reason})
        c.history = c.history[-20:]
        if m["line"] == "main":
            c.main_idx += 1
        self.outbox.append({"type": MISSION_OUTBOX, "commander": c.id, "event": "done" if success else "fail",
                            "mission": dict(m), "reason": reason})
        self.dirty.add(c.id)
        squads = [s for s in self.world.squads_of(c.id) if s.status == "active"]
        for s in squads:
            text = prompts.EVENT_MISSION_DONE.format(title=m["title"]) if success \
                else prompts.EVENT_MISSION_FAIL.format(title=m["title"], reason=reason)
            self._experience(s, "mission_done" if success else "mission_fail", text)
            self._want_reflect(s, f"任務「{m['title']}」{'達成' if success else '失敗'}")
        if success:
            for s in squads:
                s.morale = min(100, s.morale + 8)
            if squads:
                needy = min(squads, key=lambda s: len(s.alive_members()) - s.max_size)
                if len(needy.alive_members()) < needy.max_size:
                    self.pending_recruits.append((needy.id, "任務完成後，指揮部撥補的新兵"))

    def _check_missions(self, c: Commander):
        own = [s for s in self.world.squads_of(c.id) if s.status == "active"]
        for m in list(c.missions):
            cell = m["cell"]
            here = [s for s in own if s.pos == cell]
            enemies_here = [eid for eid in m.get("spawned", []) if self.world.enemies.get(eid)
                            and self.world.enemies[eid].alive]
            if not own:
                self._end_mission(c, m, False, "沒有可作戰的小隊")
                continue
            t = m["type"]
            done = False
            if t == "reach":
                done = bool(here)
            elif t == "hold":
                if here and not enemies_here and not self._enemy_at(cell):
                    m["progress"] += 1
                done = m["progress"] >= m["hold_ticks"]
            elif t == "clear":
                if m.get("clear_all"):
                    done = self._enemy_at(cell) is None and bool(here)
                else:
                    done = not enemies_here
            elif t == "extract":
                done = all(s.pos == cell for s in own)
            elif t in ("crate", "rescue"):
                done = m.get("object") not in self.world.objects
            if done:
                self._end_mission(c, m, True)
            elif self.world.tick >= m["deadline"]:
                self._end_mission(c, m, False, "超過時限")

    def missions_brief(self, commander_id: str) -> str:
        c = self.world.commanders.get(commander_id)
        if not c or not c.missions:
            return "（暫無任務）"
        return "｜".join(f"{'主線' if m['line'] == 'main' else '支線'}：{m['title']}" for m in c.missions)

    def missions_text(self, commander_id: str) -> str:
        c = self.world.commanders.get(commander_id)
        if not c or not c.missions:
            return "（暫無任務）"
        return "\n".join(missions.describe(m, self.world.tick) for m in c.missions)

    def _pickup_objects(self, squad: Squad, major: list):
        for oid, obj in list(self.world.objects.items()):
            if obj["cell"] != squad.pos or squad.contact:
                continue
            if obj["type"] == "crate":
                from engine.tactical.units import WEAPONS
                squad.ammo = min(100, squad.ammo + 50)
                for m in squad.alive_members():
                    if m.ammo is not None:
                        cap = WEAPONS[m.weapon]["ammo"]
                        m.ammo = min(cap, m.ammo + cap // 2)
                del self.world.objects[oid]
                major.append(("crate", f"在 {squad.pos} 回收補給箱", prompts.EVENT_CRATE.format(
                    cell=scenario.cell_desc(squad.pos))))
            elif obj["type"] == "remnant":
                del self.world.objects[oid]
                self.pending_recruits.append((squad.id, f"在 {scenario.cell_desc(squad.pos)} 找到的友軍殘部"))
                squad.morale = min(100, squad.morale + 5)

    # ------------------------------------------------------------------ reflection (1.5)

    def _want_reflect(self, squad: Squad, reason: str):
        if reason not in squad.reflect_reasons:
            squad.reflect_reasons.append(reason)

    async def _reflect(self, squad: Squad):
        reasons, squad.reflect_reasons = squad.reflect_reasons, []
        squad.last_reflect = self.world.tick
        leader = squad.leader
        system = prompts.REFLECT_SYSTEM.format(
            name=squad.acting_leader(), callsign=squad.callsign,
            personality=leader.get("personality", ""), background=leader.get("background", ""),
            world=units.WORLD_NOTE,
        )
        user = prompts.REFLECT_USER.format(
            reasons="；".join(reasons),
            beliefs=obedience.beliefs_text(squad, n=BELIEF_LIMIT),
            tendencies=obedience.tendency_text(squad),
            long_memory=squad.long_memory or "（無）",
            experiences=self.experience_text(squad, n=15, show_interp=True),
        )
        data = await self.llm.chat_json(system, user, temperature=0.7, priority=3)
        if not data:
            squad.reflect_reasons = reasons
            return
        before = {b["text"] for b in squad.beliefs}
        old_t = dict(squad.tendencies)
        new_beliefs = []
        for b in (data.get("beliefs") or [])[:BELIEF_LIMIT]:
            if isinstance(b, str):
                b = {"text": b}
            if not isinstance(b, dict) or not str(b.get("text", "")).strip():
                continue
            text = str(b["text"]).strip()[:60]
            origin = "protocol" if b.get("origin") == "protocol" and text in before else "experience"
            prev = next((x for x in squad.beliefs if x["text"] == text), None)
            new_beliefs.append({"text": text, "origin": origin, "t": prev["t"] if prev else self.world.tick})
        if new_beliefs:
            squad.beliefs = new_beliefs
        if isinstance(data.get("tendencies"), dict):
            squad.tendencies = obedience.normalize_tendencies(data["tendencies"])
        summary = str(data.get("summary") or "").strip()
        if summary:
            squad.long_memory = (f"{squad.long_memory} {summary}" if squad.long_memory else summary)[-400:]
        added = [b["text"] for b in squad.beliefs if b["text"] not in before]
        dropped = [t for t in before if t not in {b["text"] for b in squad.beliefs}]
        changed = {k: (old_t.get(k), v) for k, v in squad.tendencies.items() if old_t.get(k) != v}
        record = self._experience(squad, "reflect", f"反思（{'；'.join(reasons)}）", remember=False,
                                  added=added, dropped=dropped, tendencies=changed)
        record["interp"] = {"thought": data.get("thought")}

    # ------------------------------------------------------------------ front-line supply (1.6)

    async def request_supply(self, squad_id: str) -> tuple[bool, str]:
        async with self.lock:
            squad = self.world.squads.get(squad_id)
            if not squad or squad.status != "active":
                return False, "找不到該小隊。"
            ok, msg = self._request_supply_locked(squad, via_radio=False)
            if ok and squad.linked:
                self._system(squad, f"📦 指揮部：{msg}")
            self.save()
            return ok, msg

    async def deploy_generated(self, card: dict, commander_id: str) -> Squad:
        async with self.lock:
            squad = scenario.squad_from_card(card)
            squad.commander = commander_id
            self.world.squads[squad.id] = squad
            self._update_intel(squad)
            self._dirty(squad)
            self.save()
            return squad

    def _request_supply_locked(self, squad: Squad, via_radio: bool) -> tuple[bool, str]:
        c = self.world.commanders.get(squad.commander or "")
        if c is None:
            return False, "找不到指揮官。"
        if any(o["type"] == "airdrop" and o.get("commander") == c.id for o in self.world.objects.values()):
            return False, "已有一架補給空投在途中。"
        wait = c.supply_ready - self.world.tick
        if wait > 0:
            self._event(c, f"補給請求被拒：冷卻中（約 {self.seconds(wait)} 秒）")
            if via_radio:
                self._experience(squad, "supply_denied", prompts.EVENT_SUPPLY_DENIED.format(seconds=self.seconds(wait)))
            return False, f"補給空投冷卻中，約 {self.seconds(wait)} 秒後可再請求。"
        oid = self.world.next_id("o")
        self.world.objects[oid] = {"type": "airdrop", "cell": squad.pos, "squad": squad.id, "commander": c.id,
                                   "arrive": self.world.tick + SUPPLY_DELAY, "requested": self.world.tick}
        c.supply_ready = self.world.tick + SUPPLY_COOLDOWN
        self._event(c, f"{squad.callsign} 請求補給空投 → {squad.pos}（約 {self.seconds(SUPPLY_DELAY)} 秒）")
        self._experience(squad, "supply_requested", prompts.EVENT_SUPPLY_INBOUND.format(
            seconds=self.seconds(SUPPLY_DELAY), cell=scenario.cell_desc(squad.pos)))
        self.dirty.add(c.id)
        return True, f"補給空投已出發，約 {self.seconds(SUPPLY_DELAY)} 秒後抵達 {scenario.cell_desc(squad.pos)}。"

    def _airdrops(self) -> list[tuple[Squad, str]]:
        out = []
        for oid, o in list(self.world.objects.items()):
            if o["type"] != "airdrop" or self.world.tick < o["arrive"]:
                continue
            del self.world.objects[oid]
            squad = self.world.squads.get(o["squad"])
            c = self.world.commanders.get(o["commander"])
            cell = o["cell"]
            threat = self._enemy_at(cell) or any(self._enemy_at(n) for n in scenario.adjacent(cell))
            intercepted = threat and self.rng.random() < SUPPLY_INTERCEPT
            if intercepted:
                if c:
                    self._event(c, f"補給空投在 {cell} 被敵軍攔截")
                if squad and squad.status == "active":
                    out.append((squad, prompts.EVENT_SUPPLY_LOST.format(cell=scenario.cell_desc(cell))))
                continue
            if squad and squad.status == "active" and squad.pos == cell:
                from engine.tactical.units import WEAPONS
                squad.ammo = min(100, squad.ammo + 60)
                for m in squad.alive_members():
                    m.hp = min(100, m.hp + 20)
                    if m.ammo is not None:
                        cap = WEAPONS[m.weapon]["ammo"]
                        m.ammo = min(cap, m.ammo + int(cap * 0.6))
                if c:
                    self._event(c, f"補給空投在 {cell} 送達 {squad.callsign}")
                out.append((squad, prompts.EVENT_SUPPLY_DROP.format(cell=scenario.cell_desc(cell))))
            else:
                oid2 = self.world.next_id("o")
                self.world.objects[oid2] = {"type": "crate", "cell": cell, "mission": None}
                if c:
                    self._event(c, f"補給空投落在 {cell}，但小隊已離開，留下補給箱")
        return out

    def _event(self, c: Commander, text: str):
        c.events.append(f"T{self.world.tick} {text}")
        c.events = c.events[-EVENT_LOG_LIMIT:]

    # ------------------------------------------------------------------ reinforcements (1.6)

    def _need_reinforce(self, c: Commander) -> bool:
        active = [s for s in self.world.squads_of(c.id) if s.status == "active"]
        if len(active) >= ACTIVE_SQUAD_CAP or self.world.tick < c.reinforce_ready:
            return False
        if not active:
            return any(s.status == "destroyed" for s in self.world.squads_of(c.id))
        total = sum(len(s.members) for s in active)
        alive = sum(len(s.alive_members()) for s in active)
        return alive <= total // 2

    async def generate_card(self, source: str, members: int = 3) -> dict | None:
        taken = {s.callsign for s in self.world.squads.values()}
        names = {m.name for s in self.world.squads.values() for m in s.members}
        system = prompts.CARD_SYSTEM.format(world=units.WORLD_NOTE,
                                            capabilities=units.capability_text().split("\n世界觀")[0])
        data = await self.llm.chat_json(system + prompts.CARD_FORMAT, prompts.CARD_USER.format(
            taken="、".join(sorted(taken)) or "無", names="、".join(sorted(names)) or "無",
            source=source, members=members), temperature=1.0, priority=2)
        if not data or not isinstance(data.get("leader"), dict) or not data["leader"].get("name"):
            return None
        callsign = re.sub(r"[^A-Za-z]", "", str(data.get("callsign") or "")) or "Unit"
        base, n = callsign.lower(), 2
        sid = base
        while sid in self.world.squads or callsign in taken:
            sid = f"{base}{n}"
            callsign = f"{callsign.rstrip('0123456789')}{n}"
            n += 1
        leader = data["leader"]
        card = {
            "id": sid,
            "callsign": callsign,
            "generated": True,
            "leader": {
                "name": str(leader["name"])[:20],
                "personality": str(leader.get("personality", ""))[:200],
                "speech": str(leader.get("speech", ""))[:80],
                "background": str(leader.get("background", ""))[:120],
                "sample_lines": [str(x)[:60] for x in (leader.get("sample_lines") or [])[:4]],
            },
            "members": [],
            "initial_beliefs": [str(x)[:60] for x in (data.get("initial_beliefs") or [])[:3]],
            "tendencies": obedience.normalize_tendencies(data.get("tendencies") or {}),
        }
        seen = {card["leader"]["name"]} | names
        for m in (data.get("members") or [])[:members]:
            if isinstance(m, dict) and m.get("name") and m["name"] not in seen:
                card["members"].append({"name": str(m["name"])[:20], "trait": str(m.get("trait", ""))[:30]})
                seen.add(m["name"])
        return card

    async def _reinforce(self, c: Commander) -> Squad | None:
        c.reinforce_ready = self.world.tick + REINFORCE_COOLDOWN
        card = await self.generate_card("指揮部撥補給地區指揮官的增援小隊")
        if not card:
            return None
        self.new_cards.append(card)
        squad = scenario.squad_from_card(card)
        squad.commander = c.id
        squad.pos = scenario.BASE
        self.world.squads[squad.id] = squad
        self._update_intel(squad)
        self._event(c, f"指揮部增援：{squad.callsign} 小隊抵達 {scenario.BASE}")
        self.outbox.append({"type": "reinforce", "commander": c.id, "squad": squad.id, "card": card})
        self._experience(squad, "deployed", prompts.EVENT_REINFORCE.format(
            callsign=squad.callsign, cell=scenario.cell_desc(scenario.BASE)))
        self.dirty.add(c.id)
        return squad

    # ------------------------------------------------------------------ resupply / recruits

    def _base_note(self, squad: Squad) -> str:
        if squad.pos != scenario.BASE:
            return ""
        return f"這裡是後方據點，原地待命約 {self.seconds(REST_FULL)} 秒即可完成整補（彈藥、修復）。"

    def rest_progress(self, squad: Squad) -> str | None:
        if squad.pos != scenario.BASE or squad.rest_ticks <= 0:
            return None
        if squad.rest_ticks >= REST_FULL:
            return "整補完成，待命中"
        return f"整補中 {squad.rest_ticks}/{REST_FULL}"

    def _resupply(self, squad: Squad, major: list):
        at_base = squad.pos == scenario.BASE and not squad.contact and squad.action.type in ("hold", "hide")
        if not at_base:
            if squad.rest_ticks:
                squad.rest_ticks = 0
                if squad.linked:
                    self._update_intel(squad)
                    self._dirty(squad)
            return
        squad.rest_ticks += 1
        if squad.linked and squad.rest_ticks <= REST_FULL:
            self._update_intel(squad)
            if squad.rest_ticks in (1, REST_FULL):
                self._dirty(squad)
        if squad.rest_ticks < REST_FULL:
            for m in squad.alive_members():
                if m.hp < 100:
                    m.hp = min(100, m.hp + 4)
            return
        if squad.rest_ticks == REST_FULL:
            details = []
            if squad.ammo < 100 or any(m.ammo is not None or m.grenades < 2 or m.smokes < 1
                                       for m in squad.members if not m.lost):
                squad.ammo = 100
                for m in squad.members:
                    if not m.lost:
                        m.ammo, m.grenades, m.smokes = None, 2, 1
                details.append("彈藥與投擲物補滿")
            healed = [m.name for m in squad.members if not m.lost and (m.hp < 100 or m.damage or m.down)]
            for m in squad.members:
                if m.lost:
                    continue
                m.hp = min(100, m.hp + 40)
                m.damage, m.torso_hits = {}, 0
                m.down, m.down_tick, m.stabilized = False, None, False
                m.suppression = 0.0
            if healed:
                details.append(f"{'、'.join(healed)} 完成修復")
            if squad.morale < 70:
                squad.morale = min(100, squad.morale + 15)
                details.append("士氣回穩")
            if len(squad.alive_members()) < squad.max_size and self.world.tick - squad.last_recruit >= RECRUIT_GAP:
                self.pending_recruits.append((squad.id, "後方據點的補充兵"))
            if details:
                major.append(("resupply", f"在 {scenario.BASE} 整補：{'、'.join(details)}",
                              prompts.EVENT_RESUPPLY.format(detail="、".join(details))))

    async def _recruit(self, squad_id: str, source: str):
        squad = self.world.squads.get(squad_id)
        if not squad or squad.status != "active":
            return
        if len(squad.alive_members()) >= squad.max_size:
            if "殘部" in source:
                self._experience(squad, "rescue_full", prompts.EVENT_RESCUE_FULL.format(
                    cell=scenario.cell_desc(squad.pos), name="友軍殘部"))
            return
        names = {m.name for m in squad.members}
        data = await self.llm.chat_json(prompts.RECRUIT_SYSTEM, prompts.RECRUIT_USER.format(
            callsign=squad.callsign, names="、".join(sorted(names)), source=source), temperature=1.0, priority=2)
        name = str((data or {}).get("name") or "").strip()[:12]
        trait = str((data or {}).get("trait") or "沉默寡言").strip()[:30]
        if not name or name in names:
            n = 20
            while f"N-{n}" in names:
                n += 1
            name = f"N-{n}"
        hp = 60 if "殘部" in source else 100
        squad.members.append(Member(name=name, hp=hp, trait=trait, joined=self.world.tick,
                                    weapon=roster.missing_weapon(squad), prof={"rifle": 40}))
        squad.last_recruit = self.world.tick
        if "殘部" in source:
            event = prompts.EVENT_RESCUE.format(cell=scenario.cell_desc(squad.pos), name=name, trait=trait)
            kind = "rescue"
        else:
            event = prompts.EVENT_RECRUIT.format(name=name, trait=trait)
            kind = "recruit"
        record = self._experience(squad, kind, f"{name}（{trait}）歸建：{source}")
        await self._react(squad, [event], record)

    async def deploy_squad(self, card: dict, commander_id: str, reset: bool = False) -> tuple[Squad, bool]:
        async with self.lock:
            existing = self.world.squads.get(card["id"])
            if existing and not reset:
                return existing, False
            if existing and existing.commander:
                c = self.world.commanders.get(existing.commander)
                if c:
                    c.intel["squads"].pop(existing.id, None)
                    self.dirty.add(c.id)
            squad = scenario.squad_from_card(card)
            squad.commander = commander_id
            self.world.squads[squad.id] = squad
            self._update_intel(squad)
            self._dirty(squad)
            self.save()
            return squad, True

    # ------------------------------------------------------------------ memory / experience

    def _remember(self, squad: Squad, text: str):
        entry = f"T{self.world.tick} {text}"
        squad.memory.append(entry)
        squad.memory = squad.memory[-MEMORY_LIMIT:]
        if not squad.linked:
            squad.offline_events.append(entry)

    def _experience(self, squad: Squad, kind: str, text: str, remember: bool = True, **facts) -> dict:
        record = {"t": self.world.tick, "kind": kind, "text": text, **facts, "interp": {}}
        squad.experiences.append(record)
        squad.experiences = squad.experiences[-EXPERIENCE_LIMIT:]
        if remember:
            self._remember(squad, text)
        return record

    def _say(self, squad: Squad, who: str, text: str):
        squad.dialogue.append({"t": self.world.tick, "who": who, "text": text})
        squad.dialogue = squad.dialogue[-DIALOGUE_LIMIT:]

    def dialogue_text(self, squad: Squad, n: int = 8) -> str:
        lines = []
        for d in squad.dialogue[-n:]:
            who = "指揮官" if d["who"] == "commander" else "你"
            lines.append(f"T{d['t']} {who}：{d['text']}")
        return "\n".join(lines) or "（無）"

    def experience_text(self, squad: Squad, n: int = 10, show_interp: bool = False) -> str:
        lines = []
        for e in squad.experiences[-n:]:
            line = f"T{e['t']} [{e['kind']}] {e['text']}"
            if e.get("outcome"):
                line += f" → {e['outcome']}"
            if show_interp and e.get("interp"):
                parts = [f"{k}={v}" for k, v in e["interp"].items() if v]
                if parts:
                    line += f"\n    💭 {'；'.join(parts)}"
            lines.append(line)
        return "\n".join(lines) or "（無）"

    # ------------------------------------------------------------------ helpers

    def _enemy_at(self, cell: str) -> Enemy | None:
        for e in self.world.enemies.values():
            if e.alive and e.pos == cell:
                return e
        return None

    def _estimate(self, strength: int) -> int:
        return max(1, strength + self.rng.choice((-1, 0, 0, 1)))

    def _action_text(self, squad: Squad) -> str:
        a = squad.action
        if a.type == "move" and a.target:
            via = f"，經 {'→'.join(a.path[:-1])}" if len(a.path) > 1 else ""
            return f"移動 → {scenario.cell_desc(a.target)}（剩 {len(a.path)} 格{via}）"
        if a.type in TARGET_ACTIONS and a.target:
            return f"{SHORT_LABELS[a.type]} → {scenario.cell_desc(a.target)}"
        return SHORT_LABELS.get(a.type, a.type)

    def _allowed(self, squad: Squad) -> tuple[dict[str, list[str]], str]:
        adj = scenario.adjacent(squad.pos)
        allowed = {
            "keep": [], "hold": [], "hide": [],
            "move": [c for c in scenario.CELLS if c != squad.pos],
            "scout": adj, "retreat": adj,
        }
        if squad.contact:
            allowed["attack"] = []
        lines = []
        for k, targets in allowed.items():
            if targets:
                lines.append(f"- {k}：{ACTION_LABELS[k]}；target：{', '.join(targets)}")
            else:
                lines.append(f"- {k}：{ACTION_LABELS[k]}；target 填 null")
        return allowed, "\n".join(lines)

    def _known_enemy_cells(self, squad: Squad) -> set[str]:
        return set(squad.known_enemies)

    def situation(self, squad: Squad) -> str:
        cell = scenario.CELLS[squad.pos]
        adj = "、".join(scenario.cell_desc(c) for c in scenario.adjacent(squad.pos))
        members = " ".join(
            f"{m.name}({self._hp_word(m.hp)}{'，' + m.trait if m.trait and m.trait != '小隊長' else ''}"
            f"{'，新兵' if m.joined and self.world.tick - m.joined < 60 else ''})"
            for m in squad.alive_members()
        )
        downed = [m.name for m in squad.members if m.down and not m.lost]
        if downed:
            members += f"　倒地：{'、'.join(downed)}"
        known = "、".join(
            f"{c} 約{v['est']}（T{v['tick']}{'，疑似' if v.get('suspected') else ''}）"
            for c, v in squad.known_enemies.items()
        ) or "無"
        if squad.contact:
            est = squad.known_enemies.get(squad.pos, {}).get("est")
            contact = f"與敵軍交戰中（看到約 {est}）" if est else "與敵軍交戰中（敵方位置不明）"
        else:
            contact = "無"
        memory = "\n".join(f"  {m}" for m in squad.memory[-6:]) or "  （無）"
        return (
            f"時間：T{self.world.tick}\n"
            f"位置：{squad.pos} {cell['name']}（{cell['terrain']}，掩蔽：{scenario.cover_label(cell['cover'])}）\n"
            f"相鄰：{adj}\n"
            f"地圖：{scenario.MAP_NOTE}\n"
            f"隊員（{len(squad.alive_members())}/{len(squad.members)}）：{members}\n"
            f"彈藥：{self._ammo_word(squad.ammo)}　士氣：{self._morale_word(squad.morale)}\n"
            f"目前行動：{self._action_text(squad)}\n"
            f"指揮官最後交代的事：{squad.last_order or '無'}\n"
            f"指揮部任務（你只知道大概）：\n{self.missions_text(squad.commander or '')}\n"
            f"後方據點：{scenario.cell_desc(scenario.BASE)}（在此固守或隱蔽約 {self.seconds(REST_FULL)} 秒可整補彈藥、"
            f"修復傷員，可能補充新兵）；也可由指揮官向指揮部請求前線補給空投（有延遲、可能被攔截）"
            f"{'；目前' + self.rest_progress(squad) if self.rest_progress(squad) else ''}\n"
            f"已知敵情：{known}\n"
            f"接觸狀態：{contact}\n"
            f"近期經歷：\n{memory}"
        )

    @staticmethod
    def _hp_word(hp: int) -> str:
        if hp >= 90:
            return "完好"
        if hp >= 60:
            return "輕傷"
        if hp >= 30:
            return "負傷"
        return "重傷"

    @staticmethod
    def _ammo_word(ammo: int) -> str:
        if ammo >= 70:
            return f"充足（{ammo}%）"
        if ammo >= 30:
            return f"消耗中（{ammo}%）"
        if ammo > 0:
            return f"見底（{ammo}%）"
        return "打光（0%）"

    @staticmethod
    def _morale_word(morale: int) -> str:
        if morale >= 75:
            return "高昂"
        if morale >= 45:
            return "穩定"
        if morale >= 20:
            return "動搖"
        return "瀕臨崩潰"

    def _system_prompt(self, squad: Squad) -> str:
        leader = squad.leader
        samples = "\n".join(f"- {s}" for s in leader.get("sample_lines", [])) or "（無）"
        return prompts.LEADER_SYSTEM.format(
            name=squad.acting_leader(),
            callsign=squad.callsign,
            personality=leader.get("personality", ""),
            speech=leader.get("speech", ""),
            background=leader.get("background", ""),
            beliefs=obedience.beliefs_text(squad),
            tendencies=obedience.tendency_text(squad),
            long_memory=f"長期記憶：{squad.long_memory}\n" if squad.long_memory else "",
            samples=samples,
            capabilities=units.capability_text(leader),
        )

    # ------------------------------------------------------------------ action application

    def _normalize(self, squad: Squad, data: dict, allowed: dict) -> tuple[str, str | None, bool, list[str]]:
        action = str(data.get("action") or "keep").strip().lower()
        target = data.get("target")
        target = str(target).strip().upper() if target else None
        direct = bool(data.get("direct"))
        via = data.get("via") or []
        via = [str(v).strip().upper() for v in via if isinstance(v, str)] if isinstance(via, list) else []
        via = [v for v in via if v in scenario.CELLS]
        if action not in allowed:
            return "keep", None, False, []
        if action in TARGET_ACTIONS:
            if target not in allowed[action]:
                return "keep", None, False, []
        else:
            target = None
        return action, target, direct, via if action == "move" else []

    def _apply(self, squad: Squad, action: str, target: str | None, direct: bool, ordered: bool,
               via: list[str] | None = None) -> str:
        squad.idle_ticks = 0
        if action == "keep":
            return "行動未改變"
        if action == "move":
            avoid = set() if (direct or via) else self._known_enemy_cells(squad)
            path = scenario.find_route(squad.pos, target, via, avoid)
            if not path:
                return "找不到路線，行動未改變"
            squad.action = Action(type="move", target=target, duration=STEP_TICKS["move"],
                                  ordered=ordered, path=path, direct=direct or bool(via))
            return f"移動 → {target}（路線 {'→'.join(path)}）"
        squad.action = Action(type=action, target=target, duration=STEP_TICKS.get(action, 0), ordered=ordered)
        if action == "retreat" and squad.contact:
            self._release_contact(squad)
        return f"{SHORT_LABELS.get(action, action)}{' ' + target if target else ''}"

    def _release_contact(self, squad: Squad):
        e = self.world.enemies.get(squad.contact or "")
        if e and e.engaged_with == squad.id:
            e.engaged_with = None

    # ------------------------------------------------------------------ delivery / intel

    def _deliver(self, squad: Squad, text: str | None, from_order: bool = False):
        if not text:
            return
        if not from_order:
            text = ACK_PREFIX.sub("", text).strip() or text
        if squad.linked:
            self._say(squad, "leader", text)
            self._update_intel(squad)
            c = self.world.commanders.get(squad.commander or "")
            if c:
                c.intel["log"].append(f"T{self.world.tick} {squad.callsign}：{text}")
                c.intel["log"] = c.intel["log"][-LOG_LIMIT:]
            self.outbox.append({"type": "report", "squad": squad.id, "name": squad.acting_leader(), "text": text})
        else:
            squad.offline_events.append(f"T{self.world.tick}（自言）{text}")

    def _system(self, squad: Squad, text: str):
        self.outbox.append({"type": "system", "squad": squad.id, "text": text})

    def _update_intel(self, squad: Squad):
        c = self.world.commanders.get(squad.commander or "")
        if c is None:
            return
        intel = c.intel
        intel["squads"][squad.id] = {
            "callsign": squad.callsign,
            "pos": squad.pos,
            "alive": len(squad.alive_members()),
            "total": len(squad.members),
            "ammo": squad.ammo,
            "morale": squad.morale,
            "action": self._action_text(squad),
            "status": squad.status,
            "linked": squad.linked,
            "tick": self.world.tick,
            "rest": self.rest_progress(squad),
        }
        for cell in list(intel["enemies"]):
            if cell in squad.observed and squad.observed[cell] >= intel["enemies"][cell]["tick"] \
                    and cell not in squad.known_enemies:
                intel["enemies"].pop(cell)
        for cell, info in squad.known_enemies.items():
            intel["enemies"][cell] = dict(info)

    def intel_map(self, commander_id: str) -> str:
        c = self.world.commanders.get(commander_id)
        if c is None:
            return ""
        own: dict[str, list[str]] = {}
        for sid, s in c.intel["squads"].items():
            if s["status"] == "destroyed":
                continue
            own.setdefault(s["pos"], []).append(s["callsign"][0].upper())
        mission_cells = {m["cell"] for m in c.missions}
        drop_cells = {o["cell"] for o in self.world.objects.values()
                      if o["type"] == "airdrop" and o.get("commander") == commander_id}
        width = 7
        rows = []
        for r in reversed(scenario.ROWS):
            cells = []
            for col in scenario.COLS:
                cell = r + col
                tag = "".join(own.get(cell, []))[:2]
                e = c.intel["enemies"].get(cell)
                if e:
                    tag += f"{'?' if e.get('suspected') else '!'}{e['est']}"
                if cell == scenario.BASE:
                    tag = tag or "基"
                if cell in mission_cells:
                    tag += "★"
                if cell in drop_cells:
                    tag += "↓"
                cells.append(f"{cell}{tag or '·'}".ljust(width))
            rows.append(" ".join(cells).rstrip())
        legend = ("北↑  字母=我方小隊  !n=敵軍約n  ?n=疑似敵軍  ★=任務  ↓=補給空投  基=後方據點")
        return "\n".join(rows) + "\n" + legend

    def force_summary(self, commander_id: str) -> str:
        c = self.world.commanders.get(commander_id)
        if c is None:
            return ""
        squads = [s for s in c.intel["squads"].values()]
        active = [s for s in squads if s["status"] != "destroyed"]
        alive = sum(s["alive"] for s in active)
        total = sum(s["total"] for s in active)
        lost = sum(1 for s in squads if s["status"] == "destroyed")
        offline = sum(1 for sid in c.intel["squads"] if (sq := self.world.squads.get(sid)) and not sq.linked
                      and sq.status == "active")
        enemy_est = sum(e["est"] for e in c.intel["enemies"].values() if not e.get("suspected"))
        suspect = sum(e["est"] for e in c.intel["enemies"].values() if e.get("suspected"))
        wait = c.supply_ready - self.world.tick
        supply = "可請求" if wait <= 0 else f"冷卻 {self.seconds(wait)} 秒"
        if any(o["type"] == "airdrop" and o.get("commander") == commander_id for o in self.world.objects.values()):
            supply = "空投在途"
        return (f"我方：{len(active)} 支小隊、{alive}/{total} 人"
                f"{f'（失聯 {offline}）' if offline else ''}{f'，已損失 {lost} 支' if lost else ''}\n"
                f"已知敵軍：約 {enemy_est}{f'，疑似 {suspect}' if suspect else ''}（情報可能過時）\n"
                f"前線補給：{supply}")

    def intel_text(self, commander_id: str) -> str:
        intel = self.world.commanders[commander_id].intel
        lines = [f"現在時間：T{self.world.tick}"]
        for sid, s in intel["squads"].items():
            sq = self.world.squads.get(sid)
            linked = sq.linked if sq else s["linked"]
            status = "📵 失聯" if not linked else ("全滅" if s["status"] == "destroyed" else "通訊正常")
            lines.append(
                f"小隊 {sid}（{s['callsign']}）：最後回報 T{s['tick']}，位置 {s['pos']}，"
                f"{s['alive']}/{s['total']} 人，彈藥 {s['ammo']}%，士氣 {s['morale']}，"
                f"行動：{s['action']}，{status}{'，' + s['rest'] if s.get('rest') else ''}"
            )
        if intel["enemies"]:
            for cell, e in intel["enemies"].items():
                lines.append(f"敵情：{cell} 約 {e['est']}（T{e['tick']}{'，疑似' if e.get('suspected') else ''}）")
        else:
            lines.append("敵情：無確切情報")
        return "\n".join(lines)

    # ------------------------------------------------------------------ commands

    async def issue_order(self, squad_id: str, text: str) -> dict | None:
        async with self.lock:
            squad = self.world.squads.get(squad_id)
            if not squad or squad.status != "active":
                return None
            if not squad.linked:
                self._system(squad, "📵 （只有雜訊……命令未送達）")
                return None
            result = obedience.roll(squad, self.rng)
            risky_result = obedience.roll(squad, self.rng, risky=True)
            allowed, allowed_text = self._allowed(squad)
            confirmed = squad.pending_confirm
            order_text = text
            if confirmed:
                order_text = f"{text}\n{prompts.CONFIRMED_NOTE.format(order=confirmed)}"
            verify_note = prompts.VERIFY_NOTE if squad.tendencies.get("verify") and not confirmed else ""
            user = prompts.ORDER_USER.format(
                situation=self.situation(squad), allowed=allowed_text, dialogue=self.dialogue_text(squad),
                order=order_text,
                directive=(f"若命令不危險：{prompts.DIRECTIVES[result]}；若命令危險（risky）："
                           f"{prompts.DIRECTIVES[risky_result]}"),
                verify_note=verify_note,
            )
            self._say(squad, "commander", text)
            data = await self.llm.chat_json(self._system_prompt(squad), user)
            if not data:
                data = {"kind": "order", "confidence": "none", "action": "keep",
                        "report": "（雜訊）……指揮官，請重複？", "interp": "訊號太差。"}
            kind = str(data.get("kind") or "order").lower()
            confidence = str(data.get("confidence") or "high").lower()
            risky = bool(data.get("risky"))
            effective = risky_result if risky else result
            action, target, direct, via = self._normalize(squad, data, allowed)
            mapped = None
            outcome = "行動未改變"
            if kind == "order":
                squad.last_order = text
                if data.get("confirm_request") and verify_note:
                    squad.pending_confirm = text
                    outcome = "要求確認命令來源"
                    action, target = "keep", None
                else:
                    squad.pending_confirm = None
                    if confidence != "none" and action != "keep":
                        outcome = self._apply(squad, action, target, direct,
                                              ordered=(effective == obedience.COMPLY), via=via)
                        if not outcome.startswith("找不到"):
                            mapped = action
                if data.get("supply"):
                    self._request_supply_locked(squad, via_radio=True)
            record = self._experience(squad, "message", f"指揮官：「{text}」", mapped=mapped, outcome=outcome,
                                      obedience=effective if kind == "order" else None, risky=risky)
            record["interp"] = {
                "classified": kind,
                "understanding": data.get("understanding"),
                "confidence": confidence if kind == "order" else None,
                "thought": data.get("interp"),
            }
            if kind == "order" and (confidence == "none" or outcome == "要求確認命令來源"):
                self._want_reflect(squad, "收到無法對應或可疑的命令")
            report = str(data.get("report") or "").strip() or "……"
            self._deliver(squad, report, from_order=True)
            self._dirty(squad)
            self.save()
            return {"kind": kind, "confidence": confidence, "action": action, "target": target,
                    "mapped": mapped, "outcome": outcome, "obedience": effective, "risky": risky,
                    "report": report}

    async def _react(self, squad: Squad, events: list[str], record: dict) -> None:
        allowed, allowed_text = self._allowed(squad)
        user = prompts.EVENT_USER.format(
            situation=self.situation(squad), allowed=allowed_text, dialogue=self.dialogue_text(squad),
            event=" ".join(events),
        )
        if not squad.linked:
            user += prompts.OFFLINE_NOTE
        data = await self.llm.chat_json(self._system_prompt(squad), user)
        if not data:
            data = {"action": "attack" if squad.contact else "keep",
                    "report": "（雜訊）……接敵！" if squad.contact else None}
        action, target, direct, via = self._normalize(squad, data, allowed)
        record["outcome"] = self._apply(squad, action, target, direct, ordered=False, via=via)
        record["interp"] = {"thought": data.get("interp")}
        report = data.get("report")
        record["reported"] = bool(report) and squad.linked
        self._deliver(squad, str(report).strip() if report else None)
        self._dirty(squad)

    async def set_link(self, squad_id: str, linked: bool):
        async with self.lock:
            squad = self.world.squads.get(squad_id)
            if not squad or squad.linked == linked:
                return
            if not linked:
                squad.linked = False
                squad.unlinked_since = self.world.tick
                squad.offline_events = []
                self._experience(squad, "link_lost", "與指揮官的通訊中斷")
                self._system(squad, "📵 通訊中斷……只剩下雜訊。")
                self._dirty(squad)
                self.save()
                return
            start = squad.unlinked_since or self.world.tick
            squad.linked = True
            squad.unlinked_since = None
            self._want_reflect(squad, f"失聯 {self.seconds(self.world.tick - start)} 秒後恢復通訊")
            events = "\n".join(squad.offline_events[-15:]) or "（沒有特別的事）"
            self._system(squad, "📶 通訊恢復。")
            user = prompts.CATCHUP_USER.format(start=start, end=self.world.tick, events=events,
                                               situation=self.situation(squad))
            data = await self.llm.chat_json(self._system_prompt(squad), user)
            report = (data or {}).get("report") or f"……這裡是 {squad.callsign}，我們還在。"
            record = self._experience(squad, "link_restored",
                                      f"通訊恢復（失聯 {self.seconds(self.world.tick - start)} 秒）")
            record["interp"] = {"thought": (data or {}).get("interp")}
            squad.offline_events = []
            self._deliver(squad, str(report), from_order=True)
            self._dirty(squad)
            self.save()

    # ------------------------------------------------------------------ tick

    async def tick(self):
        async with self.lock:
            w = self.world
            w.tick += 1
            for c in list(w.commanders.values()):
                self._ensure_missions(c)
            self._move_enemies()
            drops = {}
            for squad, text in self._airdrops():
                drops.setdefault(squad.id, []).append(text)
            roster.sync_world(w)
            self.tac_events = self._run_engagements()
            for squad in list(w.squads.values()):
                if squad.status == "active":
                    await self._tick_squad(squad, extra_events=drops.get(squad.id, []))
            for c in list(w.commanders.values()):
                self._check_missions(c)
            while self.pending_recruits:
                sid, source = self.pending_recruits.pop(0)
                await self._recruit(sid, source)
            for c in list(w.commanders.values()):
                if self._need_reinforce(c):
                    await self._reinforce(c)
            for squad in list(w.squads.values()):
                if squad.reflect_reasons and not squad.contact and squad.status == "active" \
                        and w.tick - squad.last_reflect >= REFLECT_GAP and self.llm.can_call(3):
                    await self._reflect(squad)
                    break
            self.save()

    def _move_enemies(self):
        if not self.enemy_ai_enabled:
            return
        active = [s for s in self.world.squads.values() if s.status == "active"]
        enemy_ai.observe(self.world, self.rng, active)
        objectives = sorted({m["cell"] for c in self.world.commanders.values() for m in c.missions})
        self.enemy_log = enemy_ai.think(self.world, self.rng, objectives)
        enemy_ai.move(self.world)
        for eid in [k for k, e in self.world.enemies.items() if not e.alive and not e.engaged_with]:
            if self.world.tick % 30 == 0:
                del self.world.enemies[eid]

    # ------------------------------------------------------------------ C2：戰術層交戰

    def _run_engagements(self) -> dict:
        """每個有敵我接觸的格子推進一個戰術 tick。回傳 {squad id: [(kind, fact)]}。"""
        from engine import engagement
        events: dict[str, list] = {}
        w = self.world
        cells = set(engagement.cells_in_contact(w)) | set(self.engagements)
        for cell in sorted(cells):
            eng = self.engagements.get(cell)
            if eng is None:
                eng = engagement.Engagement(w, cell, seed=self.rng.randint(0, 2 ** 31 - 1))
                self.engagements[cell] = eng
            out = eng.step()
            for sid, evs in out["squads"].items():
                events.setdefault(sid, []).extend(evs)
            for sid, frm, dest, left in out.get("moved", []):
                msg = f"撤出 {scenario.cell_desc(frm)}，退到 {scenario.cell_desc(dest)}"
                if left:
                    msg += f"（{left} 名倒地隊員未能帶回）"
                events.setdefault(sid, []).append(("withdrawn", msg))
            for e in w.enemies.values():
                if e.alive and e.pos == cell:
                    sq = next((s for s in w.squads.values() if s.pos == cell and s.status == "active"), None)
                    e.engaged_with = sq.id if sq and eng.hot() else None
            if eng.over():
                was_hot = eng.hot() and "contact" in eng.reported
                eng.finish()
                del self.engagements[cell]
                for s in w.squads.values():
                    if s.pos != cell or s.status != "active":
                        continue
                    alive_foe = any(e.alive and e.pos == cell for e in w.enemies.values())
                    if not alive_foe and was_hot:
                        events.setdefault(s.id, []).append(("enemy_down", f"{cell} 的敵軍全數倒下"))
                    elif was_hot:
                        events.setdefault(s.id, []).append(("disengage", "與敵軍脫離接觸"))
        return events

    def in_engagement(self, squad: Squad) -> bool:
        """近期有接觸 → 作戰層移動暫停，由戰術層處理。"""
        eng = self.engagements.get(squad.pos)
        return eng is not None and eng.hot()

    async def _tick_squad(self, squad: Squad, extra_events: list[str] | None = None):
        major: list[tuple[str, str, str]] = [("supply", e, e) for e in (extra_events or [])]
        minor: list[tuple[str, str, str]] = []
        before_lost = {m.name for m in squad.members if m.lost}

        if not self.in_engagement(squad):
            self._progress_action(squad, major, minor)
            self._downed_outside_combat(squad, major)
        self._apply_tac_events(squad, major, minor, before_lost)

        if not any(not m.lost for m in squad.members) or (not squad.alive_members() and not self.in_engagement(squad)):
            squad.status = "destroyed"
            squad.contact = None
            self._experience(squad, "destroyed", "小隊全滅")
            if squad.linked:
                self._update_intel(squad)
                self._system(squad, f"☠ {squad.callsign} 小隊所有訊號消失。")
            self._dirty(squad)
            return

        self._ammo_events(squad, major, minor)
        self._detect_adjacent(squad, minor)
        self._pickup_objects(squad, major)
        self._resupply(squad, major)

        if not squad.linked:
            elapsed = self.world.tick - (squad.unlinked_since or self.world.tick)
            if squad.action.type in ("hold", "hide") and not squad.contact:
                squad.idle_ticks += 1
                if squad.idle_ticks >= 6 and not major and not minor:
                    squad.idle_ticks = 0
                    major.append(("autonomy", f"失聯 {self.seconds(elapsed)} 秒，無新命令",
                                  prompts.EVENT_AUTONOMY.format(seconds=self.seconds(elapsed))))

        for kind, fact, _ in minor:
            self._experience(squad, kind, fact)
        records = [self._experience(squad, kind, fact) for kind, fact, _ in major]
        c = self.world.commanders.get(squad.commander or "")
        if c and squad.linked:
            for kind, fact, _ in major:
                if kind in ("contact", "casualty", "enemy_down", "disengage", "ammo_out", "detected", "arrive", "resupply"):
                    self._event(c, f"{squad.callsign}：{fact}")

        trigger_minor = minor and self.world.tick - squad.last_combat_report >= MINOR_GAP
        if major or trigger_minor:
            events = [desc for _, _, desc in major] + ([desc for _, _, desc in minor] if trigger_minor else [])
            record = records[0] if records else squad.experiences[-1]
            squad.last_combat_report = self.world.tick
            await self._react(squad, events, record)
        else:
            if squad.linked and self.world.tick % 6 == 0:
                self._update_intel(squad)
            await self._maybe_chatter(squad)

    async def _maybe_chatter(self, squad: Squad):
        if not squad.linked or squad.contact or squad.action.type not in ("hold", "hide"):
            return
        if not any(d["who"] == "commander" for d in squad.dialogue):
            return
        if squad.next_chatter == 0:
            squad.next_chatter = self.world.tick + self.rng.randint(*IDLE_CHATTER)
            return
        if self.world.tick < squad.next_chatter:
            return
        squad.next_chatter = self.world.tick + self.rng.randint(*IDLE_CHATTER)
        user = prompts.IDLE_USER.format(situation=self.situation(squad), dialogue=self.dialogue_text(squad))
        data = await self.llm.chat_json(self._system_prompt(squad), user, temperature=0.95, priority=4)
        report = (data or {}).get("report")
        if report:
            record = self._experience(squad, "idle", "待命時在無線電上隨口說話")
            record["interp"] = {"thought": (data or {}).get("interp")}
            self._deliver(squad, str(report).strip())

    def _downed_outside_combat(self, squad: Squad, major: list):
        """交戰外：隊友會自動穩定倒地者；在後方據點整補時倒地者恢復行動。"""
        for m in squad.members:
            if not m.down or m.lost:
                continue
            if not m.stabilized and squad.alive_members():
                m.stabilized = True
                self._experience(squad, "stabilized", f"{m.name} 已由隊友穩定")
            if not m.stabilized and m.down_tick is not None and self.world.tick - m.down_tick >= 12:
                m.hp = 0
                m.down = False
                major.append(("casualty", f"{m.name} 陣亡", prompts.EVENT_CASUALTY.format(name=m.name)))

    def _apply_tac_events(self, squad: Squad, major: list, minor: list, before_lost: set):
        evs = self.tac_events.get(squad.id, [])
        eng = self.engagements.get(squad.pos)
        for kind, fact in evs:
            if kind == "contact":
                digits = "".join(ch for ch in fact.split("約")[-1] if ch.isdigit()) if "約" in fact else ""
                n = int(digits or 1)
                squad.contact = next((e.id for e in self.world.enemies.values()
                                      if e.alive and e.pos == squad.pos), None)
                squad.known_enemies[squad.pos] = {"est": n, "tick": self.world.tick}
                if squad.action.type in ("move", "scout"):
                    squad.action = Action(type="hold")
                major.append(("contact", fact, prompts.EVENT_CONTACT.format(
                    cell=scenario.cell_desc(squad.pos), est=n, hidden="")))
            elif kind == "casualty":
                name = fact.split()[0]
                squad.morale = max(0, squad.morale - 10)
                self._want_reflect(squad, f"隊員陣亡：{name}" + ("（執行指揮官命令中）" if squad.action.ordered else ""))
                major.append(("casualty", fact, prompts.EVENT_CASUALTY.format(name=name)))
            elif kind == "down":
                name = fact.split()[0]
                major.append(("down", fact, prompts.EVENT_DOWN.format(name=name)))
            elif kind == "wounded":
                minor.append(("wounded", fact, prompts.EVENT_WOUNDED.format(name=fact.split()[0])))
            elif kind == "kill":
                squad.kills += 1
                minor.append(("kill", fact, prompts.EVENT_KILL))
            elif kind == "enemy_down":
                squad.contact = None
                squad.known_enemies.pop(squad.pos, None)
                squad.morale = min(100, squad.morale + 10)
                major.append(("enemy_down", fact, prompts.EVENT_ENEMY_DOWN))
                self._want_reflect(squad, "一場交戰結束（敵軍被擊潰）")
            elif kind == "disengage":
                squad.contact = None
                major.append(("disengage", fact, prompts.EVENT_DISENGAGE))
                self._want_reflect(squad, "一場交戰結束（脫離接觸）")
            elif kind == "withdrawn":
                squad.contact = None
                result = self._observe(squad, squad.pos)
                major.append(("arrive", fact, prompts.EVENT_ARRIVE.format(
                    cell=scenario.cell_desc(squad.pos), result=f"{fact}。{result}" + self._base_note(squad))))
                self._want_reflect(squad, "撤退脫離交戰")
            elif kind in ("smoke", "grenade"):
                continue
        if eng is not None and eng.hot():
            squad.contact = squad.contact or next((e.id for e in self.world.enemies.values()
                                                   if e.alive and e.pos == squad.pos), None)
            known = sum(1 for uid in eng.battle.intel["blue"].last_known
                        if uid in eng.battle.units and eng.battle.units[uid].alive)
            if known:
                squad.known_enemies[squad.pos] = {"est": known, "tick": self.world.tick}
        _ = before_lost

    def _progress_action(self, squad: Squad, major: list, minor: list):
        a = squad.action
        if a.type not in STEP_TICKS:
            return
        a.progress += 1
        if a.progress < a.duration:
            return
        ordered = a.ordered
        if a.type == "move":
            if not a.direct:
                known = self._known_enemy_cells(squad)
                if any(c in known for c in a.path[:-1]):
                    new_path = scenario.find_path(squad.pos, a.target, known)
                    if new_path:
                        a.path = new_path
            squad.pos = a.path.pop(0)
            a.progress = 0
            result = self._observe(squad, squad.pos)
            if not a.path:
                squad.action = Action(type="hold")
                major.append(("arrive", f"抵達 {scenario.cell_desc(squad.pos)}",
                              prompts.EVENT_ARRIVE.format(cell=scenario.cell_desc(squad.pos), result=result + self._base_note(squad))))
            return
        if a.type == "retreat":
            origin = squad.pos
            squad.pos = a.target
            squad.contact = None
            result = self._observe(squad, squad.pos)
            squad.action = Action(type="hold")
            major.append(("arrive", f"自 {origin} 撤至 {scenario.cell_desc(squad.pos)}",
                          prompts.EVENT_ARRIVE.format(cell=scenario.cell_desc(squad.pos), result=result + self._base_note(squad))))
            return
        result = self._observe(squad, a.target)
        squad.action = Action(type="hold")
        major.append(("scout", f"偵察 {a.target}：{result}",
                      prompts.EVENT_SCOUT.format(cell=scenario.cell_desc(a.target), result=result)))

    def _observe(self, squad: Squad, cell: str) -> str:
        """偵察／抵達觀察。抵達自己所在格時不直接揭露敵情（由戰術層發現判定）。"""
        e = self._enemy_at(cell)
        squad.observed[cell] = self.world.tick
        if cell == squad.pos:
            if squad.pos in squad.known_enemies:
                return f"{scenario.cell_desc(cell)} 有敵軍活動。"
            return f"{scenario.cell_desc(cell)} 目前沒有看到敵蹤。"
        if e:
            est = self._estimate(e.strength)
            squad.known_enemies[cell] = {"est": est, "tick": self.world.tick}
            return f"{scenario.cell_desc(cell)} 有敵軍，約 {est} 個單位。"
        squad.known_enemies.pop(cell, None)
        return f"{scenario.cell_desc(cell)} 沒有敵蹤。"

    def _ammo_events(self, squad: Squad, major: list, minor: list):
        if squad.ammo > 30:
            squad.ammo_flags = []
            return
        if squad.ammo <= 0 and "out" not in squad.ammo_flags:
            squad.ammo_flags.append("out")
            major.append(("ammo_out", "彈藥打光", prompts.EVENT_AMMO_OUT))
        elif "low" not in squad.ammo_flags:
            squad.ammo_flags.append("low")
            minor.append(("ammo_low", "彈藥剩不到三成", prompts.EVENT_AMMO_LOW))

    def _detect_adjacent(self, squad: Squad, minor: list):
        chance = 0.3 if squad.action.type == "move" else 0.12
        for cell in scenario.adjacent(squad.pos):
            e = self._enemy_at(cell)
            if e and self.rng.random() < chance and cell not in squad.known_enemies:
                est = self._estimate(e.strength)
                squad.known_enemies[cell] = {"est": est, "tick": self.world.tick, "suspected": True}
                result = f"察覺 {scenario.cell_desc(cell)} 方向疑似有敵軍活動（約 {est}）"
                minor.append(("spotted", result, prompts.EVENT_SPOTTED.format(cell=squad.pos, result=result)))

    # ------------------------------------------------------------------ adjutant

    async def refresh_adjutant(self, commander_id: str) -> dict | None:
        async with self.lock:
            c = self.world.commanders.get(commander_id)
            if c is None:
                return None
            self.dirty.discard(commander_id)
            own = {s.id for s in self.world.squads_of(commander_id) if s.status == "active"}
            if not own:
                c.adjutant = {"summary": "目前沒有可指揮的小隊。等待指揮部分配部隊。", "suggestions": [],
                              "tick": self.world.tick}
                self.save()
                return c.adjutant
            cells = "、".join(scenario.cell_desc(x) for x in scenario.CELLS)
            user = prompts.ADJUTANT_USER.format(
                objective=self.missions_text(commander_id), base=scenario.cell_desc(scenario.BASE),
                map_note=scenario.MAP_NOTE, cells=cells,
                intel=self.intel_text(commander_id),
                log="\n".join(c.intel["log"][-8:]) or "（無）",
            )
            data = await self.llm.chat_json(prompts.ADJUTANT_SYSTEM, user, temperature=0.5, priority=2)
            suggestions = []
            for s in (data or {}).get("suggestions", [])[:4]:
                if not isinstance(s, dict):
                    continue
                sid = str(s.get("squad", "")).strip().lower()
                order = str(s.get("order", "")).strip()
                label = str(s.get("label", "")).strip()[:12]
                if sid in own and order and label:
                    suggestions.append({"label": label, "squad": sid, "order": order[:80]})
            summary = str((data or {}).get("summary", "")).strip()
            if not summary:
                summary = "（副官連線不穩，以下為預設建議）"
            if not suggestions:
                sid = sorted(own)[0]
                suggestions = [
                    {"label": "回報現況", "squad": sid, "order": "回報目前狀況。"},
                    {"label": "原地固守", "squad": sid, "order": "原地固守，保持警戒。"},
                ]
            c.adjutant = {"summary": summary, "suggestions": suggestions, "tick": self.world.tick}
            self.save()
            return c.adjutant
