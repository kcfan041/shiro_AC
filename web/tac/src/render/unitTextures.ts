// 個體用的程式產生貼圖（全部白色，用 tint 上色）。前方一律朝東（+x）；一格＝U px。
// 符號：我方圓形、友軍圓角方形、殘部圓形加虛線外圈、敵人菱形（辨識不只靠顏色）。
// 機兵剪影的零件：肩甲（依陣營不同形狀）、軀幹、頭、面罩、背包、腿、槍；狀態圈與標記。

import { Graphics, Rectangle, type Renderer, type Texture } from "pixi.js";

export const U = 128;
const C = U / 2;

export interface UnitTextures {
  symbol: Record<"own" | "ally" | "remnant" | "enemy", Texture>;
  symbolDown: Texture;
  ghost: Texture;
  shoulders: Record<"own" | "ally" | "remnant" | "enemy", Texture>;
  torso: Texture;
  head: Texture;
  visor: Texture;
  pack: Texture;
  legs: Texture;
  gun: Texture;
  shadow: Texture;
  ring: Texture;
  ringThick: Texture;
  ringDash: Texture;
  cross: Texture;
  frame: Texture;
  bipod: Texture;
  badge: Texture;
  /** 機兵腳下的底盤：形狀跟符號一樣分陣營（灰階也分得出來） */
  base: Record<"own" | "ally" | "remnant" | "enemy", Texture>;
}

function bake(r: Renderer, g: Graphics, w = U, h = U): Texture {
  const t = r.generateTexture({ target: g, frame: new Rectangle(0, 0, w, h), resolution: 1, antialias: true });
  g.destroy();
  return t;
}

const OUT = { width: 5, color: 0x000000, alpha: 0.55 };

function wedge(g: Graphics) {
  g.poly([C + U * 0.13, C - U * 0.14, C + U * 0.4, C, C + U * 0.13, C + U * 0.14]).fill({ color: 0xffffff });
  g.rect(C, C - U * 0.03, U * 0.47, U * 0.06).fill({ color: 0xffffff });
}

function dashedCircle(g: Graphics, r: number, width: number, n = 14) {
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * Math.PI * 2, a1 = a0 + (Math.PI * 2) / n * 0.55;
    g.moveTo(C + Math.cos(a0) * r, C + Math.sin(a0) * r).arc(C, C, r, a0, a1).stroke({ width, color: 0xffffff });
  }
}

export function makeUnitTextures(r: Renderer): UnitTextures {
  const dia = (k: number) => [C, C - U * k, C + U * k, C, C, C + U * k, C - U * k, C];
  const own = new Graphics().circle(C, C, U * 0.25).fill({ color: 0xffffff }).circle(C, C, U * 0.25).stroke(OUT);
  wedge(own);
  const ally = new Graphics().roundRect(C - U * 0.23, C - U * 0.23, U * 0.46, U * 0.46, U * 0.09).fill({ color: 0xffffff })
    .roundRect(C - U * 0.23, C - U * 0.23, U * 0.46, U * 0.46, U * 0.09).stroke(OUT);
  wedge(ally);
  const rem = new Graphics().circle(C, C, U * 0.22).fill({ color: 0xffffff }).circle(C, C, U * 0.22).stroke(OUT);
  dashedCircle(rem, U * 0.32, 6);
  wedge(rem);
  const enemy = new Graphics().poly(dia(0.3)).fill({ color: 0xffffff }).poly(dia(0.3)).stroke(OUT);
  wedge(enemy);
  const down = new Graphics().roundRect(C - U * 0.34, C - U * 0.14, U * 0.68, U * 0.28, U * 0.14).fill({ color: 0xffffff })
    .roundRect(C - U * 0.34, C - U * 0.14, U * 0.68, U * 0.28, U * 0.14).stroke(OUT);
  const ghost = new Graphics();
  const d = dia(0.28);
  for (let i = 0; i < 4; i++) {                                  // 虛線菱形
    const [x0, y0, x1, y1] = [d[i * 2], d[i * 2 + 1], d[(i * 2 + 2) % 8], d[(i * 2 + 3) % 8]];
    for (let k = 0; k < 3; k++) {
      const a = k / 3, b = a + 0.2;
      ghost.moveTo(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a).lineTo(x0 + (x1 - x0) * b, y0 + (y1 - y0) * b);
    }
  }
  ghost.stroke({ width: 7, color: 0xffffff });

  // 機兵零件：肩甲橫跨（y 方向），寬約 0.8 格
  const sh = {
    own: new Graphics().roundRect(C - U * 0.17, C - U * 0.4, U * 0.34, U * 0.8, U * 0.17).fill({ color: 0xffffff }),
    ally: new Graphics().roundRect(C - U * 0.16, C - U * 0.4, U * 0.32, U * 0.8, U * 0.05).fill({ color: 0xffffff }),
    remnant: new Graphics().roundRect(C - U * 0.15, C - U * 0.36, U * 0.3, U * 0.72, U * 0.15).fill({ color: 0xffffff }),
    enemy: new Graphics().poly([C - U * 0.16, C - U * 0.3, C + U * 0.06, C - U * 0.44, C + U * 0.18, C - U * 0.26,
      C + U * 0.18, C + U * 0.26, C + U * 0.06, C + U * 0.44, C - U * 0.16, C + U * 0.3]).fill({ color: 0xffffff }),
  };
  for (const g of Object.values(sh)) g.stroke({ width: 4, color: 0x000000, alpha: 0.5 });
  const torso = new Graphics().roundRect(C - U * 0.14, C - U * 0.22, U * 0.28, U * 0.44, U * 0.08).fill({ color: 0xffffff });
  const head = new Graphics().circle(C, C, U * 0.13).fill({ color: 0xffffff }).circle(C, C, U * 0.13).stroke({ width: 3, color: 0x000000, alpha: 0.5 });
  const visor = new Graphics().roundRect(C + U * 0.04, C - U * 0.1, U * 0.08, U * 0.2, U * 0.03).fill({ color: 0xffffff });
  const pack = new Graphics().roundRect(C - U * 0.12, C - U * 0.17, U * 0.18, U * 0.34, U * 0.04).fill({ color: 0xffffff });
  const legs = new Graphics()
    .roundRect(C - U * 0.4, C - U * 0.2, U * 0.4, U * 0.13, U * 0.06).fill({ color: 0xffffff })
    .roundRect(C - U * 0.4, C + U * 0.07, U * 0.4, U * 0.13, U * 0.06).fill({ color: 0xffffff });
  const gun = new Graphics().rect(0, 0, U, U * 0.1).fill({ color: 0xffffff }).rect(U * 0.82, 0, U * 0.18, U * 0.1).fill({ color: 0xdddddd });
  const shadow = new Graphics();
  for (let i = 10; i >= 1; i--) shadow.circle(C, C, U * 0.045 * i).fill({ color: 0xffffff, alpha: 0.09 });
  const ring = new Graphics().circle(C, C, U * 0.44).stroke({ width: 5, color: 0xffffff });
  const ringThick = new Graphics().circle(C, C, U * 0.44).stroke({ width: 11, color: 0xffffff });
  const ringDash = new Graphics();
  dashedCircle(ringDash, U * 0.46, 5, 12);
  const cross = new Graphics().rect(C - U * 0.06, C - U * 0.2, U * 0.12, U * 0.4).fill({ color: 0xffffff })
    .rect(C - U * 0.2, C - U * 0.06, U * 0.4, U * 0.12).fill({ color: 0xffffff });
  const frame = new Graphics().rect(U * 0.08, U * 0.08, U * 0.84, U * 0.84).stroke({ width: 6, color: 0xffffff });
  const bipod = new Graphics().moveTo(0, U * 0.15).lineTo(U * 0.12, U * 0.5).lineTo(0, U * 0.85).stroke({ width: 8, color: 0xffffff });
  const badge = new Graphics().roundRect(4, 4, U - 8, U * 0.6 - 8, U * 0.12).fill({ color: 0xffffff })
    .roundRect(4, 4, U - 8, U * 0.6 - 8, U * 0.12).stroke({ width: 6, color: 0x000000, alpha: 0.6 });

  const bw = 7;
  const bOwn = new Graphics().circle(C, C, U * 0.44).stroke({ width: bw, color: 0xffffff });
  const bAlly = new Graphics().roundRect(C - U * 0.4, C - U * 0.4, U * 0.8, U * 0.8, U * 0.1).stroke({ width: bw, color: 0xffffff });
  const bEnemy = new Graphics().poly(dia(0.47)).stroke({ width: bw, color: 0xffffff });
  const bRem = new Graphics();
  dashedCircle(bRem, U * 0.44, bw, 12);

  return {
    base: { own: bake(r, bOwn), ally: bake(r, bAlly), remnant: bake(r, bRem), enemy: bake(r, bEnemy) },
    symbol: { own: bake(r, own), ally: bake(r, ally), remnant: bake(r, rem), enemy: bake(r, enemy) },
    symbolDown: bake(r, down),
    ghost: bake(r, ghost),
    shoulders: { own: bake(r, sh.own), ally: bake(r, sh.ally), remnant: bake(r, sh.remnant), enemy: bake(r, sh.enemy) },
    torso: bake(r, torso), head: bake(r, head), visor: bake(r, visor), pack: bake(r, pack), legs: bake(r, legs),
    gun: bake(r, gun, U, Math.round(U * 0.1)),
    shadow: bake(r, shadow), ring: bake(r, ring), ringThick: bake(r, ringThick), ringDash: bake(r, ringDash),
    cross: bake(r, cross), frame: bake(r, frame), bipod: bake(r, bipod), badge: bake(r, badge, U, Math.round(U * 0.6)),
  };
}
