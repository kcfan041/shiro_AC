// 視野遮罩（方案 §7.4）：伺服器每秒給一次我方每個人的視野多邊形（和偵測用同一套視線），合起來挖洞，
// 洞外整片調暗。偵測在 15 m 後隨距離變差（60 m 約一半、100 m 只剩 5%），所以分兩層：NEAR 格（60 m）內完全挖開，
// 之後到多邊形邊緣只挖開一部分（FAR_CLEAR）。遮罩畫在低解析度的貼圖上（每格 RES px）再放大，加一點模糊，邊緣自然柔和；
// 新的一份到了用 FADE_S 秒淡入淡出換過去，不會一跳一跳。座標：畫面方向（y 向下），一格＝1。
// 注意：視野內不代表一定發現得了（偵測還看姿勢、距離、壓制），這只是「大致看得到哪裡」。

import { BlurFilter, Container, Graphics, RenderTexture, Sprite, type Renderer } from "pixi.js";
import type { XY } from "../protocol";

const RES = 2;          // 每格幾 px
const FADE_S = 0.5;
const NEAR = 30;        // 格（60 m）：這以內完全挖開
const FAR_CLEAR = 0.5;  // NEAR 之外到多邊形邊緣挖開的程度

export class VisionLayer {
  readonly root = new Container();
  private front: Sprite;
  private back: Sprite;
  private rts: [RenderTexture, RenderTexture];
  private cur = 0;
  private draw = new Container();
  private dark = new Graphics();
  private holes = new Graphics();
  private fade = 1;
  private key = "";
  /** 視野外的暗度（0～1） */
  darkness = 0.55;
  enabled = true;
  has = false;

  constructor(private r: Renderer, readonly w: number, readonly h: number) {
    const opt = { width: w * RES, height: h * RES, resolution: 1 };
    this.rts = [RenderTexture.create(opt), RenderTexture.create(opt)];
    this.front = new Sprite(this.rts[0]);
    this.back = new Sprite(this.rts[1]);
    for (const s of [this.back, this.front]) { s.scale.set(1 / RES); s.alpha = 0; this.root.addChild(s); }
    this.dark.rect(0, 0, w * RES, h * RES).fill({ color: 0x000000 });
    this.holes.blendMode = "erase";
    this.draw.addChild(this.dark, this.holes);
    this.draw.filters = [new BlurFilter({ strength: 3, quality: 2 })];
  }

  /** 新的一份視野（畫面方向座標：每個人的位置 c 與多邊形 pts）；跟上一份一樣就不重畫 */
  set(polys: { c: XY; pts: XY[] }[]) {
    const key = polys.map((p) => p.pts.length + ":" + (p.pts[0] ? p.pts[0].join(",") : "")).join("|") + polys.length;
    if (key === this.key) return;
    this.key = key;
    this.holes.clear();
    for (const { c, pts } of polys) {
      if (pts.length < 3) continue;
      this.holes.poly(pts.flatMap(([x, y]) => [x * RES, y * RES])).fill({ color: 0xffffff, alpha: FAR_CLEAR });
      const near = pts.map(([x, y]): XY => {
        const d = Math.hypot(x - c[0], y - c[1]);
        const k = d > NEAR ? NEAR / d : 1;
        return [c[0] + (x - c[0]) * k, c[1] + (y - c[1]) * k];
      });
      this.holes.poly(near.flatMap(([x, y]) => [x * RES, y * RES])).fill({ color: 0xffffff });
    }
    this.cur = 1 - this.cur;
    this.r.render({ container: this.draw, target: this.rts[this.cur], clear: true });
    // 新的放前面、從 0 淡入；舊的放後面、跟著淡出
    [this.front, this.back] = [this.back, this.front];
    this.front.texture = this.rts[this.cur];
    this.root.addChild(this.front);
    this.fade = this.has ? 0 : 1;
    this.has = true;
  }

  clear() { this.has = false; this.key = ""; }

  update(dt: number) {
    this.root.visible = this.enabled && this.has;
    if (!this.root.visible) return;
    this.fade = Math.min(1, this.fade + dt / FADE_S);
    this.front.alpha = this.darkness * this.fade;
    this.back.alpha = this.darkness * (1 - this.fade);
  }
}
