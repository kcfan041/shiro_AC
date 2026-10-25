"""C4：敵方戰術指揮（規則式）。只用紅方戰術情報（last_known／spotted）與己方狀態決策，不讀我方真相。

每個敵方單位每 RETHINK_S 秒重新評估一次（士氣變化或兵力比急遽變化時立即重評）：
- withdraw 命令 → retreat（往目標據點方向）
- 士氣崩潰或兵力比 < RETREAT_RATIO，且不在據點 → retreat（往最近據點）
- 不知道敵人在哪 → 守軍 hold、機動隊 advance（推向中央搜索）
- 知道敵人、推算自己未被發現（Battle.believed_seen）、距離遠於伏擊距離 → ambush
  （機動隊等 AMBUSH_PATIENCE_S 秒沒等到就出擊）
- 已知敵人在建築內、兵力比 >= BREACH_RATIO、士氣正常、機動隊或反攻中 → breach（參數隨機：閃光／破片／直接、快慢、單門或兩門）
- 兵力比 >= ATTACK_RATIO 且士氣正常 → 機動隊 flank（左右輪替），守軍 supp_advance（離據點不追擊）
- 騷擾隊（harass）：累計開火 HARASS_FIRE_S 秒後撤離（打了就跑）
- 其他 → hold
"""
from __future__ import annotations

from engine import scenario
from engine.tactical.ai import Intent

RETHINK_S = 7.5        # 每組敵軍多久重新評估一次（原 3 步）
RETREAT_RATIO = 0.5
ATTACK_RATIO = 1.5
AMBUSH_MIN_DIST = 7
HARASS_FIRE_S = 10.0   # 騷擾隊開火累計這麼久就撤（原 4 步）
AMBUSH_PATIENCE_S = 22.5   # 機動隊伏擊等不到人這麼久 → 主動出擊；守軍可一直等（原 9 步）
DUG_IN_RATIO = 1.0     # 對方挖了陣地、兵力比 ≥ 1、士氣正常 → 煙幕掩護包抄、近了丟手榴彈（不正面壓制推進）
BREACH_RATIO = 1.0    # 已知敵人在建築內、兵力比 ≥ 1 → 機動隊／反攻中破門


class EnemyCommander:
    def __init__(self, eng):
        self.eng = eng
        self.current: dict[str, tuple[Intent, int, str]] = {}   # eid -> (intent, tick, reason)
        self.fired: dict[str, float] = {}      # 每組敵軍累計開火秒數
        self.flank_side = "left"
        self.ambush_ticks: dict[str, int] = {}
        self.log: list[str] = []

    def _units(self, e):
        b = self.eng.battle
        return [b.units[u] for u, o in self.eng.owner.items() if o[1] == e.id and u in b.units and b.units[u].alive]

    def _known(self):
        b = self.eng.battle
        return [(uid, node) for uid, (node, _) in b.intel["red"].last_known.items()
                if uid in b.units and b.units[uid].alive]

    def _dug_in(self, known) -> int:
        """紅方情報裡的已知敵人，最後位置在坑裡（地面比周圍原始地面低 0.4 m 以上）的人數；不到一半回傳 0。
        只看最後已知位置與地圖（地形大家都看得到），不讀真實位置。"""
        from engine.tactical import timeline
        m = self.eng.battle.map
        n = sum(1 for _, node in known if node[2] == 0
                and timeline.dig_ref(m, node[0], node[1]) - m.ground.get(node[:2], 0) >= timeline.DIG_SHALLOW)
        return n if n and n * 2 >= len(known) else 0

    def _think_seen(self, units) -> bool:
        """紅方自己推算是否已被發現（中彈、子彈掠過、開火暴露、被盯上），不讀藍方情報。"""
        return self.eng.battle.believes_any_seen(units)

    def _toward_base(self, e) -> tuple[int, int]:
        from engine.enemy_ai import nearest_base
        base = e.goal if e.order == "withdraw" and e.goal else nearest_base(e.pos)
        if not base or base == e.pos:
            return self._away(e)
        return self.eng._toward(e.pos, base)

    def _away(self, e) -> tuple[int, int]:
        """沒有據點方向時：遠離已知敵人的地圖邊。"""
        known = self._known()
        units = self._units(e)
        if not known or not units:
            return (0, 1)
        cx = sum(u.pos[0] for u in units) / len(units)
        cy = sum(u.pos[1] for u in units) / len(units)
        kx = sum(n[0] for _, n in known) / len(known)
        ky = sum(n[1] for _, n in known) / len(known)
        dx, dy = cx - kx, cy - ky
        if abs(dy) >= abs(dx):
            return (0, 1 if dy >= 0 else -1)
        return (1 if dx > 0 else -1, 0)

    def _stalled_report(self, e) -> bool:
        """這一組敵軍自己的戰術 AI 有沒有新的「請求新指示」（每組一個 AI，各自記讀到哪）。"""
        ai = self.eng.enemy_ai(e)
        marks = self.__dict__.setdefault("_report_marks", {})
        mark = marks.get(e.id, 0)
        new = ai.reports[mark:]
        marks[e.id] = len(ai.reports)
        return any("請求新指示" in r for r in new)

    def _sop_intent(self, e, known, situation: str) -> Intent | None:
        """敵方依單位類型的固定準則（engine/sop.py）。"""
        from engine import sop
        table = sop.ENEMY_SOP.get(e.tag, sop.ENEMY_SOP["mobile"])
        act = table.get(situation)
        if act is None or not known:
            return None
        goal = min((n for _, n in known), key=lambda n: (-n[1], n[0]))
        if act == "breach":
            room = self._room_with_enemy(known)
            return Intent("breach", room=room) if room is not None else Intent("assault", goal=goal)
        if act == "retreat":
            return Intent("retreat", toward=self._toward_base(e))
        if act == "flank":
            side = self.flank_side
            self.flank_side = "right" if side == "left" else "left"
            return Intent("flank", goal=goal, side=side)
        if act in ("advance", "supp_advance", "assault"):
            return Intent(act, goal=goal)
        return Intent(act)

    def _siege(self, e, units, known, ratio, state) -> tuple[Intent, str]:
        """圍攻殘部：主動逼近，殘部在建築內就破門，兵力劣勢時壓制推進，看不到人就搜索。"""
        if not known:
            rooms = [r.room for r in getattr(self.eng.world, "remnants", {}).values()
                     if r.pos == e.pos and r.status == "holding" and r.room is not None]
            if rooms and state == "normal":
                self._siege_hint(e)
                return Intent("breach", room=rooms[0]), f"圍攻：殘部躲在建築{rooms[0]}，破門"
            spot = self._siege_hint(e)
            if spot is not None:
                return Intent("assault", goal=spot), "圍攻：朝殘部最後的槍聲位置逼近"
            return Intent("advance", goal=self.eng._center()), "圍攻：搜索殘部"
        room = self._room_with_enemy(known)
        if room is not None and ratio >= 0.8 and state == "normal":
            prev = self.current.get(e.id)
            if prev and prev[0].kind == "breach" and prev[0].room == room:
                return prev[0], prev[2]
            return Intent("breach", room=room), f"圍攻：殘部躲在建築{room}，破門"
        goal = min((n for _, n in known), key=lambda n: (n[1], n[0]))
        if state == "normal" and ratio >= 0.8:
            return Intent("assault", goal=goal), "圍攻：強攻"
        return Intent("supp_advance", goal=goal), "圍攻：壓制推進"

    def _siege_hint(self, e):
        """圍攻方本來就在跟殘部交火（任務開始前），知道大概方位：給一個殘部附近 ±2 格的模糊點，並寫進紅方情報。"""
        b = self.eng.battle
        greens = [u for u in b.units.values() if u.side == "green" and u.alive]
        if not greens:
            return None
        t = min(greens, key=lambda u: u.id)
        rx = b.r("siege_hint", e.id, t.id, "x")
        ry = b.r("siege_hint", e.id, t.id, "y")
        x = max(0, min(b.map.w - 1, t.pos[0] + int(round(rx * 4 - 2))))
        y = max(0, min(b.map.h - 1, t.pos[1] + int(round(ry * 4 - 2))))
        room = b.map.room_at(t.pos)
        if room is not None:
            x0, y0, x1, y1 = room["rect"]
            x, y = max(x0 + 1, min(x1 - 1, x)), max(y0 + 1, min(y1 - 1, y))
        if "hinted" not in self.ambush_ticks:
            self.ambush_ticks["hinted"] = 1
            b.intel["red"].last_known[t.id] = ((x, y, t.pos[2]), b.tick)
        return (x, y, t.pos[2])

    def _search_point(self, e, units):
        """搜索：輪流走向格內四個象限的點（到了就換下一個），不會停在中央不動。"""
        b = self.eng.battle
        m = b.map
        ox, oy, W, H = 0, 0, m.w, m.h
        if getattr(self.eng, "zone", False):
            from engine.tactical import worldmap
            anchor = self.ambush_ticks.setdefault(f"anchor:{e.id}", e.pos)
            ox, oy = worldmap.cell_origin(anchor)
            W = H = worldmap.SIZE
        pts = [(ox + W // 4, oy + H // 4, 0), (ox + 3 * W // 4, oy + H // 4, 0), (ox + 3 * W // 4, oy + 3 * H // 4, 0),
               (ox + W // 4, oy + 3 * H // 4, 0)]
        key = f"search:{e.id}"
        i = self.ambush_ticks.get(key, int(b.r("search0", e.id) * 4))
        cx = sum(u.pos[0] for u in units) / len(units)
        cy = sum(u.pos[1] for u in units) / len(units)
        if max(abs(cx - pts[i % 4][0]), abs(cy - pts[i % 4][1])) <= 3:
            i += 1
        self.ambush_ticks[key] = i
        x, y, _ = pts[i % 4]
        for r in range(0, 6):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    n = (x + dx, y + dy, 0)
                    if m.in_bounds(n[0], n[1]) and m.standable(n):
                        return n
        return self.eng._center()

    def _room_with_enemy(self, known) -> int | None:
        m = self.eng.battle.map
        counts: dict[int, int] = {}
        for _, node in known:
            r = m.room_at(node)
            if r is not None and r["doors"]:
                counts[r["id"]] = counts.get(r["id"], 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]

    def decide(self, e) -> Intent:
        b = self.eng.battle
        units = self._units(e)
        if not units:
            return Intent("hold")
        if any(u.fired for u in units):
            self.fired[e.id] = self.fired.get(e.id, 0.0) + b.dt
        state = b.morale_state("red")
        known = self._known()
        ratio = len(units) / max(1, len(known)) if known else 99.0
        prev = self.current.get(e.id)
        urgent = state in ("rout", "surrender") or (known and ratio < RETREAT_RATIO)
        stalled = self._stalled_report(e)
        if prev and (b.tick - prev[1]) * b.dt < RETHINK_S - 1e-9 and not urgent and not stalled:
            return prev[0]
        intent, reason = self._choose(e, units, known, ratio, state)
        if stalled and prev is not None and intent.kind == prev[0].kind:
            alt = self._sop_intent(e, known, "stalled")
            if alt is not None and alt.kind != intent.kind:
                intent, reason = alt, f"依準則（推進卡住）改為 {alt.kind}"
        if prev is None or prev[0].kind != intent.kind:
            self.log.append(f"T{self.eng.world.tick} {e.id}@{e.pos} {intent.kind}（{reason}）")
            self.eng.enemy_ai(e).new_intent()
        self.current[e.id] = (intent, b.tick, reason)
        return intent

    def _choose(self, e, units, known, ratio, state) -> tuple[Intent, str]:
        at_base = e.pos in scenario.ENEMY_BASES
        if e.order == "withdraw":
            return Intent("retreat", toward=self._toward_base(e)), "奉命撤回"
        if e.tag == "siege" and state not in ("rout", "surrender"):
            return self._siege(e, units, known, ratio, state)
        if state in ("rout", "surrender") and not at_base:
            return Intent("retreat", toward=self._toward_base(e)), "士氣崩潰"
        if known and ratio < RETREAT_RATIO and not at_base:
            return Intent("retreat", toward=self._toward_base(e)), f"兵力劣勢 {len(units)}:{len(known)}"
        if e.order == "harass" or e.id.startswith("h"):
            if self.fired.get(e.id, 0.0) >= HARASS_FIRE_S - 1e-9:
                return Intent("retreat", toward=self._toward_base(e)), "騷擾得手，撤離"
        if e.tag == "siege":
            return self._siege(e, units, known, ratio, state)
        if not known:
            if e.tag == "garrison":
                if getattr(self.eng, "zone", False):
                    # 連續地圖：守軍沒發現敵人就構工（和我方同一套挖坑），胸牆面向我方後方據點
                    from engine import zone as zmod
                    return Intent("hold", dig=True, dig_face=zmod.cell_center(scenario.BASE)), "未發現敵人，固守構工"
                return Intent("hold"), "未發現敵人，固守"
            if e.tag == "patrol" and getattr(self.eng, "zone", False):
                return Intent("hold"), "巡邏中"
            return Intent("advance", goal=self._search_point(e, units)), "搜索"
        goal = min((n for _, n in known), key=lambda n: (-n[1], n[0]))
        nearest = min(min(abs(n[0] - u.pos[0]) + abs(n[1] - u.pos[1]) for u in units) for _, n in known)
        waited = self.ambush_ticks.get(e.id, 0.0)
        seen = self._think_seen(units)
        if not seen and nearest >= AMBUSH_MIN_DIST and (e.tag == "garrison" or waited < AMBUSH_PATIENCE_S - 1e-9):
            self.ambush_ticks[e.id] = waited + RETHINK_S
            return Intent("ambush", ambush_range=10), "推算未被察覺，設伏"
        was_ambush = e.id in self.current and self.current[e.id][0].kind == "ambush"
        reason_tail = "推算已暴露，放棄設伏" if seen and was_ambush else None
        pre = f"{reason_tail}；" if reason_tail else ""
        target_room = self._room_with_enemy(known)
        if target_room is not None and ratio >= BREACH_RATIO and state == "normal" and not at_base \
                and (e.tag == "mobile" or e.order in ("counter", "hunt", "harass")):
            prev = self.current.get(e.id)
            if prev and prev[0].kind == "breach" and prev[0].room == target_room:
                return prev[0], prev[2]
            b = self.eng.battle
            roll = b.r("enemy_breach", e.id, b.tick)
            prep = None if roll < 0.5 else ("frag" if roll < 0.8 else "none")
            tempo = "quick" if b.r("enemy_tempo", e.id, b.tick) < 0.5 else "stack"
            entry = "multi" if len(units) >= 3 and b.r("enemy_entry", e.id, b.tick) < 0.4 else "one"
            return (Intent("breach", room=target_room, prep=prep, tempo=tempo, entry=entry),
                    pre + f"敵人在建築{target_room}內，破門（{prep or '自選'}／{tempo}／{entry}）")
        dug = self._dug_in(known)
        if dug and ratio >= DUG_IN_RATIO and state == "normal" and not (e.tag == "garrison" and at_base):
            prev = self.current.get(e.id)
            if prev and prev[0].kind == "flank" and prev[0].dug_in:
                side = prev[0].side                # 包抄方向不要每次重評就換邊
            else:
                side = self.flank_side
                self.flank_side = "right" if side == "left" else "left"
            return (Intent("flank", goal=goal, side=side, dug_in=True),
                    pre + f"敵人挖了陣地（{dug}/{len(known)} 人在坑裡），煙幕掩護 {side} 翼包抄、近了丟手榴彈")
        if ratio >= ATTACK_RATIO and state == "normal":
            if e.tag == "garrison":
                if at_base:
                    return Intent("hold"), pre + "據點守備不出擊"
                return Intent("supp_advance", goal=goal), pre + f"兵力優勢 {len(units)}:{len(known)}，壓制推進"
            side = self.flank_side
            self.flank_side = "right" if side == "left" else "left"
            return Intent("flank", goal=goal, side=side), pre + f"兵力優勢 {len(units)}:{len(known)}，{side} 翼包抄"
        return Intent("hold"), pre + "固守交火"
