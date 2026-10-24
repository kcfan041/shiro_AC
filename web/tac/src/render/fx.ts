// 特效圖層（方案 §7.3）：槍口火光（附近地面短暫被照亮）、曳光（連發抽樣）、依材質的彈著（塵土、碎屑、火花、血點）、
// 手榴彈拋物線（地上有影子）、爆炸（閃光＋衝擊環＋碎屑＋煙＋焦痕）、閃光彈、煙霧（淡入淡出、緩慢飄動）、畫面震動。
// 全部預先配置（物件池），跑的時候不新建物件；粒子分三池共 3000 個，滿了覆蓋最舊的；分頁在背景時不產生。
// 座標：畫面方向（x 向東、y 向下），一格＝1。

import { Container, Graphics, Particle, ParticleContainer, Rectangle, Sprite, type Renderer, type Texture } from "pixi.js";
import type { XY } from "../protocol";
import { impactStyle, shakeFor, TracerThinner, tracerStyle } from "../fxplan";

const TRACER_SPEED = 75;     // 格／秒（約 150 m/s：看得出方向，不是真實彈速）
const TRACER_LEN = 3;
const SCORCH_MAX = 40, SCORCH_S = 60;

interface FxTextures { dot: Texture; spark: Texture; chip: Texture; tracer: Texture; ring: Texture; puff: Texture; glow: Texture; scorch: Texture }

function bake(r: Renderer, g: Graphics, w: number, h: number): Texture {
  const t = r.generateTexture({ target: g, frame: new Rectangle(0, 0, w, h), resolution: 2, antialias: true });
  g.destroy();
  return t;
}

export function makeFxTextures(r: Renderer): FxTextures {
  const dot = new Graphics();
  for (let i = 8; i >= 1; i--) dot.circle(16, 16, i * 2).fill({ color: 0xffffff, alpha: 0.16 });
  const spark = new Graphics().circle(8, 8, 3).fill({ color: 0xffffff }).circle(8, 8, 6).fill({ color: 0xffffff, alpha: 0.3 });
  const chip = new Graphics().rect(0, 0, 8, 8).fill({ color: 0xffffff });
  const tracer = new Graphics();
  for (let i = 0; i < 32; i++) tracer.rect(i * 2, 1, 2, 2).fill({ color: 0xffffff, alpha: (i / 31) ** 2 });
  tracer.rect(60, 0, 4, 4).fill({ color: 0xffffff });
  const ring = new Graphics().circle(64, 64, 56).stroke({ width: 8, color: 0xffffff });
  const puff = new Graphics();
  for (let i = 16; i >= 1; i--) puff.circle(64, 64, i * 4).fill({ color: 0xffffff, alpha: 0.06 });
  const glow = new Graphics();
  for (let i = 16; i >= 1; i--) glow.circle(64, 64, i * 4).fill({ color: 0xffffff, alpha: 0.05 * (1 - i / 17) + 0.01 });
  const scorch = new Graphics();
  for (let i = 12; i >= 1; i--) scorch.circle(64, 64, i * 5).fill({ color: 0xffffff, alpha: 0.08 });
  return { dot: bake(r, dot, 32, 32), spark: bake(r, spark, 16, 16), chip: bake(r, chip, 8, 8), tracer: bake(r, tracer, 64, 4),
    ring: bake(r, ring, 128, 128), puff: bake(r, puff, 128, 128), glow: bake(r, glow, 128, 128), scorch: bake(r, scorch, 128, 128) };
}

interface Live { p: Particle; vx: number; vy: number; life: number; max: number; a0: number; s0: number; grow: number; drag: number; px: number }

/** 一池粒子（同一張貼圖） */
class Pool {
  readonly c: ParticleContainer;
  private live: (Live | null)[] = [];
  private next = 0;
  constructor(tex: Texture, readonly cap: number, readonly px: number, additive = false) {
    this.c = new ParticleContainer({ dynamicProperties: { position: true, vertex: true, color: true, rotation: false, uvs: false } });
    if (additive) this.c.blendMode = "add";
    for (let i = 0; i < cap; i++) {
      this.c.addParticle(new Particle({ texture: tex, anchorX: 0.5, anchorY: 0.5, alpha: 0 }));
      this.live.push(null);
    }
  }
  emit(x: number, y: number, vx: number, vy: number, life: number, size: number, tint: number, alpha: number, grow = 0, drag = 2) {
    const i = this.next;
    this.next = (this.next + 1) % this.cap;
    const p = this.c.particleChildren[i] as Particle;
    p.x = x; p.y = y; p.tint = tint; p.alpha = alpha;
    p.scaleX = p.scaleY = size / this.px;
    this.live[i] = { p, vx, vy, life, max: life, a0: alpha, s0: size, grow, drag, px: this.px };
  }
  update(dt: number) {
    for (let i = 0; i < this.cap; i++) {
      const l = this.live[i];
      if (!l) continue;
      l.life -= dt;
      if (l.life <= 0) { l.p.alpha = 0; this.live[i] = null; continue; }
      const k = l.life / l.max, f = Math.exp(-l.drag * dt);
      l.vx *= f; l.vy *= f;
      l.p.x += l.vx * dt; l.p.y += l.vy * dt;
      l.p.alpha = l.a0 * k;
      l.p.scaleX = l.p.scaleY = (l.s0 + l.grow * (1 - k)) / l.px;
    }
  }
  get active(): number { return this.live.reduce((n, l) => n + (l ? 1 : 0), 0); }
}

interface Tracer { s: Sprite; x0: number; y0: number; x1: number; y1: number; t: number; dur: number; mat: string; hit: boolean }
interface Timed { s: Sprite; t: number; dur: number; r: number; a: number }
interface Throw { s: Sprite; sh: Sprite; from: XY; to: XY; t: number; dur: number }
interface Puff { s: Sprite; x: number; y: number; vx: number; vy: number; ph: number }
interface Cloud { key: string; x: number; y: number; r: number; density: number; a: number; target: number; puffs: Puff[] }

export interface ShotFx { mat?: string | null; hit?: boolean; unseen?: boolean; ow?: boolean; mode?: string | null; shooter?: string | null }

export class FxLayer {
  /** 地上（個體下面）：焦痕、衝擊環、火光照亮 */
  readonly ground = new Container();
  /** 煙霧（樹冠上面） */
  readonly smoke = new Container();
  /** 粒子與空中的東西（煙霧上面） */
  readonly air = new Container();
  private dust: Pool;
  private chips: Pool;
  private sparks: Pool;
  private tracers: Tracer[] = [];
  private tracerPool: Sprite[] = [];
  private timed: Timed[] = [];
  private timedPool: Sprite[] = [];
  private throws: Throw[] = [];
  private scorches: { s: Sprite; t: number }[] = [];
  private clouds = new Map<string, Cloud>();
  private thinner = new TracerThinner();
  private shakeAmp = 0;
  private shakeT = 0;
  /** 這一幀的畫面震動（格），加在世界容器上 */
  shake: XY = [0, 0];
  enabled = { tracers: true, particles: true, smoke: true, shake: true };
  paused = false;
  /** 粒子數量倍率（自動畫質降級時 0.5） */
  density = 1;

  constructor(private tex: FxTextures) {
    this.dust = new Pool(tex.dot, 1500, 32);
    this.chips = new Pool(tex.chip, 800, 8);
    this.sparks = new Pool(tex.spark, 700, 16, true);
    this.air.addChild(this.dust.c, this.chips.c, this.sparks.c);
  }

  get activeParticles(): number { return this.dust.active + this.chips.active + this.sparks.active; }
  get activeTracers(): number { return this.tracers.length; }

  private sprite(pool: Sprite[], tex: Texture, parent: Container, additive = false): Sprite {
    const s = pool.pop() || new Sprite(tex);
    s.texture = tex;
    s.visible = true;
    s.blendMode = additive ? "add" : "normal";
    if (!s.parent) parent.addChild(s);
    return s;
  }

  private glow(x: number, y: number, r: number, color: number, alpha: number, dur: number, parent = this.ground) {
    const s = this.sprite(this.timedPool, this.tex.glow, parent, true);
    s.anchor.set(0.5);
    s.tint = color;
    s.x = x; s.y = y;
    this.timed.push({ s, t: 0, dur, r, a: alpha });
  }

  /** 開一槍：槍口火光（附近地面亮一下）→ 曳光飛過去 → 依材質的彈著 */
  shot(from: XY, to: XY, o: ShotFx, t: number) {
    if (this.paused) return;
    if (!o.unseen) {
      this.glow(from[0], from[1], 1.6, 0xffd890, 0.35, 0.07);
      if (this.enabled.particles) this.sparks.emit(from[0], from[1], 0, 0, 0.05, 0.5, 0xfff2b0, 1, 0, 0);
    }
    const d = Math.hypot(to[0] - from[0], to[1] - from[1]);
    if (this.enabled.tracers && this.thinner.take(o.shooter ?? null, t)) {
      const st = tracerStyle(o);
      const s = this.sprite(this.tracerPool, this.tex.tracer, this.air, true);
      s.anchor.set(1, 0.5);
      s.tint = st.color;
      s.alpha = st.alpha;
      s.rotation = Math.atan2(to[1] - from[1], to[0] - from[0]);
      s.scale.set(Math.min(TRACER_LEN, d) / 64, st.width / 4);
      s.x = from[0]; s.y = from[1];
      this.tracers.push({ s, x0: from[0], y0: from[1], x1: to[0], y1: to[1], t: 0, dur: d / TRACER_SPEED, mat: o.mat || "", hit: !!o.hit });
    } else {
      this.impact(to, o.mat, !!o.hit, Math.atan2(from[1] - to[1], from[0] - to[0]));
    }
  }

  /** 彈著：back＝朝射手的方向（塵土往回噴） */
  impact(at: XY, mat: string | null | undefined, hit: boolean, back = 0) {
    if (this.paused || !this.enabled.particles) return;
    const st = impactStyle(mat, hit);
    const n = (c: number) => Math.ceil(c * this.density);
    for (let k = 0; k < n(st.dust); k++) {
      const a = back + (Math.random() - 0.5) * 2.2, v = st.speed * (0.3 + Math.random());
      this.dust.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, st.dustLife * (0.7 + Math.random() * 0.6), st.dustSize * 0.6,
        st.dustColor, 0.75, st.dustSize, 2.5);
    }
    for (let k = 0; k < n(st.chips); k++) {
      const a = back + (Math.random() - 0.5) * 2.6, v = st.speed * (1.5 + Math.random() * 2);
      this.chips.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 0.25 + Math.random() * 0.2, 0.08, st.chipColor, 1, 0, 4);
    }
    for (let k = 0; k < n(st.sparks); k++) {
      const a = back + (Math.random() - 0.5) * 2, v = 1 + Math.random() * 2.5;
      this.sparks.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 0.12 + Math.random() * 0.1, 0.22, st.sparkColor, 1, 0, 3);
    }
  }

  /** 投擲：拋物線飛行物，地上有影子 */
  throwArc(from: XY, to: XY, dur: number) {
    if (this.paused) return;
    const s = new Sprite(this.tex.dot), sh = new Sprite(this.tex.dot);
    s.anchor.set(0.5); sh.anchor.set(0.5);
    s.tint = 0xb0bca8; sh.tint = 0x000000; sh.alpha = 0.35;
    this.ground.addChild(sh);
    this.air.addChild(s);
    this.throws.push({ s, sh, from, to, t: 0, dur });
  }

  /** 爆炸；ownNearDist＝爆點到最近我方的距離（格，畫面震動用） */
  blast(at: XY, flash: boolean, ownNearDist = Infinity) {
    if (this.paused) return;
    this.glow(at[0], at[1], flash ? 14 : 7, flash ? 0xffffff : 0xffc070, flash ? 0.9 : 0.7, flash ? 0.25 : 0.18);
    const ring = this.sprite(this.timedPool, this.tex.ring, this.ground, true);
    ring.anchor.set(0.5);
    ring.tint = flash ? 0xffffff : 0xffb070;
    ring.x = at[0]; ring.y = at[1];
    this.timed.push({ s: ring, t: 0, dur: 0.45, r: flash ? 9 : 5, a: 0.8 });
    if (this.enabled.particles) {
      for (let k = 0; k < 6; k++) this.sparks.emit(at[0], at[1], 0, 0, 0.12, flash ? 6 : 3, flash ? 0xffffff : 0xffd080, 0.9, flash ? 10 : 5, 0);
      if (!flash) {
        for (let k = 0; k < Math.ceil(36 * this.density); k++) {
          const a = Math.random() * Math.PI * 2, v = 3 + Math.random() * 9;
          this.chips.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 0.4 + Math.random() * 0.5, 0.12, k % 3 ? 0x5a4e40 : 0xffa040, 1, 0, 3);
        }
        for (let k = 0; k < Math.ceil(10 * this.density); k++) {
          const a = Math.random() * Math.PI * 2, v = 0.3 + Math.random() * 0.8;
          this.dust.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 1.6 + Math.random(), 1.8, 0x4a4a46, 0.55, 2.5, 0.5);
        }
      }
    }
    if (!flash) {
      const s = new Sprite(this.tex.scorch);
      s.anchor.set(0.5);
      s.tint = 0x000000;
      s.alpha = 0.5;
      s.scale.set(3.2 / 128);
      s.rotation = Math.random() * Math.PI * 2;
      s.x = at[0]; s.y = at[1];
      this.ground.addChildAt(s, 0);
      this.scorches.push({ s, t: 0 });
      while (this.scorches.length > SCORCH_MAX) this.scorches.shift()!.s.destroy();
    }
    if (this.enabled.shake) {
      const amp = shakeFor(ownNearDist, ownNearDist < 6, flash);
      if (amp > this.shakeAmp) { this.shakeAmp = amp; this.shakeT = 0; }
    }
  }

  /** 這一刻的煙霧（伺服器影格的 smokes）：新的淡入、消失的淡出，濃度決定不透明度 */
  setSmokes(list: { x: number; y: number; r: number; density?: number }[]) {
    const seen = new Set<string>();
    for (const sm of list) {
      const key = `${sm.x.toFixed(1)},${sm.y.toFixed(1)}`;
      seen.add(key);
      let c = this.clouds.get(key);
      if (!c) {
        c = { key, x: sm.x, y: sm.y, r: sm.r, density: sm.density ?? 1, a: 0, target: 1, puffs: [] };
        const n = Math.max(8, Math.round(sm.r * 3));
        for (let k = 0; k < n; k++) {
          const s = new Sprite(this.tex.puff);
          s.anchor.set(0.5);
          s.tint = 0xb8c0bc;
          const a = Math.random() * Math.PI * 2, d = Math.sqrt(Math.random()) * sm.r * 0.75;
          s.scale.set((sm.r * (0.7 + Math.random() * 0.4)) / 64);
          s.alpha = 0;
          this.smoke.addChild(s);
          c.puffs.push({ s, x: sm.x + Math.cos(a) * d, y: sm.y + Math.sin(a) * d, vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25, ph: Math.random() * 6 });
        }
        this.clouds.set(key, c);
      }
      c.density = sm.density ?? 1;
      c.target = 1;
    }
    for (const c of this.clouds.values()) if (!seen.has(c.key)) c.target = 0;
  }

  update(dt: number, now: number) {
    this.dust.update(dt);
    this.chips.update(dt);
    this.sparks.update(dt);
    this.tracers = this.tracers.filter((tr) => {
      tr.t += dt;
      const q = Math.min(1, tr.t / Math.max(1e-3, tr.dur));
      tr.s.x = tr.x0 + (tr.x1 - tr.x0) * q;
      tr.s.y = tr.y0 + (tr.y1 - tr.y0) * q;
      if (q < 1) return true;
      this.impact([tr.x1, tr.y1], tr.mat, tr.hit, Math.atan2(tr.y0 - tr.y1, tr.x0 - tr.x1));
      tr.s.visible = false;
      this.tracerPool.push(tr.s);
      return false;
    });
    this.timed = this.timed.filter((g) => {
      g.t += dt;
      const q = g.t / g.dur;
      if (q >= 1) { g.s.visible = false; this.timedPool.push(g.s); return false; }
      const isRing = g.s.texture === this.tex.ring;
      g.s.scale.set((g.r * (isRing ? 0.2 + 0.8 * q : 1)) / (isRing ? 56 : 64));
      g.s.alpha = g.a * (1 - q);
      return true;
    });
    this.throws = this.throws.filter((th) => {
      th.t += dt;
      const q = Math.min(1, th.t / th.dur);
      const gx = th.from[0] + (th.to[0] - th.from[0]) * q, gy = th.from[1] + (th.to[1] - th.from[1]) * q;
      const h = Math.sin(q * Math.PI) * 2.5;
      th.sh.position.set(gx, gy);
      th.sh.scale.set(0.35 / 32);
      th.s.position.set(gx, gy - h);
      th.s.scale.set((0.35 + h * 0.08) / 32 * 1.4);
      if (q < 1) return true;
      th.s.destroy(); th.sh.destroy();
      return false;
    });
    for (const sc of this.scorches) {
      sc.t += dt;
      sc.s.alpha = 0.5 * Math.max(0, 1 - sc.t / SCORCH_S);
    }
    this.smoke.visible = this.enabled.smoke;
    for (const [key, c] of this.clouds) {
      c.a += (c.target - c.a) * (1 - Math.exp(-dt / (c.target > c.a ? 0.6 : 0.9)));
      if (c.target === 0 && c.a < 0.02) { for (const p of c.puffs) p.s.destroy(); this.clouds.delete(key); continue; }
      for (const p of c.puffs) {
        p.x += p.vx * dt; p.y += p.vy * dt;
        p.s.x = p.x + Math.sin(now / 1.7 + p.ph) * 0.35;
        p.s.y = p.y + Math.cos(now / 2.1 + p.ph) * 0.35;
        p.s.alpha = 0.4 * c.density * c.a * (0.85 + 0.15 * Math.sin(now / 0.9 + p.ph));
      }
    }
    if (this.shakeAmp > 0) {
      this.shakeT += dt;
      const k = Math.max(0, 1 - this.shakeT / 0.35);
      if (k <= 0) { this.shakeAmp = 0; this.shake = [0, 0]; }
      else this.shake = [Math.sin(this.shakeT * 70) * this.shakeAmp * k, Math.cos(this.shakeT * 55) * this.shakeAmp * k];
    }
  }
}
