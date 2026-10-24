"""戰術地圖實驗頁效能量測：用無頭 Edge 開建置好的實驗頁（web/static/dist/lab.html），
讓頁面照常跑 4 秒，量實際的畫面更新率與每幀間隔（含顯示卡與合成），另外用 __tac.bench() 量每幀的 JS 成本。

用法：python scripts/tac_bench.py [--soft] [--size=1920,1080] [--dpr=1.5] [--json]
  --soft   強制軟體繪圖（SwiftShader，完全不用顯示卡）：最差的情況，這裡跑得順，內顯一定跑得順
  需要先 python scripts/build_web.py；有 web/tac/fixtures/replay.json（scripts/tac_fixture.py）時一併量回放。
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import quote

from aiohttp import ClientSession, web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "web", "static", "dist")
FIXTURES = os.path.join(ROOT, "web", "tac", "fixtures")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def arg(name: str, default: str) -> str:
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


async def cdp_eval(ws, expr: str, msg_id: list) -> object:
    msg_id[0] += 1
    mid = msg_id[0]
    await ws.send_json({"id": mid, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}})
    while True:
        m = await ws.receive_json()
        if m.get("id") == mid:
            res = m.get("result", {})
            if "exceptionDetails" in res:
                raise RuntimeError(res["exceptionDetails"].get("text"))
            return res.get("result", {}).get("value")


BENCH_JS = """(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 100 && !window.__tac; i++) await sleep(100);
  if (!window.__tac) return null;
  const out = {};
  await sleep(1500);
  for (const z of [14, 3, 30]) {
    __tac.zoom(z);
    await sleep(1000);
    __tac.reset();
    await sleep(4000);
    const live = __tac.stats();                   // 實際畫面更新（含顯示卡、合成）
    const cpu = __tac.bench(120);                 // 每幀 JS 成本
    out['zoom' + z] = { ...live, jsMs: cpu.updateMs };
  }
  return out;
})()"""


async def run() -> int:
    if not os.path.exists(os.path.join(DIST, "lab.html")):
        print("沒有 web/static/dist/lab.html：先跑 python scripts/build_web.py")
        return 1
    soft = "--soft" in sys.argv
    w, h = arg("size", "1920,1080").split(",")
    dpr = arg("dpr", "1.5")
    app = web.Application()
    if os.path.isdir(FIXTURES):
        app.router.add_static("/static/dist/fixtures/", FIXTURES)
    app.router.add_static("/static/dist/", DIST)
    runner = web.AppRunner(app)
    await runner.setup()
    port, dbg = free_port(), free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    prof = tempfile.mkdtemp(prefix="ac_bench_")
    flags = ["--headless=new", f"--remote-debugging-port={dbg}", f"--user-data-dir={prof}", f"--window-size={w},{h}",
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--no-first-run"]
    flags += ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if soft else ["--enable-gpu", "--use-angle=d3d11"]
    edge = subprocess.Popen([EDGE, *flags, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {}
    try:
        async with ClientSession() as s:
            for _ in range(50):
                try:
                    async with s.get(f"http://127.0.0.1:{dbg}/json/version") as r:
                        if r.status == 200:
                            break
                except Exception:
                    await asyncio.sleep(0.2)
            modes = [("壓力 120 人", "mode=stress&n=120"), ("壓力 300 人", "mode=stress&n=300")]
            if os.path.exists(os.path.join(FIXTURES, "replay.json")):
                modes.append(("回放（fixture，交火最密的一段）", "mode=fixture&speed=1&hot=1"))
            for label, qs in modes:
                url = f"http://127.0.0.1:{port}/static/dist/lab.html?{qs}&dpr={dpr}"
                async with s.put(f"http://127.0.0.1:{dbg}/json/new?{quote(url, safe='')}") as r:
                    tab = await r.json()
                async with s.ws_connect(tab["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                    mid = [0]
                    await asyncio.sleep(3)
                    info = await cdp_eval(ws, "(() => { const c = document.querySelector('canvas'); const g = c && c.getContext('webgl2');"
                                              " const e = g && g.getExtension('WEBGL_debug_renderer_info');"
                                              " return { canvas: c ? [c.width, c.height] : null, gpu: e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : null }; })()", mid)
                    res = await cdp_eval(ws, BENCH_JS, mid)
                    results[label] = {"info": info, "bench": res}
                async with s.get(f"http://127.0.0.1:{dbg}/json/close/{tab['id']}"):
                    pass
    finally:
        edge.terminate()
        try:
            edge.wait(10)
        except subprocess.TimeoutExpired:
            edge.kill()
        await runner.cleanup()
        shutil.rmtree(prof, ignore_errors=True)
    if "--json" in sys.argv:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0
    print(f"{'軟體繪圖' if soft else '顯示卡'}｜視窗 {w}×{h}｜DPR {dpr}")
    for label, r in results.items():
        info, b = r["info"] or {}, r["bench"]
        print(f"\n{label}｜畫布 {info.get('canvas')}｜{info.get('gpu')}")
        if not b:
            print("  實驗頁沒有啟動")
            continue
        for k, v in b.items():
            print(f"  {k:7s} {v['fps']:5.1f} fps｜每幀平均 {v['avgMs']:6.2f} ms｜p95 {v['p95Ms']:6.2f}｜最大 {v['maxMs']:6.2f}"
                  f"｜>33ms {v['slow']}/{v['frames']}｜JS {v['jsMs']:.2f} ms｜個體 {v['units']}、粒子 {v['particles']}、曳光 {v['tracers']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
