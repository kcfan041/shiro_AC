// 地形圖層（方案 §7.1）：每個作戰格（25×25）一塊，畫成貼圖之後只貼圖不重畫。
// 依縮放分三個烘焙級距（每格 8／16／32 px），跨級距才重烘，而且只烘看得到的塊（每幀最多 BAKES_PER_FRAME 塊，
// 還沒烘好的先用低一級的貼圖頂著）；地圖平移時對齊整數像素，所以不閃。
// 牆：相鄰的牆畫成連續的一道（只有朝外的邊才有亮邊與牆面），陰影往東南投、不重疊加深；窗與門依牆的走向畫；
// 植被分兩層：地上的暗色底在個體下面，樹冠在個體上面（半透明）。座標：小格，畫面 y＝h − 世界 y。

import { Container, Graphics, Rectangle, Sprite, Text, type Renderer, type Texture } from "pixi.js";
import { TIERS, tierFor } from "../lod";
import type { WorldTerrain } from "../protocol";
import type { Theme } from "../theme";

const BAKES_PER_FRAME = 3;
const LRU_MAX = 40;

const WALLISH = new Set(["#", "|", "W"]);

function hash(x: number, y: number, k = 0): number {
  let h = (x * 374761393 + y * 668265263 + k * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function shade(c: number, k: number): number {
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v * (1 + k))));
  return (f((c >> 16) & 255) << 16) | (f((c >> 8) & 255) << 8) | f(c & 255);
}

class Grid {
  readonly t: WorldTerrain;
  readonly indoor: Uint8Array;
  readonly doors = new Set<number>();
  constructor(t: WorldTerrain) {
    this.t = t;
    this.indoor = new Uint8Array(t.w * t.h);
    for (const r of t.rooms) {
      const [x0, y0, x1, y1] = r.rect;
      for (let y = y0 + 1; y < y1; y++) for (let x = x0 + 1; x < x1; x++) this.indoor[y * t.w + x] = 1;
      for (const [dx, dy] of r.doors) this.doors.add(dy * t.w + dx);
    }
  }
  ch(x: number, y: number): string {
    if (x < 0 || y < 0 || x >= this.t.w || y >= this.t.h) return ".";
    return this.t.terrain[y][x];
  }
  wall(x: number, y: number): boolean { return WALLISH.has(this.ch(x, y)); }
  ground(x: number, y: number): string { return this.t.ground?.[y]?.[x] ?? "d"; }
  door(x: number, y: number): boolean { return this.doors.has(y * this.t.w + x); }
  isIndoor(x: number, y: number): boolean { return x >= 0 && y >= 0 && x < this.t.w && y < this.t.h && this.indoor[y * this.t.w + x] === 1; }
}

function drawBase(g: Graphics, G: Grid, th: Theme, cx: number, cy: number, n: number, s: number) {
  const ox = cx * n, oy = cy * n;
  // 畫面上第 j 列（由上往下）＝世界 y＝oy + n − 1 − j
  const P = (x: number, y: number): [number, number] => [(x - ox) * s, (oy + n - 1 - y) * s];
  const each = (f: (x: number, y: number, px: number, py: number) => void) => {
    for (let y = oy; y < oy + n && y < G.t.h; y++) for (let x = ox; x < ox + n && x < G.t.w; x++) { const [px, py] = P(x, y); f(x, y, px, py); }
  };
  const lw = Math.max(1, s / 16);
  // 1. 地面：材質底色＋每格一點明暗雜訊；室內地板亮一階；路緣
  each((x, y, px, py) => {
    const gc = G.ground(x, y) as keyof Theme["ground"];
    const base = G.isIndoor(x, y) || G.door(x, y) ? th.indoor : th.ground[gc] ?? th.ground.d;
    g.rect(px, py, s, s).fill({ color: shade(base, (hash(x, y) - 0.5) * 2 * th.noise * (gc === "r" ? 0.5 : 1)) });   // 柏油路比較均勻
    if (s >= 16 && !G.isIndoor(x, y) && hash(x, y, 7) < 0.25) {          // 細小斑點
      const k = hash(x, y, 3);
      g.rect(px + k * s * 0.7, py + hash(x, y, 4) * s * 0.7, s * 0.12, s * 0.12).fill({ color: shade(base, -0.25) });
    }
    if (gc === "r") {
      // 車道中線（街道、路口模板的第 12 條）：虛線，每格畫半格
      const lx = x % n, ly = y % n, tpl = (G.t.templates?.[G.t.rows[Math.floor(y / n)] + G.t.cols[Math.floor(x / n)]] || "").split(":").pop();
      const lane = Math.max(1, s * 0.08);
      if (lx === 12 && (tpl === "street" || (tpl === "crossroad" && Math.abs(ly - 12) > 2)) && y % 2 === 0)
        g.rect(px + s / 2 - lane / 2, py + s * 0.1, lane, s * 0.8).fill({ color: th.lane });
      if (ly === 12 && tpl === "crossroad" && Math.abs(lx - 12) > 2 && x % 2 === 0)
        g.rect(px + s * 0.1, py + s / 2 - lane / 2, s * 0.8, lane).fill({ color: th.lane });
      const edge = (nx: number, ny: number) => !G.wall(nx, ny) && G.ground(nx, ny) !== "r";
      if (edge(x - 1, y)) g.rect(px, py, lw, s).fill({ color: th.curb });
      if (edge(x + 1, y)) g.rect(px + s - lw, py, lw, s).fill({ color: th.curb });
      if (edge(x, y + 1)) g.rect(px, py, s, lw).fill({ color: th.curb });
      if (edge(x, y - 1)) g.rect(px, py + s - lw, s, lw).fill({ color: th.curb });
    }
  });
  // 2. 陰影（光從西北來）：在非牆格裡畫被西邊、北邊的牆擋住的部分，彼此不重疊
  const sh = s * 0.28, shLow = s * 0.14;
  each((x, y, px, py) => {
    if (G.wall(x, y)) return;
    const W = G.wall(x - 1, y), N = G.wall(x, y + 1), NW = G.wall(x - 1, y + 1);
    const lowW = G.ch(x - 1, y) === "=", lowN = G.ch(x, y + 1) === "=";
    const a = th.shadowAlpha;
    if (W) g.rect(px, py, sh, s).fill({ color: th.shadow, alpha: a });
    else if (lowW) g.rect(px, py, shLow, s).fill({ color: th.shadow, alpha: a * 0.7 });
    const left = W ? sh : lowW ? shLow : 0;
    if (N) g.rect(px + left, py, s - left, sh).fill({ color: th.shadow, alpha: a });
    else if (lowN) g.rect(px + left, py, s - left, shLow).fill({ color: th.shadow, alpha: a * 0.7 });
    if (NW && !W && !N) g.rect(px, py, sh, sh).fill({ color: th.shadow, alpha: a });
  });
  // 3. 地上的東西：牆、窗、門、矮牆、瓦礫、植被底、樓梯
  each((x, y, px, py) => {
    const c = G.ch(x, y);
    if (c === "#" || c === "|" || c === "W") {
      const win = c === "W";
      g.rect(px, py, s, s).fill({ color: win ? th.window : th.wall });
      const top = s * 0.14, face = s * 0.2;
      if (!G.wall(x, y + 1)) g.rect(px, py, s, top).fill({ color: th.wallTop });             // 北邊：上緣亮邊
      if (!G.wall(x - 1, y)) g.rect(px, py, top * 0.6, s).fill({ color: shade(th.wallTop, -0.15) });
      if (!G.wall(x, y - 1)) g.rect(px, py + s - face, s, face).fill({ color: th.wallSide });  // 南邊：牆面
      if (!G.wall(x + 1, y)) g.rect(px + s - top * 0.6, py, top * 0.6, s).fill({ color: th.wallSide });
      if (win) {
        const horiz = G.wall(x - 1, y) || G.wall(x + 1, y);
        if (horiz) g.rect(px + s * 0.1, py + s * 0.42, s * 0.8, Math.max(1, s * 0.12)).fill({ color: th.glass });
        else g.rect(px + s * 0.42, py + s * 0.1, Math.max(1, s * 0.12), s * 0.8).fill({ color: th.glass });
      } else if (s >= 16 && hash(x, y, 9) < 0.18 && !G.wall(x, y + 1)) {                       // 上緣小缺口
        g.rect(px + hash(x, y, 2) * s * 0.6, py, s * 0.25, s * 0.1).fill({ color: th.wall });
      }
    } else if (G.door(x, y)) {
      const horiz = G.wall(x - 1, y) || G.wall(x + 1, y);
      const post = s * 0.18;
      if (horiz) {
        g.rect(px, py + s * 0.3, post, s * 0.4).fill({ color: th.door });
        g.rect(px + s - post, py + s * 0.3, post, s * 0.4).fill({ color: th.door });
      } else {
        g.rect(px + s * 0.3, py, s * 0.4, post).fill({ color: th.door });
        g.rect(px + s * 0.3, py + s - post, s * 0.4, post).fill({ color: th.door });
      }
    } else if (c === "=") {
      const vert = (G.ch(x, y + 1) === "=" || G.ch(x, y - 1) === "=") && G.ch(x - 1, y) !== "=" && G.ch(x + 1, y) !== "=";
      if (vert) {
        g.rect(px + s * 0.34, py, s * 0.32, s).fill({ color: th.low });
        g.rect(px + s * 0.34, py, s * 0.1, s).fill({ color: th.lowTop });
      } else {
        g.rect(px, py + s * 0.34, s, s * 0.32).fill({ color: th.low });
        g.rect(px, py + s * 0.34, s, s * 0.1).fill({ color: th.lowTop });
      }
    } else if (c === "%") {
      for (let k = 0; k < 3; k++) {
        const bx = px + hash(x, y, 10 + k) * s * 0.65, by = py + hash(x, y, 20 + k) * s * 0.65, r = s * (0.12 + hash(x, y, 30 + k) * 0.14);
        g.poly([bx, by + r, bx + r * 0.8, by, bx + r * 2, by + r * 0.4, bx + r * 1.6, by + r * 1.6, bx + r * 0.4, by + r * 1.8])
          .fill({ color: shade(th.rubble, (hash(x, y, 40 + k) - 0.5) * 0.4) });
      }
    } else if (c === '"') {
      g.circle(px + s / 2, py + s / 2, s * 0.55).fill({ color: th.vegBase, alpha: 0.9 });
    } else if (c === "H") {
      for (let k = 0; k < 4; k++) g.rect(px + s * 0.1, py + s * (0.12 + k * 0.22), s * 0.8, Math.max(1, s * 0.08)).fill({ color: th.stair });
    }
  });
}

function drawCanopy(g: Graphics, G: Grid, th: Theme, cx: number, cy: number, n: number, s: number): boolean {
  const ox = cx * n, oy = cy * n;
  let any = false;
  for (let y = oy; y < oy + n && y < G.t.h; y++) {
    for (let x = ox; x < ox + n && x < G.t.w; x++) {
      if (G.ch(x, y) !== '"') continue;
      any = true;
      const px = (x - ox) * s + s / 2, py = (oy + n - 1 - y) * s + s / 2;
      for (let k = 0; k < 3; k++) {
        const r = s * (0.32 + hash(x, y, 50 + k) * 0.2);
        const dx = (hash(x, y, 60 + k) - 0.5) * s * 0.6, dy = (hash(x, y, 70 + k) - 0.5) * s * 0.6;
        g.circle(px + dx, py + dy, r).fill({ color: shade(th.canopy, (hash(x, y, 80 + k) - 0.5) * 0.3), alpha: 0.78 });
      }
      g.circle(px - s * 0.12, py - s * 0.12, s * 0.18).fill({ color: shade(th.canopy, 0.35), alpha: 0.35 });
    }
  }
  return any;
}

interface Baked { base: Texture; canopy: Texture | null; used: number }

export class TerrainLayer {
  /** 個體下面：地面、牆、陰影 */
  readonly base = new Container();
  /** 個體上面：樹冠 */
  readonly canopy = new Container();
  /** 地面上（個體與煙下面）：格界、小格格線、格子名稱 */
  readonly overlay = new Container();
  private G: Grid;
  private n: number;
  private cw: number;
  private ch: number;
  private baked = new Map<string, Baked>();
  private baseSprites: Sprite[] = [];
  private canopySprites: Sprite[] = [];
  private grid = new Graphics();
  private fine = new Graphics();
  private labels: Text[] = [];
  private gridKey = "";
  private fineKey = "";
  private frame = 0;
  bakeCount = 0;
  tier = TIERS[0];

  constructor(private r: Renderer, readonly T: WorldTerrain, private th: Theme) {
    this.G = new Grid(T);
    this.n = T.cell || 25;
    this.cw = Math.ceil(T.w / this.n);
    this.ch = Math.ceil(T.h / this.n);
    for (let cy = 0; cy < this.ch; cy++) {
      for (let cx = 0; cx < this.cw; cx++) {
        const b = new Sprite(), c = new Sprite();
        b.x = c.x = cx * this.n;
        b.y = c.y = T.h - (cy + 1) * this.n;
        this.base.addChild(b);
        this.canopy.addChild(c);
        this.baseSprites.push(b);
        this.canopySprites.push(c);
        this.show(cx, cy, this.bake(cx, cy, TIERS[0]), TIERS[0]);   // 最低級距一開始全部烘好，整張圖馬上看得到
      }
    }
    this.overlay.addChild(this.fine, this.grid);
    for (let r2 = 0; r2 < T.rows.length; r2++) {
      for (let c = 0; c < T.cols.length; c++) {
        const id = T.rows[r2] + T.cols[c];
        const t = new Text({ text: `${id} ${T.names[id] || ""}`, style: { fontFamily: "monospace", fontSize: 24, fill: th.labelColor } });
        t.alpha = 0.8;
        t.x = c * this.n + 0.4;
        t.y = T.h - (r2 + 1) * this.n + 0.3;
        this.overlay.addChild(t);
        this.labels.push(t);
      }
    }
  }

  private bake(cx: number, cy: number, s: number): Baked {
    const key = `${cx},${cy},${s}`;
    const hit = this.baked.get(key);
    if (hit) { hit.used = this.frame; return hit; }
    const n = this.n;
    const gb = new Graphics();
    drawBase(gb, this.G, this.th, cx, cy, n, s);
    const base = this.r.generateTexture({ target: gb, frame: new Rectangle(0, 0, n * s, n * s), resolution: 1, antialias: true });
    gb.destroy();
    const gc = new Graphics();
    const any = drawCanopy(gc, this.G, this.th, cx, cy, n, s);
    const canopy = any ? this.r.generateTexture({ target: gc, frame: new Rectangle(0, 0, n * s, n * s), resolution: 1, antialias: true }) : null;
    gc.destroy();
    const b = { base, canopy, used: this.frame };
    this.baked.set(key, b);
    this.bakeCount++;
    if (this.baked.size > LRU_MAX + this.cw * this.ch) {               // 最低級距常駐，其他的照最近使用丟
      const old = [...this.baked.entries()].filter(([k]) => !k.endsWith(`,${TIERS[0]}`)).sort((a, b2) => a[1].used - b2[1].used)[0];
      if (old) {
        const [k, v] = old;
        const [ox, oy, os] = k.split(",").map(Number);
        const i = oy * this.cw + ox;
        if (this.baseSprites[i].texture === v.base) this.show(ox, oy, this.baked.get(`${ox},${oy},${TIERS[0]}`)!, TIERS[0]);
        v.base.destroy(true);
        v.canopy?.destroy(true);
        this.baked.delete(k);
        void os;
      }
    }
    return b;
  }

  private show(cx: number, cy: number, b: Baked, s: number) {
    const i = cy * this.cw + cx;
    const bs = this.baseSprites[i], cs = this.canopySprites[i];
    bs.texture = b.base;
    bs.scale.set(1 / s);
    cs.visible = !!b.canopy;
    if (b.canopy) { cs.texture = b.canopy; cs.scale.set(1 / s); }
  }

  /** 每幀：view＝看得到的世界範圍（畫面方向座標 [x0, y0, x1, y1]），zoom＝每格 CSS px，dpr＝裝置像素比 */
  update(view: [number, number, number, number], zoom: number, dpr: number) {
    this.frame++;
    this.tier = tierFor(zoom * dpr);
    const n = this.n, H = this.T.h;
    let bakes = 0;
    for (let cy = 0; cy < this.ch; cy++) {
      for (let cx = 0; cx < this.cw; cx++) {
        const x0 = cx * n, y0 = H - (cy + 1) * n;
        const vis = x0 + n >= view[0] && x0 <= view[2] && y0 + n >= view[1] && y0 <= view[3];
        if (!vis) continue;
        const want = this.baked.get(`${cx},${cy},${this.tier}`);
        if (want) { want.used = this.frame; this.show(cx, cy, want, this.tier); continue; }
        if (bakes < BAKES_PER_FRAME) { bakes++; this.show(cx, cy, this.bake(cx, cy, this.tier), this.tier); }
      }
    }
    // 格界：線寬固定 1 個 CSS px
    const gk = zoom.toFixed(2);
    if (gk !== this.gridKey) {
      this.gridKey = gk;
      this.grid.clear();
      for (let i = 0; i <= this.cw; i++) this.grid.moveTo(i * n, 0).lineTo(i * n, H);
      for (let j = 0; j <= this.ch; j++) this.grid.moveTo(0, j * n).lineTo(this.T.w, j * n);
      this.grid.stroke({ width: 1 / zoom, color: this.th.grid, alpha: 0.55 });
    }
    // 小格格線：只在個體級距、只畫看得到的範圍
    const fine = zoom >= 16;
    this.fine.visible = fine;
    if (fine) {
      const fx0 = Math.max(0, Math.floor(view[0])), fx1 = Math.min(this.T.w, Math.ceil(view[2]));
      const fy0 = Math.max(0, Math.floor(view[1])), fy1 = Math.min(H, Math.ceil(view[3]));
      const fk = `${fx0},${fx1},${fy0},${fy1},${gk}`;
      if (fk !== this.fineKey) {
        this.fineKey = fk;
        this.fine.clear();
        for (let x = fx0; x <= fx1; x++) this.fine.moveTo(x, fy0).lineTo(x, fy1);
        for (let y = fy0; y <= fy1; y++) this.fine.moveTo(fx0, y).lineTo(fx1, y);
        this.fine.stroke({ width: 1 / zoom, color: this.th.gridFine, alpha: 0.45 });
      }
    }
    // 格子名稱：固定約 12 CSS px；拉遠時大一點
    const px = zoom < 6 ? 14 : 12;
    for (const t of this.labels) {
      t.scale.set(px / 24 / zoom);
      t.resolution = Math.max(1, dpr * 2);
    }
  }
}
