// 壓力測試用的假戰場（S0 技術驗證）：不經伺服器，自己產生地形、個體走動、射擊與爆炸，
// 用來量 renderer 在大量個體與特效下的幀率。規則是隨便的，只求負載像真的交火；同一個種子結果相同。

import type { WorldTerrain, XY } from "./protocol";
import { makeRng, randInt, type Rng } from "./rng";

const BLOCK = new Set(["#", "|", "W", "H"]);

export function walkable(t: WorldTerrain, x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= t.w || y >= t.h) return false;
  return !BLOCK.has(t.terrain[y][x]);
}

/** 假地形：一般街區（建築外牆、窗、門、矮牆、瓦礫、植被），格式同 /api/world_terrain。 */
export function makeTerrain(seed: number, w = 125, h = 125): WorldTerrain {
  const rng = makeRng(seed);
  const g: string[][] = Array.from({ length: h }, () => Array.from({ length: w }, () => "."));
  const rooms: WorldTerrain["rooms"] = [];
  for (let i = 0; i < 60; i++) {
    const bw = randInt(rng, 5, 11), bh = randInt(rng, 5, 9);
    if (bw + 3 > w || bh + 3 > h) continue;
    const x0 = randInt(rng, 1, w - bw - 2), y0 = randInt(rng, 1, h - bh - 2);
    const x1 = x0 + bw - 1, y1 = y0 + bh - 1;
    let clash = false;
    for (let y = y0 - 1; y <= y1 + 1 && !clash; y++) for (let x = x0 - 1; x <= x1 + 1; x++) if (g[y][x] !== ".") { clash = true; break; }
    if (clash) continue;
    const doors: XY[] = [];
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const edge = x === x0 || x === x1 || y === y0 || y === y1;
        if (!edge) continue;
        const corner = (x === x0 || x === x1) && (y === y0 || y === y1);
        const r = rng();
        if (!corner && r < 0.08) { g[y][x] = "."; doors.push([x, y]); }
        else g[y][x] = !corner && r < 0.3 ? "W" : "#";
      }
    }
    if (!doors.length) { g[y0][x0 + 2] = "."; doors.push([x0 + 2, y0]); }
    rooms.push({ id: rooms.length, rect: [x0, y0, x1, y1], doors });
  }
  for (let i = 0; i < 500; i++) {
    const x = randInt(rng, 0, w - 1), y = randInt(rng, 0, h - 1);
    if (g[y][x] !== ".") continue;
    const r = rng();
    g[y][x] = r < 0.4 ? "=" : r < 0.7 ? "%" : '"';
  }
  const rows = ["A", "B", "C", "D", "E"], cols = ["1", "2", "3", "4", "5"];
  const names: Record<string, string> = {};
  for (const r of rows) for (const c of cols) names[r + c] = "測試街區";
  return { w, h, cell: Math.round(w / 5), terrain: g.map((row) => row.join("")), rows, cols, names, rooms };
}

export interface StressUnit {
  id: string;
  side: "own" | "ally" | "enemy";
  x: number;
  y: number;
  /** 朝向（弧度，0＝東，逆時針） */
  facing: number;
  posture: "stand" | "crouch" | "prone";
  down: boolean;
  tx: number;
  ty: number;
  cool: number;
  burst: number;
  target: StressUnit | null;
}

export type StressEvent =
  | { kind: "shot"; t: number; from: XY; to: XY; hit: boolean; shooter: string }
  | { kind: "throw"; t: number; from: XY; to: XY; land: number }
  | { kind: "blast"; t: number; at: XY };

const SPEED = 1.5;          // 格／秒（約 3 m/s）
const RANGE = 40;           // 射程（格）

export class StressScene {
  readonly units: StressUnit[] = [];
  t = 0;
  private rng: Rng;
  private nextThrow = 1.5;
  private pending: { t: number; at: XY }[] = [];

  constructor(readonly terrain: WorldTerrain, n: number, seed = 1, readonly center: XY = [terrain.w / 2, terrain.h / 2]) {
    this.rng = makeRng(seed);
    const sides: StressUnit["side"][] = ["own", "ally", "enemy"];
    for (let i = 0; i < n; i++) {
      const side = i % 2 === 0 ? "enemy" : sides[(i >> 1) % 2];
      const [x, y] = this.spot(side === "enemy" ? 12 : -12);
      this.units.push({ id: `u${i}`, side, x, y, facing: 0, posture: "stand", down: false, tx: x, ty: y,
        cool: this.rng() * 2, burst: 0, target: null });
    }
  }

  /** 在中心附近（往東或往西偏 dx）找一格可站的地方 */
  private spot(dx: number): XY {
    for (let k = 0; k < 200; k++) {
      const x = Math.floor(this.center[0] + dx + (this.rng() - 0.5) * 30);
      const y = Math.floor(this.center[1] + (this.rng() - 0.5) * 40);
      if (walkable(this.terrain, x, y)) return [x + 0.5, y + 0.5];
    }
    return [this.center[0], this.center[1]];
  }

  /** 前進 dt 秒，回傳這段時間發生的事件 */
  step(dt: number): StressEvent[] {
    const out: StressEvent[] = [];
    const t1 = this.t + dt;
    for (const u of this.units) {
      if (u.down) continue;
      const dx = u.tx - u.x, dy = u.ty - u.y, d = Math.hypot(dx, dy);
      if (d < 0.05) {
        const nx = Math.floor(u.x + (this.rng() - 0.5) * 12), ny = Math.floor(u.y + (this.rng() - 0.5) * 12);
        if (walkable(this.terrain, nx, ny)) { u.tx = nx + 0.5; u.ty = ny + 0.5; }
        if (this.rng() < 0.1) u.posture = (["stand", "crouch", "prone"] as const)[randInt(this.rng, 0, 2)];
      } else {
        const s = Math.min(d, SPEED * dt * (u.posture === "prone" ? 0.3 : u.posture === "crouch" ? 0.7 : 1));
        const nx = u.x + (dx / d) * s, ny = u.y + (dy / d) * s;
        if (walkable(this.terrain, Math.floor(nx), Math.floor(ny))) { u.x = nx; u.y = ny; }
        else { u.tx = u.x; u.ty = u.y; }
        if (!u.target) u.facing = Math.atan2(dy, dx);
      }
      u.cool -= dt;
      if (u.cool > 0) continue;
      if (!u.target || u.target.down) u.target = this.nearestFoe(u);
      if (!u.target) { u.cool = 1; continue; }
      const tg = u.target;
      u.facing = Math.atan2(tg.y - u.y, tg.x - u.x);
      const miss = this.rng() < 0.85;
      const to: XY = miss ? [tg.x + (this.rng() - 0.5) * 3, tg.y + (this.rng() - 0.5) * 3] : [tg.x, tg.y];
      out.push({ kind: "shot", t: t1, from: [u.x, u.y], to, hit: !miss, shooter: u.id });
      if (!miss && this.rng() < 0.05) tg.down = true;
      u.burst = u.burst > 0 ? u.burst - 1 : randInt(this.rng, 0, 3);
      u.cool = u.burst > 0 ? 0.1 : 0.8 + this.rng() * 2.2;
    }
    if (t1 >= this.nextThrow) {
      const live = this.units.filter((u) => !u.down);
      const u = live[randInt(this.rng, 0, Math.max(0, live.length - 1))];
      const tg = u && this.nearestFoe(u);
      if (u && tg) {
        const land = t1 + 1.2;
        out.push({ kind: "throw", t: t1, from: [u.x, u.y], to: [tg.x, tg.y], land });
        this.pending.push({ t: land, at: [tg.x, tg.y] });
      }
      this.nextThrow = t1 + 1.5;
    }
    this.pending = this.pending.filter((p) => {
      if (p.t > t1) return true;
      out.push({ kind: "blast", t: p.t, at: p.at });
      return false;
    });
    for (const u of this.units) if (u.down && this.rng() < 0.02 * dt) u.down = false;   // 倒地的人慢慢「復活」，負載才不會越跑越低
    this.t = t1;
    return out;
  }

  private nearestFoe(u: StressUnit): StressUnit | null {
    let best: StressUnit | null = null, bd = RANGE;
    for (const o of this.units) {
      if (o.down || (o.side === "enemy") === (u.side === "enemy")) continue;
      const d = Math.hypot(o.x - u.x, o.y - u.y);
      if (d < bd) { bd = d; best = o; }
    }
    return best;
  }
}
