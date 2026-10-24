// 戰術地圖實驗頁（S0 技術驗證、S1 播放時鐘／個體軌跡／相機）。
//   ?mode=stress&n=120  假戰場壓力測試（不需要伺服器）
//   ?mode=fixture       播 scripts/tac_fixture.py 產生的回放（web/tac/fixtures/replay.json，只在 npm run dev 有）
//   ?replay=<id>        向伺服器要一場回放（要先登入，例如 web_demo 的 ?token= 連結）
// 播回放的方式：
//   預設「假即時」：用 Feed 照伺服器的節奏把影格交給播放時鐘，可以加 &jitter=200（到達時間抖動 ms）、
//     &scale=0.8（伺服器變慢）、&outage=10,5（第 10 秒起斷線 5 秒），量時鐘在這些情況下的表現。
//   &play=replay：回放模式（照播放速度 &speed=、不用緩衝）。
//   &hot=1：從開火最多的地方開始；&follow=1：鏡頭跟隨我方。
//   &theme=slate|green：色調（預設灰藍）；&look=auto|symbol|mech：個體外觀（預設依縮放）。
// window.__tac.stats() 給量測腳本讀。

import { Application, Container } from "pixi.js";
import { Camera } from "../camera";
import { BattleClock } from "../clock";
import { Feed } from "../feed";
import { checkFrame, checkTerrain, type Fixture, type Frame, type FrameUnit, type WorldTerrain } from "../protocol";
import { TerrainLayer } from "../render/terrain";
import { UnitLayer, type Look } from "../render/units";
import { makeUnitTextures } from "../render/unitTextures";
import { FrameTimer } from "../stats";
import { makeTerrain, StressScene, walkable } from "../stress";
import { cssHex, getTheme, THEMES } from "../theme";
import { SNAP_DIST, Tracks, type UnitSample } from "../tracks";
import { SoundEngine, type Listener, type SoundReq } from "../audio";
import { FxLayer, makeFxTextures } from "../render/fx";
import { IntentLayer } from "../render/intent";
import { VisionLayer } from "../render/vision";

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

/** 壓力測試的個體 → 和回放一樣的樣本格式（給同一個個體圖層畫） */
function stressSample(u: import("../stress").StressUnit, h: number): UnitSample {
  const f: FrameUnit = { id: u.id, side: u.side, name: u.id, pos: [Math.floor(u.x), Math.floor(u.y)], posture: u.posture, facing: [1, 0],
    down: u.down, weapon: ["rifle", "lmg", "dmr", "shotgun"][+u.id.slice(1) % 4] };
  return { id: u.id, side: u.side, name: u.id, x: u.x, y: h - u.y, z: 0, facing: u.facing, posture: u.posture, postureFrom: u.posture, postureK: 1,
    down: u.down, downK: u.down ? 1 : 0, dead: false, alpha: 1, aim: u.cool < 0.4 && u.target && !u.down ? 1 - u.cool / 0.4 : null, moving: Math.hypot(u.tx - u.x, u.ty - u.y) > 0.05, u: f };
}

async function main() {
  const theme = getTheme(q.get("theme"));
  document.body.style.background = cssHex(theme.bg);
  const app = new Application();
  try {
    // ?dpr=1.5：模擬高解析度螢幕（量效能用）
    await app.init({ resizeTo: stage, resolution: +(q.get("dpr") || 0) || window.devicePixelRatio || 1, autoDensity: true, antialias: true,
      background: cssHex(theme.bg), preference: "webgl" });
  } catch (e) {
    hud.textContent = `無法啟動 WebGL：${e}`;
    return;
  }
  stage.appendChild(app.canvas);
  const src = await loadSource();
  const T = src.terrain;
  const tProblems = checkTerrain(T);
  const fProblems = src.frames ? src.frames.slice(0, 50).flatMap(checkFrame) : [];
  const world = new Container();
  app.stage.addChild(world);
  const t0 = performance.now();
  const terrain = new TerrainLayer(app.renderer, T, theme);
  const bakeMs = performance.now() - t0;
  const fx = new FxLayer(makeFxTextures(app.renderer));
  const units = new UnitLayer(makeUnitTextures(app.renderer), theme);
  units.look = (q.get("look") as Look) || "auto";
  const vision = new VisionLayer(app.renderer, T.w, T.h);
  const intents = new IntentLayer(theme, T);
  // 由下往上：地面與牆 → 格線與格子名稱 → 小隊意圖（路線、圈） → 地上特效 → 個體 → 樹冠 → 煙 → 粒子、曳光與飛行物
  // → 視野遮罩（視野外調暗） → 個體標籤 → 小隊標籤（格線放在地面層，煙才蓋得住；標籤在最上面、不被調暗）
  world.addChild(terrain.base, terrain.overlay, intents.ground, fx.ground, units.root, terrain.canopy, fx.smoke, fx.air,
    vision.root, units.labels, intents.labels);
  const audio = new SoundEngine();
  const listener = (): Listener => ({ x: cam.x, y: cam.y, halfWidth: app.screen.width / 2 / cam.zoom });
  const sound = (r: SoundReq) => audio.play(r, listener());
  // 瀏覽器規定要先互動一次才能出聲：之前開過聲音的話，第一次點擊或按鍵時自動打開
  const wake = () => { if (audio.wanted && !audio.on) void audio.enable(); };
  window.addEventListener("pointerdown", wake);
  window.addEventListener("keydown", wake);
  const sy = (y: number) => T.h - y;
  const walk = (x: number, y: number) => walkable(T, x, y);
  const now = () => performance.now() / 1000;
  let drawn = 0;

  // ---------------------------------------------------------------- 鏡頭
  const cam = new Camera();
  cam.jumpTo(T.w / 2, T.h / 2);
  let drag: { x: number; y: number } | null = null;
  app.canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; });
  window.addEventListener("pointerup", () => { drag = null; });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    cam.pan(e.clientX - drag.x, e.clientY - drag.y);
    drag = { x: e.clientX, y: e.clientY };
  });
  app.canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = app.canvas.getBoundingClientRect();
    cam.zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.2 : 1 / 1.2);
  }, { passive: false });
  function applyCam(dtS: number) {
    cam.setViewport(app.screen.width, app.screen.height);
    cam.update(dtS);
    world.scale.set(cam.zoom);
    const [ox, oy] = cam.origin();
    world.x = ox + Math.round(fx.shake[0] * cam.zoom);
    world.y = oy + Math.round(fx.shake[1] * cam.zoom);
    const [x0, y0] = cam.toWorld(0, 0), [x1, y1] = cam.toWorld(app.screen.width, app.screen.height);
    terrain.update([x0, y0, x1, y1], cam.zoom, app.renderer.resolution);
  }

  // ---------------------------------------------------------------- 資料來源
  let n = +(q.get("n") || 120);
  let scene: StressScene | null = null;
  let frames = src.frames;
  const replayMode = q.get("play") === "replay";
  let speed = +(q.get("speed") || 1);
  const jitter = +(q.get("jitter") || 0) / 1000, scale = +(q.get("scale") || 1);
  const outage = q.get("outage") ? (q.get("outage")!.split(",").map(Number) as [number, number]) : null;
  if (frames && frames.length && q.get("hot")) {     // 從開火最多的那一格前 5 秒開始
    let best = 0;
    frames.forEach((f, i) => { if (f.shots.length > frames![best].shots.length) best = i; });
    frames = frames.slice(Math.max(0, best - 10));
  }
  let clock = new BattleClock(), tracks = new Tracks(walk), feed: Feed | null = null;
  let wall = 0, prevBt: number | null = null, fi = 0, lastJumps = 0;
  let ratio0: [number, number] | null = null;
  const lastPos = new Map<string, [number, number]>();
  const jumpLog: [number, number][] = [];             // [實際秒, 這一幀最大位移（格）]
  const bigJumps: { id: string; bt: number; d: number; from: [number, number]; to: [number, number] }[] = [];   // 單幀位移 > 0.2 格（除錯用）
  let maxJump = 0;

  function resetPlayback() {
    clock = new BattleClock();
    tracks = new Tracks(walk);
    wall = 0; prevBt = null; fi = 0; lastJumps = 0; ratio0 = null; maxJump = 0;
    lastPos.clear(); jumpLog.length = 0; bigJumps.length = 0;
    fx.setSmokes([]);
    vision.clear();
    if (!frames) return;
    if (replayMode) clock.startReplay(frames[0].ts0 ?? 0, speed);
    else feed = new Feed(frames, { jitter, timeScale: scale, outage, seed: 1 });
  }
  function resetStress() {
    scene = new StressScene(T, n, 1, [T.w / 2, T.h / 2]);
    fx.setSmokes(Array.from({ length: 6 }, (_, k) => ({ x: T.w / 2 + (k - 2.5) * 6, y: sy(T.h / 2 + ((k % 3) - 1) * 10), r: 4, density: 0.9 })));
    cam.jumpTo(T.w / 2, sy(T.h / 2));
  }
  if (frames && frames.length) {
    resetPlayback();
    const f0 = frames[Math.min(frames.length - 1, q.get("hot") ? 10 : Math.floor(frames.length / 2))];
    const live = f0.units.filter((u) => !u.dead);
    if (live.length) cam.jumpTo(live.reduce((a, u) => a + u.pos[0], 0) / live.length + 0.5, sy(live.reduce((a, u) => a + u.pos[1], 0) / live.length + 0.5));
  } else {
    resetStress();
  }
  let samples: UnitSample[] = [];
  const followOwn = () => {
    const own = samples.filter((u) => u.side === "own" && !u.down && u.alpha > 0.5);
    if (!own.length) return null;
    return [own.reduce((a, u) => a + u.x, 0) / own.length, sy(own.reduce((a, u) => a + u.y, 0) / own.length)] as [number, number];
  };
  if (q.get("follow")) cam.follow = followOwn;

  const hears: { ts: number; at: [number, number] }[] = [];   // 最近聽到的槍聲（看不到的射手的聲音位置）
  const wasDown = new Set<string>();
  /** 爆點到最近我方的距離（格，畫面震動用） */
  const ownDist = (x: number, y: number) => samples.reduce((m, u) => u.side === "own" && !u.down ? Math.min(m, Math.hypot(u.x - x, sy(u.y) - y)) : m, Infinity);
  function stepPlayback(dtS: number) {
    if (!frames || !frames.length) return;
    wall += dtS;
    if (replayMode) {
      while (fi < frames.length && (frames[fi].ts0 ?? 0) <= (clock.bt ?? 0) + 2) tracks.ingest(frames[fi++], clock.bt);
    } else {
      for (const { frame, at } of feed!.take(wall)) {
        clock.push(frame.ts0 ?? 0, frame.ts1 ?? 0, at);
        tracks.ingest(frame, clock.bt);
      }
    }
    const bt = clock.tick(dtS);
    if (bt === null) return;
    if (clock.primed && !ratio0) ratio0 = [wall, bt];
    const endT = frames[frames.length - 1].ts1 ?? 0;
    if (bt >= endT && (replayMode || feed!.done)) { resetPlayback(); return; }
    // 這一刻的影格（煙霧、最後已知位置）
    let cur = frames[0];
    for (const f of frames) { if ((f.ts0 ?? 0) > bt) break; cur = f; }
    fx.setSmokes(cur.smokes.map((sm) => ({ x: sm.x, y: sy(sm.y), r: sm.r, density: sm.density })));
    // 特效：時鐘跳過去（斷線、落後）時不補播
    const jumped = clock.jumps !== lastJumps;
    lastJumps = clock.jumps;
    if (prevBt !== null && !jumped && bt - prevBt < 1) {
      for (const e of tracks.fxBetween(prevBt, bt)) {
        if (e.kind === "hear") {
          hears.push({ ts: e.ts, at: [e.at[0] + 0.5, sy(e.at[1] + 0.5)] });
          while (hears.length > 20) hears.shift();
        } else if (e.kind === "shot") {
          const from: [number, number] = [e.from[0], sy(e.from[1])], to: [number, number] = [e.to[0], sy(e.to[1])];
          fx.shot(from, to, e, bt);
          if (e.shooter) units.shot(e.shooter, now());
          // 看不到的射手：聲音從「聽到槍聲」的誤差位置來，沒有的話從看得到的那段彈道起點
          const h = e.unseen ? hears.filter((x) => Math.abs(x.ts - e.ts) < 1).pop() : null;
          const at = h ? h.at : from;
          sound({ kind: "gun", x: at[0], y: at[1], weapon: e.unseen ? undefined : e.weapon ?? undefined, shooter: e.shooter ?? (h ? `hear${h.ts}` : null) });
          if (e.mat && e.mat !== "air") sound({ kind: "impact", x: to[0], y: to[1], mat: { wall: "hard", low: "hard", rubble: "hard", ground: "dirt", body: "flesh" }[e.mat] ?? "soft" });
        } else if (e.kind === "blast") {
          const at: [number, number] = [e.at[0], sy(e.at[1])];
          const flash = e.item === "flashes";
          fx.blast(at, flash, ownDist(at[0], at[1]));
          sound({ kind: flash ? "flash" : "boom", x: at[0], y: at[1] });
        } else {
          fx.throwArc([e.from[0], sy(e.from[1])], [e.to[0], sy(e.to[1])], Math.max(0.2, e.land - e.ts));
        }
      }
    }
    prevBt = bt;
    samples = tracks.sample(bt);
    for (const u of samples) {                                   // 我方有人倒地：警示音
      if (u.side === "own" && u.down && !wasDown.has(u.id)) { wasDown.add(u.id); sound({ kind: "alert" }); }
      else if (!u.down) wasDown.delete(u.id);
    }
    for (const id of [...lastPos.keys()]) if (!samples.some((u) => u.id === id)) lastPos.delete(id);   // 上一幀看不到的人不算位移
    let worst = 0;
    for (const u of samples) {
      const p = lastPos.get(u.id);
      if (p && !jumped) {
        const d = Math.hypot(u.x - p[0], u.y - p[1]);
        if (d <= SNAP_DIST) worst = Math.max(worst, d);           // 超過 3 格是伺服器資料本身的瞬移（重生、整批換位置）
        if (d > 0.2 && bigJumps.length < 200) bigJumps.push({ id: u.id, bt, d, from: p, to: [u.x, u.y] });
      }
      lastPos.set(u.id, [u.x, u.y]);
    }
    const screen = samples.map((u) => ({ ...u, y: sy(u.y) }));
    drawn = units.update(screen,
      (cur.ghosts || []).map((g) => ({ id: g.id, x: g.pos[0] + 0.5, y: sy(g.pos[1] + 0.5), age: g.age })), cam.zoom, app.renderer.resolution, now());
    // 視野（最近一份）與小隊意圖（這一格的）
    let vf: Frame | null = null;
    for (const f of frames) { if ((f.ts0 ?? 0) > bt) break; if (f.vision) vf = f; }
    if (vf) {
      // 每個人的視野以他在那一格影格的位置為中心（近距離完全看得清楚、遠處只看得到一部分）
      const at = new Map(vf.units.map((u) => [u.id, [u.pos[0] + 0.5, sy(u.pos[1] + 0.5)] as [number, number]]));
      vision.set(Object.entries(vf.vision!).filter(([id]) => at.has(id))
        .map(([id, p]) => ({ c: at.get(id)!, pts: p.map(([x, y]) => [x, sy(y)] as [number, number]) })));
    }
    intents.update(cur.intents || {}, screen, sy, cam.zoom, app.renderer.resolution, cur.world_tick ?? 0);
    jumpLog.push([wall, worst]);
    while (jumpLog.length && jumpLog[0][0] < wall - 5) jumpLog.shift();
    maxJump = Math.max(maxJump, worst);
  }

  function stepStress(dtS: number) {
    if (!scene) return;
    for (const ev of scene.step(dtS)) {
      if (ev.kind === "shot") {
        const mat = ev.hit ? "body" : (["wall", "ground", "low", "rubble", "air"] as const)[Math.floor(Math.random() * 5)];
        fx.shot([ev.from[0], sy(ev.from[1])], [ev.to[0], sy(ev.to[1])], { mat, hit: ev.hit, shooter: ev.shooter }, scene.t);
        units.shot(ev.shooter, now());
        sound({ kind: "gun", x: ev.from[0], y: sy(ev.from[1]), weapon: ["rifle", "lmg", "dmr", "shotgun"][+ev.shooter.slice(1) % 4], shooter: ev.shooter });
      } else if (ev.kind === "throw") {
        fx.throwArc([ev.from[0], sy(ev.from[1])], [ev.to[0], sy(ev.to[1])], ev.land - ev.t);
      } else {
        fx.blast([ev.at[0], sy(ev.at[1])], false, ownDist(ev.at[0], sy(ev.at[1])));
        sound({ kind: "boom", x: ev.at[0], y: sy(ev.at[1]) });
      }
    }
    samples = scene.units.map((u) => stressSample(u, T.h));
    drawn = units.update(samples.map((u) => ({ ...u, y: sy(u.y) })), [], cam.zoom, app.renderer.resolution, now());
  }

  // ---------------------------------------------------------------- 控制列
  const ctl = document.getElementById("ctl")!;
  ctl.innerHTML = `
    <button data-k="tracers" class="on">曳光</button><button data-k="particles" class="on">粒子</button>
    <button data-k="smoke" class="on">煙霧</button><button data-k="shake" class="on">震動</button><button id="vis" class="on">視野</button><button id="int" class="on">意圖</button><button id="snd">🔇 聲音</button>
    ${frames ? `<button id="fol" class="${cam.follow ? "on" : ""}">🎯 跟隨</button>` : ""}
    ${frames && replayMode ? `<label>速度 <select id="spd">${[0.25, 0.5, 1, 2, 4].map((v) => `<option ${v === speed ? "selected" : ""}>${v}</option>`).join("")}</select></label>` : ""}
    ${frames ? "" : `<label>人數 <select id="num">${[40, 80, 120, 200, 300].map((v) => `<option ${v === n ? "selected" : ""}>${v}</option>`).join("")}</select></label>`}
    <label>色調 <select id="thm">${Object.values(THEMES).map((t) => `<option value="${t.name}" ${t.name === theme.name ? "selected" : ""}>${t.label}</option>`).join("")}</select></label>
    <label>外觀 <select id="look">${[["auto", "自動"], ["symbol", "符號"], ["mech", "機兵"]].map(([v, l]) => `<option value="${v}" ${v === units.look ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <button id="rst">重設統計</button>`;
  for (const b of ctl.querySelectorAll<HTMLButtonElement>("button[data-k]")) {
    b.onclick = () => {
      const k = b.dataset.k as keyof FxLayer["enabled"];
      fx.enabled[k] = !fx.enabled[k];
      b.classList.toggle("on", fx.enabled[k]);
    };
  }
  const snd = document.getElementById("snd") as HTMLButtonElement;
  snd.onclick = async () => {
    if (audio.on) await audio.disable(); else await audio.enable();
    snd.textContent = audio.on ? "🔊 聲音" : "🔇 聲音";
    snd.classList.toggle("on", audio.on);
  };
  const vis = document.getElementById("vis") as HTMLButtonElement, intB = document.getElementById("int") as HTMLButtonElement;
  vis.onclick = () => { vision.enabled = !vision.enabled; vis.classList.toggle("on", vision.enabled); };
  intB.onclick = () => { intents.enabled = !intents.enabled; intB.classList.toggle("on", intents.enabled); };
  const thm = document.getElementById("thm") as HTMLSelectElement;
  thm.onchange = () => { const u = new URL(location.href); u.searchParams.set("theme", thm.value); location.href = u.toString(); };   // 地形要重烘，直接重新載入
  const lk = document.getElementById("look") as HTMLSelectElement;
  lk.onchange = () => { units.look = lk.value as Look; };
  const fol = document.getElementById("fol") as HTMLButtonElement | null;
  if (fol) fol.onclick = () => { cam.follow = cam.follow ? null : followOwn; };
  const timer = new FrameTimer(300);
  const num = document.getElementById("num") as HTMLSelectElement | null;
  if (num) num.onchange = () => { n = +num.value; resetStress(); timer.reset(); };
  const spd = document.getElementById("spd") as HTMLSelectElement | null;
  if (spd) spd.onchange = () => { speed = +spd.value; clock.speed = speed; };
  (document.getElementById("rst") as HTMLButtonElement).onclick = () => { timer.reset(); maxJump = 0; };

  // ---------------------------------------------------------------- 每幀
  const playStats = () => {
    const c = clock.state();
    const ratio = ratio0 && wall - ratio0[0] > 1 ? (c.bt - ratio0[1]) / (wall - ratio0[0]) : null;
    return { ...c, ratio, maxJump, jump5s: jumpLog.reduce((m, [, j]) => Math.max(m, j), 0), wall };
  };
  let lastHud = 0;
  app.ticker.add((tk) => {
    const dtS = Math.min(0.1, tk.deltaMS / 1000);
    const u0 = performance.now();
    if (frames && frames.length) stepPlayback(dtS); else stepStress(dtS);
    fx.update(dtS, u0);
    vision.update(dtS);
    applyCam(dtS);
    if (fol) fol.classList.toggle("on", !!cam.follow);
    timer.push(tk.deltaMS, performance.now() - u0);
    if (u0 - lastHud > 250) {
      lastHud = u0;
      const s = timer.stats();
      const p = frames ? playStats() : null;
      hud.innerHTML = `<b>${src.label}</b>｜${app.renderer.name}｜DPR ${app.renderer.resolution}｜${app.screen.width}×${app.screen.height}<br>
        ${s.fps.toFixed(1)} fps｜平均 ${s.avgMs.toFixed(1)} ms｜p95 ${s.p95Ms.toFixed(1)}｜最大 ${s.maxMs.toFixed(1)}｜&gt;33ms ${s.slow}/${s.frames}｜JS 更新 ${s.updateMs.toFixed(2)} ms<br>
        個體 ${drawn}｜粒子 ${fx.activeParticles}｜曳光 ${fx.activeTracers}｜聲音 ${audio.limiter.active}（播 ${audio.played}、合併 ${audio.limiter.merged}、丟 ${audio.limiter.dropped}）｜縮放 ${cam.zoom.toFixed(1)} px/格｜地形烘焙 ${bakeMs.toFixed(0)} ms（級距 ${terrain.tier}、共 ${terrain.bakeCount} 塊）
        ${p ? `<br>${replayMode ? "回放" : `假即時（抖動 ±${(jitter * 1000).toFixed(0)} ms、伺服器 ×${scale}${outage ? `、斷線 ${outage[0]}s 起 ${outage[1]}s` : ""}）`}｜戰鬥 ${p.bt.toFixed(1)} s｜速度 ×${p.rate.toFixed(3)}｜緩衝 ${p.buffer.toFixed(2)} s（平滑 ${p.smooth.toFixed(2)}）｜伺服器 ×${p.serverRate.toFixed(2)}
          ｜播放／戰鬥 ${p.ratio === null ? "—" : p.ratio.toFixed(3)}｜卡住 ${p.stalls}、跳過 ${p.jumps}｜單幀最大位移 ${p.jump5s.toFixed(3)} 格（全程 ${p.maxJump.toFixed(3)}）` : ""}
        ${hud.dataset.warn ? `<br><span class="warn">${hud.dataset.warn}</span>` : ""}
        ${tProblems.length || fProblems.length ? `<br><span class="warn">資料問題：${[...tProblems, ...fProblems].slice(0, 5).join("；")}</span>` : ""}`;
    }
  });

  (window as unknown as { __tac: unknown }).__tac = {
    stats: () => ({ ...timer.stats(), units: drawn, particles: fx.activeParticles, tracers: fx.activeTracers,
      voices: audio.limiter.active, renderer: app.renderer.name, dpr: app.renderer.resolution, w: app.screen.width, h: app.screen.height, bakeMs,
      play: frames ? playStats() : null }),
    reset: () => { timer.reset(); maxJump = 0; },
    jumps: () => bigJumps,
    /** 不等畫面更新，連續算＋畫 count 幀（每幀 1/60 秒）：量每幀的 JS 成本 */
    bench: (count = 120) => {
      const gl = (app.renderer as unknown as { gl?: WebGLRenderingContext }).gl;
      const tm = new FrameTimer(count);
      for (let i = 0; i < count; i++) {
        const a = performance.now();
        if (frames && frames.length) stepPlayback(1 / 60); else stepStress(1 / 60);
        fx.update(1 / 60, a);
        applyCam(1 / 60);
        const b = performance.now();
        app.renderer.render(app.stage);
        gl?.finish();
        tm.push(performance.now() - a, b - a);
      }
      const s = tm.stats();
      return { ...s, renderMs: s.avgMs - s.updateMs, units: drawn, particles: fx.activeParticles, tracers: fx.activeTracers };
    },
    zoom: (z: number) => cam.setZoom(z),
    sound: async (on = true) => { if (on) await audio.enable(); else await audio.disable(); },
    soundStats: () => ({ on: audio.on, played: audio.played, active: audio.limiter.active, merged: audio.limiter.merged, dropped: audio.limiter.dropped }),
    look: (l: Look) => { units.look = l; },
    cam: () => ({ x: cam.x, y: cam.y, zoom: cam.zoom }),
    jump: (x: number, y: number) => { cam.follow = null; cam.jumpTo(x, y); },
    terrain: () => ({ tier: terrain.tier, bakes: terrain.bakeCount }),
  };
}

main();
