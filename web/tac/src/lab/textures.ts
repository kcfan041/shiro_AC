// 實驗頁特效用的程式產生貼圖：柔和圓點、曳光、衝擊環、煙霧（全部白色，用 tint 上色）。
// 個體與地形的貼圖在 src/render/；特效在 S3 搬過去。

import { Graphics, Rectangle, type Renderer, type Texture } from "pixi.js";

export interface LabTextures {
  dot: Texture;
  tracer: Texture;
  ring: Texture;
  puff: Texture;
}

function bake(r: Renderer, g: Graphics, w: number, h: number): Texture {
  const tex = r.generateTexture({ target: g, frame: new Rectangle(0, 0, w, h), resolution: 2, antialias: true });
  g.destroy();
  return tex;
}

export function makeTextures(r: Renderer): LabTextures {
  const dot = new Graphics();
  for (let i = 8; i >= 1; i--) dot.circle(16, 16, i * 2).fill({ color: 0xffffff, alpha: 0.14 });
  const tracer = new Graphics();
  for (let i = 0; i < 32; i++) tracer.rect(i * 2, 1, 2, 2).fill({ color: 0xffffff, alpha: (i / 31) ** 2 });
  tracer.rect(60, 0, 4, 4).fill({ color: 0xffffff });
  const ring = new Graphics().circle(64, 64, 56).stroke({ width: 8, color: 0xffffff });
  const puff = new Graphics();
  for (let i = 16; i >= 1; i--) puff.circle(64, 64, i * 4).fill({ color: 0xffffff, alpha: 0.06 });
  return { dot: bake(r, dot, 32, 32), tracer: bake(r, tracer, 64, 4), ring: bake(r, ring, 128, 128), puff: bake(r, puff, 128, 128) };
}
