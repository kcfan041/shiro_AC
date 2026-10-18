import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import scenario
from engine.sim import ACK_PREFIX, Simulation
from lib.llm import LLMClient

PATH = "./data/dialogue_test.json"
NON_ORDERS = ["發動古代魔法", "發動不存在的攻擊", "幹幹叫", "新命令記住145876932", "回報"]
issues: list[str] = []


def check(cond: bool, msg: str):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        issues.append(msg)


def show(sim: Simulation) -> list[dict]:
    items = [x for x in sim.drain() if x["type"] in ("report", "system")]
    for item in items:
        print(f"    [{item.get('name', '系統')}] {item['text']}")
    return items


async def main():
    load_dotenv()
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        card = json.load(f)
    if os.path.exists(PATH):
        os.remove(PATH)
    llm = LLMClient()
    sim = Simulation(llm, save_path=PATH, rng=random.Random(5))
    sim.world.enemies.clear()
    sim.ensure_commander("1", "測試")
    await sim.deploy_squad(card, "1")
    sq = sim.world.squads["alpha"]

    print("== 尋路 ==")
    sq.known_enemies["B2"] = {"est": 4, "tick": 0}
    path = scenario.find_path("A1", "C2", set(sq.known_enemies))
    check(path is not None and "B2" not in path, f"避開已知敵情 B2：{path}")
    check(scenario.find_path("A1", "C2", set()) is not None, "無敵情時可直達")
    sq.known_enemies.clear()

    print("== 非命令 / 無法執行 ==")
    for text in NON_ORDERS:
        before = (sq.action.type, sq.action.target)
        print(f"  指揮官：{text}")
        r = await sim.issue_order("alpha", text)
        show(sim)
        print(f"    kind={r['kind']} conf={r['confidence']} action={r['action']} mapped={r['mapped']}")
        if text != "回報":
            check((sq.action.type, sq.action.target) == before, f"「{text}」未改變行動")
    exp = [e for e in sq.experiences if e["kind"] == "message"]
    magic = next(e for e in exp if "古代魔法" in e["text"])
    check(magic["mapped"] is None, "古代魔法 mapped=None")
    check(bool(magic["interp"].get("thought")), f"有內心解讀：{magic['interp'].get('thought')}")

    print("== 多格移動 ==")
    print("  指揮官：由 A1 經 A2、B2 前往 C2，沿途警戒")
    r = await sim.issue_order("alpha", "由 A1 經 A2、B2 前往 C2，沿途警戒，不主動接戰")
    show(sim)
    check(sq.action.type == "move" and sq.action.target == "C2", f"目標 C2，路線 {sq.action.path}")
    check(sq.action.path == ["A2", "B2", "C2"], "遵循指定途經點 A2、B2")
    reports = []
    for _ in range(12):
        await sim.tick()
        reports += show(sim)
        if sq.action.type != "move":
            break
    check(sq.pos == "C2", f"抵達 C2（目前 {sq.pos}）")
    check(len(reports) <= 2, f"行進中發言次數 {len(reports)}（<=2）")
    for item in reports:
        check(not ACK_PREFIX.match(item["text"]), f"事件回報非「收到」開頭：{item['text'][:20]}")

    print("== 模糊命令 ==")
    print("  指揮官：去騷擾一下東邊")
    r = await sim.issue_order("alpha", "去騷擾一下東邊")
    show(sim)
    print(f"    kind={r['kind']} conf={r['confidence']} action={r['action']} target={r['target']}")
    last = sq.experiences[-1]["interp"]
    print(f"    理解：{last.get('understanding')}")

    print("\n== GM 視角經歷 ==")
    print(sim.experience_text(sq, n=12, show_interp=True))
    await llm.close()
    os.remove(PATH)
    print(f"\n{'全部通過' if not issues else '問題：' + '；'.join(issues)}")


if __name__ == "__main__":
    asyncio.run(main())
