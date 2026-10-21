"""網頁伺服器（aiohttp）：帳號、REST、WebSocket 推播、背景 tick。

每個瀏覽器一條 WebSocket；伺服器只推給該指揮官看得到的資料：
- state：整份指揮官畫面（每 tick、或有變動時）
- radio：無線電訊息（下令、回報、系統、回執、自主、提議、支援）
- tactical：自己小隊所在交戰格的影格（依情報過濾）
GM 可看任一格的真相。觀戰者看所有指揮官的畫面，但不能下令。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from aiohttp import WSMsgType, web

from engine import scenario, views
from engine.replay import ReplayStore, for_viewer
from engine.sim import Simulation
from web.accounts import Accounts

log = logging.getLogger(__name__)
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEFAULTS = {"host": "0.0.0.0", "port": 8080, "tick_seconds": 5, "tac_steps": 2, "react_gap": 2,
            "llm_max_per_minute": 60, "gm": [],
            "starter_cards": ["alpha", "bravo", "charlie", "delta", "echo"], "max_commanders": 4,
            "adjutant_min_interval": 30, "save_path": "./data/web_world.json", "replays": "./data/replays",
            "accounts": "./data/accounts.json", "zone_mode": True, "allies": True}
COOKIE = "ac_session"
SLOW_STEP = 1.0          # 一拍（模擬＋推播）超過這麼多秒 → 寫 log（含各部分耗時）


def load_config(path: str = "./setting/web.json") -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf8") as f:
            cfg.update(json.load(f))
    return cfg


def load_card(card_id: str) -> dict | None:
    for base in ("./setting/squads", "./setting example/squads"):
        p = os.path.join(base, f"{card_id}.json")
        if os.path.exists(p):
            with open(p, encoding="utf8") as f:
                return json.load(f)
    return None


class Game:
    def __init__(self, cfg: dict, llm, sim: Simulation | None = None, accounts: Accounts | None = None):
        self.cfg = cfg
        self.sim = sim or Simulation(llm, save_path=cfg["save_path"], tick_seconds=cfg["tick_seconds"])
        if self.sim.replays is None:
            self.sim.replays = ReplayStore(cfg["replays"])
        self.sim.tick_seconds = cfg["tick_seconds"]
        self.sim.tac_steps = cfg["tac_steps"]
        self.sim.react_gap = cfg["react_gap"]
        self.sim.background = cfg.get("background", True)
        self.sim.zone_mode = cfg.get("zone_mode", True)
        self.sim.allies_enabled = cfg.get("allies", True) and self.sim.zone_mode
        self.sim.on_outbox = self._kick
        self.accounts = accounts or Accounts(cfg["accounts"], cfg.get("gm"))
        self.clients: dict[web.WebSocketResponse, dict] = {}
        self.ticker: asyncio.Task | None = None
        self.adj_tasks: dict[str, asyncio.Task] = {}
        self.last_adj: dict[str, float] = {}
        self.tick_busy = False
        self._kick_task: asyncio.Task | None = None

    def _kick(self):
        """引擎有訊息要馬上推（下令的「回覆中…」、背景回覆）：排一次推播。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._kick_task is None or self._kick_task.done():
            self._kick_task = loop.create_task(self._kick_push())

    async def _kick_push(self):
        await asyncio.sleep(0)
        try:
            await self.push_outbox()
        except Exception:
            log.exception("kick push failed")

    # ------------------------------------------------------------------ helpers

    def role(self, uid: str | None) -> str:
        if uid is None:
            return "guest"
        if self.accounts.is_gm(uid):
            return "gm"
        if uid in self.sim.world.commanders and self.sim.world.squads_of(uid):
            return "commander"
        return "visitor"

    def own_squad(self, uid: str, sid: str):
        s = self.sim.world.squads.get(sid)
        return s if s is not None and s.commander == uid else None

    def state_for(self, uid: str) -> dict:
        st = views.commander_state(self.sim, uid)
        st["me"] = {"id": uid, "name": self.accounts.name(uid), "role": self.role(uid)}
        st["replays"] = self.sim.replays.list_for(uid, gm=self.accounts.is_gm(uid))[:20]
        for sq in st["squads"]:
            s = self.sim.world.squads.get(sq["id"])
            sq["radio"] = views.radio_log(s) if s else []
        return st

    def commanders_list(self) -> list[dict]:
        w = self.sim.world
        return [{"id": c.id, "name": c.name, "squads": len([s for s in w.squads_of(c.id) if s.status == "active"])}
                for c in w.commanders.values()]

    # ------------------------------------------------------------------ push

    async def send(self, ws, msg: dict):
        try:
            await ws.send_str(json.dumps(msg, ensure_ascii=False))
        except (ConnectionResetError, RuntimeError):
            pass

    def viewing(self, info: dict) -> str | None:
        """這條連線在看哪位指揮官：自己；觀戰／GM 可切換。"""
        return info.get("watch") or info.get("uid")

    async def push_state(self, only: set | None = None):
        for ws, info in list(self.clients.items()):
            cid = self.viewing(info)
            if not cid or (only is not None and cid not in only):
                continue
            try:
                st = self.state_for(cid)
            except Exception:
                log.exception("state_for failed %s", cid)
                continue
            await self.send(ws, {"type": "state", "state": st})

    async def push_outbox(self):
        items = self.sim.drain()
        for card in self.sim.new_cards:
            os.makedirs("./setting/squads", exist_ok=True)
            path = os.path.join("./setting/squads", f"{card['id']}.json")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf8") as f:
                    json.dump(card, f, ensure_ascii=False, indent=4)
        self.sim.new_cards.clear()
        if not items:
            return
        by_cid: dict[str, list] = {}
        for it in items:
            cid = it.get("commander")
            if cid is None and it.get("squad"):
                s = self.sim.world.squads.get(it["squad"])
                cid = s.commander if s else None
            if cid:
                by_cid.setdefault(cid, []).append(_public_item(it, self.sim))
        for ws, info in list(self.clients.items()):
            cid = self.viewing(info)
            if cid in by_cid:
                await self.send(ws, {"type": "radio", "items": by_cid[cid]})

    async def push_tactical(self):
        if self.sim.zone_mode:
            return await self.push_world()
        for ws, info in list(self.clients.items()):
            cid = self.viewing(info)
            if not cid:
                continue
            gm = self.accounts.is_gm(info.get("uid"))
            cells = {s.pos for s in self.sim.world.squads_of(cid) if s.status == "active" and s.linked}
            if gm and info.get("gm_cell"):
                cells.add(info["gm_cell"])
            frames = {}
            for cell in cells:
                eng = self.sim.engagements.get(cell)
                if eng is None or eng.last_frame is None:
                    continue
                full_gm = gm and info.get("gm_cell") == cell
                frames[cell] = for_viewer(eng.last_frame, cid, self.sim.world, gm=full_gm)
            if frames:
                await self.send(ws, {"type": "tactical", "frames": frames})

    async def push_world(self):
        """連續地圖：每拍推一份「我看得到的所有區域」影格（全圖座標，依情報過濾）＋區域外的情報敵情。"""
        for ws, info in list(self.clients.items()):
            cid = self.viewing(info)
            if not cid:
                continue
            gm = self.accounts.is_gm(info.get("uid")) and info.get("gm_truth")
            await self.send(ws, {"type": "world", "world": self.world_for(cid, gm)})

    def world_for(self, cid: str, gm: bool = False) -> dict:
        w = self.sim.world
        view = views.zone_view(self.sim, cid, gm=gm)
        frames = []
        for z in self.sim.zones:
            if z.last_frame is None:
                continue
            if not gm and not any(s.commander == cid and s.linked and s.status == "active" and s.pos in z.cells
                                  for s in w.squads.values()):
                continue
            f = for_viewer(z.last_frame, cid, w, gm=gm)
            f["cells"] = sorted(z.cells)
            f["hot"] = sorted(z.cells_hot())
            frames.append(f)
        view["frames"] = frames
        return view

    async def after_change(self, cids: set | None = None):
        await self.push_outbox()
        await self.push_state(cids)

    # ------------------------------------------------------------------ tick / adjutant

    async def tick_once(self):
        """一個作戰 tick（含 tac_steps 步戰術層）。GM／測試用；背景迴圈用 step_once。"""
        for _ in range(max(1, self.sim.tac_steps)):
            await self.step_once()

    async def step_once(self):
        """一拍（戰術層一步）：每 tac_steps 拍做一次作戰層（那一拍的戰術步由作戰 tick 自己跑），其他拍只推戰術層。"""
        if self.tick_busy:
            return
        self.tick_busy = True
        self.phase = (getattr(self, "phase", -1) + 1) % max(1, self.sim.tac_steps)
        full = self.phase == 0
        t0 = time.perf_counter()
        prof = self.sim.prof = {}
        try:
            if full:
                await self.sim.tick(tac_steps=1)
            else:
                await self.sim.tactical_step()
        finally:
            self.tick_busy = False
        t1 = time.perf_counter()
        steps = (self.push_outbox, self.push_tactical, self.push_state) if full else (self.push_outbox,
                                                                                    self.push_tactical)
        for step in steps:
            try:
                await step()
            except Exception:
                log.exception("push failed: %s", step.__name__)
        t2 = time.perf_counter()
        if t2 - t0 > SLOW_STEP:
            parts = "、".join(f"{k} {v * 1000:.0f}ms" for k, v in sorted(prof.items(), key=lambda kv: -kv[1]))
            log.warning("慢的一拍（%s）：總 %.0fms｜模擬 %.0fms（%s）｜推播 %.0fms（%d 連線）",
                        "作戰 tick" if full else "戰術步", (t2 - t0) * 1000, (t1 - t0) * 1000, parts,
                        (t2 - t1) * 1000, len(self.clients))
        if full:
            self.schedule_adjutants()

    async def tick_loop(self):
        while True:
            t0 = time.monotonic()
            if self.sim.world.running:
                try:
                    await self.step_once()
                except Exception:
                    log.exception("tick failed")
            await asyncio.sleep(max(0.2, self.sim.step_seconds - (time.monotonic() - t0)))

    def schedule_adjutants(self, force: str | None = None):
        now = time.monotonic()
        for cid in list(self.sim.dirty) + ([force] if force else []):
            if cid not in self.sim.world.commanders:
                continue
            if cid != force and now - self.last_adj.get(cid, 0) < self.cfg["adjutant_min_interval"]:
                continue
            t = self.adj_tasks.get(cid)
            if t is not None and not t.done():
                continue
            self.last_adj[cid] = now
            self.adj_tasks[cid] = asyncio.create_task(self._adjutant(cid))

    async def _adjutant(self, cid: str):
        try:
            await self.sim.refresh_adjutant(cid)
            await self.push_state({cid})
        except Exception:
            log.exception("adjutant failed")

    # ------------------------------------------------------------------ actions

    async def enlist(self, uid: str) -> tuple[bool, str]:
        w = self.sim.world
        if self.sim.world.squads_of(uid):
            return False, "你已經是指揮官了。"
        active = {s.commander for s in w.squads.values() if s.status == "active"}
        if len(active) >= self.cfg["max_commanders"]:
            return False, f"指揮官名額已滿（{self.cfg['max_commanders']} 位）。"
        used = {s.card_id for s in w.squads.values()}
        names = list(self.cfg["starter_cards"])
        card = None
        for name in [n for n in names if n not in used] + names:
            card = load_card(name)
            if card:
                break
        if card is None:
            return False, "找不到起始角色卡。"
        name = self.accounts.name(uid)
        self.sim.ensure_commander(uid, name)
        sq, _ = await self.sim.deploy_squad(card, uid, reset=True)
        for other in list(w.commanders):
            if other != uid:
                self.sim.announce(other, f"指揮部：{name} 就任地區指揮官，{sq.callsign} 小隊進駐 {scenario.BASE}")
        if not w.running:
            w.running = True
            self.sim.save()
        self.schedule_adjutants(force=uid)
        return True, f"就任成功：{sq.callsign} 小隊在 {scenario.cell_desc(sq.pos)} 待命。"


def _public_item(it: dict, sim) -> dict:
    out = {k: v for k, v in it.items() if k not in ("card",)}
    if it.get("type") == "mission":
        m = it["mission"]
        out["mission"] = {"title": m["title"], "line": m["line"], "cell": m["cell"]}
    s = sim.world.squads.get(it.get("squad") or "")
    if s is not None:
        out["callsign"] = s.callsign
    return out


# ---------------------------------------------------------------------- HTTP


def make_app(game: Game) -> web.Application:
    app = web.Application()
    app["game"] = game
    r = app.router
    r.add_get("/", index)
    r.add_static("/static/", STATIC)
    r.add_post("/api/register", api_register)
    r.add_post("/api/login", api_login)
    r.add_post("/api/logout", api_logout)
    r.add_get("/api/me", api_me)
    r.add_post("/api/enlist", api_enlist)
    r.add_get("/api/state", api_state)
    r.add_post("/api/order", api_order)
    r.add_post("/api/mark", api_mark)
    r.add_post("/api/quick", api_quick)
    r.add_post("/api/support", api_support)
    r.add_post("/api/supply", api_supply)
    r.add_post("/api/autonomy", api_autonomy)
    r.add_post("/api/cancel_auto", api_cancel_auto)
    r.add_post("/api/accept", api_accept)
    r.add_post("/api/medevac", api_medevac)
    r.add_post("/api/suggestion", api_suggestion)
    r.add_post("/api/adjutant", api_adjutant)
    r.add_get("/api/tactical/{cell}", api_tactical)
    r.add_get("/api/world_terrain", api_world_terrain)
    r.add_get("/api/world", api_world)
    r.add_get("/api/replay/{rid}", api_replay)
    r.add_get("/api/commanders", api_commanders)
    r.add_get("/api/radio/{sid}", api_radio)
    r.add_post("/api/gm/{action}", api_gm)
    r.add_get("/ws", ws_handler)
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)
    return app


async def _start(app):
    g: Game = app["game"]
    if g.sim.zone_mode:
        from engine.tactical import worldmap
        worldmap.global_map()     # 預先建好全圖（約 1 秒），不要等到第一拍才建
    if g.cfg.get("autostart_tick", True):
        g.ticker = asyncio.create_task(g.tick_loop())


async def _stop(app):
    g: Game = app["game"]
    if g.ticker:
        g.ticker.cancel()
    for t in g.adj_tasks.values():
        t.cancel()
    for ws in list(g.clients):
        await ws.close()
    if hasattr(g.sim.llm, "close"):
        await g.sim.llm.close()


async def index(req):
    """?token=... → 設成 session cookie（示範／測試用連結），再導回首頁。"""
    g: Game = req.app["game"]
    tok = req.query.get("token")
    if tok and g.accounts.user_of(tok):
        resp = web.HTTPFound("/")
        resp.set_cookie(COOKIE, tok, max_age=7 * 24 * 3600, httponly=True, samesite="Lax")
        raise resp
    return web.FileResponse(os.path.join(STATIC, "index.html"))


def _uid(req) -> str | None:
    g: Game = req.app["game"]
    return g.accounts.user_of(req.cookies.get(COOKIE) or req.headers.get("X-Session"))


async def _json(req) -> dict:
    try:
        return await req.json()
    except Exception:
        return {}


def _err(msg: str, status: int = 400):
    return web.json_response({"ok": False, "error": msg}, status=status)


def _need(req, role: str = "user"):
    g: Game = req.app["game"]
    uid = _uid(req)
    if uid is None:
        raise web.HTTPUnauthorized(text=json.dumps({"ok": False, "error": "請先登入"}),
                                   content_type="application/json")
    if role == "gm" and not g.accounts.is_gm(uid):
        raise web.HTTPForbidden(text=json.dumps({"ok": False, "error": "需要 GM 權限"}),
                                content_type="application/json")
    return uid


def _session_response(data: dict, token: str | None):
    resp = web.json_response(data)
    if token:
        resp.set_cookie(COOKIE, token, max_age=7 * 24 * 3600, httponly=True, samesite="Lax")
    return resp


async def api_register(req):
    g: Game = req.app["game"]
    d = await _json(req)
    ok, res = g.accounts.register(str(d.get("name", "")), str(d.get("password", "")))
    if not ok:
        return _err(res)
    token = g.accounts.login(str(d.get("name", "")), str(d.get("password", "")))
    return _session_response({"ok": True, "token": token}, token)


async def api_login(req):
    g: Game = req.app["game"]
    d = await _json(req)
    token = g.accounts.login(str(d.get("name", "")), str(d.get("password", "")))
    if not token:
        return _err("暱稱或密碼錯誤。", 401)
    return _session_response({"ok": True, "token": token}, token)


async def api_logout(req):
    g: Game = req.app["game"]
    tok = req.cookies.get(COOKIE)
    if tok:
        g.accounts.logout(tok)
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE)
    return resp


async def api_me(req):
    g: Game = req.app["game"]
    uid = _uid(req)
    if uid is None:
        return web.json_response({"ok": True, "me": None})
    return web.json_response({"ok": True, "me": {"id": uid, "name": g.accounts.name(uid), "role": g.role(uid),
                                                 "gm": g.accounts.is_gm(uid)}})


async def api_enlist(req):
    g: Game = req.app["game"]
    uid = _need(req)
    ok, msg = await g.enlist(uid)
    if not ok:
        return _err(msg)
    await g.after_change({uid})
    return web.json_response({"ok": True, "message": msg})


async def api_state(req):
    g: Game = req.app["game"]
    uid = _need(req)
    watch = req.query.get("watch")
    if watch and watch != uid and g.role(uid) == "commander" and not g.accounts.is_gm(uid):
        return _err("指揮官不能看別人的指揮所。", 403)
    return web.json_response({"ok": True, "state": g.state_for(watch or uid)})


async def api_commanders(req):
    g: Game = req.app["game"]
    _need(req)
    return web.json_response({"ok": True, "commanders": g.commanders_list()})


async def api_radio(req):
    g: Game = req.app["game"]
    uid = _need(req)
    s = g.own_squad(uid, req.match_info["sid"])
    if s is None and not g.accounts.is_gm(uid):
        return _err("不是你的小隊。", 403)
    s = s or g.sim.world.squads.get(req.match_info["sid"])
    return web.json_response({"ok": True, "radio": views.radio_log(s, 80) if s else []})


async def _squad_action(req, fn):
    g: Game = req.app["game"]
    uid = _need(req)
    d = await _json(req)
    s = g.own_squad(uid, str(d.get("squad", "")))
    if s is None:
        return _err("不是你的小隊。", 403)
    res = await fn(g, s, d)
    await g.after_change({uid})
    if isinstance(res, dict) and res.get("error"):
        return web.json_response({"ok": False, "error": res["error"], "result": res})
    return web.json_response({"ok": res is not None and res is not False, "result": res})


async def api_order(req):
    """打字下令：馬上回應（命令與「回覆中…」立刻推到網頁），LLM 在背景理解，回來後回執與回覆再推。"""
    g: Game = req.app["game"]
    uid = _need(req)
    d = await _json(req)
    s = g.own_squad(uid, str(d.get("squad", "")))
    if s is None:
        return _err("不是你的小隊。", 403)
    text = str(d.get("text", "")).strip()[:300]
    if not text:
        return _err("沒有內容。")
    if not s.linked or s.status != "active":
        await g.sim.issue_order(s.id, text)
        await g.after_change({uid})
        return web.json_response({"ok": False, "error": "通訊中斷，命令沒有送達。"})

    async def run():
        try:
            await g.sim.issue_order(s.id, text)
            await g.after_change({uid})
        except Exception:
            log.exception("order failed")
    if d.get("wait"):
        await run()
        return web.json_response({"ok": True})
    asyncio.create_task(run())
    return web.json_response({"ok": True, "pending": True})


async def api_quick(req):
    async def fn(g, s, d):
        via = [str(x).upper() for x in (d.get("via") or []) if isinstance(x, str)]
        target = d.get("target")
        target = str(target).upper() if target and str(target).lower() not in ("left", "right") else target
        res = await g.sim.quick_order(s.id, str(d.get("action", "")), target, via)
        if res and res.get("error"):
            return {"error": res["error"]}
        return res
    return await _squad_action(req, fn)


def _cell_point(d: dict) -> tuple[str | None, list]:
    """前端可以給 {cell, point:[格內 x,y]} 或 {global:[全圖 x,y]}（連續地圖）。"""
    gp = d.get("global")
    if isinstance(gp, list) and len(gp) >= 2:
        from engine.tactical import worldmap
        try:
            cell, local = worldmap.to_local((float(gp[0]), float(gp[1]), 0))
        except (TypeError, ValueError):
            return None, []
        return cell, [local[0], local[1]]
    return (str(d.get("cell", "")).upper() or None), (d.get("point") or [])


async def api_mark(req):
    async def fn(g, s, d):
        cell, pt = _cell_point(d)
        return await g.sim.mark_order(s.id, str(d.get("kind", "")), pt or [0, 0], cell=cell)
    return await _squad_action(req, fn)


async def api_supply(req):
    async def fn(g, s, d):
        ok, msg = await g.sim.request_supply(s.id)
        return {"ok": ok, "message": msg}
    return await _squad_action(req, fn)


async def api_autonomy(req):
    async def fn(g, s, d):
        return await g.sim.set_autonomy(s.id, str(d.get("level", "")))
    return await _squad_action(req, fn)


async def api_cancel_auto(req):
    async def fn(g, s, d):
        return await g.sim.cancel_auto(s.id)
    return await _squad_action(req, fn)


async def api_accept(req):
    async def fn(g, s, d):
        return await g.sim.accept_proposal(s.id)
    return await _squad_action(req, fn)


async def api_medevac(req):
    async def fn(g, s, d):
        ok, msg = await g.sim.request_medevac(s.id)
        return {"ok": ok, "message": msg} if ok else {"error": msg}
    return await _squad_action(req, fn)


async def api_suggestion(req):
    g: Game = req.app["game"]
    uid = _need(req)
    d = await _json(req)
    c = g.sim.world.commanders.get(uid)
    sug = (c.adjutant.get("suggestions") or []) if c else []
    i = int(d.get("index", -1))
    if not (0 <= i < len(sug)):
        return _err("建議已失效，請更新。")
    s = g.own_squad(uid, sug[i]["squad"])
    if s is None:
        return _err("不是你的小隊。", 403)
    res = await g.sim.issue_order(s.id, sug[i]["order"])
    await g.after_change({uid})
    return web.json_response({"ok": res is not None, "result": res})


async def api_adjutant(req):
    g: Game = req.app["game"]
    uid = _need(req)
    g.schedule_adjutants(force=uid)
    return web.json_response({"ok": True})


async def api_support(req):
    g: Game = req.app["game"]
    uid = _need(req)
    d = await _json(req)
    cell, pt = _cell_point(d)
    ok, msg = await g.sim.call_support(uid, str(d.get("kind", "")), cell or "", pt)
    await g.after_change({uid})
    return web.json_response({"ok": ok, "message": msg}) if ok else _err(msg)


async def api_tactical(req):
    g: Game = req.app["game"]
    uid = _need(req)
    cell = req.match_info["cell"].upper()
    gm = g.accounts.is_gm(uid) and req.query.get("gm") == "1"
    watch = req.query.get("watch") if g.role(uid) != "commander" or g.accounts.is_gm(uid) else None
    cid = watch or uid
    data = views.tactical_view(g.sim, cid, cell, gm=gm)
    if data is None:
        return _err("你沒有小隊在這一格（或通訊中斷）。", 403)
    eng = g.sim.engagements.get(cell)
    if eng is not None and eng.last_frame is not None:
        data["frame"] = for_viewer(eng.last_frame, cid, g.sim.world, gm=gm)
    return web.json_response({"ok": True, "tactical": data})


async def api_world_terrain(req):
    _need(req)
    return web.json_response({"ok": True, "terrain": views.world_terrain()})


async def api_world(req):
    g: Game = req.app["game"]
    uid = _need(req)
    watch = req.query.get("watch")
    if watch and watch != uid and g.role(uid) == "commander" and not g.accounts.is_gm(uid):
        return _err("指揮官不能看別人的指揮所。", 403)
    gm = g.accounts.is_gm(uid) and req.query.get("gm") == "1"
    return web.json_response({"ok": True, "world": g.world_for(watch or uid, gm)})


async def api_replay(req):
    g: Game = req.app["game"]
    uid = _need(req)
    rp = g.sim.replays.load(req.match_info["rid"])
    gm = g.accounts.is_gm(uid)
    if rp is None or (not gm and uid not in rp["commanders"]):
        return _err("沒有這場回放。", 404)
    cid = uid if uid in rp["commanders"] else rp["commanders"][0]
    full = gm and req.query.get("gm") == "1"
    frames = [for_viewer(f, cid, g.sim.world, gm=full) for f in rp["frames"]]
    return web.json_response({"ok": True, "replay": {k: rp[k] for k in ("id", "cell", "w", "h", "terrain", "rooms",
                                                                       "start_tick", "end_tick")},
                              "frames": frames})


async def api_gm(req):
    g: Game = req.app["game"]
    _need(req, "gm")
    action = req.match_info["action"]
    d = await _json(req)
    sim = g.sim
    w = sim.world
    msg = "ok"
    if action == "tick":
        n = max(1, min(30, int(d.get("count", 1))))
        for _ in range(n):
            await g.tick_once()
        msg = f"已推進 {n} tick，現在 T{w.tick}"
    elif action == "run":
        w.running = bool(d.get("on"))
        sim.save()
        msg = f"背景 tick：{'on' if w.running else 'off'}"
    elif action == "link":
        s = sim.resolve(str(d.get("squad", "")))
        if s is None:
            return _err("沒有這支小隊")
        await sim.set_link(s.id, bool(d.get("on")))
        msg = f"{s.callsign} 通訊 → {'on' if d.get('on') else 'off'}"
    elif action == "reset":
        async with sim.lock:
            roster = sim.reset()
        for sid, cid, card_id in roster:
            card = load_card(card_id)
            if card and cid:
                await sim.deploy_squad(card, cid)
        sim.live_cache.clear()
        sim.supports.clear()
        msg = "戰場已重置"
    elif action == "wipe":
        async with sim.lock:
            sim.wipe()
        sim.live_cache.clear()
        sim.supports.clear()
        msg = "存檔已清除（指揮官要重新就任）"
    elif action == "reserve":
        from engine import reserve
        import random
        reserve.fill(w, random.Random())
        w.reserve_announced = False
        sim.save()
        msg = f"預備兵池補滿：{reserve.remaining(w)} 人"
    elif action == "status":
        return web.json_response({"ok": True, "status": gm_status(sim)})
    elif action == "watch":
        return web.json_response({"ok": True})
    else:
        return _err("沒有這個指令")
    await g.push_outbox()
    await g.push_state()
    return web.json_response({"ok": True, "message": msg})


def gm_status(sim) -> dict:
    from engine import enemy_ai, obedience
    w = sim.world
    squads = []
    for s in w.squads.values():
        squads.append({"id": s.id, "callsign": s.callsign, "commander": s.commander, "pos": s.pos, "status": s.status,
                       "linked": s.linked, "action": sim._action_text(s), "autonomy": s.autonomy,
                       "obedience": obedience.score(s), "tendencies": obedience.tendency_text(s),
                       "beliefs": [b["text"] for b in s.beliefs], "morale": s.morale, "ammo": s.ammo,
                       "members": [f"{m.name}({m.hp})" for m in s.members],
                       "experience": sim.experience_text(s, n=10, show_interp=True)})
    enemies = [{"id": e.id, "tag": e.tag, "pos": e.pos, "strength": e.strength, "order": e.order, "goal": e.goal}
               for e in w.enemies.values() if e.alive]
    return {"tick": w.tick, "running": w.running, "squads": squads, "enemies": enemies,
            "enemy_total": enemy_ai.total_strength(w), "engagements": sorted(sim.engagements),
            "enemy_log": list(sim.enemy_log)}


# ---------------------------------------------------------------------- WebSocket


async def ws_handler(req):
    g: Game = req.app["game"]
    uid = _uid(req) or g.accounts.user_of(req.query.get("token"))
    if uid is None:
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    info = {"uid": uid, "watch": None, "gm_cell": None}
    g.clients[ws] = info
    try:
        await g.send(ws, {"type": "hello", "me": {"id": uid, "name": g.accounts.name(uid), "role": g.role(uid),
                                                  "gm": g.accounts.is_gm(uid)}})
        if uid in g.sim.world.commanders:
            await g.send(ws, {"type": "state", "state": g.state_for(uid)})
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            t = d.get("type")
            if t == "watch":
                target = d.get("commander")
                if g.role(uid) == "commander" and target != uid and not g.accounts.is_gm(uid):
                    await g.send(ws, {"type": "error", "error": "指揮官不能看別人的指揮所。"})
                    continue
                info["watch"] = target if target in g.sim.world.commanders else None
                cid = g.viewing(info)
                if cid in g.sim.world.commanders:
                    await g.send(ws, {"type": "state", "state": g.state_for(cid)})
            elif t == "gm_cell" and g.accounts.is_gm(uid):
                info["gm_cell"] = d.get("cell")
            elif t == "gm_truth" and g.accounts.is_gm(uid):
                info["gm_truth"] = bool(d.get("on"))
            elif t == "ping":
                await g.send(ws, {"type": "pong"})
    finally:
        g.clients.pop(ws, None)
    return ws
