"""需要 LLM：兩位指揮官的小隊（都是 alpha 角色卡）同格對上守軍。
檢查：回報各自分流到自己的小隊頻道、隊長知道同格友軍、兩人同時下令不互相干擾、副官各自只看到自己的部隊、
每個 tick 的耗時（背景 tick 是 10 秒，兩隊同時接敵時不能拖太久）。"""
import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import roster
from engine.engagement import label_of
from engine.models import Enemy
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/multi_play_test.json"
CELL = "C3"


def card(name):
    with open(f"./setting example/squads/{name}.json", encoding="utf8") as f:
        return json.load(f)


def show(sim, owners):
    bad = 0
    for item in sim.drain():
        if "text" not in item:
            continue
        sq = sim.world.squads.get(item.get("squad", ""))
        who = owners.get(sq.commander, "?") if sq else "?"
        print(f"  [T{sim.world.tick} → {who}的頻道｜{label_of(sim.world, sq) if sq else '-'}"
              f"｜{item.get('name', '系統')}] {item['text']}")
        if sq is None:
            bad += 1
    return bad


async def tick(sim, times):
    t0 = time.monotonic()
    await sim.tick()
    times.append(time.monotonic() - t0)


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    sim = Simulation(LLMClient(), save_path=PATH, rng=random.Random(11))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    owners = {"100": "甲", "200": "乙"}
    for cid, name in owners.items():
        sim.ensure_commander(cid, name)
    a, _ = await sim.deploy_squad(card("alpha"), "100")
    b, _ = await sim.deploy_squad(card("alpha"), "200")
    sim.world.enemies["x"] = Enemy(id="x", pos=CELL, strength=4, route=[CELL], move_every=999, cooldown=999,
                                   tag="garrison", home=CELL)
    a.pos = b.pos = CELL
    roster.sync_world(sim.world)
    sim.save()
    times: list[float] = []
    bad = 0
    for _ in range(3):
        await tick(sim, times)
        bad += show(sim, owners)
    eng = sim.engagements.get(CELL)
    assert eng is not None, "兩隊同格應該開戰"
    sa = sim.situation(a)
    print("\n甲的隊長看到的同格友軍：", [ln for ln in sa.splitlines() if "同格友軍" in ln])
    print("\n== 甲、乙同時下令 ==")
    ra, rb = await asyncio.gather(sim.issue_order(a.id, "從左邊包抄他們，乙那隊在正面"),
                                  sim.issue_order(b.id, "原地壓制，掩護友軍從左邊上"))
    print(f"  甲 → {ra and ra['action']} {ra and ra['target']}｜{ra and ra['outcome']}")
    print(f"  乙 → {rb and rb['action']} {rb and rb['target']}｜{rb and rb['outcome']}")
    bad += show(sim, owners)
    for _ in range(10):
        await tick(sim, times)
        bad += show(sim, owners)
        if not sim.engagements.get(CELL):
            break
    for cid in owners:
        await sim.refresh_adjutant(cid)
        c = sim.world.commanders[cid]
        mine = set(c.intel["squads"])
        assert all(sim.world.squads[s].commander == cid for s in mine), "副官只看自己的部隊"
        print(f"\n副官（{owners[cid]}）：{(c.adjutant or {}).get('summary', '')[:200]}")
        print(f"  建議：{[s.get('label') for s in (c.adjutant or {}).get('suggestions', [])]}")
        for s in (c.adjutant or {}).get("suggestions", []):
            assert sim.world.squads[s["squad"]].commander == cid, "副官建議只指揮自己的小隊"
    print(f"\n甲 {len(a.alive_members())}/{len(a.members)}｜乙 {len(b.alive_members())}/{len(b.members)}｜"
          f"敵 {sim.world.enemies['x'].strength if sim.world.enemies['x'].alive else 0}")
    print(f"tick 耗時：平均 {sum(times) / len(times):.1f}s，最長 {max(times):.1f}s（{len(times)} 次）")
    assert not bad, "有訊息找不到小隊"
    await sim.llm.close()
    os.remove(PATH)
    print("multi_play_test OK")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
