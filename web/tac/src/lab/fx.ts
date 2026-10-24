// 實驗頁特效：曳光、槍口火光、彈著粒子、手榴彈拋物線、爆炸（閃光＋衝擊環＋碎屑）、煙霧。
// 全部預先配置好（物件池），跑的時候不新建物件；粒子有上限，超過就覆蓋最舊的。
// 座標單位＝小格，畫面 y 已翻轉。

import { Container, Particle, ParticleContainer, Sprite, type Texture } from "pixi.js";
import type { LabTextures } from "./textures";
import type { XY } from "../protocol";

const TRACER_SPEED = 75;     // 格／秒（約 150 m/s，看得出方向；不是真實彈速）
const TRACER_LEN = 3;        // 曳光段長度（格）

interface Live { p: Particle; vx: number; vy: number; life: number; max: number; a0: number; s0: number; grow: number; drag: number }
interface Tracer { s: Sprite; x0: number; y0: number; x1: number; y1: number; t: number; dur: number }
interface Ring { s: Sprite; t: number; dur: number; r: number }
interface Throw { s: Sprite; from: XY; to: XY; t: number; dur: number }
interface Puff { s: Sprite; x: number; y: number; vx: number; vy: number; ph: number; a: number }

export class Fx {
  readonly ground = new Container();      // 地面特效（衝擊環）
  readonly air = new Container();         // 空中特效（曳光、飛行物）
  readonly smoke = new Container();
  readonly particles: ParticleContainer;
  private live: (Live | null)[] = [];
  private next = 0;
  private tracers: Tracer[] = [];
  private tracerPool: Sprite[] = [];
  private rings: Ring[] = [];
  private ringPool: Sprite[] = [];
  private throws: Throw[] = [];
  private puffs: Puff[] = [];
  enabled = { tracers: true, particles: true, smoke: true };

  constructor(private tex: LabTextures, readonly cap = 3000) {
    this.particles = new ParticleContainer({ dynamicProperties: { position: true, vertex: true, color: true, rotation: false, uvs: false } });
    for (let i = 0; i < cap; i++) {
      const p = new Particle({ texture: tex.dot, anchorX: 0.5, anchorY: 0.5, alpha: 0 });
      this.particles.addParticle(p);
      this.live.push(null);
    }
  }

  get activeParticles(): number { return this.live.reduce((n, l) => n + (l ? 1 : 0), 0); }
  get activeTracers(): number { return this.tracers.length; }

  private emit(x: number, y: number, vx: number, vy: number, life: number, size: number, tint: number, alpha: number, grow = 0, drag = 2) {
    if (!this.enabled.particles) return;
    const i = this.next;
    this.next = (this.next + 1) % this.cap;
    const p = this.particles.particleChildren[i] as Particle;
    p.x = x; p.y = y; p.tint = tint; p.alpha = alpha;
    p.scaleX = p.scaleY = size / 32;
    this.live[i] = { p, vx, vy, life, max: life, a0: alpha, s0: size, grow, drag };
  }

  private sprite(pool: Sprite[], texture: Texture, parent: Container): Sprite {
    const s = pool.pop() || new Sprite(texture);
    s.visible = true;
    if (!s.parent) parent.addChild(s);
    return s;
  }

  /** 開一槍：槍口火光＋曳光（飛到之後彈著） */
  shot(from: XY, to: XY, hit: boolean, tint = 0xffe696) {
    for (let k = 0; k < 2; k++) this.emit(from[0], from[1], 0, 0, 0.06, 1.1, 0xfff2b0, 0.9, 0, 0);
    const d = Math.hypot(to[0] - from[0], to[1] - from[1]);
    if (this.enabled.tracers) {
      const s = this.sprite(this.tracerPool, this.tex.tracer, this.air);
      s.anchor.set(1, 0.5);
      s.tint = tint;
      s.rotation = Math.atan2(to[1] - from[1], to[0] - from[0]);
      s.scale.set(Math.min(TRACER_LEN, d) / 64, 0.15 / 4);
      s.x = from[0]; s.y = from[1];
      this.tracers.push({ s, x0: from[0], y0: from[1], x1: to[0], y1: to[1], t: 0, dur: d / TRACER_SPEED });
    } else {
      this.impact(to, hit);
    }
  }

  /** 彈著：塵土（沒打中）或紅點（命中） */
  impact(at: XY, hit: boolean) {
    const n = hit ? 4 : 6;
    for (let k = 0; k < n; k++) {
      const a = Math.random() * Math.PI * 2, v = 0.5 + Math.random() * 1.5;
      this.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 0.35 + Math.random() * 0.25, hit ? 0.5 : 0.6,
        hit ? 0xff4a3a : 0xb8b0a0, 0.8, hit ? 0 : 1.2);
    }
  }

  throwArc(from: XY, to: XY, dur: number) {
    const s = new Sprite(this.tex.dot);
    s.anchor.set(0.5);
    s.tint = 0x9aa890;
    this.air.addChild(s);
    this.throws.push({ s, from, to, t: 0, dur });
  }

  blast(at: XY, flash = false) {
    for (let k = 0; k < 6; k++) this.emit(at[0], at[1], 0, 0, 0.12, flash ? 10 : 5, flash ? 0xffffff : 0xffd080, 0.9, 20, 0);
    if (!flash) {
      for (let k = 0; k < 30; k++) {
        const a = Math.random() * Math.PI * 2, v = 2 + Math.random() * 8;
        this.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 0.5 + Math.random() * 0.6, 0.4, k % 3 ? 0x6a5a48 : 0xffa040, 0.9, 0, 3);
      }
      for (let k = 0; k < 8; k++) {
        const a = Math.random() * Math.PI * 2, v = 0.3 + Math.random() * 0.8;
        this.emit(at[0], at[1], Math.cos(a) * v, Math.sin(a) * v, 1.5 + Math.random(), 2.5, 0x4a4a46, 0.5, 2.5, 0.5);
      }
    }
    const s = this.sprite(this.ringPool, this.tex.ring, this.ground);
    s.anchor.set(0.5);
    s.tint = flash ? 0xffffff : 0xffb070;
    s.x = at[0]; s.y = at[1];
    this.rings.push({ s, t: 0, dur: 0.45, r: flash ? 9 : 5 });
  }

  /** 一團煙（持續存在，慢慢飄動） */
  addSmoke(x: number, y: number, r: number, n = 18) {
    for (let k = 0; k < n; k++) {
      const s = new Sprite(this.tex.puff);
      s.anchor.set(0.5);
      s.tint = 0xb4beb9;
      const a = Math.random() * Math.PI * 2, d = Math.sqrt(Math.random()) * r * 0.7;
      const px = x + Math.cos(a) * d, py = y + Math.sin(a) * d;
      s.scale.set((r * 0.9) / 64);
      this.smoke.addChild(s);
      this.puffs.push({ s, x: px, y: py, vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3, ph: Math.random() * 6, a: 0.35 });
    }
  }

  clearSmoke() {
    for (const p of this.puffs) p.s.destroy();
    this.puffs = [];
  }

  update(dt: number, now: number) {
    for (let i = 0; i < this.cap; i++) {
      const l = this.live[i];
      if (!l) continue;
      l.life -= dt;
      if (l.life <= 0) { l.p.alpha = 0; this.live[i] = null; continue; }
      const k = l.life / l.max;
      const f = Math.exp(-l.drag * dt);
      l.vx *= f; l.vy *= f;
      l.p.x += l.vx * dt; l.p.y += l.vy * dt;
      l.p.alpha = l.a0 * k;
      const sz = l.s0 + l.grow * (1 - k);
      l.p.scaleX = l.p.scaleY = sz / 32;
    }
    this.tracers = this.tracers.filter((tr) => {
      tr.t += dt;
      const q = Math.min(1, tr.t / Math.max(1e-3, tr.dur));
      tr.s.x = tr.x0 + (tr.x1 - tr.x0) * q;
      tr.s.y = tr.y0 + (tr.y1 - tr.y0) * q;
      if (q < 1) return true;
      this.impact([tr.x1, tr.y1], false);
      tr.s.visible = false;
      this.tracerPool.push(tr.s);
      return false;
    });
    this.rings = this.rings.filter((rg) => {
      rg.t += dt;
      const q = rg.t / rg.dur;
      if (q >= 1) { rg.s.visible = false; this.ringPool.push(rg.s); return false; }
      rg.s.scale.set((rg.r * (0.2 + 0.8 * q)) / 56);
      rg.s.alpha = 0.8 * (1 - q);
      return true;
    });
    this.throws = this.throws.filter((th) => {
      th.t += dt;
      const q = Math.min(1, th.t / th.dur);
      th.s.x = th.from[0] + (th.to[0] - th.from[0]) * q;
      th.s.y = th.from[1] + (th.to[1] - th.from[1]) * q - Math.sin(q * Math.PI) * 2.5;
      th.s.scale.set((0.5 + Math.sin(q * Math.PI) * 0.4) / 32 * 1.2);
      if (q < 1) return true;
      th.s.destroy();
      return false;
    });
    this.smoke.visible = this.enabled.smoke;
    for (const p of this.puffs) {
      p.x += p.vx * dt; p.y += p.vy * dt;
      p.s.x = p.x + Math.sin(now / 1700 + p.ph) * 0.4;
      p.s.y = p.y + Math.cos(now / 2100 + p.ph) * 0.4;
      p.s.alpha = p.a * (0.85 + 0.15 * Math.sin(now / 900 + p.ph));
    }
  }
}
