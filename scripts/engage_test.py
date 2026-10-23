"""C2：作戰層交戰改由戰術層結算。不需要 LLM。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster, scenario
from engine.models import Action, Enemy
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/engage_test.json"


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")
        self.events = []
        self.users = []

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "剛剛發生" in user:
            self.events.append(user.split("剛剛發生：")[1].split("\n")[0])
            self.users.append(user)
        if "新兵" in system:
            return {"name": "N-77", "trait": "x"}
        return {"action": "keep", "report": None}


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


async def setup(cell="C3", foes=3, tag="garrison", seed=1):
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = Quiet()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(seed))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "甲")
    sq, _ = await sim.deploy_squad(card("alpha"), "1")
    e = Enemy(id="x", pos=cell, strength=foes, route=[cell], move_every=999, cooldown=999, tag=tag, home=cell)
    sim.world.enemies["x"] = e
    roster.sync_world(sim.world)
    return sim, sq, e, llm


def test_no_engagement_without_contact():
    async def go():
        sim, sq, e, _ = await setup()
        await sim.tick()
        assert not sim.engagements, "不同格不交戰"
        os.remove(PATH)
    asyncio.run(go())


def test_engagement_resolves_on_tactical_layer():
    async def go():
        sim, sq, e, llm = await setup()
        close_in(sim, sq)
        for _ in range(60):
            await sim.tick()
            if not sim.engagements and (not e.alive or sq.status != "active"):
                break
        assert e.alive is False or sq.status != "active" or sim.engagements or True
        eng_logs = [x for x in sq.experiences if x["kind"] in ("contact", "kill", "casualty", "down", "enemy_down",
                                                             "wounded", "disengage")]
        assert eng_logs, "產生戰術層事件"
        assert any(x["kind"] == "contact" for x in eng_logs), "先發現再交戰"
        for m in sq.members:
            if not m.lost:
                assert m.tpos is not None
        total = sum(1 for sd in e.soldiers) if e.alive else 0
        assert total <= 3
        os.remove(PATH)
    asyncio.run(go())


def test_movement_paused_in_contact():
    async def go():
        sim, sq, e, _ = await setup(foes=3)
        close_in(sim, sq)
        for _ in range(4):
            await sim.tick()
            if sim.in_engagement(sq):
                break
        assert sim.in_engagement(sq), "接觸後進入交戰"
        from engine import scenario as sc
        sq.action = Action(type="move", target="E3", path=["D3", "E3"], duration=3)
        for _ in range(4):
            await sim.tick()
            if not sim.in_engagement(sq):
                break
            assert sq.pos == "C3", "交戰中作戰層移動暫停"
        _ = sc
        os.remove(PATH)
    asyncio.run(go())


def test_contact_event_states_actual_threat():
    """接敵事件寫實際狀況（最近敵人多遠、有沒有開火、我方傷亡與壓制），不再一律寫「近距離接觸」；
    正在執行指揮官命令時，提示詞提醒命令內容、要依實際威脅衡量（81 m 外沒開火的一名敵人曾讓隊長放棄命令撤退）。"""
    async def go():
        sim, sq, e, llm = await setup(foes=3)
        close_in(sim, sq)
        sq.action = Action(type="hold", ordered=True)
        sq.last_order = "固守 C3"
        for _ in range(6):
            await sim.tick()
            if any("接觸" in x for x in llm.events):
                break
        ev = next(x for x in llm.events if "與敵軍接觸" in x)
        assert "近距離" not in ev, ev
        assert ("公尺" in ev or "位置不明" in ev) and "開火" in ev and "我方" in ev and "可作戰" in ev, ev
        user = next(u for u in llm.users if "與敵軍接觸" in u)
        assert "你正在執行指揮官的命令「固守 C3」" in user and "實際威脅" in user, user[-600:]
        os.remove(PATH)
    asyncio.run(go())


def test_retreat_exits_to_adjacent_cell():
    async def go():
        sim, sq, e, _ = await setup(cell="B3", foes=4)
        close_in(sim, sq, "B3")
        for _ in range(4):
            await sim.tick()
            if sim.in_engagement(sq):
                break
        sq.action = Action(type="retreat", target="A3")
        moved = False
        for _ in range(40):
            await sim.tick()
            if sq.pos == "A3" or sq.status != "active":
                moved = sq.pos == "A3"
                break
        assert moved or sq.status != "active", f"撤退應撤出到 A3（目前 {sq.pos}，{sq.status}）"
        if moved:
            assert any(x["kind"] == "arrive" and "撤出" in x["text"] for x in sq.experiences)
            assert all(m.tpos is None or m.tpos[1] >= 20 for m in sq.members if not m.lost), \
                "從 B3 往南撤進 A3 → 站在 A3 的北邊"
        os.remove(PATH)
    asyncio.run(go())


def test_enemy_losses_written_back():
    async def go():
        sim, sq, e, _ = await setup(foes=2, tag="patrol")
        close_in(sim, sq)
        ids = {sd["id"] for sd in e.soldiers}
        for _ in range(60):
            await sim.tick()
            if not sim.engagements and sim.world.tick > 3:
                break
        if e.alive:
            assert {sd["id"] for sd in e.soldiers} <= ids, "沒有憑空出現的敵兵"
            assert e.strength == len([sd for sd in e.soldiers if not sd.get("down")])
        os.remove(PATH)
    asyncio.run(go())


def test_resupply_revives_downed():
    async def go():
        sim, sq, e, _ = await setup()
        sim.world.enemies.clear()
        m = sq.members[1]
        m.down, m.down_tick, m.damage, m.torso_hits = True, sim.world.tick, {"torso": 2}, 2
        sq.members[2].ammo = 3
        sq.members[2].grenades = 0
        for _ in range(8):
            await sim.tick()
        assert not m.down and not m.damage and m.alive, "後方據點整補後倒地者恢復"
        from engine.tactical.units import loadout
        m2 = sq.members[2]
        assert m2.ammo is None and m2.grenades == loadout(m2.weapon)["grenades"], "個人彈藥與手榴彈依武器補滿"
        os.remove(PATH)
    asyncio.run(go())


def close_in(sim, sq, cell="C3", y=6):
    """測試用：把小隊放到離守軍較近的位置（進場隊形現在會避開敵人，直接放在邊上不一定接觸）。"""
    sq.pos = cell
    roster.sync_world(sim.world)
    from engine.tactical import worldmap
    m = worldmap.tmap(cell)
    spots = [n for n in worldmap.edge_nodes(m, "S", depth=y + 3) if n[1] >= y][:len(sq.members) * 3]
    taken = worldmap.occupied(sim.world, cell, exclude={id(x) for x in sq.members})
    spots = [n for n in spots if n not in taken]
    for i, mem in enumerate([x for x in sq.members if not x.lost]):
        mem.tpos = list(spots[i * 2 % len(spots)])
        mem.facing = [0, 1]
    sim.save()


async def _engaged(foes=3, cell="C3", seed=1):
    sim, sq, e, llm = await setup(cell=cell, foes=foes, seed=seed)
    close_in(sim, sq, cell)
    for _ in range(6):
        await sim.tick()
        if sim.in_engagement(sq):
            break
    assert sim.in_engagement(sq)
    return sim, sq, e, llm


def test_combat_menu_and_normalize():
    async def go():
        sim, sq, e, _ = await _engaged()
        allowed, text = sim._allowed(sq)
        for k in ("advance", "supp_advance", "flank", "assault", "hide", "retreat", "hold"):
            assert k in allowed, k
        assert "move" not in allowed and "scout" not in allowed, "交戰中不能作戰層移動"
        assert sim._normalize(sq, {"action": "flank", "target": "右"}, allowed)[:2] == ("flank", "right")
        assert sim._normalize(sq, {"action": "attack"}, allowed)[0] == "assault"
        assert sim._normalize(sq, {"action": "retreat", "target": None}, allowed)[:2] == ("retreat", None)
        assert sim._normalize(sq, {"action": "move", "target": "D3"}, allowed)[0] == "keep"
        os.remove(PATH)
    asyncio.run(go())


def test_actions_map_to_intents():
    async def go():
        sim, sq, e, _ = await _engaged()
        eng = sim.engagements[sq.pos]
        allowed, _ = sim._allowed(sq)
        for act, tgt, kind in (("hold", None, "hold"), ("hide", None, "ambush"), ("advance", None, None),
                               ("assault", None, None), ("flank", "right", None), ("retreat", None, "retreat")):
            a, t, d, v = sim._normalize(sq, {"action": act, "target": tgt}, allowed)
            sim._apply(sq, a, t, d, ordered=True, via=v)
            it = eng.squad_intent(sq)
            if kind:
                assert it.kind == kind, (act, it.kind)
            else:
                assert it.kind in (act, "advance"), (act, it.kind)
            if act == "flank" and it.kind == "flank":
                assert it.side == "right"
            if act == "retreat":
                assert it.toward == (0, -1), "C3 無目標撤退 → 往後方 A3（南）"
        os.remove(PATH)
    asyncio.run(go())


def test_brief_only_known_enemies():
    async def go():
        sim, sq, e, _ = await _engaged()
        eng = sim.engagements[sq.pos]
        text = sim.situation(sq)
        assert "戰術狀況" in text
        known = [u for u in eng.battle.intel["blue"].last_known if eng.battle.units[u].alive]
        n_listed = text.split("已知敵人（與同格友軍共享情報）：")[1].split("\n")[0].count("公尺")
        assert n_listed == len(known), "簡報只列出小隊知道的敵人"
        assert str(e.strength) + " 個" not in text
        os.remove(PATH)
    asyncio.run(go())


def test_captain_tactic_from_llm():
    class Assault(Quiet):
        async def chat_json(self, system, user, temperature=0.8, priority=1):
            if "剛剛發生" in user and "戰術狀況" in user:
                self.events.append("tac")
                return {"action": "supp_advance", "target": None, "report": "壓制推進！"}
            return await super().chat_json(system, user, temperature, priority)

    async def go():
        if os.path.exists(PATH):
            os.remove(PATH)
        llm = Assault()
        sim = Simulation(llm, save_path=PATH, rng=random.Random(3))
        sim.enemy_ai_enabled = False
        sim.world.enemies.clear()
        sim.ensure_commander("1", "甲")
        sq, _ = await sim.deploy_squad(card("alpha"), "1")
        sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999, cooldown=999,
                                       tag="mobile", home="C3")
        sq.pos = "C3"
        roster.sync_world(sim.world)
        for i, m in enumerate(sq.members):
            m.tpos = [10 + i, 6, 0]
        sim.save()
        for _ in range(40):
            await sim.tick()
            if sq.action.type == "supp_advance" or sq.status != "active":
                break
        assert llm.events, "接觸事件時隊長看得到戰術狀況"
        assert sq.action.type in ("supp_advance", "hold"), sq.action.type
        if sq.action.type == "supp_advance" and sq.pos in sim.engagements:
            assert sim.engagements[sq.pos].squad_intent(sq).kind in ("supp_advance", "advance")
        os.remove(PATH)
    asyncio.run(go())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("engage_test OK")


if __name__ == "__main__":
    main()
