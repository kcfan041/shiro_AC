"""戰術地圖實驗頁截圖：用無頭 Edge 開建置好的實驗頁，等一段時間後截圖（同一場回放、同一個時間點，比較不同色調／外觀）。

用法：python scripts/tac_shot.py <輸出.png> [--q="mode=fixture&hot=1&theme=slate"] [--wait=8] [--size=1600,1000] [--dpr=1]
       [--eval="__tac.zoom(20)"]（等待前在頁面執行，例如縮放、移動鏡頭）
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
            url = f"http://127.0.0.1:{port}/static/dist/lab.html?{qs}&dpr={dpr}"
            async with s.put(f"http://127.0.0.1:{dbg}/json/new?{quote(url, safe='')}") as r:
                tab = await r.json()
            async with s.ws_connect(tab["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                mid = [0]
                for _ in range(20):                    # 頁面還在載入時執行環境會被換掉（Uncaught），重試
                    try:
                        if await cdp_eval(ws, "(async () => { for (let i = 0; i < 100 && !window.__tac; i++) await new Promise(r => setTimeout(r, 100)); return !!window.__tac; })()", mid):
                            break
                    except RuntimeError:
                        await asyncio.sleep(0.5)
                if ev:
                    await cdp_eval(ws, ev, mid)
                await asyncio.sleep(wait)
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
    print(f"screenshot: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
