"""戰術地圖實驗頁截圖：用無頭 Edge 開建置好的實驗頁，等一段時間後截圖（同一場回放、同一個時間點，比較不同色調／外觀）。

用法：python scripts/tac_shot.py <輸出.png> [--q="mode=fixture&hot=1&theme=slate"] [--wait=8] [--size=1600,1000] [--dpr=1]
       [--eval="__tac.zoom(20)"]（等待前在頁面執行，例如縮放、移動鏡頭；當作使用者動作，可以開聲音）
       [--after="JSON.stringify(__tac.stats())"]（截圖前在頁面執行並印出結果）
       [--hq] [--user=甲]（改開指揮室：啟動 scripts/web_demo.py 示範伺服器，用示範帳號登入：甲、乙是指揮官，boss 是 GM）
需要先 python scripts/build_web.py；回放用 web/tac/fixtures/replay.json（scripts/tac_fixture.py）。
"""
import asyncio
import base64
import os
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote

from aiohttp import ClientSession, web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tac_bench import DIST, EDGE, FIXTURES, arg, cdp_eval, close_browser, free_port  # noqa: E402


async def run() -> int:
    out = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not out:
        print(__doc__)
        return 1
    qs, wait = arg("q", "mode=fixture&hot=1"), float(arg("wait", "8"))
    w, h = arg("size", "1600,1000").split(",")
    dpr, ev = arg("dpr", "1"), arg("eval", "")
    hq = "--hq" in sys.argv
    demo = None
    if hq:                                    # 示範伺服器（不需要 LLM），印出登入連結
        demo_port = free_port()
        demo = subprocess.Popen([sys.executable, "-B", "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_demo.py"),
                                 str(demo_port), f"--exit-after={int(wait) + 120}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        token = None
        for line in demo.stdout:
            if line.startswith(arg("user", "甲") + ":") and "token=" in line:
                token = line.strip().split("token=")[1]
                break
        if not token:
            print("示範伺服器沒有啟動")
            return 1
    app = web.Application()
    if os.path.isdir(FIXTURES):
        app.router.add_static("/static/dist/fixtures/", FIXTURES)
    app.router.add_static("/static/dist/", DIST)
    runner = web.AppRunner(app)
    await runner.setup()
    port, dbg = free_port(), free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    prof = tempfile.mkdtemp(prefix="ac_shot_")
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={dbg}", f"--user-data-dir={prof}",
                             f"--window-size={w},{h}", "--enable-gpu", "--use-angle=d3d11", "--no-first-run",
                             "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with ClientSession() as s:
            for _ in range(50):
                try:
                    async with s.get(f"http://127.0.0.1:{dbg}/json/version") as r:
                        if r.status == 200:
                            break
                except Exception:
                    await asyncio.sleep(0.2)
            url = (f"http://127.0.0.1:{demo_port}/?token={token}" if hq
                   else f"http://127.0.0.1:{port}/static/dist/lab.html?{qs}&dpr={dpr}")
            async with s.put(f"http://127.0.0.1:{dbg}/json/new?{quote(url, safe='')}") as r:
                tab = await r.json()
            async with s.ws_connect(tab["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                mid = [0]
                ready = "window.__tac" if not hq else "(window.Tac && window.Tac.ready) || (window.G && G.state && G.state.renderer === 'canvas')"
                for _ in range(40):                    # 頁面還在載入時執行環境會被換掉（Uncaught），重試
                    try:
                        if await cdp_eval(ws, f"(async () => {{ for (let i = 0; i < 100 && !({ready}); i++) await new Promise(r => setTimeout(r, 100)); return !!({ready}); }})()", mid):
                            break
                    except RuntimeError:
                        await asyncio.sleep(0.5)
                if ev:
                    await cdp_eval(ws, ev, mid, gesture=True)
                await asyncio.sleep(wait)
                after = arg("after", "")
                if after:                          # 截圖前在頁面執行、印出結果（例如讀統計）
                    print(await cdp_eval(ws, after, mid))
                mid[0] += 1
                await ws.send_json({"id": mid[0], "method": "Page.captureScreenshot", "params": {"format": "png"}})
                while True:
                    m = await ws.receive_json()
                    if m.get("id") == mid[0]:
                        break
                with open(out, "wb") as f:
                    f.write(base64.b64decode(m["result"]["data"]))
                await ws.send_json({"id": 99999, "method": "Page.close"})
            await close_browser(s, dbg)
    finally:
        try:
            edge.wait(10)
        except subprocess.TimeoutExpired:
            edge.kill()
        await runner.cleanup()
        shutil.rmtree(prof, ignore_errors=True)
        if demo is not None:
            demo.kill()
    print(f"screenshot: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
