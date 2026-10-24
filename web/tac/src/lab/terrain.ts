// 地形烘焙：整張全圖切成每個作戰格一塊（25×25 小格），每塊畫成一張貼圖，之後只貼圖不重畫。
// S0 只驗證「分塊烘焙＋貼圖縮放」的效能與清晰度；正式的地形外觀在 S2。

import { Container, Graphics, Rectangle, Sprite, Text, type Renderer } from "pixi.js";
import type { WorldTerrain } from "../protocol";

const GROUND = 0x0c120e, INDOOR = 0x131c16;
const WALL = 0x3d4a40, WALL_TOP = 0x6b7d6f, SHADOW = 0x000000;
const WINDOW = 0x2a4458, GLASS = 0x7fb0d0;
const LOW = 0x55603e, RUBBLE = 0x4d4232, VEG = 0x1d3a20, STAIR = 0x666666;

/** 每格幾 px 烘焙（縮放倍率跨級距時才需要重烘，S0 先固定一個級距） */
export const BAKE_PX = 16;

function indoorMask(t: WorldTerrain): Uint8Array {
  const m = new Uint8Array(t.w * t.h);
  for (const r of t.rooms) {
    const [x0, y0, x1, y1] = r.rect;
    for (let y = y0 + 1; y < y1; y++) for (let x = x0 + 1; x < x1; x++) m[y * t.w + x] = 1;
  }
  return m;
}

function drawChunk(g: Graphics, t: WorldTerrain, indoor: Uint8Array, cx: number, cy: number, n: number) {
  const s = BAKE_PX;
  g.rect(0, 0, n * s, n * s).fill({ color: GROUND });
  // 先畫地板與陰影，再畫牆（陰影才不會蓋到牆）
  for (let j = 0; j < n; j++) {
    const y = cy * n + (n - 1 - j);
    if (y < 0 || y >= t.h) continue;
    for (let i = 0; i < n; i++) {
      const x = cx * n + i;
      if (x >= t.w) continue;
      const ch = t.terrain[y][x];
      const px = i * s, py = j * s;
      if (indoor[y * t.w + x]) g.rect(px, py, s, s).fill({ color: INDOOR });
      if (ch === "#" || ch === "|" || ch === "W") g.rect(px + s * 0.25, py + s * 0.25, s, s).fill({ color: SHADOW, alpha: 0.35 });
    }
  }
  for (let j = 0; j < n; j++) {
    const y = cy * n + (n - 1 - j);
    if (y < 0 || y >= t.h) continue;
    for (let i = 0; i < n; i++) {
      const x = cx * n + i;
      if (x >= t.w) continue;
      const ch = t.terrain[y][x];
      const px = i * s, py = j * s;
      switch (ch) {
        case "#":
        case "|":
          g.rect(px, py, s, s).fill({ color: WALL });
          g.rect(px, py, s, 2).fill({ color: WALL_TOP });
          break;
        case "W":
          g.rect(px, py, s, s).fill({ color: WINDOW });
          g.rect(px + 2, py + s / 2 - 1, s - 4, 2).fill({ color: GLASS });
          break;
        case "=":
          g.rect(px + 1, py + s * 0.35, s - 2, s * 0.3).fill({ color: LOW });
          break;
        case "%":
          g.rect(px + s * 0.15, py + s * 0.2, s * 0.35, s * 0.3).fill({ color: RUBBLE });
          g.rect(px + s * 0.55, py + s * 0.5, s * 0.3, s * 0.3).fill({ color: RUBBLE });
          break;
        case '"':
          g.circle(px + s / 2, py + s / 2, s * 0.55).fill({ color: VEG, alpha: 0.85 });
          break;
        case "H":
          for (let k = 0; k < 4; k++) g.rect(px + 2, py + 2 + k * (s / 4), s - 4, 2).fill({ color: STAIR });
          break;
      }
    }
  }
}

/** 建好地形圖層（單位＝小格；y 已經翻轉成畫面座標：畫面 y＝h − 世界 y） */
export function buildTerrain(r: Renderer, t: WorldTerrain): Container {
  const root = new Container();
  const n = t.cell || 25;
  const indoor = indoorMask(t);
  const cw = Math.ceil(t.w / n), chh = Math.ceil(t.h / n);
  for (let cy = 0; cy < chh; cy++) {
    for (let cx = 0; cx < cw; cx++) {
      const g = new Graphics();
      drawChunk(g, t, indoor, cx, cy, n);
      const tex = r.generateTexture({ target: g, frame: new Rectangle(0, 0, n * BAKE_PX, n * BAKE_PX), resolution: 1 });
      g.destroy();
      const sp = new Sprite(tex);
      sp.x = cx * n;
      sp.y = t.h - (cy + 1) * n;
      sp.scale.set(1 / BAKE_PX);
      root.addChild(sp);
    }
  }
  // 作戰格邊界與名稱
  const grid = new Graphics();
  for (let i = 0; i <= cw; i++) grid.moveTo(i * n, 0).lineTo(i * n, t.h);
  for (let j = 0; j <= chh; j++) grid.moveTo(0, j * n).lineTo(t.w, j * n);
  grid.stroke({ width: 0.12, color: 0x50825f, alpha: 0.5 });
  root.addChild(grid);
  for (let r2 = 0; r2 < t.rows.length; r2++) {
    for (let c = 0; c < t.cols.length; c++) {
      const id = t.rows[r2] + t.cols[c];
      const label = new Text({ text: `${id} ${t.names[id] || ""}`, style: { fontFamily: "monospace", fontSize: 28, fill: 0x8cbe96 } });
      label.alpha = 0.75;
      label.scale.set(1 / 28);
      label.x = c * n + 0.5;
      label.y = t.h - (r2 + 1) * n + 0.4;
      root.addChild(label);
    }
  }
  return root;
}
