// 模擬即時推送：把一串影格照「伺服器算完一步就送」的時間交出來，可以加到達時間的抖動、伺服器變慢、中途斷線。
// 給 S1 的驗收測試與實驗頁用（實驗頁播回放時用它假裝是即時戰鬥，才量得到時鐘在抖動下的表現）。
// 另外附一個合成影格產生器：照伺服器的語意（路徑每格的起步 pst、抵達 pt、走向中的下一格 nx）做出走路的人。

import type { Frame, FrameUnit, XY } from "./protocol";
import { makeRng } from "./rng";

export interface FeedOptions {
  /** 到達時間抖動（秒，均勻分布 ±jitter） */
  jitter?: number;
  /** 伺服器推進速度（1＝正常，0.8＝時間變慢） */
  timeScale?: number;
  /** 斷線：[從實際第幾秒, 斷幾秒]，期間的影格在恢復時一起到 */
  outage?: [number, number] | null;
  seed?: number;
}

export class Feed {
  private i = 0;
  private due: number[];

  constructor(readonly frames: Frame[], opt: FeedOptions = {}) {
    const rng = makeRng(opt.seed ?? 1);
    const scale = opt.timeScale ?? 1, jit = opt.jitter ?? 0;
    const t0 = frames.length ? frames[0].ts1 ?? 0 : 0;
    let prev = -Infinity;
    this.due = frames.map((f) => {
      let w = ((f.ts1 ?? 0) - t0) / scale + (rng() * 2 - 1) * jit;
      if (opt.outage && w >= opt.outage[0] && w < opt.outage[0] + opt.outage[1]) w = opt.outage[0] + opt.outage[1];
      w = Math.max(w, prev);          // 依序到達（WebSocket 不會亂序）
      prev = w;
      return w;
    });
  }

  /** 實際時間到 wallS 為止該到的影格 */
  take(wallS: number): { frame: Frame; at: number }[] {
    const out: { frame: Frame; at: number }[] = [];
    while (this.i < this.frames.length && this.due[this.i] <= wallS) {
      out.push({ frame: this.frames[this.i], at: this.due[this.i] });
      this.i++;
    }
    return out;
  }

  get done(): boolean { return this.i >= this.frames.length; }
  reset() { this.i = 0; }
}

export interface Walker {
  id: string;
  /** 依序要走的格子（第一格是起點） */
  route: XY[];
  /** 走一格幾秒 */
  tileS: number;
  /** 什麼時候出發 */
  startS?: number;
  /** 在第幾格（route 索引）停下等多久（被擋住：等的時候不給 nx） */
  waits?: Record<number, number>;
  side?: string;
}

/** 照伺服器語意產生影格：每步 step 秒（0.5），涵蓋 [ts0, ts1]。 */
export function walkFrames(walkers: Walker[], seconds: number, step = 0.5): Frame[] {
  // 先算每個人每一格的起步、抵達時間
  const plans = walkers.map((w) => {
    const legs: { t0: number; t1: number; to: XY }[] = [];
    let t = w.startS ?? 0;
    for (let i = 1; i < w.route.length; i++) {
      t += w.waits?.[i - 1] ?? 0;
      legs.push({ t0: t, t1: t + w.tileS, to: w.route[i] });
      t += w.tileS;
    }
    return { w, legs };
  });
  const frames: Frame[] = [];
  for (let k = 0; k * step < seconds; k++) {
    const ts0 = k * step, ts1 = ts0 + step;
    const units: FrameUnit[] = plans.map(({ w, legs }) => {
      const done = legs.filter((l) => l.t1 <= ts0);
      const from = done.length ? done[done.length - 1].to : w.route[0];
      const now = legs.filter((l) => l.t1 > ts0 && l.t1 <= ts1);
      const pos = now.length ? now[now.length - 1].to : from;
      const next = legs.find((l) => l.t1 > ts1 && l.t0 <= ts1);
      const u: FrameUnit = { id: w.id, side: w.side ?? "own", name: w.id, from: [...from], pos: [...pos], posture: "stand",
        facing: [1, 0], down: false, path: now.map((l) => [...l.to] as XY), pt: now.map((l) => l.t1), pst: now.map((l) => l.t0) };
      if (next) { u.nx = [...next.to]; u.nst = next.t0; u.nt = next.t1; }
      return u;
    });
    frames.push({ t: k, ts0, ts1, units, shots: [], blasts: [], smokes: [] });
  }
  return frames;
}
