"""網頁伺服器（不開瀏覽器）：註冊登入、就任、資料隔離、WebSocket 只推自己、下令、自主等級、支援、迷霧、
GM 權限、回放權限。不需要 LLM。"""
import asyncio
import json
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer

from engine.models import Enemy
from engine.sim import Simulation
from lib.llm import LLMClient
from web.accounts import Accounts
from web.app import DEFAULTS, Game, make_app

ROOT = "./data/web_test"


class Fake(LLMClient):
    def __init__(self):
        super().__init__(base_url="x", model="x")

    async def chat_json(self, system, user, temperature=0.8, priority=1):
        if "副官" in system:
            return {"summary": "測試", "suggestions": [{"label": "固守", "squad": "alpha", "order": "原地固守"}]}
        if "個性與說話方式" in system:
            return {"personality": "x", "speech": "x", "background": "x", "sample_lines": [], "beliefs": [],
                    "callsign": "Kilo"}
        if "指揮官剛剛在無線電說" in user:
            return {"kind": "order", "understanding": "固守", "confidence": "high", "action": "hold",
                    "report": "收到，固守。"}
        return {"action": "keep", "report": None}


def make_game():
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT)
    cfg = dict(DEFAULTS, save_path=f"{ROOT}/world.json", replays=f"{ROOT}/replays", accounts=f"{ROOT}/acc.json",
               gm=["boss"], autostart_tick=False, adjutant_min_interval=0)
    sim = Simulation(Fake(), save_path=cfg["save_path"], rng=random.Random(4))
    sim.enemy_ai_enabled = False
    sim.world.enemies.clear()
    return Game(cfg, None, sim=sim, accounts=Accounts(cfg["accounts"], cfg["gm"]))


async def login(client, name, pw="pass1"):
    r = await client.post("/api/register", json={"name": name, "password": pw})
    d = await r.json()
    assert d["ok"], d
    client.session.cookie_jar.clear()
    return d["token"]


async def main():
    game = make_game()
    client = TestClient(TestServer(make_app(game)))
    await client.start_server()
    try:
        h = lambda tok: {"X-Session": tok}
        ta = await login(client, "甲")
        tb = await login(client, "乙")
        tg = await login(client, "boss")
        r = await client.post("/api/register", json={"name": "甲", "password": "xxxx"})
        assert r.status == 400, "暱稱重複"
        r = await client.post("/api/login", json={"name": "甲", "password": "wrong"})
        assert r.status == 401
        client.session.cookie_jar.clear()
        r = await client.get("/api/state")
        assert r.status == 401, "沒登入"

        for tok in (ta, tb):
            r = await client.post("/api/enlist", headers=h(tok))
            assert (await r.json())["ok"]
        r = await client.post("/api/enlist", headers=h(ta))
        assert r.status == 400, "不能重複就任"
        uid_a = game.accounts.user_of(ta)
        uid_b = game.accounts.user_of(tb)
        sa = game.sim.world.squads_of(uid_a)[0]
        sb = game.sim.world.squads_of(uid_b)[0]
        assert sa.callsign != sb.callsign, "起始角色卡錯開"

        st = (await (await client.get("/api/state", headers=h(ta))).json())["state"]
        assert [s["id"] for s in st["squads"]] == [sa.id] and st["me"]["role"] == "commander"
        r = await client.get(f"/api/state?watch={uid_b}", headers=h(ta))
        assert r.status == 403, "指揮官不能看別人"
        r = await client.get(f"/api/state?watch={uid_a}", headers=h(tg))
        assert r.status == 200, "GM 可以看"

        r = await client.post("/api/order", headers=h(ta), json={"squad": sb.id, "text": "撤退"})
        assert r.status == 403, "不能指揮別人的小隊"

        wa = await client.ws_connect(f"/ws?token={ta}")
        wb = await client.ws_connect(f"/ws?token={tb}")
        for ws in (wa, wb):
            hello = json.loads((await ws.receive()).data)
            assert hello["type"] == "hello"
            st0 = json.loads((await ws.receive()).data)
            assert st0["type"] == "state"
        r = await client.post("/api/order", headers=h(ta), json={"squad": sa.id, "text": "原地固守"})
        d = await r.json()
        assert d["ok"] and d["result"]["receipt"]["label"]
        got_a = []
        while True:
            try:
                m = await asyncio.wait_for(wa.receive(), 0.5)
            except asyncio.TimeoutError:
                break
            got_a.append(json.loads(m.data))
        radio = [x for m in got_a if m["type"] == "radio" for x in m["items"]]
        assert any(x["type"] == "receipt" for x in radio) and any(x["type"] == "report" for x in radio)
        got_b = []
        while True:
            try:
                m = await asyncio.wait_for(wb.receive(), 0.3)
            except asyncio.TimeoutError:
                break
            got_b.append(json.loads(m.data))
        assert not any(m["type"] == "radio" for m in got_b), "乙收不到甲的無線電"
        assert all(s["id"] == sb.id for m in got_b if m["type"] == "state" for s in m["state"]["squads"])

        r = await client.post("/api/autonomy", headers=h(ta), json={"squad": sa.id, "level": "standby"})
        assert (await r.json())["ok"] and sa.autonomy == "standby"

        sa.pos = "C3"
        game.sim.world.enemies["x"] = Enemy(id="x", pos="C3", strength=3, route=["C3"], move_every=999,
                                            cooldown=999, tag="garrison", home="C3")
        from engine import roster
        roster.sync_world(game.sim.world)
        for _ in range(3):
            await game.tick_once()
        tac = []
        while True:
            try:
                m = await asyncio.wait_for(wa.receive(), 0.3)
            except asyncio.TimeoutError:
                break
            j = json.loads(m.data)
            if j["type"] == "tactical":
                tac.append(j)
        assert tac and "C3" in tac[-1]["frames"], "交戰影格推給甲"
        eng = game.sim.engagements["C3"]
        spotted = set(eng.battle.intel["blue"].spotted)
        fr = tac[-1]["frames"]["C3"]
        assert {u["id"] for u in fr["units"] if u["side"] == "enemy"} <= spotted, "迷霧"
        r = await client.get("/api/tactical/C3", headers=h(tb))
        assert r.status == 403, "乙沒有小隊在 C3"
        r = await client.get("/api/tactical/C3?gm=1", headers=h(tg))
        d = await r.json()
        assert d["ok"] and len([u for u in d["tactical"]["frame"]["units"] if u["kind"] == "e"]) >= 1

        mine = next(u for uid, u in eng.battle.units.items() if eng.owner[uid][1] == sa.id)
        r = await client.post("/api/support", headers=h(ta), json={"kind": "smoke", "cell": "C3",
                                                                 "point": [mine.pos[0], mine.pos[1] + 3]})
        assert (await r.json())["ok"]
        r = await client.post("/api/support", headers=h(tb), json={"kind": "smoke", "cell": "C3", "point": [5, 5]})
        assert r.status == 400, "乙沒有小隊在 C3 不能叫煙幕"

        r = await client.post("/api/gm/tick", headers=h(ta), json={"count": 1})
        assert r.status == 403, "一般帳號不能用 GM"
        r = await client.post("/api/gm/tick", headers=h(tg), json={"count": 2})
        assert (await r.json())["ok"]
        r = await client.post("/api/gm/status", headers=h(tg))
        assert (await r.json())["status"]["squads"]

        game.sim.world.enemies["x"].alive = False
        for u in eng.battle.units.values():
            if u.side == "red":
                u.down = True
        for _ in range(3):
            await game.tick_once()
        lst = game.sim.replays.list_for(uid_a)
        assert lst, "交戰結束有回放"
        r = await client.get(f"/api/replay/{lst[0]['id']}", headers=h(ta))
        d = await r.json()
        assert d["ok"] and d["frames"] and d["replay"]["terrain"]
        r = await client.get(f"/api/replay/{lst[0]['id']}", headers=h(tb))
        assert r.status == 404, "乙看不到甲的回放"

        while True:
            try:
                await asyncio.wait_for(wa.receive(), 0.2)
            except asyncio.TimeoutError:
                break
        c_a = game.sim.world.commanders[uid_a]
        main = next(m for m in c_a.missions if m["line"] == "main")
        sa.pos = main["cell"]
        sa.action = __import__("engine.models", fromlist=["Action"]).Action(type="hold")
        game.sim.world.enemies.clear()
        for _ in range(2):
            await game.tick_once()
        msgs = []
        while True:
            try:
                m = await asyncio.wait_for(wa.receive(), 0.5)
            except asyncio.TimeoutError:
                break
            msgs.append(json.loads(m.data))
        done = [x for m in msgs if m["type"] == "radio" for x in m["items"]
                if x["type"] == "mission" and x["event"] == "done"]
        assert done and done[0]["mission"]["title"] == main["title"], "開著網頁也收到任務完成"
        last = [m for m in msgs if m["type"] == "state"][-1]["state"]
        assert any(h["title"] == main["title"] and h["success"] for h in last["missions"]["history"]), "畫面任務已更新"
        assert all(a["title"] != main["title"] for a in last["missions"]["active"])

        r = await client.get("/")
        assert r.status == 200
        await wa.close()
        await wb.close()
    finally:
        await client.close()
        shutil.rmtree(ROOT, ignore_errors=True)
    print("web_test OK")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
