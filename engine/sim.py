from __future__ import annotations

import asyncio
import logging
import random
import re

from engine import enemy_ai, missions, obedience, persona, prompts, reserve, roster, scenario, store, units
from engine.models import Action, Commander, Enemy, Member, Squad, World, refill_loadout
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
    "retreat": "撤退到相鄰區塊（脫離接觸）",
}
COMBAT_LABELS = {
    "keep": "維持目前戰術",
    "hold": "固守：就地找掩體，對看得到的敵人射擊",
    "advance": "推進：兩組交替前進，接近敵人最後位置",
    "supp_advance": "壓制推進：一半人持續壓制，一半人推進",
    "flank": "側翼包抄：繞到敵人側面；target 填 left 或 right",
    "assault": "突擊：全員衝上去，手榴彈與近戰（傷亡風險高）",
    "hide": "伏擊：隱蔽不動，敵人進入近距離才開火",
    "retreat": "撤退：交替掩護後撤，拖回倒地隊員，撤出到相鄰區塊",
    "breach": ("破門：攻進一棟建築。target＝建築編號（見戰術狀況）或 null；"
               "另給 prep（flash 閃光／frag 破片／smoke 煙霧／none 直接進／null）、"
               "entry（one 同一扇門／multi 兩扇門同時，需 ≥2 門 ≥3 人）、tempo（stack 到齊再進／quick 快速）"),
}
TACTICS = {"advance", "supp_advance", "flank", "assault", "breach"}
SHORT_LABELS = {"hold": "固守", "move": "移動", "scout": "偵察", "hide": "隱蔽", "attack": "攻擊", "retreat": "撤退",
                "advance": "推進", "supp_advance": "壓制推進", "flank": "側翼包抄", "assault": "突擊", "breach": "破門"}
BREACH_PREP = {"flash", "frag", "smoke", "none"}
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
WANDERER_TTL = 60              # 遊蕩生化人放著不撿，60 tick 後離開
WANDERER_SIGNAL_P = 0.5        # 「偵察可疑訊號」支線有一半機率在那裡找到遊蕩生化人
WANDERER_INTERVAL = (50, 90)   # 地圖上隨機出現的間隔
WANDERER_MAX = 2
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
        self.remnant_events: dict = {}
        self.world: World = store.load(save_path) or scenario.new_world()

    def save(self):
        roster.sync_world(self.world)
        store.save(self.world, self.save_path)

    def reset(self, keep_squads: bool = True) -> list[tuple[str, str | None, str]]:
        """回傳 [(小隊 id, 指揮官 id, 角色卡 id)]，供重新部署。"""
        roster = [(s.id, s.commander, s.card_id or s.id) for s in self.world.squads.values()] if keep_squads else []
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
            card = load_card(squad.card_id or squad.id)
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

    def _start_mission(self, c: Commander, spec: dict, line: str) -> bool:
        """開任務。營救殘部：圍攻兵力在全圖上限內往下減（最少 min_strength）；連最少都放不下就不開，回傳 False。"""
        if spec.get("type") == "remnant":
            for e in spec.get("spawn", []):
                lo = e.get("min_strength", e["strength"])
                n = e["strength"]
                while n > lo and not enemy_ai.can_spawn(self.world, n):
                    n -= 1
                if not enemy_ai.can_spawn(self.world, n):
                    return False
                e["strength"] = n
        m = {**spec, "line": line, "id": self.world.next_id("m"), "started": self.world.tick,
             "deadline": self.world.tick + spec["time_limit"] if spec.get("time_limit") else None,
             "progress": 0, "spawned": []}
        for e in spec.get("spawn", []):
            eid = self.world.next_id("e")
            if not enemy_ai.can_spawn(self.world, e["strength"]):
                continue
            besieger = m["type"] == "remnant"
            self.world.enemies[eid] = Enemy(id=eid, pos=e["pos"], strength=e["strength"], route=list(e["route"]),
                                            move_every=e.get("move_every", 999), cooldown=e.get("move_every", 999),
                                            tag="siege" if besieger else "garrison", home=e["pos"],
                                            max_strength=e["strength"])
            m["spawned"].append(eid)
        if m["type"] == "crate":
            oid = self.world.next_id("o")
            self.world.objects[oid] = {"type": "crate", "cell": m["cell"], "mission": m["id"]}
            m["object"] = oid
        if m["type"] == "rescue":
            oid = self.world.next_id("o")
            self.world.objects[oid] = {"type": "wanderer", "cell": m["cell"], "mission": m["id"],
                                       "until": None}
            m["object"] = oid
        if m["type"] == "remnant":
            from engine import remnants
            r = remnants.spawn(self.world, self.rng, m["cell"], c.id, m["id"])
            m["remnant"] = r.id
            self.remnant_events.setdefault(r.id, []).append(("sos", ""))
        if m["type"] == "reach" and m["line"] == "side" and self.rng.random() < WANDERER_SIGNAL_P:
            oid = self.world.next_id("o")
            self.world.objects[oid] = {"type": "wanderer", "cell": m["cell"], "mission": None,
                                       "until": self.world.tick + WANDERER_TTL}
        c.missions.append(m)
        self.outbox.append({"type": MISSION_OUTBOX, "commander": c.id, "event": "start", "mission": dict(m)})
        self.dirty.add(c.id)
        return True

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
            busy |= {m["cell"] for o in self.world.commanders.values() if o.id != c.id
                     for m in o.missions if m["line"] == "side"}
            spec = missions.roll_side(self.rng, busy)
            if not self._start_mission(c, spec, "side"):
                self._start_mission(c, missions.roll_side(self.rng, busy, kind=self.rng.choice(
                    [k for k in missions.SIDE_TEMPLATES if k != "rescue"])), "side")
            c.next_side = self.world.tick + self.rng.randint(*SIDE_INTERVAL)

    def _end_mission(self, c: Commander, m: dict, success: bool, reason: str = ""):
        c.missions.remove(m)
        for eid in m.get("spawned", []):
            e = self.world.enemies.get(eid)
            if e and e.alive and not e.engaged_with:
                enemy_ai.withdraw(self.world, e)
        if m.get("object"):
            self.world.objects.pop(m["object"], None)
        if m.get("remnant"):
            r = self.world.remnants.get(m["remnant"])
            if r is not None and r.status == "holding":
                r.status = "destroyed"
            self.world.remnants.pop(m["remnant"], None)
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
            elif t == "remnant":
                r = self.world.remnants.get(m.get("remnant", ""))
                if r is None or r.status in ("surrendered", "destroyed"):
                    reason = "殘部投降" if r is not None and r.status == "surrendered" else "殘部全滅"
                    self._end_mission(c, m, False, reason)
                    continue
                done = r.status == "rescued"
            if done:
                self._end_mission(c, m, True)
            elif m.get("deadline") is not None and self.world.tick >= m["deadline"]:
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
            elif obj["type"] in ("wanderer", "remnant"):
                del self.world.objects[oid]
                self.pending_recruits.append((squad.id, f"在 {scenario.cell_desc(squad.pos)} 找到的遊蕩生化人"))
                squad.morale = min(100, squad.morale + 5)

    # ------------------------------------------------------------------ reflection (1.5)

    def _want_reflect(self, squad: Squad, reason: str):
        if reason not in squad.reflect_reasons:
            squad.reflect_reasons.append(reason)

    async def _reflect(self, squad: Squad):
        reasons, squad.reflect_reasons = squad.reflect_reasons, []
        squad.last_reflect = self.world.tick
        leader = squad.leader
        sp = squad.speaker_member()
        system = prompts.REFLECT_SYSTEM.format(
            name=squad.acting_leader(), callsign=squad.callsign,
            personality=(sp.personality if sp and sp.personality else leader.get("personality", "")),
            background=(sp.background if sp and sp.background else leader.get("background", "")),
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
        if sp is not None:
            sp.beliefs = [dict(b) for b in squad.beliefs]
            sp.tendencies = dict(squad.tendencies)
            from engine import sop
            new_sop = sop.normalize(data.get("sop"))
            if new_sop:
                sp.sop = new_sop
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
            squad = self.resolve(squad_id)
            if not squad or squad.status != "active":
                return False, "找不到該小隊。"
            ok, msg = self._request_supply_locked(squad, via_radio=False)
            if ok and squad.linked:
                self._system(squad, f"📦 指揮部：{msg}")
            self.save()
            return ok, msg

    async def deploy_generated(self, card: dict, commander_id: str) -> Squad:
        async with self.lock:
            squad = self._make_squad(card, commander_id)
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
        sid, callsign = self._unique_callsign(re.sub(r"[^A-Za-z]", "", str(data.get("callsign") or "")) or "Unit")
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

    async def leader_voice(self, entry: dict, context: str) -> dict:
        """白紙隊長要開口：依性格數字與經歷請 LLM 寫個性、說話風格、背景、台詞。失敗時用數字組出簡單描述。"""
        m = reserve.to_member(entry, "rifle", self.world.tick) if isinstance(entry, dict) else entry
        data = await self.llm.chat_json(prompts.VOICE_SYSTEM.format(world=units.WORLD_NOTE), prompts.VOICE_USER.format(
            name=m.name, traits=persona.traits_text(m), short=persona.describe_short(m), context=context),
            temperature=0.95, priority=2)
        if not data or not data.get("personality"):
            return {"personality": f"{persona.describe_short(m)}的生化人。", "speech": "簡短的軍事無線電口吻。",
                    "background": context[:60], "sample_lines": []}
        return {"personality": str(data.get("personality", ""))[:200], "speech": str(data.get("speech", ""))[:80],
                "background": str(data.get("background", ""))[:120],
                "sample_lines": [str(x)[:60] for x in (data.get("sample_lines") or [])[:4]],
                "callsign": re.sub(r"[^A-Za-z]", "", str(data.get("callsign") or ""))[:12],
                "beliefs": [str(x)[:60] for x in (data.get("beliefs") or [])[:2]]}

    def _taken_card_ids(self) -> set[str]:
        return {s.card_id or s.id for s in self.world.squads.values()}

    def _unique_callsign(self, callsign: str) -> tuple[str, str]:
        """回傳（角色卡 id, 呼號）。角色卡 id 全區唯一（角色卡檔案共用一個資料夾）。"""
        taken = {s.callsign for s in self.world.squads.values()}
        cards = self._taken_card_ids()
        callsign = callsign or "Unit"
        base, n = callsign.lower(), 2
        sid = base
        while sid in cards or callsign in taken:
            sid = f"{base}{n}"
            callsign = f"{callsign.rstrip('0123456789')}{n}"
            n += 1
        return sid, callsign

    async def card_from_reserve(self, n: int, source: str) -> dict | None:
        """從預備兵池抽 n 人組成增援小隊：第一人當隊長（LLM 寫人格＋呼號），其餘白紙。池少於 2 人 → None。"""
        if reserve.remaining(self.world) < 2:
            self._reserve_empty_notice()
            return None
        picks = [reserve.draw(self.world) for _ in range(min(n, reserve.remaining(self.world)))]
        lead = picks[0]
        taken = "、".join(sorted(s.callsign for s in self.world.squads.values())) or "無"
        voice = await self.leader_voice(lead, f"{source}；他將帶領這支新小隊，必須給一個新的英文呼號"
                                              f"（不可與既有呼號重複：{taken}）")
        sid, callsign = self._unique_callsign(voice.get("callsign") or self.rng.choice(
            ("Ember", "Kilo", "Talon", "Vesper", "Onyx", "Wren", "Harrow", "Lumen")))
        card = {
            "id": sid, "callsign": callsign, "generated": True,
            "leader": {"name": lead["name"], "personality": voice["personality"], "speech": voice["speech"],
                       "background": voice["background"], "sample_lines": voice["sample_lines"],
                       "traits": lead["traits"], "persona": max(0.3, lead.get("persona", 0.1))},
            "members": [{"name": p["name"], "traits": p["traits"], "persona": p.get("persona", 0.1)}
                        for p in picks[1:]],
            "initial_beliefs": voice.get("beliefs") or ["先活下來，才能完成任務。"],
            "tendencies": obedience.normalize_tendencies({}),
        }
        return card

    async def _reinforce(self, c: Commander) -> Squad | None:
        c.reinforce_ready = self.world.tick + REINFORCE_COOLDOWN
        card = await self.card_from_reserve(self.rng.randint(3, 4), "指揮部從預備兵力撥給地區指揮官的增援小隊")
        if not card:
            return None
        self.new_cards.append(card)
        squad = self._make_squad(card, c.id)
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
            from engine.tactical.units import loadout
            if squad.ammo < 100 or any(m.ammo is not None or
                                       any(getattr(m, k) != v for k, v in loadout(m.weapon).items())
                                       for m in squad.members if not m.lost):
                squad.ammo = 100
                for m in squad.members:
                    if not m.lost:
                        m.ammo = None
                        refill_loadout(m)
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
            if "遊蕩" in source:
                entry = reserve.make_blank(self.world, self.rng, persona_value=0.15)
                gone = reserve.give_back(self.world, reserve.to_member(entry, "rifle", self.world.tick))
                self._experience(squad, "rescue_full", prompts.EVENT_WANDERER_FULL.format(
                    cell=scenario.cell_desc(squad.pos), back="已指引他回後方編入預備兵力" if gone
                    else "預備兵力已滿，只能讓他自行離開"))
            return
        wanderer = "遊蕩" in source
        if wanderer:
            entry = reserve.make_blank(self.world, self.rng, persona_value=0.15)
            hp = 60
        else:
            entry = reserve.draw(self.world, for_squad=squad.id, rng=self.rng)
            if entry is None:
                self._reserve_empty_notice(squad)
                return
            hp = 100
        returning = bool(entry.pop("returning", False))
        if returning:
            weapon = entry.get("weapon") or roster.missing_weapon(squad)
            recruit = reserve.to_member(entry, weapon, self.world.tick, hp=hp)
            squad.members.append(recruit)
            squad.last_recruit = self.world.tick
            record = self._experience(squad, "returned", f"{recruit.name} 從後方歸隊")
            await self._react(squad, [prompts.EVENT_RETURNED.format(name=recruit.name)], record)
            return
        recruit = reserve.to_member(entry, roster.missing_weapon(squad), self.world.tick, hp=hp)
        if wanderer:
            from engine.tactical.units import WEAPONS
            recruit.ammo = int(WEAPONS[recruit.weapon]["ammo"] * self.rng.uniform(0.3, 0.6))
            recruit.grenades, recruit.smokes, recruit.flashes = self.rng.choice(((1, 0, 0), (0, 1, 0), (0, 0, 1),
                                                                                 (0, 0, 0)))
        squad.members.append(recruit)
        squad.last_recruit = self.world.tick
        trait = persona.describe_short(recruit)
        name = recruit.name
        if wanderer:
            event = prompts.EVENT_WANDERER.format(cell=scenario.cell_desc(squad.pos), name=name)
            kind = "rescue"
        else:
            event = prompts.EVENT_RECRUIT.format(name=name, trait=trait)
            kind = "recruit"
        record = self._experience(squad, kind, f"{name}（{trait}）歸建：{source}")
        await self._react(squad, [event], record)

    def _reserve_empty_notice(self, squad: Squad | None = None):
        if self.world.reserve_announced:
            return
        self.world.reserve_announced = True
        for c in self.world.commanders.values():
            self._event(c, "指揮部：預備兵力已耗盡，之後不再有補充兵與增援。")
            self.dirty.add(c.id)
        if squad is not None:
            self._experience(squad, "reserve_empty", "指揮部通知：預備兵力已耗盡")

    @staticmethod
    def squad_id(commander_id: str | None, card_id: str) -> str:
        """小隊 id＝<指揮官 Discord id>.<角色卡 id>（兩位指揮官都部署 alpha 也不會撞）。"""
        return f"{commander_id}.{card_id}" if commander_id else card_id

    def find_squad(self, commander_id: str | None, card_id: str) -> Squad | None:
        return self.world.squads.get(self.squad_id(commander_id, card_id))

    def resolve(self, key: str) -> Squad | None:
        """完整 id；或全區唯一的角色卡 id／呼號（GM 指令與測試用）。"""
        s = self.world.squads.get(key)
        if s is not None:
            return s
        k = key.lower()
        hits = [x for x in self.world.squads.values() if k in ((x.card_id or "").lower(), x.callsign.lower())]
        return hits[0] if len(hits) == 1 else None

    def _make_squad(self, card: dict, commander_id: str | None) -> Squad:
        squad = scenario.squad_from_card(card)
        squad.card_id = card["id"]
        squad.id = self.squad_id(commander_id, card["id"])
        squad.commander = commander_id
        return squad

    async def deploy_squad(self, card: dict, commander_id: str, reset: bool = False) -> tuple[Squad, bool]:
        async with self.lock:
            existing = self.find_squad(commander_id, card["id"])
            if existing and not reset:
                return existing, False
            if existing and existing.commander:
                c = self.world.commanders.get(existing.commander)
                if c:
                    c.intel["squads"].pop(existing.id, None)
                    self.dirty.add(c.id)
            squad = self._make_squad(card, commander_id)
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
        if a.type == "flank" and a.flank:
            return f"側翼包抄（{'左' if a.flank == 'left' else '右'}）"
        if a.type == "breach":
            p = a.breach or {}
            bits = [f"建築 {p['room']}" if p.get("room") is not None else "自選建築",
                    {"flash": "閃光", "frag": "破片", "smoke": "煙霧", "none": "直接進"}.get(p.get("prep"), "自選投擲"),
                    {"multi": "兩門同時", "one": "單門"}.get(p.get("entry"), ""),
                    {"quick": "快速", "stack": "到齊再進"}.get(p.get("tempo"), "")]
            return f"破門（{'、'.join(x for x in bits if x)}）"
        return SHORT_LABELS.get(a.type, a.type)

    def _allowed(self, squad: Squad) -> tuple[dict[str, list[str]], str]:
        adj = scenario.adjacent(squad.pos)
        if self.in_engagement(squad):
            allowed = {"keep": [], "hold": [], "advance": [], "supp_advance": [], "flank": ["left", "right"],
                       "assault": [], "hide": [], "retreat": adj}
            eng = self.engagements.get(squad.pos)
            rooms = eng.building_ids(squad) if eng else []
            if rooms:
                allowed["breach"] = [str(r) for r in rooms]
            lines = []
            for k, targets in allowed.items():
                if k in ("flank", "breach"):
                    lines.append(f"- {k}：{COMBAT_LABELS[k]}")
                elif targets:
                    lines.append(f"- {k}：{COMBAT_LABELS[k]}；target：{', '.join(targets)}"
                                 f"（預設往後方據點方向）")
                else:
                    lines.append(f"- {k}：{COMBAT_LABELS[k]}；target 填 null")
            lines.append("（交戰中無法做作戰層移動或偵察；要離開這一格只能 retreat。）")
            return allowed, "\n".join(lines)
        allowed = {
            "keep": [], "hold": [], "hide": [],
            "move": [c for c in scenario.CELLS if c != squad.pos],
            "scout": adj, "retreat": adj,
        }
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
            f"{self._tactical_brief(squad)}"
            f"近期經歷：\n{memory}"
        )

    def _tactical_brief(self, squad: Squad) -> str:
        eng = self.engagements.get(squad.pos)
        if eng is None or not eng.hot():
            return ""
        text = eng.brief(squad)
        return text + "\n" if text else ""

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
        """系統提示用目前發言人自己的人格（接手的人說話風格會變）。"""
        sp = squad.speaker_member()
        card = squad.leader
        personality = (sp.personality if sp and sp.personality else card.get("personality", ""))
        speech = (sp.speech if sp and sp.speech else card.get("speech", ""))
        background = (sp.background if sp and sp.background else card.get("background", ""))
        lines = (sp.sample_lines if sp and sp.sample_lines else card.get("sample_lines", []))
        samples = "\n".join(f"- {s}" for s in lines) or "（無）"
        handover = ""
        orig = squad.commander_name or (squad.members[0].name if squad.members else None)
        if sp is not None and orig and sp.name != orig:
            handover = f"\n（你是在原隊長 {orig} 倒下後接手指揮的。之前的經歷是全隊共同的記憶。）"
        return prompts.LEADER_SYSTEM.format(
            name=squad.acting_leader(),
            callsign=squad.callsign,
            personality=personality + handover,
            speech=speech,
            background=background,
            beliefs=obedience.beliefs_text(squad),
            tendencies=obedience.tendency_text(squad),
            long_memory=f"長期記憶：{squad.long_memory}\n" if squad.long_memory else "",
            samples=samples,
            capabilities=units.capability_text(card),
        )

    # ------------------------------------------------------------------ 換人帶隊（階段 3）

    @staticmethod
    def _successor(squad: Squad):
        alive = squad.alive_members()
        if not alive:
            return None
        return max(alive, key=lambda m: (m.led_before, m.persona, m.traits.get("comp", 0.0), -squad.members.index(m)))

    async def _check_handover(self, squad: Squad):
        """發言人倒下 → 接手（曾帶隊 > 人格值 > 沉著）；原隊長在據點整補後恢復指揮。"""
        if squad.status != "active":
            return
        if squad.commander_name is None and squad.members:
            squad.commander_name = squad.members[0].name
            squad.speaker = squad.members[0].name
        cur = squad.member(squad.speaker)
        orig = squad.member(squad.commander_name)
        if cur is not None and cur.alive and not (orig is not None and orig.alive and cur is not orig
                                                  and squad.rest_ticks >= REST_FULL):
            await self._maybe_revoice(squad, cur)
            return
        if orig is not None and orig.alive and (cur is None or not cur.alive or squad.rest_ticks >= REST_FULL):
            new = orig
        else:
            new = self._successor(squad)
        if new is None or (cur is not None and new is cur):
            return
        old_name = cur.name if cur else "—"
        back = new is orig
        await self._ensure_voice(squad, new, "接手指揮" if not back else "恢復指揮")
        squad.speaker = new.name
        self._swap_beliefs(squad, cur, new)
        if not back:
            new.led_before = True
        text = (f"🔁 {old_name} 倒下，{new.name} 接手指揮。" if not back
                else f"🔁 {new.name} 修復完成，恢復指揮。")
        self._system(squad, text)
        self._experience(squad, "handover", text.lstrip("🔁 "))
        self._want_reflect(squad, f"{'換人帶隊' if not back else '原隊長歸隊'}：{new.name}")
        self._dirty(squad)

    async def _ensure_voice(self, squad: Squad, m, why: str):
        if m.personality:
            return
        ctx = (f"{squad.callsign} 小隊隊員，{why}。小隊近期經歷：" +
               "；".join(x["text"] for x in squad.experiences[-6:]))[:300]
        voice = await self.leader_voice(m, ctx)
        for k in ("personality", "speech", "background", "sample_lines"):
            setattr(m, k, voice.get(k) or getattr(m, k))
        m.voice_level = m.persona
        if not m.beliefs:
            m.beliefs = [{"text": b, "origin": "experience", "t": self.world.tick} for b in voice.get("beliefs", [])]

    async def _maybe_revoice(self, squad: Squad, m):
        """人格值跨過 0.4、0.7 → 重寫一次個性文字（跟上他的變化）。"""
        for mark in (0.4, 0.7):
            if m.voice_level < mark <= m.persona and m.personality:
                ctx = (f"{squad.callsign} 小隊的帶隊者，經歷許多交戰後個性更鮮明。原本的個性：{m.personality}。"
                       f"近期經歷：" + "；".join(x["text"] for x in squad.experiences[-6:]))[:400]
                voice = await self.leader_voice(m, ctx)
                for k in ("personality", "speech", "sample_lines"):
                    setattr(m, k, voice.get(k) or getattr(m, k))
                m.voice_level = m.persona
                self._experience(squad, "revoice", f"{m.name} 的個性變得更鮮明")
                return

    def _swap_beliefs(self, squad: Squad, old, new):
        """信念與傾向每人一份（B）：小隊欄位保存目前發言人的，換人時存回舊的、載入新的。"""
        if old is not None:
            old.beliefs = [dict(b) for b in squad.beliefs]
            old.tendencies = dict(squad.tendencies)
        if new.beliefs:
            squad.beliefs = [dict(b) for b in new.beliefs]
        if new.tendencies:
            squad.tendencies = obedience.normalize_tendencies(new.tendencies)

    # ------------------------------------------------------------------ action application

    def _normalize(self, squad: Squad, data: dict, allowed: dict) -> tuple[str, str | None, bool, list[str]]:
        action = str(data.get("action") or "keep").strip().lower()
        target = data.get("target")
        target = str(target).strip().upper() if target else None
        direct = bool(data.get("direct"))
        via = data.get("via") or []
        via = [str(v).strip().upper() for v in via if isinstance(v, str)] if isinstance(via, list) else []
        via = [v for v in via if v in scenario.CELLS]
        if action == "attack" and "assault" in allowed:
            action = "assault"
        if action not in allowed:
            return "keep", None, False, []
        if action == "flank":
            side = str(data.get("target") or "").strip().lower()
            side = {"左": "left", "右": "right"}.get(side, side)
            return action, side if side in ("left", "right") else "left", False, []
        if action == "breach":
            raw = str(data.get("target") or "").strip()
            digits = "".join(ch for ch in raw if ch.isdigit())
            room = digits if digits in allowed["breach"] else None
            prep = str(data.get("prep") or "").strip().lower()
            entry = str(data.get("entry") or "").strip().lower()
            tempo = str(data.get("tempo") or "").strip().lower()
            self._breach_params = {"room": int(room) if room is not None else None,
                                   "prep": prep if prep in BREACH_PREP else None,
                                   "entry": entry if entry in ("one", "multi") else None,
                                   "tempo": tempo if tempo in ("stack", "quick") else None}
            return action, room, False, []
        if action == "retreat" and "assault" in allowed and target not in allowed[action]:
            return action, None, False, []
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
        if action == "flank":
            squad.action = Action(type="flank", flank=target, ordered=ordered)
            return self._action_text(squad)
        if action == "breach":
            params = dict(getattr(self, "_breach_params", None) or {})
            self._breach_params = None
            squad.action = Action(type="breach", breach=params, ordered=ordered)
            return self._action_text(squad)
        if self.in_engagement(squad) and action == "retreat":
            squad.action = Action(type="retreat", target=target, ordered=ordered)
            return f"撤退{' → ' + target if target else '（往後方）'}"
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
                f"預備兵力：剩 {reserve.remaining(self.world)} 人\n"
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
                f"小隊 {sq.card_id if sq and sq.card_id else sid}（{s['callsign']}）：最後回報 T{s['tick']}，位置 {s['pos']}，"
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
            squad = self.resolve(squad_id)
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
            data = {"action": "keep", "report": "（雜訊）……接敵！" if squad.contact else None}
            record["sop_fallback"] = True
        action, target, direct, via = self._normalize(squad, data, allowed)
        record["outcome"] = self._apply(squad, action, target, direct, ordered=False, via=via)
        record["interp"] = {"thought": data.get("interp")}
        report = data.get("report")
        record["reported"] = bool(report) and squad.linked
        self._deliver(squad, str(report).strip() if report else None)
        self._dirty(squad)

    # ------------------------------------------------------------------ 應變準則（SOP）

    def squad_sop(self, squad: Squad) -> dict:
        from engine import sop
        sp = squad.speaker_member()
        if sp is None:
            return sop.derive({})
        return {**sop.derive(sp.traits, sp.persona), **sop.normalize(sp.sop)}

    def _apply_sop(self, squad: Squad, kinds: list[tuple[str, str]]) -> str | None:
        """戰術層出現卡住、倒地、彈藥見底、士氣崩潰 → 照帶隊者的準則先換戰術（LLM 回來再覆蓋）。
        指揮官明確下令時只允許保命動作。回傳換成的行動或 None。"""
        from engine import sop
        if not self.in_engagement(squad):
            return None
        table = self.squad_sop(squad)
        ordered = squad.action.type if squad.action.ordered else None
        for kind, text in kinds:
            sit = sop.situation_from_text(kind, text)
            if sit is None:
                continue
            if sit == "stalled":
                eng = self.engagements.get(squad.pos)
                if eng is not None:
                    lk = eng.battle.intel["blue"].last_known
                    if any(eng.battle.map.room_at(n) for n, _ in lk.values()):
                        sit = "enemy_in_building"
            act = sop.pick(table, sit, ordered)
            if act is None or act == squad.action.type:
                continue
            allowed, _ = self._allowed(squad)
            if act not in allowed:
                continue
            target = None
            if act == "flank":
                target = "left" if self.rng.random() < 0.5 else "right"
            if act == "breach":
                self._breach_params = {}
            self._apply(squad, act, target, False, ordered=False)
            self._experience(squad, "sop", f"依準則（{sop.SITUATION_LABELS[sit]}）先改為{SHORT_LABELS.get(act, act)}")
            return act
        return None

    async def set_link(self, squad_id: str, linked: bool):
        async with self.lock:
            squad = self.resolve(squad_id)
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
            self._wanderers()
            drops = {}
            for squad, text in self._airdrops():
                drops.setdefault(squad.id, []).append(text)
            roster.sync_world(w)
            self._check_standoffs()
            self.tac_events = self._run_engagements()
            for squad in list(w.squads.values()):
                if squad.status == "active":
                    await self._tick_squad(squad, extra_events=drops.get(squad.id, []))
            await self._tick_remnants()
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

    # ------------------------------------------------------------------ 失聯友軍殘部（綠方）

    def _check_standoffs(self):
        from engine import remnants
        for r in self.world.remnants.values():
            reason = remnants.check_standoff(self.world, r)
            if reason:
                self.remnant_events.setdefault(r.id, []).append(("engage", reason))
                c = self.world.commanders.get(r.commander or "")
                if c:
                    self._event(c, f"失聯友軍（{scenario.cell_desc(r.pos)}）：{reason}，交戰開始")
                    self.dirty.add(c.id)

    async def _tick_remnants(self):
        from engine import remnants
        w = self.world
        for r in list(w.remnants.values()):
            evs = self.remnant_events.pop(r.id, [])
            if r.status != "holding":
                continue
            remnants.leader_of(r)
            if remnants.in_standoff(w, r):
                result = remnants.outcome(w, r)
                if result in ("surrendered", "destroyed"):
                    r.status = result
                    await self._remnant_resolved(r)
                    continue
                if evs:
                    await self._remnant_radio(r, evs)
                continue
            if any(s.status == "active" and s.pos == r.pos for s in w.squads.values()) \
                    and "met" not in r.radio_flags:
                r.radio_flags.append("met")
                evs.append(("met", ""))
            if sum(1 for m in r.alive_members() if (m.ammo or 999) < 30) >= max(1, len(r.alive_members()) // 2) \
                    and "ammo" not in r.radio_flags:
                r.radio_flags.append("ammo")
                evs.append(("ammo", ""))
            result = remnants.outcome(w, r)
            if result:
                r.status = result
                await self._remnant_resolved(r)
                continue
            if evs:
                await self._remnant_radio(r, evs)

    def remnant_status_text(self, m: dict) -> str:
        """任務看板：殘部的狀況（只有指揮官聽到的無線電內容；不洩漏真實人數以外的細節）。"""
        from engine import remnants
        r = self.world.remnants.get(m.get("remnant", ""))
        if r is None:
            return "殘部狀態：失去訊號"
        if remnants.in_standoff(self.world, r):
            left = r.standoff_until - self.world.tick
            return f"敵軍集結中（約 {self.seconds(left)} 秒後進攻）｜殘部：{r.last_line or '斷續的求救訊號'}"
        return f"殘部最後回報：{r.last_line or '斷續的求救訊號'}"

    async def _remnant_radio(self, r, evs: list):
        """臨時隊長的無線電：求救、有人倒下、彈藥見底、看到援軍、準備投降。放在總部事件欄（之後可能改）。"""
        from engine import remnants
        c = self.world.commanders.get(r.commander or "")
        lead = remnants.leader_of(r)
        if c is None or lead is None:
            return
        urgent = any(k in ("sos", "met", "surrender", "engage") for k, _ in evs)
        if not urgent and self.world.tick - r.last_radio < remnants.RADIO_GAP:
            return
        r.last_radio = self.world.tick
        if not r.voiced or not lead.personality:
            voice = await self.leader_voice(lead, f"失聯的友軍殘部臨時隊長，困在 {scenario.cell_desc(r.pos)} 被敵軍圍攻、"
                                                  f"正在固守等待救援")
            for k in ("personality", "speech", "background", "sample_lines"):
                setattr(lead, k, voice.get(k) or getattr(lead, k))
            lead.voice_level = lead.persona
            r.voiced = True
        what = {"sos": "第一次發出求救：躲在建築裡，外面約 4～5 名敵軍正在集結", "down": "有隊員倒下",
                "casualty": "有隊員陣亡", "ammo": "彈藥快沒了", "met": "看到我方小隊趕到",
                "surrender": "有人撐不住投降了"}
        engage = {"敵軍發起攻擊": "敵軍開始進攻了", "援軍接近": "敵軍發現援軍接近，正在強攻"}

        def fact(k, n):
            if k == "engage":
                return engage.get(n, n)
            return what.get(k, k) + (f"（{n}）" if n else "")
        facts = "、".join(dict.fromkeys(fact(k, n) for k, n in evs))
        alive = len([m for m in r.members if m.alive])
        user = prompts.REMNANT_RADIO_USER.format(cell=scenario.cell_desc(r.pos), alive=alive, facts=facts)
        system = prompts.REMNANT_RADIO_SYSTEM.format(name=lead.name, personality=lead.personality,
                                                     speech=lead.speech)
        data = await self.llm.chat_json(system, user, temperature=0.9, priority=3)
        line = str((data or {}).get("report") or "").strip() or f"……這裡是 {scenario.cell_desc(r.pos)} 的失聯單位，還有 {alive} 個人……"
        r.last_line = line
        self._event(c, f"📻 失聯友軍（{scenario.cell_desc(r.pos)}，{lead.name}）：{line}")
        self.dirty.add(c.id)

    async def _remnant_resolved(self, r):
        """結局處理：救回（自成小隊或併入）／投降／被殲滅。"""
        from engine import remnants
        c = self.world.commanders.get(r.commander or "")
        if r.status != "rescued":
            if c:
                self._event(c, f"失聯友軍（{scenario.cell_desc(r.pos)}）{'投降了' if r.status == 'surrendered' else '訊號全數消失'}")
                self.dirty.add(c.id)
            return
        alive = remnants.survivors(r)
        present = sorted([s for s in self.world.squads.values() if s.status == "active" and s.pos == r.pos],
                         key=lambda s: s.id)
        if c is None or not any(s.commander == c.id for s in present):
            other = next((self.world.commanders.get(s.commander or "") for s in present
                          if self.world.commanders.get(s.commander or "")), None)
            if other is not None:
                if c is not None and other.id != c.id:
                    self._event(c, f"失聯友軍（{scenario.cell_desc(r.pos)}）由 {other.name} 的部隊救出，歸其指揮")
                    self.dirty.add(c.id)
                c = other
        here = [s for s in present if c is None or s.commander == c.id]
        active_n = len([s for s in self.world.squads.values() if s.status == "active"
                        and (c is None or s.commander == c.id)])
        if len(alive) >= 2 and active_n < ACTIVE_SQUAD_CAP and c is not None:
            await self._remnant_new_squad(r, c, alive)
            return
        target = here[0] if here else None
        for m in alive:
            m.tpos = None
            if target is not None and len(target.alive_members()) < target.max_size:
                m.joined = self.world.tick
                target.members.append(m)
                self._experience(target, "rescue", f"{m.name}（{persona.describe_short(m)}）從失聯殘部歸建")
            elif reserve.give_back(self.world, m):
                if c:
                    self._event(c, f"{m.name} 被送回後方，編入預備兵力")
        if target is not None:
            record = self._experience(target, "rescue", prompts.EVENT_REMNANT_JOIN.format(
                cell=scenario.cell_desc(r.pos), n=len(alive)))
            await self._react(target, [prompts.EVENT_REMNANT_JOIN.format(
                cell=scenario.cell_desc(r.pos), n=len(alive))], record)
        r.members = []

    async def _remnant_new_squad(self, r, c: Commander, alive: list):
        from engine import remnants
        lead = remnants.leader_of(r) or alive[0]
        taken = "、".join(sorted(s.callsign for s in self.world.squads.values())) or "無"
        voice = await self.leader_voice(lead, f"原本是失聯友軍殘部的臨時隊長，在 {scenario.cell_desc(r.pos)} 固守到被救出，"
                                              f"現在要正式帶領倖存者編成一支小隊，必須給一個新的英文呼號"
                                              f"（不可與既有呼號重複：{taken}）")
        if not lead.personality or not r.voiced:
            for k in ("personality", "speech", "background", "sample_lines"):
                setattr(lead, k, voice.get(k) or getattr(lead, k))
        sid, callsign = self._unique_callsign(voice.get("callsign") or self.rng.choice(
            ("Remnant", "Ash", "Holdout", "Ember", "Stray")))
        others = [m for m in alive if m is not lead]
        lead.trait = "小隊長"
        card = {"id": sid, "callsign": callsign, "generated": True,
                "leader": {"name": lead.name, "personality": lead.personality, "speech": lead.speech,
                           "background": lead.background, "sample_lines": list(lead.sample_lines),
                           "traits": dict(lead.traits), "persona": lead.persona},
                "members": [],
                "initial_beliefs": voice.get("beliefs") or ["我們活下來了，下次不會再被丟下。"],
                "tendencies": obedience.normalize_tendencies({})}
        squad = self._make_squad(card, c.id)
        for m in [lead] + others:
            m.tpos = None
            m.joined = self.world.tick
        squad.members = [lead] + others
        lead.beliefs = [dict(b) for b in squad.beliefs]
        lead.tendencies = dict(squad.tendencies)
        squad.pos = r.pos
        squad.max_size = max(4, len(squad.members))
        squad.morale = max(40, r.morale)
        self.new_cards.append(card)
        self.world.squads[squad.id] = squad
        self._update_intel(squad)
        self._event(c, f"失聯友軍獲救：{lead.name} 帶著 {len(others)} 人編成 {callsign} 小隊")
        self.outbox.append({"type": "reinforce", "commander": c.id, "squad": squad.id, "card": card})
        self._experience(squad, "deployed", prompts.EVENT_REMNANT_SQUAD.format(
            cell=scenario.cell_desc(r.pos), callsign=callsign))
        self.dirty.add(c.id)
        r.members = []

    def _wanderers(self):
        """遊蕩生化人：隨機出現在沒有敵人的格子；放著不撿 60 tick 後離開；敵軍經過會帶走。"""
        w = self.world
        for oid, obj in list(w.objects.items()):
            if obj["type"] not in ("wanderer", "remnant"):
                continue
            if obj.get("until") is not None and w.tick >= obj["until"]:
                del w.objects[oid]
                continue
            if self._enemy_at(obj["cell"]) is not None and not any(
                    s.pos == obj["cell"] and s.status == "active" for s in w.squads.values()):
                del w.objects[oid]
                self.enemy_log = (self.enemy_log + [f"T{w.tick} 敵軍在 {obj['cell']} 帶走一名遊蕩生化人"])[-8:]
        if not w.commanders or not any(s.status == "active" for s in w.squads.values()):
            return
        if w.next_wanderer == 0:
            w.next_wanderer = w.tick + self.rng.randint(*WANDERER_INTERVAL)
            return
        if w.tick < w.next_wanderer:
            return
        w.next_wanderer = w.tick + self.rng.randint(*WANDERER_INTERVAL)
        if sum(1 for o in w.objects.values() if o["type"] == "wanderer") >= WANDERER_MAX:
            return
        blocked = {scenario.BASE, *scenario.ENEMY_BASES}
        cells = [c for c in scenario.CELLS if c not in blocked and self._enemy_at(c) is None
                 and not any(s.pos == c for s in w.squads.values())]
        if not cells:
            return
        oid = w.next_id("o")
        w.objects[oid] = {"type": "wanderer", "cell": self.rng.choice(cells), "mission": None,
                          "until": w.tick + WANDERER_TTL}

    def _move_enemies(self):
        if not self.enemy_ai_enabled:
            return
        active = [s for s in self.world.squads.values() if s.status == "active"]
        enemy_ai.observe(self.world, self.rng, active)
        objectives = sorted({m["cell"] for c in self.world.commanders.values() for m in c.missions})
        decisions = enemy_ai.think(self.world, self.rng, objectives)
        if decisions:
            self.enemy_log = (self.enemy_log + [f"T{self.world.tick} {d}" for d in decisions])[-8:]
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
            if eng.commander.log:
                self.enemy_log = (self.enemy_log + eng.commander.log)[-8:]
                eng.commander.log = []
            for sid, evs in out["squads"].items():
                events.setdefault(sid, []).extend(evs)
            for sid, frm, dest, left in out.get("moved", []):
                msg = f"撤出 {scenario.cell_desc(frm)}，退到 {scenario.cell_desc(dest)}"
                cared = [n for s2, n in out.get("left_behind", []) if s2 == sid]
                if left and not cared:
                    msg += f"（{left} 名倒地隊員未能帶回）"
                elif cared:
                    msg += f"（{'、'.join(cared)} 倒地留在原地，由同格友軍照顧）"
                events.setdefault(sid, []).append(("withdrawn", msg))
            for rid, evs in out.get("remnants", {}).items():
                self.remnant_events.setdefault(rid, []).extend(evs)
            if out.get("medevac"):
                self._after_medevac(out["medevac"])
            for e in w.enemies.values():
                if e.alive and e.pos == cell:
                    sq = next((s for s in w.squads.values() if s.pos == cell and s.status == "active"), None)
                    rem = next((r for r in w.remnants.values() if r.pos == cell and r.status == "holding"), None)
                    who = sq.id if sq else (rem.id if rem else None)
                    e.engaged_with = who if who and eng.hot() else None
            if eng.over():
                was_hot = eng.hot() and "contact" in eng.reported
                self._after_medevac(eng.finish())
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

    def _after_medevac(self, results: list):
        """後送結果通知原小隊與指揮官。"""
        for sid, m, ok in results:
            s = self.world.squads.get(sid)
            c = self.world.commanders.get(s.commander or "") if s else None
            if ok:
                text = prompts.EVENT_MEDEVAC.format(name=m.name)
                if c:
                    self._event(c, f"{m.name}（原屬 {s.callsign if s else sid}）已被後送回後方，編入預備兵力")
                    self.dirty.add(c.id)
            else:
                text = f"{m.name} 沒能撐到後送（陣亡或預備兵力已滿）。"
                if c:
                    self._event(c, f"{m.name}（原屬 {s.callsign if s else sid}）{'陣亡' if m.lost else '未能後送'}")
            if s is not None and s.status == "active":
                self._experience(s, "medevac", text)

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
            reserve.clear_home(self.world, squad.id)
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
        await self._check_handover(squad)

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
                if kind in ("contact", "casualty", "down", "routed", "request", "enemy_down", "disengage", "ammo_out",
                            "detected", "arrive", "resupply"):
                    self._event(c, f"{squad.callsign}：{fact}")

        trigger_minor = minor and self.world.tick - squad.last_combat_report >= MINOR_GAP
        sop_kinds = [(k, f) for k, f, _ in major + minor if k in ("request", "down", "ammo_out", "ammo_low", "routed")]
        if sop_kinds and not self.llm.can_call(1):
            self._apply_sop(squad, [("request", "士氣崩潰") if k == "routed" else (k, f) for k, f in sop_kinds])
        if major or trigger_minor:
            events = [desc for _, _, desc in major] + ([desc for _, _, desc in minor] if trigger_minor else [])
            record = records[0] if records else squad.experiences[-1]
            squad.last_combat_report = self.world.tick
            await self._react(squad, events, record)
            if record.get("sop_fallback") and sop_kinds:
                self._apply_sop(squad, [("request", "士氣崩潰") if k == "routed" else (k, f) for k, f in sop_kinds])
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

    def _end_tactics(self, squad: Squad):
        """交戰結束：戰術動作（推進、突擊等）與無目標撤退回到固守。"""
        a = squad.action
        if a.type in TACTICS or a.type == "attack" or (a.type == "retreat" and not a.target):
            squad.action = Action(type="hold")

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
                self._end_tactics(squad)
                squad.contact = None
                squad.known_enemies.pop(squad.pos, None)
                squad.morale = min(100, squad.morale + 10)
                major.append(("enemy_down", fact, prompts.EVENT_ENEMY_DOWN))
                self._want_reflect(squad, "一場交戰結束（敵軍被擊潰）")
            elif kind == "disengage":
                self._end_tactics(squad)
                squad.contact = None
                major.append(("disengage", fact, prompts.EVENT_DISENGAGE))
                self._want_reflect(squad, "一場交戰結束（脫離接觸）")
            elif kind == "withdrawn":
                squad.contact = None
                result = self._observe(squad, squad.pos)
                major.append(("arrive", fact, prompts.EVENT_ARRIVE.format(
                    cell=scenario.cell_desc(squad.pos), result=f"{fact}。{result}" + self._base_note(squad))))
                self._want_reflect(squad, "撤退脫離交戰")
            elif kind == "request":
                if "士氣崩潰" in fact:
                    major.append(("routed", "士氣崩潰，小隊自行後撤", prompts.EVENT_ROUTED))
                else:
                    major.append(("request", fact, prompts.EVENT_REQUEST.format(text=fact)))
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
            if not a.target:
                squad.action = Action(type="hold")
                return
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
            by_card = {}
            for x in own:
                sq = self.world.squads[x]
                by_card[(sq.card_id or x).lower()] = x
                by_card[sq.callsign.lower()] = x
                by_card[x.lower()] = x
            for s in (data or {}).get("suggestions", [])[:4]:
                if not isinstance(s, dict):
                    continue
                sid = by_card.get(str(s.get("squad", "")).strip().lower(), "")
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
