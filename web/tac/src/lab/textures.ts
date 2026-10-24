// 實驗頁用的程式產生貼圖：個體符號（依陣營不同形狀）、柔和圓點、曳光、衝擊環、煙霧。
// 全部畫成白色，用 tint 上色；個體符號的「前方」朝東（+x）。

import { Graphics, Rectangle, type Renderer, type Texture } from "pixi.js";

export interface LabTextures {
  unit: Record<"own" | "ally" | "remnant" | "enemy", Texture>;
  downed: Texture;
  dot: Texture;
  tracer: Texture;
  ring: Texture;
  puff: Texture;
  ghost: Texture;
}

const U = 64;   // 個體貼圖邊長（px）

function bake(r: Renderer, g: Graphics, w: number, h: number): Texture {
  const tex = r.generateTexture({ target: g, frame: new Rectangle(0, 0, w, h), resolution: 2, antialias: true });
  g.destroy();
  return tex;
}

/** 朝向楔形＋槍線（畫在身體前方） */
function wedge(g: Graphics) {
  g.poly([U * 0.62, U * 0.36, U * 0.86, U * 0.5, U * 0.62, U * 0.64]).fill({ color: 0xffffff });
  g.rect(U * 0.5, U * 0.47, U * 0.48, U * 0.06).fill({ color: 0xffffff });
}

export function makeTextures(r: Renderer): LabTextures {
  const c = U / 2;
  const own = new Graphics().circle(c, c, U * 0.24).fill({ color: 0xffffff }).circle(c, c, U * 0.24).stroke({ width: 3, color: 0x000000, alpha: 0.5 });
  wedge(own);
  const ally = new Graphics().roundRect(c - U * 0.22, c - U * 0.22, U * 0.44, U * 0.44, U * 0.1).fill({ color: 0xffffff })
    .roundRect(c - U * 0.22, c - U * 0.22, U * 0.44, U * 0.44, U * 0.1).stroke({ width: 3, color: 0x000000, alpha: 0.5 });
  wedge(ally);
  const rem = new Graphics().circle(c, c, U * 0.22).fill({ color: 0xffffff }).circle(c, c, U * 0.3).stroke({ width: 3, color: 0xffffff, alpha: 0.6 });
  wedge(rem);
  const dia = [c, c - U * 0.28, c + U * 0.28, c, c, c + U * 0.28, c - U * 0.28, c];
  const enemy = new Graphics().poly(dia).fill({ color: 0xffffff }).poly(dia).stroke({ width: 3, color: 0x000000, alpha: 0.5 });
  wedge(enemy);
  const downed = new Graphics().roundRect(c - U * 0.32, c - U * 0.13, U * 0.64, U * 0.26, U * 0.13).fill({ color: 0xffffff, alpha: 0.75 });
  const ghost = new Graphics().poly(dia).stroke({ width: 4, color: 0xffffff });

  const dot = new Graphics();
  for (let i = 8; i >= 1; i--) dot.circle(16, 16, i * 2).fill({ color: 0xffffff, alpha: 0.14 });
  const tracer = new Graphics();
  for (let i = 0; i < 32; i++) tracer.rect(i * 2, 1, 2, 2).fill({ color: 0xffffff, alpha: (i / 31) ** 2 });
  tracer.rect(60, 0, 4, 4).fill({ color: 0xffffff });
  const ring = new Graphics().circle(64, 64, 56).stroke({ width: 8, color: 0xffffff });
  const puff = new Graphics();
  for (let i = 16; i >= 1; i--) puff.circle(64, 64, i * 4).fill({ color: 0xffffff, alpha: 0.06 });

  return {
    unit: { own: bake(r, own, U, U), ally: bake(r, ally, U, U), remnant: bake(r, rem, U, U), enemy: bake(r, enemy, U, U) },
    downed: bake(r, downed, U, U),
    ghost: bake(r, ghost, U, U),
    dot: bake(r, dot, 32, 32),
    tracer: bake(r, tracer, 64, 4),
    ring: bake(r, ring, 128, 128),
    puff: bake(r, puff, 128, 128),
  };
}

export const SIDE_COLOR: Record<string, number> = { own: 0x6fe37f, ally: 0x7fd4c4, remnant: 0xa6e3c8, enemy: 0xe0584d };
