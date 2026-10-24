// 戰術地圖實驗頁：和指揮室用同一個 TacView，資料來源換成本機回放或假戰場，量播放與效能。
//   ?mode=stress&n=120  假戰場壓力測試（不需要伺服器；manual 模式，不走時鐘與軌跡）
//   ?mode=fixture       播 scripts/tac_fixture.py 產生的回放（web/tac/fixtures/replay.json，只在 npm run dev 有）
//   ?replay=<id>        向伺服器要一場回放（要先登入，例如 web_demo 的 ?token= 連結）
// 播回放的方式：
//   預設「假即時」：用 Feed 照伺服器的節奏把影格交給 TacView，可以加 &jitter=200（到達時間抖動 ms）、
//     &scale=0.8（伺服器變慢）、&outage=10,5（第 10 秒起斷線 5 秒），量時鐘在這些情況下的表現。
//   &play=replay：回放模式（照播放速度 &speed=、不用緩衝）。
//   &hot=1：從開火最多的地方開始；&follow=1：鏡頭跟隨我方。
//   &theme=slate|green：色調（預設灰藍）；&look=auto|symbol|mech：個體外觀（預設依縮放）；&dpr=1.5：模擬高解析度螢幕。
// window.__tac 給量測腳本（tac_bench.py、tac_shot.py）讀。

import { Feed } from "../feed";
import { checkFrame, checkTerrain, type Fixture, type Frame, type FrameUnit, type WorldTerrain } from "../protocol";
import type { Look } from "../render/units";
import { FrameTimer } from "../stats";
import { makeTerrain, StressScene, type StressUnit } from "../stress";
import { cssHex, getTheme, THEMES } from "../theme";
import { SNAP_DIST, type UnitSample } from "../tracks";
import { TacView } from "../view";

const q = new URLSearchParams(location.search);
const hud = document.getElementById("hud")!;
const stage = document.getElementById("stage")!;

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

async function loadSource(): Promise<{ terrain: WorldTerrain; frames: Frame[] | null; label: string }> {
  const mode = q.get("mode") || (q.get("replay") ? "replay" : "fixture");
  if (mode === "replay") {
    const t = await fetchJson<{ ok: boolean; terrain: WorldTerrain }>("/api/world_terrain");
    const r = await fetchJson<{ ok: boolean; frames: Frame[] }>(`/api/replay/${encodeURIComponent(q.get("replay") || "")}`);
    if (t?.ok && r?.ok) return { terrain: t.terrain, frames: r.frames, label: `回放 ${q.get("replay")}` };
    hud.dataset.warn = "拿不到回放（沒登入或沒有這場）→ 改跑壓力測試";
  }
  if (mode === "fixture") {
    const fx = await fetchJson<Fixture>("fixtures/replay.json");
    if (fx) return { terrain: fx.terrain, frames: fx.frames, label: `回放 ${fx.replay.id}（fixture）` };
    hud.dataset.warn = "沒有 fixtures/replay.json（先跑 scripts/tac_fixture.py）→ 改跑壓力測試";
  }
  const t = await fetchJson<{ ok: boolean; terrain: WorldTerrain }>("/api/world_terrain");
  return { terrain: t?.ok ? t.terrain : makeTerrain(7), frames: null, label: "壓力測試" };
}

/** 壓力測試的個體 → 和回放一樣的樣本格式（世界座標） */
function stressSample(u: StressUnit): UnitSample {
  const f: FrameUnit = { id: u.id, side: u.side, name: u.id, pos: [Math.floor(u.x), Math.floor(u.y)], posture: u.posture, facing: [1, 0],
    down: u.down, weapon: ["rifle", "lmg", "dmr", "shotgun"][+u.id.slice(1) % 4] };
  return { id: u.id, side: u.side, name: u.id, x: u.x, y: u.y, z: 0, facing: u.facing, posture: u.posture, postureFrom: u.posture, postureK: 1,
    down: u.down, downK: u.down ? 1 : 0, dead: false, alpha: 1, aim: u.cool < 0.4 && u.target && !u.down ? 1 - u.cool / 0.4 : null,
    moving: Math.hypot(u.tx - u.x, u.ty - u.y) > 0.05, u: f };
}

async function main() {
  const theme = getTheme(q.get("theme"));
  document.body.style.background = cssHex(theme.bg);
  const src = await loadSource();
  const T = src.terrain;
  const problems = [...checkTerrain(T), ...(src.frames ? src.frames.slice(0, 50).flatMap(checkFrame) : [])];
  const t0 = performance.now();
  let view: TacView;
  try {
    view = await TacView.create(stage, T, { theme: q.get("theme") || undefined, look: (q.get("look") as Look) || "auto", dpr: +(q.get("dpr") || 0) || undefined });
  } catch (e) {
    hud.textContent = `無法啟動 WebGL：${e}`;
    return;
  }
  const bakeMs = performance.now() - t0;
  const sy = (y: number) => T.h - y;

  // ---------------------------------------------------------------- 資料來源
  let n = +(q.get("n") || 120);
  let frames = src.frames;
  const replayMode = q.get("play") === "replay";
  const speed = +(q.get("speed") || 1);
  const jitter = +(q.get("jitter") || 0) / 1000, scale = +(q.get("scale") || 1);
  const outage = q.get("outage") ? (q.get("outage")!.split(",").map(Number) as [number, number]) : null;
  if (frames && frames.length && q.get("hot")) {     // 從開火最多的那一格前 5 秒開始
    let best = 0;
    frames.forEach((f, i) => { if (f.shots.length > frames![best].shots.length) best = i; });
    frames = frames.slice(Math.max(0, best - 10));
  }
  let feed: Feed | null = null, wall = 0, fi = 0, ratio0: [number, number] | null = null, maxJump = 0, lastJumps = 0, jumpAt = -Infinity;
  const lastPos = new Map<string, [number, number]>();
  const jumpLog: [number, number][] = [];
  let scene: StressScene | null = null;

  function resetPlayback() {
    view.reset(false);
    wall = 0; fi = 0; ratio0 = null; maxJump = 0; lastJumps = 0; jumpAt = -Infinity;
    lastPos.clear(); jumpLog.length = 0;
    if (!frames) return;
    if (replayMode) view.clock.startReplay(frames[0].ts0 ?? 0, speed);
    else feed = new Feed(frames, { jitter, timeScale: scale, outage, seed: 1 });
  }
  function resetStress() {
    scene = new StressScene(T, n, 1, [T.w / 2, T.h / 2]);
    view.fx.setSmokes(Array.from({ length: 6 }, (_, k) => ({ x: T.w / 2 + (k - 2.5) * 6, y: sy(T.h / 2 + ((k % 3) - 1) * 10), r: 4, density: 0.9 })));
    view.cam.jumpTo(T.w / 2, sy(T.h / 2));
  }

  /** 播回放：照 Feed 的時間把影格交給 TacView；量播放／戰鬥時間比與單幀位移 */
  function feedPlayback(dtS: number) {
    if (!frames || !frames.length) return;
    wall += dtS;
    if (replayMode) {
      while (fi < frames.length && (frames[fi].ts0 ?? 0) <= (view.clock.bt ?? 0) + 2) view.ingest(frames[fi++]);
    } else {
      for (const { frame, at } of feed!.take(wall)) view.ingest(frame, at);
    }
    const bt = view.clock.bt;
    if (bt === null) return;
    if (view.clock.primed && !ratio0) ratio0 = [wall, bt];
    if (bt >= (frames[frames.length - 1].ts1 ?? 0) && (replayMode || feed!.done)) { resetPlayback(); return; }
    const jumped = view.clock.jumps !== lastJumps;
    lastJumps = view.clock.jumps;
    if (jumped) jumpAt = bt;
    let worst = 0;
    for (const id of [...lastPos.keys()]) if (!view.samples.some((u) => u.id === id)) lastPos.delete(id);
    for (const u of view.samples) {
      const p = lastPos.get(u.id);
      if (p && bt - jumpAt > 0.5) {
        const d = Math.hypot(u.x - p[0], u.y - p[1]);
        if (d <= SNAP_DIST) worst = Math.max(worst, d);
      }
      lastPos.set(u.id, [u.x, u.y]);
    }
    jumpLog.push([wall, worst]);
    while (jumpLog.length && jumpLog[0][0] < wall - 5) jumpLog.shift();
    maxJump = Math.max(maxJump, worst);
  }

  if (frames && frames.length) {
    resetPlayback();
    const f0 = frames[Math.min(frames.length - 1, q.get("hot") ? 10 : Math.floor(frames.length / 2))];
    const live = f0.units.filter((u) => !u.dead);
    if (live.length) view.cam.jumpTo(live.reduce((a, u) => a + u.pos[0], 0) / live.length + 0.5, sy(live.reduce((a, u) => a + u.pos[1], 0) / live.length + 0.5));
    if (q.get("follow")) {
      const own = f0.units.find((u) => u.side === "own");
      if (own) view.follow((own.group || own.squad || "") as string);
    }
    view.app.ticker.add((tk) => feedPlayback(Math.min(0.1, tk.deltaMS / 1000)));
  } else {
    view.manual = true;
    resetStress();
    view.onTick = (dtS) => {
      if (!scene) return;
      const now = performance.now() / 1000;
      for (const ev of scene.step(dtS)) {
        if (ev.kind === "shot") {
          const mat = ev.hit ? "body" : (["wall", "ground", "low", "rubble", "air"] as const)[Math.floor(Math.random() * 5)];
          view.fx.shot([ev.from[0], sy(ev.from[1])], [ev.to[0], sy(ev.to[1])], { mat, hit: ev.hit, shooter: ev.shooter }, scene.t);
          view.units.shot(ev.shooter, now);
          view.audio.play({ kind: "gun", x: ev.from[0], y: sy(ev.from[1]), weapon: ["rifle", "lmg", "dmr", "shotgun"][+ev.shooter.slice(1) % 4], shooter: ev.shooter },
            { x: view.cam.x, y: view.cam.y, halfWidth: view.app.screen.width / 2 / view.cam.zoom });
        } else if (ev.kind === "throw") {
          view.fx.throwArc([ev.from[0], sy(ev.from[1])], [ev.to[0], sy(ev.to[1])], ev.land - ev.t);
        } else {
          view.fx.blast([ev.at[0], sy(ev.at[1])], false, 20);
        }
      }
      view.draw(scene.units.map(stressSample), [], now);
    };
  }

  // ---------------------------------------------------------------- 控制列
  const ctl = document.getElementById("ctl")!;
  ctl.innerHTML = `
    <button data-k="tracers" class="on">曳光</button><button data-k="particles" class="on">粒子</button><button data-k="smoke" class="on">煙霧</button>
    ${frames ? "" : `<label>人數 <select id="num">${[40, 80, 120, 200, 300].map((v) => `<option ${v === n ? "selected" : ""}>${v}</option>`).join("")}</select></label>`}
    <label>色調 <select id="thm">${Object.values(THEMES).map((t) => `<option value="${t.name}" ${t.name === theme.name ? "selected" : ""}>${t.label}</option>`).join("")}</select></label>
    <label>外觀 <select id="look">${[["auto", "自動"], ["symbol", "符號"], ["mech", "機兵"]].map(([v, l]) => `<option value="${v}" ${v === view.units.look ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <button id="rst">重設統計</button>`;
  for (const b of ctl.querySelectorAll<HTMLButtonElement>("button[data-k]")) {
    b.onclick = () => {
      const k = b.dataset.k as "tracers" | "particles" | "smoke";
      view.fx.enabled[k] = !view.fx.enabled[k];
      b.classList.toggle("on", view.fx.enabled[k]);
    };
  }
  const num = document.getElementById("num") as HTMLSelectElement | null;
  if (num) num.onchange = () => { n = +num.value; resetStress(); view.timer.reset(); };
  const thm = document.getElementById("thm") as HTMLSelectElement;
  thm.onchange = () => { const u = new URL(location.href); u.searchParams.set("theme", thm.value); location.href = u.toString(); };   // 地形要重烘，直接重新載入
  const lk = document.getElementById("look") as HTMLSelectElement;
  lk.onchange = () => { view.units.look = lk.value as Look; };
  (document.getElementById("rst") as HTMLButtonElement).onclick = () => { view.timer.reset(); maxJump = 0; };

  // ---------------------------------------------------------------- 統計
  const playStats = () => {
    const c = view.clock.state();
    const ratio = ratio0 && wall - ratio0[0] > 1 ? (c.bt - ratio0[1]) / (wall - ratio0[0]) : null;
    return { ...c, ratio, maxJump, jump5s: jumpLog.reduce((m, [, j]) => Math.max(m, j), 0), wall };
  };
  setInterval(() => {
    const s = view.timer.stats();
    const p = frames ? playStats() : null;
    hud.innerHTML = `<b>${src.label}</b>｜${view.app.renderer.name}｜DPR ${view.app.renderer.resolution}｜${view.app.screen.width}×${view.app.screen.height}<br>
      ${s.fps.toFixed(1)} fps｜平均 ${s.avgMs.toFixed(1)} ms｜p95 ${s.p95Ms.toFixed(1)}｜最大 ${s.maxMs.toFixed(1)}｜&gt;33ms ${s.slow}/${s.frames}｜JS 更新 ${s.updateMs.toFixed(2)} ms<br>
      個體 ${view.samples.length}｜粒子 ${view.fx.activeParticles}｜曳光 ${view.fx.activeTracers}｜聲音 ${view.audio.limiter.active}（播 ${view.audio.played}、合併 ${view.audio.limiter.merged}、丟 ${view.audio.limiter.dropped}）｜縮放 ${view.cam.zoom.toFixed(1)} px/格｜地形 ${bakeMs.toFixed(0)} ms（級距 ${view.terrain.tier}、共 ${view.terrain.bakeCount} 塊）
      ${p ? `<br>${replayMode ? "回放" : `假即時（抖動 ±${(jitter * 1000).toFixed(0)} ms、伺服器 ×${scale}${outage ? `、斷線 ${outage[0]}s 起 ${outage[1]}s` : ""}）`}｜戰鬥 ${p.bt.toFixed(1)} s｜速度 ×${p.rate.toFixed(3)}｜緩衝 ${p.buffer.toFixed(2)} s｜伺服器 ×${p.serverRate.toFixed(2)}
        ｜播放／戰鬥 ${p.ratio === null ? "—" : p.ratio.toFixed(3)}｜卡住 ${p.stalls}、跳過 ${p.jumps}｜單幀最大位移 ${p.jump5s.toFixed(3)} 格（全程 ${p.maxJump.toFixed(3)}）` : ""}
      ${hud.dataset.warn ? `<br><span class="warn">${hud.dataset.warn}</span>` : ""}
      ${problems.length ? `<br><span class="warn">資料問題：${problems.slice(0, 5).join("；")}</span>` : ""}`;
  }, 250);

  (window as unknown as { __tac: unknown }).__tac = {
    stats: () => ({ ...view.timer.stats(), units: view.samples.length, particles: view.fx.activeParticles, tracers: view.fx.activeTracers,
      voices: view.audio.limiter.active, renderer: view.app.renderer.name, dpr: view.app.renderer.resolution, w: view.app.screen.width, h: view.app.screen.height, bakeMs,
      play: frames ? playStats() : null }),
    reset: () => { view.timer.reset(); maxJump = 0; },
    /** 不等畫面更新，連續算＋畫 count 幀（每幀 1/60 秒）：量每幀的 JS 成本 */
    bench: (count = 120) => {
      const gl = (view.app.renderer as unknown as { gl?: WebGLRenderingContext }).gl;
      const tm = new FrameTimer(count);
      for (let i = 0; i < count; i++) {
        const a = performance.now();
        if (frames) feedPlayback(1 / 60);
        view.frame(1000 / 60);
        const b = performance.now();
        view.app.renderer.render(view.app.stage);
        gl?.finish();
        tm.push(performance.now() - a, b - a);
      }
      const s = tm.stats();
      return { ...s, renderMs: s.avgMs - s.updateMs, units: view.samples.length, particles: view.fx.activeParticles, tracers: view.fx.activeTracers };
    },
    zoom: (z: number) => view.cam.setZoom(z),
    look: (l: Look) => { view.units.look = l; },
    cam: () => view.camera(),
    jump: (x: number, y: number) => { view.cam.follow = null; view.cam.jumpTo(x, y); },
    terrain: () => ({ tier: view.terrain.tier, bakes: view.terrain.bakeCount }),
    sound: async (on = true) => { if (on) await view.audio.enable(); else await view.audio.disable(); },
    soundStats: () => ({ on: view.audio.on, played: view.audio.played, active: view.audio.limiter.active, merged: view.audio.limiter.merged, dropped: view.audio.limiter.dropped }),
  };
}

main();
