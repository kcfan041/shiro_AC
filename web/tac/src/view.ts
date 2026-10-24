// 戰術圖本體（指揮室與實驗頁共用）：PixiJS 畫布＋各圖層＋播放時鐘＋個體軌跡＋相機＋輸入＋注意力＋HUD＋音效＋自動畫質。
// 即時：pushWorld(伺服器的 world 推送) → 合併各區域的影格 → 時鐘與軌跡；畫面照戰鬥時鐘播（方案 §5）。
// 實驗頁的假戰場用 manual 模式（不走時鐘與軌跡，自己每幀給樣本）。
// 座標：伺服器與事件用「世界座標」（小格，y 向北）；畫面內部用「畫面方向」（y 向下＝h − 世界 y）。

import { Application, Container } from "pixi.js";
import { Attention, type Attn } from "./attention";
import { SoundEngine, type SoundReq } from "./audio";
import { Camera } from "./camera";
import { BattleClock } from "./clock";
import { Hud } from "./hud";
import { mergeFrames } from "./merge";
import type { Frame, WorldTerrain, XY } from "./protocol";
import { FxLayer, makeFxTextures } from "./render/fx";
import { IntentLayer } from "./render/intent";
import { OverlayLayer, type WorldView } from "./render/overlay";
import { TerrainLayer } from "./render/terrain";
import { UnitLayer, type Look } from "./render/units";
import { makeUnitTextures } from "./render/unitTextures";
import { VisionLayer } from "./render/vision";
import { FrameTimer } from "./stats";
import { walkable } from "./stress";
import { getTheme, cssHex, type Theme } from "./theme";
import { Tracks, type UnitSample } from "./tracks";

export interface TacOptions { theme?: string; look?: Look; dpr?: number; hud?: boolean }
export interface TacEvents {
  pick(u: UnitSample | null): void;
  /** 工具點了位置（世界座標的小格） */
  place(kind: string, x: number, y: number): void;
  /** 跟隨開了或關了（使用者拖曳鏡頭會關掉） */
  follow(on: boolean): void;
  attention(a: Attn): void;
}

const TOOL_TEXT: Record<string, string> = { rally: "集結點", flank: "包抄點", fire: "集火點", airdrop: "空投落點", smoke: "煙幕位置", drone: "無人機中心" };
const KEEP_FRAMES = 80;          // 保留最近幾格影格（40 秒；煙霧、最後已知、視野、意圖用）
type Quality = "auto" | "high" | "low";

export class TacView {
  readonly app: Application;
  readonly cam = new Camera();
  readonly theme: Theme;
  readonly world = new Container();
  terrain!: TerrainLayer;
  fx!: FxLayer;
  units!: UnitLayer;
  vision!: VisionLayer;
  intents!: IntentLayer;
  overlay!: OverlayLayer;
  hud: Hud | null = null;
  readonly audio = new SoundEngine("/static/dist/sounds/");
  readonly attention = new Attention();
  clock = new BattleClock();
  tracks!: Tracks;
  frames: Frame[] = [];
  view: WorldView | null = null;
  samples: UnitSample[] = [];
  /** 實驗頁假戰場：不走時鐘與軌跡，onTick 自己畫 */
  manual = false;
  onTick: ((dt: number) => void) | null = null;
  readonly timer = new FrameTimer(300);
  on: Partial<TacEvents> = {};
  gm = false;
  private tool: { kind: string; squad: string | null } | null = null;
  private cursor: XY | null = null;         // 畫面方向
  private mouse: XY | null = null;          // 畫布上的 px
  private prevBt: number | null = null;
  private lastJumps = 0;
  private lastCur: Frame | null = null;
  private tickAt = 0;
  private tickSecs = 5;
  private diag = false;
  private lastHud = 0;
  private quality: Quality = "auto";
  private qLevel = 0;
  private slowS = 0;
  private fastS = 0;
  private baseRes = 1;
  private followSquad: string | null = null;
  private followFallback: (() => XY | null) | null = null;
  private hears: { ts: number; at: XY }[] = [];
  private wasDown = new Set<string>();
  private destroyed = false;

  private constructor(readonly host: HTMLElement, readonly T: WorldTerrain, opt: TacOptions) {
    this.theme = getTheme(opt.theme);
    this.app = new Application();
  }

  static async create(host: HTMLElement, T: WorldTerrain, opt: TacOptions = {}): Promise<TacView> {
    const v = new TacView(host, T, opt);
    v.baseRes = opt.dpr || window.devicePixelRatio || 1;
    await v.app.init({ resizeTo: host, resolution: v.baseRes, autoDensity: true, antialias: true, background: cssHex(v.theme.bg), preference: "webgl" });
    host.appendChild(v.app.canvas);
    v.build(opt);
    return v;
  }

  private build(opt: TacOptions) {
    const r = this.app.renderer, T = this.T, th = this.theme;
    this.terrain = new TerrainLayer(r, T, th);
    this.fx = new FxLayer(makeFxTextures(r));
    this.units = new UnitLayer(makeUnitTextures(r), th);
    this.units.look = opt.look || "auto";
    this.vision = new VisionLayer(r, T.w, T.h);
    this.intents = new IntentLayer(th, T);
    this.overlay = new OverlayLayer(th, T);
    this.tracks = new Tracks((x, y) => walkable(T, x, y));
    // 由下往上：地面與牆 → 格線與名稱 → 區域外調暗、標定、支援 → 小隊意圖（路線、圈） → 地上特效 → 個體 → 樹冠 → 煙 → 空中特效
    // → 視野遮罩 → 個體標籤 → 小隊標籤 → 情報字、倒數、預覽、邊緣箭頭
    this.world.addChild(this.terrain.base, this.terrain.overlay, this.overlay.ground, this.intents.ground, this.fx.ground, this.units.root,
      this.terrain.canopy, this.fx.smoke, this.fx.air, this.vision.root, this.units.labels, this.intents.labels, this.overlay.top);
    this.app.stage.addChild(this.world);
    this.cam.jumpTo(T.w / 2, T.h / 2);
    if (opt.hud !== false) {
      this.hud = new Hud(this.host, th, {
        fight: (cell) => this.lookAtCell(cell),
        card: (a) => { if (a.at) this.lookAt(a.at[0] + 0.5, a.at[1] + 0.5); this.attention.dismiss(a.id); },
        toggle: (k, v) => this.toggle(k, v),
        sound: async () => { if (this.audio.on) await this.audio.disable(); else await this.audio.enable(); return this.audio.on; },
      }, { vision: ["視野", true], intent: ["意圖", true], shake: ["震動", true] }, () => this.qualityText());
    }
    this.bindInput();
    window.addEventListener("keydown", this.onKey);
    const wake = () => { if (this.audio.wanted && !this.audio.on) void this.audio.enable(); };
    this.host.addEventListener("pointerdown", wake);
    this.app.ticker.add((tk) => this.frame(tk.deltaMS));
  }

  // ---------------------------------------------------------------- 資料

  /** 伺服器的 world 推送（/api/world 或 WebSocket）：區域畫面＋各區域的影格 */
  pushWorld(w: WorldView & { frames?: Frame[] }, wallS = performance.now() / 1000) {
    this.view = w;
    const f = mergeFrames(w.frames || [], this.gm);
    if (f) this.ingest(f, wallS);
  }

  ingest(f: Frame, wallS = performance.now() / 1000) {
    if (f.ts0 == null || f.ts1 == null) return;
    this.clock.push(f.ts0, f.ts1, wallS);
    this.tracks.ingest(f, this.clock.bt);
    this.frames.push(f);
    this.frames.sort((a, b) => (a.ts0 ?? 0) - (b.ts0 ?? 0));
    while (this.frames.length > KEEP_FRAMES) this.frames.shift();
  }

  /** 換視角（GM 真相、觀戰別人）或回到即時：清掉播放狀態重新開始 */
  reset(gm = this.gm) {
    this.gm = gm;
    this.overlay.gm = gm;
    this.clock = new BattleClock();
    this.tracks = new Tracks((x, y) => walkable(this.T, x, y));
    this.frames = [];
    this.prevBt = null;
    this.lastCur = null;
    this.samples = [];
    this.vision.clear();
    this.fx.setSmokes([]);
  }

  /** 作戰 tick 時鐘：上一次完整狀態的時間（performance.now 毫秒）與一拍幾秒（時間列用） */
  setTick(atMs: number, secs: number) { this.tickAt = atMs; this.tickSecs = secs; }

  // ---------------------------------------------------------------- 鏡頭

  private sy = (y: number) => this.T.h - y;

  squadCenter(sid: string): XY | null {
    const mem = this.samples.filter((u) => (u.u.group || u.u.squad) === sid && !u.dead && u.alpha > 0.3);
    const live = mem.filter((u) => !u.down);
    const list = live.length ? live : mem;
    if (!list.length) return null;
    return [list.reduce((a, u) => a + u.x, 0) / list.length, this.sy(list.reduce((a, u) => a + u.y, 0) / list.length)];
  }

  /** 跟隨小隊（null＝不跟隨）；fallback：小隊不在畫面資料裡時的位置（世界座標） */
  follow(sid: string | null, fallback: (() => XY | null) | null = null) {
    this.followSquad = sid;
    this.followFallback = fallback;
    this.cam.follow = sid ? () => {
      const c = this.squadCenter(sid);
      if (c) return c;
      const f = this.followFallback?.();
      return f ? [f[0], this.sy(f[1])] : null;
    } : null;
  }

  /** 正在跟隨的小隊 */
  get following(): string | null { return this.followSquad; }

  /** 鏡頭平滑移到某點（世界座標）；取消跟隨 */
  lookAt(x: number, y: number) {
    const tgt: XY = [x, this.sy(y)];
    this.followSquad = null;
    this.cam.follow = () => tgt;
    this.on.follow?.(false);
  }

  lookAtCell(cell: string) {
    const r = this.T.rows.indexOf(cell[0]), c = this.T.cols.indexOf(cell.slice(1));
    if (r >= 0 && c >= 0) this.lookAt(c * this.T.cell + this.T.cell / 2, r * this.T.cell + this.T.cell / 2);
  }

  zoomBy(f: number) { this.cam.zoomAt(this.app.screen.width / 2, this.app.screen.height / 2, f); }

  /** 目前的鏡頭（世界座標；迷你作戰圖畫視窗框用） */
  camera() { return { x: this.cam.x, y: this.sy(this.cam.y), zoom: this.cam.zoom, w: this.app.screen.width, h: this.app.screen.height }; }

  setTool(kind: string | null, squad: string | null = null) {
    this.tool = kind ? { kind, squad } : null;
    this.app.canvas.style.cursor = kind ? "crosshair" : "";
  }

  // ---------------------------------------------------------------- 輸入

  private bindInput() {
    const c = this.app.canvas;
    let down: { x: number; y: number; moved: boolean } | null = null;
    const local = (e: PointerEvent | WheelEvent): XY => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener("pointerdown", (e) => { const [x, y] = local(e); down = { x, y, moved: false }; c.setPointerCapture(e.pointerId); });
    c.addEventListener("pointermove", (e) => {
      const [x, y] = local(e);
      this.mouse = [x, y];
      this.cursor = this.cam.toWorld(x, y);
      if (!down) return;
      if (!down.moved && Math.hypot(x - down.x, y - down.y) > 4) down.moved = true;
      if (down.moved) {
        const had = !!this.cam.follow;
        this.cam.pan(x - down.x, y - down.y);
        down.x = x; down.y = y;
        if (had) { this.followSquad = null; this.on.follow?.(false); }
      }
    });
    c.addEventListener("pointerup", (e) => {
      const d = down;
      down = null;
      if (!d || d.moved) return;
      const [x, y] = local(e);
      const [wx, wy] = this.cam.toWorld(x, y);
      if (this.tool) {
        const k = this.tool.kind;
        this.setTool(null);
        this.on.place?.(k, Math.floor(wx), Math.floor(this.sy(wy)));
        return;
      }
      this.on.pick?.(this.pickAt(wx, wy));
    });
    c.addEventListener("pointerleave", () => { this.mouse = null; this.cursor = null; this.hud?.setTip(null); });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [x, y] = local(e);
      this.cam.zoomAt(x, y, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });
  }

  private onKey = (e: KeyboardEvent) => {
    const tg = e.target as HTMLElement | null;
    if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.tagName === "SELECT")) return;
    if (e.key === "F3") { e.preventDefault(); this.diag = !this.diag; if (!this.diag) this.hud?.setDiag(null); }
  };

  /** 畫面方向的點附近最近的個體 */
  private pickAt(x: number, y: number): UnitSample | null {
    let best: UnitSample | null = null, bd = Math.max(1.2, 10 / this.cam.zoom);
    for (const u of this.samples) {
      if (u.alpha < 0.3) continue;
      const d = Math.hypot(u.x - x, this.sy(u.y) - y);
      if (d < bd) { bd = d; best = u; }
    }
    return best;
  }

  private toggle(k: string, v: boolean) {
    if (k === "vision") this.vision.enabled = v;
    else if (k === "intent") this.intents.enabled = v;
    else if (k === "shake") this.fx.enabled.shake = v;
    else if (k === "quality") {
      this.quality = this.quality === "auto" ? "high" : this.quality === "high" ? "low" : "auto";
      this.applyQuality(this.quality === "low" ? 1 : 0);
    }
  }

  // ---------------------------------------------------------------- 自動畫質（方案 §13）

  private qualityText(): string {
    const lv = this.qLevel ? "低" : "高";
    return this.quality === "auto" ? `畫質：自動（${lv}）` : `畫質：固定${lv}`;
  }

  private applyQuality(level: number) {
    if (level === this.qLevel) return;
    this.qLevel = level;
    const res = level ? 1 : this.baseRes;
    this.app.renderer.resize(this.app.screen.width, this.app.screen.height, res);
    this.fx.density = level ? 0.5 : 1;
  }

  private autoQuality(dtS: number) {
    if (this.quality !== "auto" || document.hidden) return;
    const fps = 1 / Math.max(1e-3, dtS);
    if (fps < 50) { this.slowS += dtS; this.fastS = 0; } else if (fps >= 58) { this.fastS += dtS; this.slowS = 0; }
    if (this.qLevel === 0 && this.slowS > 3) { this.applyQuality(1); this.slowS = 0; }
    if (this.qLevel === 1 && this.fastS > 10) { this.applyQuality(0); this.fastS = 0; }
  }

  // ---------------------------------------------------------------- 每幀

  private sound(r: SoundReq) {
    this.audio.play(r, { x: this.cam.x, y: this.cam.y, halfWidth: this.app.screen.width / 2 / this.cam.zoom });
  }

  /** 一幀（app.ticker 每幀呼叫；量測用的 bench 也直接呼叫） */
  frame(deltaMS: number) {
    if (this.destroyed) return;
    const dtS = Math.min(0.1, deltaMS / 1000);
    const t0 = performance.now();
    const now = t0 / 1000;
    if (this.manual) this.onTick?.(dtS);
    else this.stepLive(dtS, now);
    this.fx.update(dtS, now);
    this.vision.update(dtS);
    // 鏡頭
    this.cam.setViewport(this.app.screen.width, this.app.screen.height);
    this.cam.update(dtS);
    this.world.scale.set(this.cam.zoom);
    const [ox, oy] = this.cam.origin();
    this.world.x = ox + Math.round(this.fx.shake[0] * this.cam.zoom);
    this.world.y = oy + Math.round(this.fx.shake[1] * this.cam.zoom);
    const [x0, y0] = this.cam.toWorld(0, 0), [x1, y1] = this.cam.toWorld(this.app.screen.width, this.app.screen.height);
    this.terrain.update([x0, y0, x1, y1], this.cam.zoom, this.app.renderer.resolution);
    const toolFrom = this.tool?.squad ? this.squadCenter(this.tool.squad) : null;
    const arrows = this.attention.cards(t0).filter((a) => a.at).map((a): XY => [a.at![0] + 0.5, this.sy(a.at![1] + 0.5)]);
    this.overlay.update(this.view, now, this.cam.zoom, this.app.renderer.resolution, (sid) => this.squadCenter(sid),
      this.tool ? { kind: this.tool.kind, from: toolFrom } : null, this.cursor, arrows, [x0, y0, x1, y1]);
    this.timer.push(deltaMS, performance.now() - t0);
    this.autoQuality(dtS);
    if (this.hud && t0 - this.lastHud > 200) { this.lastHud = t0; this.updateHud(t0); }
  }

  private stepLive(dtS: number, now: number) {
    const bt = this.clock.tick(dtS);
    if (bt === null) return;
    let cur: Frame | null = null, vf: Frame | null = null;
    for (const f of this.frames) { if ((f.ts0 ?? 0) > bt) break; cur = f; if (f.vision) vf = f; }
    if (!cur) return;
    const sy = this.sy;
    if (cur !== this.lastCur) {
      this.lastCur = cur;
      const sups = (this.view?.zones || []).flatMap((z) => z.supports || []);
      for (const a of this.attention.feed(cur, performance.now(), sups)) this.on.attention?.(a);
    }
    const jumped = this.clock.jumps !== this.lastJumps;
    this.lastJumps = this.clock.jumps;
    if (this.prevBt !== null && !jumped && bt - this.prevBt < 1) {
      for (const e of this.tracks.fxBetween(this.prevBt, bt)) {
        if (e.kind === "hear") {
          this.hears.push({ ts: e.ts, at: [e.at[0] + 0.5, sy(e.at[1] + 0.5)] });
          while (this.hears.length > 20) this.hears.shift();
        } else if (e.kind === "shot") {
          const from: XY = [e.from[0], sy(e.from[1])], to: XY = [e.to[0], sy(e.to[1])];
          this.fx.shot(from, to, e, bt);
          if (e.shooter) this.units.shot(e.shooter, now);
          const h = e.unseen ? this.hears.filter((x) => Math.abs(x.ts - e.ts) < 1).pop() : null;
          const at = h ? h.at : from;
          this.sound({ kind: "gun", x: at[0], y: at[1], weapon: e.unseen ? undefined : e.weapon ?? undefined, shooter: e.shooter ?? (h ? `hear${h.ts}` : null) });
          if (e.mat && e.mat !== "air") this.sound({ kind: "impact", x: to[0], y: to[1], mat: ({ wall: "hard", low: "hard", rubble: "hard", ground: "dirt", body: "flesh" } as Record<string, string>)[e.mat] ?? "soft" });
        } else if (e.kind === "blast") {
          const at: XY = [e.at[0], sy(e.at[1])], flash = e.item === "flashes";
          this.fx.blast(at, flash, this.ownDist(at));
          this.sound({ kind: flash ? "flash" : "boom", x: at[0], y: at[1] });
        } else {
          this.fx.throwArc([e.from[0], sy(e.from[1])], [e.to[0], sy(e.to[1])], Math.max(0.2, e.land - e.ts));
        }
      }
    }
    this.prevBt = bt;
    this.samples = this.tracks.sample(bt);
    for (const u of this.samples) {
      if (u.side === "own" && u.down && !this.wasDown.has(u.id)) { this.wasDown.add(u.id); this.sound({ kind: "alert" }); }
      else if (!u.down) this.wasDown.delete(u.id);
    }
    this.fx.setSmokes(cur.smokes.map((s) => ({ x: s.x, y: sy(s.y), r: s.r, density: s.density })));
    if (vf) {
      const at = new Map(vf.units.map((u) => [u.id, [u.pos[0] + 0.5, sy(u.pos[1] + 0.5)] as XY]));
      this.vision.set(Object.entries(vf.vision!).filter(([id]) => at.has(id)).map(([id, p]) => ({ c: at.get(id)!, pts: p.map(([x, y]) => [x, sy(y)] as XY) })));
    }
    this.draw(this.samples, (cur.ghosts || []).map((g) => ({ id: g.id, x: g.pos[0] + 0.5, y: sy(g.pos[1] + 0.5), age: g.age })), now);
    this.intents.update(cur.intents || {}, this.screenSamples, sy, this.cam.zoom, this.app.renderer.resolution, cur.world_tick ?? 0);
  }

  private screenSamples: UnitSample[] = [];

  /** 畫個體（樣本是世界座標）；manual 模式的實驗頁也用這個 */
  draw(samples: UnitSample[], ghosts: { id: string; x: number; y: number; age: number }[] = [], now = performance.now() / 1000): number {
    this.samples = samples;
    this.screenSamples = samples.map((u) => ({ ...u, y: this.sy(u.y) }));
    return this.units.update(this.screenSamples, ghosts, this.cam.zoom, this.app.renderer.resolution, now);
  }

  private ownDist(at: XY): number {
    return this.screenSamples.reduce((m, u) => u.side === "own" && !u.down ? Math.min(m, Math.hypot(u.x - at[0], u.y - at[1])) : m, Infinity);
  }

  private updateHud(nowMs: number) {
    const hud = this.hud!;
    hud.setFights(this.view ? [...new Set(this.view.zones.flatMap((z) => z.hot))].sort() : []);
    hud.setCards(this.attention.cards(nowMs));
    hud.setHint(this.tool ? `點選位置：${TOOL_TEXT[this.tool.kind] || this.tool.kind}（Esc 取消）` : null);
    const k = this.tickAt ? 1 - (nowMs - this.tickAt) / (this.tickSecs * 1000) : null;
    hud.setBar(this.manual ? null : k, this.clock.bt === null ? 0 : this.clock.latest - this.clock.bt);
    // 懸停說明
    if (this.mouse && this.cursor && !this.tool) {
      const u = this.pickAt(this.cursor[0], this.cursor[1]);
      if (u) {
        const side = { own: "我方", ally: "友軍", remnant: "殘部", enemy: "敵兵" }[u.side as string] || u.side;
        const posture = { stand: "站", crouch: "蹲", prone: "臥" }[u.posture] || u.posture;
        const st = [u.down ? (u.u.stabilized ? "倒地（已止血）" : "倒地") : posture, u.u.pinned ? "抬不起頭" : u.u.suppressed ? "被壓制" : "", (u.z || 0) > 0 ? `${Math.round(u.z) + 1} 樓` : ""]
          .filter(Boolean).join("｜");
        const why = u.u.why && u.side === "own" ? `<br>${u.u.why.replace(/[<>&]/g, "")}` : "";
        hud.setTip(`<b>${(u.side === "enemy" ? "敵兵" : u.name).replace(/[<>&]/g, "")}</b>（${side}）${st}${why}`, this.mouse[0], this.mouse[1]);
      } else hud.setTip(null);
    } else hud.setTip(null);
    if (this.diag) {
      const s = this.timer.stats(), c = this.clock.state();
      hud.setDiag(`${s.fps.toFixed(0)} fps  幀 ${s.avgMs.toFixed(1)} ms（p95 ${s.p95Ms.toFixed(1)}）  JS ${s.updateMs.toFixed(2)} ms\n` +
        `戰鬥 ${c.bt.toFixed(1)} s  速度 ×${c.rate.toFixed(3)}  緩衝 ${c.buffer.toFixed(2)} s  伺服器 ×${c.serverRate.toFixed(2)}  卡住 ${c.stalls}  跳過 ${c.jumps}\n` +
        `個體 ${this.samples.length}  粒子 ${this.fx.activeParticles}  曳光 ${this.fx.activeTracers}  聲音 ${this.audio.limiter.active}  ${this.qualityText()}  DPR ${this.app.renderer.resolution}`);
    }
  }

  destroy() {
    this.destroyed = true;
    window.removeEventListener("keydown", this.onKey);
    this.hud?.destroy();
    this.app.destroy(true, { children: true, texture: true });
  }
}
