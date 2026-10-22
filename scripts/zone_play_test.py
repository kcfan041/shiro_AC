"""連續地圖實測（需要 LLM）：網頁版設定（zone 模式、作戰 tick 5 秒、戰術步 2.5 秒）下，小隊打字下令前往 D3，
途中在 C3 接敵；交戰中指揮官下令「邊打邊往 C2 移動」→ 隊長應選 move（連續地圖交戰中可移動），小隊實際往 C2 走；
再下令包抄 → flank。印出隊長回報與檢查結果。"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from engine import roster, scenario, zone as zmod
from engine.models import Enemy
from engine.sim import Simulation
from lib.llm import LLMClient

PATH = "./data/zone_play_test.json"
problems = []


def check(ok: bool, label: str):
    print(f"  {'✓' if ok else '✗'} {label}")
    if not ok:
        problems.append(label)


def show(sim, sq):
    for item in sim.drain():
        if "text" in item:
            print(f"  [T{sim.world.tick} {item.get('name', '系統')}] {item['text']}")
    print(f"    ── 行動：{sim._action_text(sq)}｜{sq.pos}｜交戰 {'是' if sim.in_engagement(sq) else '否'}"
          f"｜可戰 {len(sq.alive_members())}/{len(sq.members)}｜倒地 {sum(1 for m in sq.members if m.down)}")


def order_line(r):
    print(f"  → kind={r['kind']} conf={r['confidence']} action={r['action']} target={r['target']}"
          f"｜{r['obedience']}｜{r['outcome']}｜回執：{r['receipt']['label']}")


async def main():
    load_dotenv()
    if os.path.exists(PATH):
        os.remove(PATH)
    with open("./setting example/squads/alpha.json", encoding="utf8") as f:
        card = json.load(f)
    sim = Simulation(LLMClient(), save_path=PATH, rng=random.Random(7), tick_seconds=5)
    sim.zone_mode = True
    sim.tac_steps = 2
    sim.react_gap = 2
    sim.enemy_ai_enabled = False
    sim._ensure_missions = lambda c: None
    sim._wanderers = lambda: None
    sim._need_reinforce = lambda c: False
    sim.world.enemies.clear()
    sim.ensure_commander("1", "測試")
    sq, _ = await sim.deploy_squad(card, "1")
    sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999, cooldown=999,
                                   tag="garrison", home="C3")
    roster.sync_world(sim.world)
    sim.save()
    await sim.tick()
    show(sim, sq)

    print("\n== 行軍 ==\n指揮官：前往 D3 中繼塔，沿途警戒")
    r = await sim.issue_order(sq.id, "前往 D3 中繼塔，沿途警戒")
    order_line(r)
    check(r["action"] == "move" and r["target"] == "D3", "打字下令前往 D3 → move D3")
    show(sim, sq)
    for _ in range(40):
        await sim.tick()
        show(sim, sq)
        if sim.in_engagement(sq):
            break
    check(sim.in_engagement(sq), "途中接敵")
    if not sim.in_engagement(sq):
        print(f"\n問題：{'、'.join(problems)}")
        await sim.llm.close() if hasattr(sim.llm, "close") else None
        return
    for _ in range(2):
        await sim.tick()
        show(sim, sq)
    allowed, allowed_text = sim._allowed(sq)
    check("move" in allowed, "連續地圖交戰中，可選行動含 move")
    st = sim.situation(sq)
    if "戰術狀況" in st:
        print("\n" + st.split("戰術狀況")[1].split("近期經歷")[0])

    print("== 交戰中下令移動 ==\n指揮官：別戀戰，邊打邊往 C2 移動")
    start = zmod.squad_center(sq)
    r = await sim.issue_order(sq.id, "別戀戰，邊打邊往 C2 移動")
    order_line(r)
    check(r["action"] == "move" and r["target"] == "C2", "交戰中下令移動 → 隊長選 move C2（不是 keep／hold）")
    show(sim, sq)
    moved_hot = False
    for _ in range(16):
        await sim.tick()
        show(sim, sq)
        c = zmod.squad_center(sq)
        if sim.in_engagement(sq) and sq.action.type == "move" and start and c and \
                max(abs(c[0] - start[0]), abs(c[1] - start[1])) >= 6:
            moved_hot = True
        if sq.pos == "C2" or sq.status != "active":
            break
    if r["action"] == "move":
        check(moved_hot or sq.pos == "C2", "邊打邊走：交戰中仍往 C2 前進")

    if sim.in_engagement(sq) and sq.status == "active":
        print("\n== 交戰中改戰術 ==\n指揮官：從左邊包抄他們")
        r = await sim.issue_order(sq.id, "從左邊包抄他們")
        order_line(r)
        check(r["action"] in ("flank", "supp_advance", "advance"), "交戰中下令包抄 → 戰術選單的行動")
        for _ in range(6):
            await sim.tick()
            show(sim, sq)
    print(f"\n小隊 {sq.pos}｜可戰 {len(sq.alive_members())}/{len(sq.members)}｜"
          f"敵 {sum(1 for e in sim.world.enemies.values() for sd in e.soldiers if e.alive) if sim.world.enemies else 0}"
          f"｜{scenario.cell_desc(sq.pos)}")
    print(f"\n{'問題：' + '、'.join(problems) if problems else '全部通過'}")
    if os.path.exists(PATH):
        os.remove(PATH)
    await sim.llm.close() if hasattr(sim.llm, "close") else None


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
