// 相機：跟隨、拖曳、以游標為中心縮放；緩動依實際經過時間計算（跟畫面更新率無關，60 Hz 和 144 Hz 手感一樣）。
// 座標：x、y 是「畫面方向」的世界座標（單位＝小格，y 向下＝世界 y 翻轉後），zoom＝每格幾 px（CSS 像素）。

export const FOLLOW_TAU = 0.25;
export const ZOOM_TAU = 0.12;
export const ZOOM_MIN = 3, ZOOM_MAX = 40;

export class Camera {
  x = 0;
  y = 0;
  zoom = 14;
  targetZoom = 14;
  /** 跟隨目標（null＝不跟隨） */
  follow: (() => [number, number] | null) | null = null;
  private anchor: [number, number] | null = null;     // 縮放時固定不動的螢幕點
  w = 800;
  h = 600;

  setViewport(w: number, h: number) { this.w = w; this.h = h; }

  /** 世界（畫面方向）→ 螢幕 px */
  toScreen(x: number, y: number): [number, number] {
    return [(x - this.x) * this.zoom + this.w / 2, (y - this.y) * this.zoom + this.h / 2];
  }

  toWorld(sx: number, sy: number): [number, number] {
    return [(sx - this.w / 2) / this.zoom + this.x, (sy - this.h / 2) / this.zoom + this.y];
  }

  /** 拖曳 dx、dy px：取消跟隨 */
  pan(dx: number, dy: number) {
    this.follow = null;
    this.x -= dx / this.zoom;
    this.y -= dy / this.zoom;
  }

  /** 滾輪：以螢幕點 (sx, sy) 為中心，目標縮放乘上 factor */
  zoomAt(sx: number, sy: number, factor: number) {
    this.anchor = [sx, sy];
    this.targetZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, this.targetZoom * factor));
  }

  setZoom(z: number) {
    this.zoom = this.targetZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z));
    this.anchor = null;
  }

  jumpTo(x: number, y: number) { this.x = x; this.y = y; }

  update(dtS: number) {
    if (Math.abs(this.targetZoom - this.zoom) > 1e-4) {
      const [sx, sy] = this.anchor ?? [this.w / 2, this.h / 2];
      const [wx, wy] = this.toWorld(sx, sy);
      this.zoom += (this.targetZoom - this.zoom) * (1 - Math.exp(-dtS / ZOOM_TAU));
      if (Math.abs(this.targetZoom - this.zoom) < 1e-3) this.zoom = this.targetZoom;
      if (!this.follow) {                              // 跟隨中：以小隊為中心縮放；否則游標下的點不動
        this.x = wx - (sx - this.w / 2) / this.zoom;
        this.y = wy - (sy - this.h / 2) / this.zoom;
      }
    }
    const tgt = this.follow?.();
    if (tgt) {
      const k = 1 - Math.exp(-dtS / FOLLOW_TAU);
      this.x += (tgt[0] - this.x) * k;
      this.y += (tgt[1] - this.y) * k;
    }
  }

  /** 世界容器的位置（對齊整數像素，平移時地形不閃） */
  origin(): [number, number] {
    return [Math.round(this.w / 2 - this.x * this.zoom), Math.round(this.h / 2 - this.y * this.zoom)];
  }
}
