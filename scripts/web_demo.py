"""網頁版示範伺服器（不需要 LLM）：兩位指揮官「甲」「乙」（密碼 pass1）與 GM「boss」，甲的小隊在 C3 接敵。
用來看畫面與動畫，或給無頭瀏覽器截圖。

用法：python scripts/web_demo.py [port，預設 8090] [--llm 用真的 LLM]
登入後網址加 ?token=... 也可以（印在畫面上）。
"""
import asyncio
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from dotenv import load_dotenv

from engine import roster
from engine.models import Enemy
from engine.sim import Simulation
from lib.llm import LLMClient
from web.accounts import Accounts
from web.app import DEFAULTS, Game, load_card, make_app

ROOT = "./data/web_demo"


class Quiet(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "副官" in system:
            return {"summary": "示範模式（沒有 LLM）。", "suggestions": []}
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Kilo"}
        if "指揮官剛剛在無線電說" in user:
            import re
            order = user.split("指揮官剛剛在無線電說")[1].split("\n")[0]
            cells = re.findall(r"\b([A-E][1-5])\b", order)
            act, target, via = "hold", None, []
            for k, a in (("包抄", "flank"), ("推進", "supp_advance"), ("突擊", "assault"), ("伏擊", "hide"),
                         ("破門", "breach"), ("隱蔽", "hide"), ("偵察", "scout"), ("撤退", "retreat"), ("移動", "move"),
                         ("去", "move")):
                if k in order:
                    act = a
                    break
            if act in ("move", "scout", "retreat") and cells:
                target = cells[0] if act != "move" else cells[0]
                if act == "move":
                    via = [c for c in cells[1:]]
            elif act == "flank":
                target = "right" if "右" in order else "left"
            return {"kind": "order", "understanding": f"示範：{act} {target or ''}", "confidence": "high",
                    "action": act, "target": target, "via": via, "report": f"收到（示範：{act} {target or ''}）"}
        return {"action": "keep", "report": None}


async def build(use_llm: bool):
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT)
    cfg = dict(DEFAULTS, save_path=f"{ROOT}/world.json", replays=f"{ROOT}/replays", accounts=f"{ROOT}/acc.json",
               gm=["boss"], adjutant_min_interval=30)
    llm = LLMClient() if use_llm else Quiet()
    sim = Simulation(llm, save_path=cfg["save_path"], rng=random.Random(5))
    acc = Accounts(cfg["accounts"], cfg["gm"])
    game = Game(cfg, llm, sim=sim, accounts=acc)
    tokens = {}
    for name in ("甲", "乙", "boss"):
        ok, uid = acc.register(name, "pass1")
        tokens[name] = (uid, acc.login(name, "pass1"))
    for name in ("甲", "乙"):
        await game.enlist(tokens[name][0])
    a = sim.world.squads_of(tokens["甲"][0])[0]
    if sim.zone_mode:
        from engine.models import Action
        a.pos = "B3"
        for m in a.members:
            m.gpos = None
        a.action = Action(type="move", target="C3", path=["C3"], duration=3, ordered=True)
    else:
        a.pos = "C3"
    sim.world.enemies["demo"] = Enemy(id="demo", pos="C3", strength=5, route=["C3"], move_every=999, cooldown=999,
                                      tag="garrison", home="C3")
    if "--paused" in sys.argv:
        sim.world.running = False
    roster.sync_world(sim.world)
    sim.save()
    return game, tokens


def main():
    load_dotenv()
    port = int(next((x for x in sys.argv[1:] if x.isdigit()), 8090))
    use_llm = "--llm" in sys.argv
    exit_after = next((int(x.split("=")[1]) for x in sys.argv[1:] if x.startswith("--exit-after=")), 0)
    game, tokens = asyncio.run(build(use_llm))
    for name, (uid, tok) in tokens.items():
        print(f"{name}: http://localhost:{port}/?token={tok}", flush=True)
    app = make_app(game)
    if exit_after:
        async def _auto_exit(app):
            async def stop():
                await asyncio.sleep(exit_after)
                os._exit(0)
            asyncio.create_task(stop())
        app.on_startup.append(_auto_exit)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
