// 幀時間統計：最近 N 幀的平均、p95、最大值，以及超過 33 ms（看得出卡頓）的幀數。

export interface FrameStats {
  fps: number;
  avgMs: number;
  p95Ms: number;
  maxMs: number;
  /** 超過 33 ms 的幀數（最近 N 幀內） */
  slow: number;
  /** 每幀 JS 更新（取樣＋各層更新）的平均毫秒 */
  updateMs: number;
  frames: number;
}

export class FrameTimer {
  private dts: number[] = [];
  private ups: number[] = [];

  constructor(private readonly size = 300) {}

  push(frameMs: number, updateMs = 0) {
    this.dts.push(frameMs);
    this.ups.push(updateMs);
    if (this.dts.length > this.size) { this.dts.shift(); this.ups.shift(); }
  }

  reset() { this.dts = []; this.ups = []; }

  stats(): FrameStats {
    const n = this.dts.length;
    if (!n) return { fps: 0, avgMs: 0, p95Ms: 0, maxMs: 0, slow: 0, updateMs: 0, frames: 0 };
    const sum = this.dts.reduce((a, b) => a + b, 0);
    const sorted = [...this.dts].sort((a, b) => a - b);
    const p95 = sorted[Math.min(n - 1, Math.floor(n * 0.95))];
    return {
      fps: 1000 / (sum / n),
      avgMs: sum / n,
      p95Ms: p95,
      maxMs: sorted[n - 1],
      slow: this.dts.filter((d) => d > 33).length,
      updateMs: this.ups.reduce((a, b) => a + b, 0) / n,
      frames: n,
    };
  }
}
