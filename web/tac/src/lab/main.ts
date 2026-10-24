// S0 實驗頁：驗證 PixiJS 在本專案的資料量下跑得動、看得清楚。
//   ?mode=stress&n=120  假戰場壓力測試（不需要伺服器）
//   ?mode=fixture       播 scripts/tac_fixture.py 產生的回放（web/tac/fixtures/replay.json，只在 npm run dev 有）
//   ?replay=<id>        向伺服器要一場回放（要先登入，例如 web_demo 的 ?token= 連結）
// 播放用的是簡化時鐘（每格影格照它的戰鬥時間播）；正式的播放時鐘、軌跡、相機在 S1。
// window.__tac.stats() 給量測腳本讀。

import { Application, Container, Sprite } from "pixi.js";
import { checkFrame, checkTerrain, type Fixture, type Frame, type FrameUnit, type WorldTerrain, type XY } from "../protocol";
import { FrameTimer } from "../stats";
import { makeTerrain, StressScene } from "../stress";
import { LabAudio } from "./audio";
import { Fx } from "./fx";
import { buildTerrain } from "./terrain";
import { makeTextures, SIDE_COLOR, type LabTextures } from "./textures";

const q = new URLSearchParams(location.search);
const hud = document.getElementById("hud")!;
const stage = document.getElementById("stage")!;

interface Cam { x: number; y: number; zoom: number; tz: number; ax: number; ay: number }

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

class UnitLayer {
  readonly root = new Container();
  private sprites = new Map<string, Sprite>();
  private seen = new Set<string>();
  constructor(private tex: LabTextures) {}

  begin() { this.seen.clear(); }

  put(id: string, side: string, x: number, y: number, facing: number, posture: string, down: boolean, ghost = false) {
    let s = this.sprites.get(id);
    if (!s) {
      s = new Sprite(this.tex.unit.own);
      s.anchor.set(0.5);
      this.root.addChild(s);
      this.sprites.set(id, s);
    }
    this.seen.add(id);
    s.visible = true;
    s.texture = ghost ? this.tex.ghost : down ? this.tex.downed : this.tex.unit[(side in this.tex.unit ? side : "own") as keyof LabTextures["unit"]];
    s.tint = SIDE_COLOR[side] ?? 0xcccccc;
    s.alpha = ghost ? 0.55 : down ? 0.8 : 1;
    s.x = x; s.y = y;
    s.rotation = -facing;
    const k = posture === "prone" ? 0.8 : posture === "crouch" ? 0.9 : 1;
    s.scale.set((1.3 * k) / 64, (1.3 * (posture === "prone" ? 0.7 : 1)) / 64);
  }

  end() { for (const [id, s] of this.sprites) if (!this.seen.has(id)) s.visible = false; }
  get count(): number { return this.seen.size; }
}

/** 影格裡個體在戰鬥時間 bt 的位置（簡化版：有抵達時間就照時間，否則整格均分） */
function unitAt(u: FrameUnit, f: Frame, bt: number): XY {
  const pts: XY[] = [u.from || u.pos, ...(u.path || [])].map((p) => [p[0], p[1]] as XY);
  if (pts.length === 1) return pts[0];
  const ts0 = f.ts0 ?? 0, ts1 = f.ts1 ?? ts0 + 0.5;
  for (let i = 1; i < pts.length; i++) {
    const t1 = u.pt ? u.pt[i - 1] : ts0 + ((ts1 - ts0) * i) / (pts.length - 1);
    const t0 = u.pst ? Math.min(u.pst[i - 1], t1) : u.pt ? (i === 1 ? ts0 : u.pt[i - 2]) : ts0 + ((ts1 - ts0) * (i - 1)) / (pts.length - 1);
    if (bt < t0) return pts[i - 1];
    if (bt < t1) {
      const k = (bt - t0) / Math.max(1e-6, t1 - t0);
      return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k];
    }
  }
  return pts[pts.length - 1];
}

async function main() {
  const app = new Application();
  try {
    // ?dpr=1.5：模擬高解析度螢幕（量效能用）
    await app.init({ resizeTo: stage, resolution: +(q.get("dpr") || 0) || window.devicePixelRatio || 1, autoDensity: true, antialias: true,
      background: "#070a08", preference: "webgl" });
  } catch (e) {
    hud.textContent = `無法啟動 WebGL：${e}`;
    return;
  }
  stage.appendChild(app.canvas);
  const src = await loadSource();
  const T = src.terrain;
  const tProblems = checkTerrain(T);
  const fProblems = src.frames ? src.frames.slice(0, 50).flatMap(checkFrame) : [];
  const tex = makeTextures(app.renderer);
  const world = new Container();
  app.stage.addChild(world);
  const t0 = performance.now();
  world.addChild(buildTerrain(app.renderer, T));
  const bakeMs = performance.now() - t0;
  const fx = new Fx(tex);
  const units = new UnitLayer(tex);
  world.addChild(fx.ground, units.root, fx.smoke, fx.particles, fx.air);
  const audio = new LabAudio();
  const sy = (y: number) => T.h - y;

  // ---------------------------------------------------------------- 鏡頭（S0 簡化：以游標為中心縮放、平滑）
  const cam: Cam = { x: T.w / 2, y: T.h / 2, zoom: 14, tz: 14, ax: 0, ay: 0 };
  let drag: { x: number; y: number } | null = null;
  app.canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; });
  window.addEventListener("pointerup", () => { drag = null; });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    cam.x -= (e.clientX - drag.x) / cam.zoom;
    cam.y -= (e.clientY - drag.y) / cam.zoom;
    drag = { x: e.clientX, y: e.clientY };
  });
  app.canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = app.canvas.getBoundingClientRect();
    cam.ax = e.clientX - r.left; cam.ay = e.clientY - r.top;
    cam.tz = Math.max(3, Math.min(40, cam.tz * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
  }, { passive: false });

  function applyCam(dtS: number) {
    const W = app.screen.width, H = app.screen.height;
    if (Math.abs(cam.tz - cam.zoom) > 1e-3) {
      // 游標下的世界點縮放前後不動
      const wx = cam.x + (cam.ax - W / 2) / cam.zoom, wy = cam.y + (cam.ay - H / 2) / cam.zoom;
      cam.zoom += (cam.tz - cam.zoom) * (1 - Math.exp(-dtS / 0.12));
      cam.x = wx - (cam.ax - W / 2) / cam.zoom;
      cam.y = wy - (cam.ay - H / 2) / cam.zoom;
    }
    world.scale.set(cam.zoom);
    world.x = Math.round(W / 2 - cam.x * cam.zoom);
    world.y = Math.round(H / 2 - cam.y * cam.zoom);
  }

  // ---------------------------------------------------------------- 資料來源
  let n = +(q.get("n") || 120);
  let scene: StressScene | null = null;
  const frames = src.frames;
  let fi = 0, bt = 0, speed = +(q.get("speed") || 1);
  function resetStress() {
    scene = new StressScene(T, n, 1, [T.w / 2, T.h / 2]);
    fx.clearSmoke();
    for (let k = 0; k < 6; k++) fx.addSmoke(T.w / 2 + (k - 2.5) * 6, sy(T.h / 2 + ((k % 3) - 1) * 10), 4);
    cam.x = T.w / 2; cam.y = sy(T.h / 2);
  }
  if (frames && frames.length) {
    bt = frames[0].ts0 ?? 0;
    if (q.get("hot")) {                          // ?hot=1：從開火最多的那一格前 2 秒開始（量測用）
      let best = 0;
      frames.forEach((f, i) => { if (f.shots.length > frames[best].shots.length) best = i; });
      fi = Math.max(0, best - 4);
      bt = frames[fi].ts0 ?? bt;
    }
    const f0 = frames[q.get("hot") ? fi : Math.floor(frames.length / 2)];
    const live = f0.units.filter((u) => !u.dead);
    if (live.length) {
      cam.x = live.reduce((a, u) => a + u.pos[0], 0) / live.length;
      cam.y = sy(live.reduce((a, u) => a + u.pos[1], 0) / live.length);
    }
  } else {
    resetStress();
  }

  let smokeSig = "";
  function stepReplay(dtS: number) {
    if (!frames || !frames.length) return;
    bt += dtS * speed;
    while (fi < frames.length - 1 && bt >= (frames[fi].ts1 ?? bt)) fi++;
    if (fi >= frames.length - 1 && bt >= (frames[fi].ts1 ?? bt) + 2) { fi = 0; bt = frames[0].ts0 ?? 0; }
    const f = frames[fi];
    const sig = f.smokes.map((sm) => `${sm.x},${sm.y}`).join(";");
    if (sig !== smokeSig) {                     // 煙霧換了才重建（S0 簡化；正式版會淡入淡出）
      smokeSig = sig;
      fx.clearSmoke();
      for (const sm of f.smokes) fx.addSmoke(sm.x, sy(sm.y), sm.r);
    }
    for (const sh of f.shots) {
      const ts = sh.ts ?? f.ts0 ?? 0;
      if (ts > bt - dtS * speed && ts <= bt) {
        fx.shot([sh.from[0], sy(sh.from[1])], [sh.to[0], sy(sh.to[1])], !!sh.hit, sh.unseen ? 0xff7060 : sh.ow ? 0x5adce8 : 0xffe696);
        audio.play("gun", sh.from[0] - cam.x, sy(sh.from[1]) - cam.y, sh.shooter || "");
      }
    }
    for (const bl of f.blasts) {
      const ts = bl.ts ?? f.ts0 ?? 0;
      if (ts > bt - dtS * speed && ts <= bt) {
        fx.blast([bl.at[0], sy(bl.at[1])], bl.item === "flashes");
        audio.play("boom", bl.at[0] - cam.x, sy(bl.at[1]) - cam.y);
      }
    }
    units.begin();
    for (const u of f.units) {
      if (u.dead || u.exited) continue;
      const [x, y] = unitAt(u, f, bt);
      const face = Math.atan2(u.facing[1], u.facing[0]);
      units.put(u.id, u.side, x + 0.5, sy(y + 0.5), face, u.posture, u.down);
    }
    for (const g of f.ghosts || []) units.put(`g:${g.id}`, "enemy", g.pos[0] + 0.5, sy(g.pos[1] + 0.5), 0, "stand", false, true);
    units.end();
  }

  function stepStress(dtS: number) {
    if (!scene) return;
    for (const ev of scene.step(dtS)) {
      if (ev.kind === "shot") {
        fx.shot([ev.from[0], sy(ev.from[1])], [ev.to[0], sy(ev.to[1])], ev.hit);
        audio.play("gun", ev.from[0] - cam.x, sy(ev.from[1]) - cam.y, ev.shooter);
      } else if (ev.kind === "throw") {
        fx.throwArc([ev.from[0], sy(ev.from[1])], [ev.to[0], sy(ev.to[1])], ev.land - ev.t);
      } else {
        fx.blast([ev.at[0], sy(ev.at[1])]);
        audio.play("boom", ev.at[0] - cam.x, sy(ev.at[1]) - cam.y);
      }
    }
    units.begin();
    for (const u of scene.units) units.put(u.id, u.side, u.x, sy(u.y), u.facing, u.posture, u.down);
    units.end();
  }

  // ---------------------------------------------------------------- 控制列
  const ctl = document.getElementById("ctl")!;
  ctl.innerHTML = `
    <button data-k="tracers" class="on">曳光</button><button data-k="particles" class="on">粒子</button>
    <button data-k="smoke" class="on">煙霧</button><button id="snd">🔇 聲音</button>
    ${frames ? `<label>速度 <select id="spd">${[0.5, 1, 2, 4].map((v) => `<option ${v === speed ? "selected" : ""}>${v}</option>`).join("")}</select></label>`
    : `<label>人數 <select id="num">${[40, 80, 120, 200, 300].map((v) => `<option ${v === n ? "selected" : ""}>${v}</option>`).join("")}</select></label>`}
    <button id="rst">重設統計</button>`;
  for (const b of ctl.querySelectorAll<HTMLButtonElement>("button[data-k]")) {
    b.onclick = () => {
      const k = b.dataset.k as keyof Fx["enabled"];
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
  const timer = new FrameTimer(300);
  const num = document.getElementById("num") as HTMLSelectElement | null;
  if (num) num.onchange = () => { n = +num.value; resetStress(); timer.reset(); };
  const spd = document.getElementById("spd") as HTMLSelectElement | null;
  if (spd) spd.onchange = () => { speed = +spd.value; };
  (document.getElementById("rst") as HTMLButtonElement).onclick = () => timer.reset();

  // ---------------------------------------------------------------- 每幀
  let lastHud = 0;
  app.ticker.add((tk) => {
    const dtS = Math.min(0.1, tk.deltaMS / 1000);
    const u0 = performance.now();
    if (frames && frames.length) stepReplay(dtS); else stepStress(dtS);
    fx.update(dtS, u0);
    applyCam(dtS);
    timer.push(tk.deltaMS, performance.now() - u0);
    if (u0 - lastHud > 250) {
      lastHud = u0;
      const s = timer.stats();
      hud.innerHTML = `<b>${src.label}</b>｜${app.renderer.name}｜DPR ${window.devicePixelRatio}｜${app.screen.width}×${app.screen.height}<br>
        ${s.fps.toFixed(1)} fps｜平均 ${s.avgMs.toFixed(1)} ms｜p95 ${s.p95Ms.toFixed(1)}｜最大 ${s.maxMs.toFixed(1)}｜&gt;33ms ${s.slow}/${s.frames}｜JS 更新 ${s.updateMs.toFixed(2)} ms<br>
        個體 ${units.count}｜粒子 ${fx.activeParticles}｜曳光 ${fx.activeTracers}｜聲音 ${audio.active}（播 ${audio.played}、丟 ${audio.dropped}）｜縮放 ${cam.zoom.toFixed(1)} px/格｜地形烘焙 ${bakeMs.toFixed(0)} ms
        ${hud.dataset.warn ? `<br><span class="warn">${hud.dataset.warn}</span>` : ""}
        ${tProblems.length || fProblems.length ? `<br><span class="warn">資料問題：${[...tProblems, ...fProblems].slice(0, 5).join("；")}</span>` : ""}`;
    }
  });

  (window as unknown as { __tac: unknown }).__tac = {
    stats: () => ({ ...timer.stats(), units: units.count, particles: fx.activeParticles, tracers: fx.activeTracers,
      voices: audio.active, renderer: app.renderer.name, dpr: window.devicePixelRatio, w: app.screen.width, h: app.screen.height, bakeMs }),
    reset: () => timer.reset(),
    /** 不等畫面更新，連續算＋畫 count 幀，每幀等顯示卡畫完（gl.finish）才計時：量的是一幀的真實成本，
     *  分頁在背景被限速時也準。回傳每幀毫秒的統計。 */
    bench: (count = 120) => {
      const gl = (app.renderer as unknown as { gl?: WebGLRenderingContext }).gl;
      const tm = new FrameTimer(count);
      for (let i = 0; i < count; i++) {
        const a = performance.now();
        if (frames && frames.length) stepReplay(1 / 60); else stepStress(1 / 60);
        fx.update(1 / 60, a);
        applyCam(1 / 60);
        const b = performance.now();
        app.renderer.render(app.stage);
        gl?.finish();
        const c = performance.now();
        tm.push(c - a, b - a);
      }
      const s = tm.stats();
      return { ...s, renderMs: s.avgMs - s.updateMs, units: units.count, particles: fx.activeParticles, tracers: fx.activeTracers };
    },
    zoom: (z: number) => { cam.tz = cam.zoom = z; },
  };
}

main();
