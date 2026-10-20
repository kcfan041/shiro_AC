"""應變準則（SOP）：依性格推、反思寫入、只當備援、服從度限制、敵方準則、簡易隊長。不需要 LLM。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import sop
from engine.models import Action
from engine.tactical import runner, scenarios


def test_derive_follows_traits():
    bold = sop.derive({"aggr": 0.8, "caut": -0.3})
    shy = sop.derive({"aggr": -0.3, "caut": 0.8, "bond": 0.5})
    assert bold["stalled"] == "assault" and shy["stalled"] == "supp_advance"
    assert shy["downed"] == "retreat" and shy["outnumbered"] == "retreat"
    assert set(bold.values()) <= sop.ACTIONS


def test_normalize_and_pick_obedience():
    assert sop.normalize({"stalled": "Flank", "downed": "dance"}) == {"stalled": "flank"}
    table = {"stalled": "assault", "outnumbered": "retreat"}
    assert sop.pick(table, "stalled") == "assault"
    assert sop.pick(table, "stalled", ordered_action="hold") is None, "明確命令下不能改成攻擊"
    assert sop.pick(table, "outnumbered", ordered_action="hold") == "retreat", "保命動作可以"


def test_fallback_when_llm_fails():
    from scripts.engage_test import _engaged, PATH

    async def go():
        sim, sq, e, llm = await _engaged()

        async def dead(*a, **k):
            return None
        sim.llm.chat_json = dead
        sp = sq.speaker_member()
        sp.sop = {"stalled": "flank"}
        sq.action = Action(type="hold")
        sim.tac_events = {sq.id: [("request", "無法推進：所有路線都暴露於已知敵人射界，已就地固守，請求新指示")]}
        await sim._tick_squad(sq)
        assert sq.action.type == "flank", "LLM 失敗 → 依準則改戰術"
        assert any(x["kind"] == "sop" for x in sq.experiences)
        sq.action = Action(type="hold", ordered=True)
        sim.tac_events = {sq.id: [("request", "無法推進，請求新指示")]}
        await sim._tick_squad(sq)
        assert sq.action.type == "hold", "明確命令固守 → 準則不改成包抄"
        os.remove(PATH)
    asyncio.run(go())


def test_llm_ok_no_sop_override():
    from scripts.engage_test import _engaged, PATH

    async def go():
        sim, sq, e, llm = await _engaged()
        sq.speaker_member().sop = {"stalled": "assault"}
        sq.action = Action(type="hold")
        sim.tac_events = {sq.id: [("request", "無法推進，請求新指示")]}
        await sim._tick_squad(sq)
        assert sq.action.type == "hold", "LLM 正常回應（keep）時準則不介入"
        os.remove(PATH)
    asyncio.run(go())


def test_reflect_writes_sop():
    from scripts.handover_test import fresh, PATH

    async def go():
        sim, sq, llm = await fresh()
        orig = llm.chat_json

        async def wrap(system, user, temperature=0.8, priority=1):
            if "獨處整理思緒" in system:
                return {"beliefs": [{"text": "x", "origin": "experience"}], "tendencies": {}, "summary": "s",
                        "sop": {"stalled": "breach", "downed": "retreat", "bogus": "x"}}
            return await orig(system, user, temperature, priority)
        llm.chat_json = wrap
        sq.reflect_reasons = ["t"]
        await sim._reflect(sq)
        sp = sq.speaker_member()
        assert sp.sop == {"stalled": "breach", "downed": "retreat"}
        assert sim.squad_sop(sq)["stalled"] == "breach"
        os.remove(PATH)
    asyncio.run(go())


def test_simple_captain_breaks_stalemate():
    """簡易隊長（應變準則）在僵局時會改戰術：比較「有發生戰術變更」的場數，而不是勝負（勝負只有 6 場，太敏感）。"""
    b = scenarios.LARGE["district"]
    n = 6
    avg = sop.derive({})
    changed = 0
    draws_plain = draws_capt = 0
    for s in range(n):
        r0 = runner.run(b, seed=s, max_ticks=60)
        r1 = runner.run(b, seed=s, max_ticks=60, sop={"blue": avg, "red": avg})
        draws_plain += r0["winner"] == "draw"
        draws_capt += r1["winner"] == "draw"
        changed += r1.get("sop_changes", 0) > 0
    print(f"    district 平手：沒有隊長 {draws_plain}/{n}，簡易隊長 {draws_capt}/{n}；隊長改戰術 {changed}/{n} 場")
    assert changed >= n // 2, "僵局時隊長會依準則改戰術"
    assert draws_capt <= draws_plain + 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print(f"OK {k}")
    print("sop_test OK")


if __name__ == "__main__":
    main()
