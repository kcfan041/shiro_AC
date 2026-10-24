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
               gm=["boss"], autostart_tick=False, adjutant_min_interval=0, zone_mode=False)
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
        assert d["ok"] and d.get("pending"), "打字下令馬上回應，不等 LLM"
        await asyncio.sleep(0.3)
        got_a = []
        while True:
            try:
                m = await asyncio.wait_for(wa.receive(), 0.5)
            except asyncio.TimeoutError:
                break
            got_a.append(json.loads(m.data))
        radio = [x for m in got_a if m["type"] == "radio" for x in m["items"]]
        kinds = [x["type"] for x in radio]
        assert "order" in kinds and "typing" in kinds and "receipt" in kinds and "report" in kinds, kinds
        assert kinds.index("typing") < kinds.index("receipt"), "先「回覆中…」再回執"

        r = await client.post("/api/quick", headers=h(ta), json={"squad": sa.id, "action": "hide"})
        d = await r.json()
        assert d["ok"] and d["result"]["receipt"]["quick"], d
        if d["result"]["obedience"] != "refuse":
            assert sa.action.type == "hide", "快捷命令當場生效"
        r = await client.post("/api/quick", headers=h(ta), json={"squad": sa.id, "action": "scout", "target": "E5"})
        assert r.status == 200 and not (await r.json())["ok"], "偵察要相鄰格"
        await game.sim.settle()
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
        assert d["replay"]["zone"] is False and "bbox" in d["replay"], "回放帶 zone／bbox（連續地圖回放靠它開全圖播放）"
        assert d["frames"][0].get("ts1") is not None, "影格帶戰鬥時間（前端依時間播放）"
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
    await zone_mode_checks()
    await idle_pause_checks()
    print("web_test OK")


async def zone_mode_checks():
    """連續地圖：/api/world_terrain、WebSocket world 推播只含自己看得到的區域、全圖座標下令支援。"""
    game = make_game()
    game.sim.zone_mode = True
    client = TestClient(TestServer(make_app(game)))
    await client.start_server()
    try:
        h = lambda tok: {"X-Session": tok}
        ta = await login(client, "甲")
        tb = await login(client, "乙")
        for tok in (ta, tb):
            assert (await (await client.post("/api/enlist", headers=h(tok))).json())["ok"]
        uid_a, uid_b = game.accounts.user_of(ta), game.accounts.user_of(tb)
        sa = game.sim.world.squads_of(uid_a)[0]
        sb = game.sim.world.squads_of(uid_b)[0]
        sb.pos = "E5"
        for m in sb.members:
            m.gpos = None
        d = await (await client.get("/api/world_terrain", headers=h(ta))).json()
        assert d["ok"] and d["terrain"]["w"] == 125 and len(d["terrain"]["terrain"]) == 125
        wa = await client.ws_connect(f"/ws?token={ta}")
        for _ in range(2):
            await wa.receive()
        await game.tick_once()
        got = []
        while True:
            try:
                m = await asyncio.wait_for(wa.receive(), 0.4)
            except asyncio.TimeoutError:
                break
            got.append(json.loads(m.data))
        worlds = [m["world"] for m in got if m["type"] == "world"]
        assert worlds, "zone 模式推 world"
        wv = worlds[-1]
        cells = {c for z in wv["zones"] for c in z["cells"]}
        assert sa.pos in cells and "E5" not in cells, "只看得到自己的活動區域"
        ids = {u["id"] for f in wv["frames"] for u in f["units"]}
        assert any(sa.id in i for i in ids) and not any(sb.id in i for i in ids)
        from engine.tactical import worldmap
        ox, oy = worldmap.cell_origin(sa.pos)
        r = await client.post("/api/support", headers=h(ta), json={"kind": "smoke", "global": [ox + 12, oy + 14]})
        assert (await r.json())["ok"], "全圖座標叫支援"
        assert game.sim.supports[-1]["cell"] == sa.pos and game.sim.supports[-1]["x"] == 12
        await wa.close()
    finally:
        await client.close()
        shutil.rmtree(ROOT, ignore_errors=True)


async def idle_pause_checks():
    """沒有玩家在線：最後一位指揮官／GM 離線超過 idle_pause_s 秒才自動暫停；只有觀戰者不算；有人連上就恢復；
    GM 關掉背景 tick 時不論有沒有人都不跑。"""
    game = make_game()
    client = TestClient(TestServer(make_app(game)))
    await client.start_server()
    try:
        h = lambda tok: {"X-Session": tok}
        ta = await login(client, "甲")
        tv = await login(client, "路人")
        tg = await login(client, "boss")
        assert (await (await client.post("/api/enlist", headers=h(ta))).json())["ok"]
        assert game.sim.world.running
        t = game._last_online
        assert game.should_step(t + 59), "寬限期內照跑"
        assert not game.should_step(t + 60) and game.idle_paused, "沒人在線 60 秒 → 暫停"
        wv = await client.ws_connect(f"/ws?token={tv}")
        await wv.receive()
        assert not game.should_step(t + 70), "只有觀戰者在線仍暫停"
        uid_a = game.accounts.user_of(ta)
        st = (await (await client.get(f"/api/state?watch={uid_a}", headers=h(tv))).json())["state"]
        assert st["running"] and st["idle_paused"], "觀戰畫面看得出是沒人在線而暫停"
        wa = await client.ws_connect(f"/ws?token={ta}")
        await wa.receive()
        game._beat_start = 123.0
        assert game.should_step(t + 80) and not game.idle_paused, "指揮官連上 → 恢復"
        assert game._beat_start is None, "恢復時不把暫停算進拍間隔"
        await wa.close()
        await asyncio.sleep(0.05)
        assert game.should_step(t + 139), "離線後重新起算寬限期"
        assert not game.should_step(t + 140)
        wg = await client.ws_connect(f"/ws?token={tg}")
        await wg.receive()
        assert game.should_step(t + 150), "GM 在線也算有玩家"
        game.sim.world.running = False
        assert not game.should_step(t + 151), "GM 關掉背景 tick 就不跑"
        await wg.close()
        await wv.close()
    finally:
        await client.close()
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
