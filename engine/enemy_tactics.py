"""C4：敵方戰術指揮（規則式）。只用紅方戰術情報（last_known／spotted）與己方狀態決策，不讀我方真相。

每個敵方單位每 RETHINK tick 重新評估一次（士氣變化或兵力比急遽變化時立即重評）：
- withdraw 命令 → retreat（往目標據點方向）
- 士氣崩潰或兵力比 < RETREAT_RATIO，且不在據點 → retreat（往最近據點）
- 不知道敵人在哪 → 守軍 hold、機動隊 advance（推向中央搜索）
- 知道敵人、對方還不知道我們、距離遠於伏擊距離 → ambush（機動隊等 AMBUSH_PATIENCE tick 沒等到就出擊）
- 兵力比 >= ATTACK_RATIO 且士氣正常 → 機動隊 flank（左右輪替），守軍 supp_advance（離據點不追擊）
- 騷擾隊（harass）：交火 HARASS_FIRE tick 後撤離（打了就跑）
- 其他 → hold
"""
from __future__ import annotations

from engine import scenario
from engine.tactical.ai import Intent

RETHINK = 3
RETREAT_RATIO = 0.5
ATTACK_RATIO = 1.5
AMBUSH_MIN_DIST = 7
HARASS_FIRE = 4
AMBUSH_PATIENCE = 9   # 機動隊伏擊等不到人 → 主動出擊；守軍可一直等


class EnemyCommander:
    def __init__(self, eng):
        self.eng = eng
        self.current: dict[str, tuple[Intent, int, str]] = {}   # eid -> (intent, tick, reason)
        self.fired: dict[str, int] = {}
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

    def _blue_knows(self, units) -> bool:
        lk = self.eng.battle.intel["blue"].last_known
        return any(u.id in lk for u in units)

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

    def decide(self, e) -> Intent:
        b = self.eng.battle
        units = self._units(e)
        if not units:
            return Intent("hold")
        if any(u.fired for u in units):
            self.fired[e.id] = self.fired.get(e.id, 0) + 1
        state = b.morale_state("red")
        known = self._known()
        ratio = len(units) / max(1, len(known)) if known else 99.0
        prev = self.current.get(e.id)
        urgent = state in ("rout", "surrender") or (known and ratio < RETREAT_RATIO)
        if prev and b.tick - prev[1] < RETHINK and not urgent:
            return prev[0]
        intent, reason = self._choose(e, units, known, ratio, state)
        if prev is None or prev[0].kind != intent.kind:
            self.log.append(f"T{self.eng.world.tick} {e.id}@{e.pos} {intent.kind}（{reason}）")
            self.eng.ais["red"].new_intent()
        self.current[e.id] = (intent, b.tick, reason)
        return intent

    def _choose(self, e, units, known, ratio, state) -> tuple[Intent, str]:
        at_base = e.pos in scenario.ENEMY_BASES
        if e.order == "withdraw":
            return Intent("retreat", toward=self._toward_base(e)), "奉命撤回"
        if state in ("rout", "surrender") and not at_base:
            return Intent("retreat", toward=self._toward_base(e)), "士氣崩潰"
        if known and ratio < RETREAT_RATIO and not at_base:
            return Intent("retreat", toward=self._toward_base(e)), f"兵力劣勢 {len(units)}:{len(known)}"
        if e.order == "harass" or e.id.startswith("h"):
            if self.fired.get(e.id, 0) >= HARASS_FIRE:
                return Intent("retreat", toward=self._toward_base(e)), "騷擾得手，撤離"
        if not known:
            if e.tag == "garrison":
                return Intent("hold"), "未發現敵人，固守"
            return Intent("advance", goal=self.eng._center()), "搜索"
        goal = min((n for _, n in known), key=lambda n: (-n[1], n[0]))
        nearest = min(min(abs(n[0] - u.pos[0]) + abs(n[1] - u.pos[1]) for u in units) for _, n in known)
        waited = self.ambush_ticks.get(e.id, 0)
        if not self._blue_knows(units) and nearest >= AMBUSH_MIN_DIST and (e.tag == "garrison" or waited < AMBUSH_PATIENCE):
            self.ambush_ticks[e.id] = waited + RETHINK
            return Intent("ambush", ambush_range=10), "敵方未察覺，設伏"
        if ratio >= ATTACK_RATIO and state == "normal":
            if e.tag == "garrison":
                if at_base:
                    return Intent("hold"), "據點守備不出擊"
                return Intent("supp_advance", goal=goal), f"兵力優勢 {len(units)}:{len(known)}，壓制推進"
            side = self.flank_side
            self.flank_side = "right" if side == "left" else "left"
            return Intent("flank", goal=goal, side=side), f"兵力優勢 {len(units)}:{len(known)}，{side} 翼包抄"
        return Intent("hold"), "固守交火"
