// 戰鬥時鐘：整個戰術畫面只有一個「現在播到的戰鬥時間」bt（秒），每幀依實際經過的時間前進。
//
// 即時：收到的影格帶 ts0～ts1，bt 平均落後最新的 ts1 一段緩衝 D（存糧，影格晚一點到也不會斷）。
//   播放速度 rate＝伺服器推進速度的估計 ×（1 ＋ K ×（平滑後的緩衝 − D）），限制在伺服器速度的 0.9～1.1 倍：
//   伺服器穩定時收斂到 1.0；影格的到達時間抖動只會讓緩衝量上下動，畫面速度幾乎不變；伺服器變慢（時間倍率 < 1）也跟著變慢。
//   緩衝見底：bt 停在最新的 ts1，不倒退、不越過（後面發生什麼還不知道）。
//   落後超過 JUMP_S（分頁在背景、斷線重連）：直接跳到 latest − D，記下跳過的區間（重要事件由注意力佇列補上）。
// 回放：rate＝播放速度，可暫停、拖曳，不用緩衝控制。

export const BUFFER_S = 0.75;
export const JUMP_S = 3;
const K = 0.15;                 // 緩衝誤差 1 秒 → 速度差 15%（再被 ±10% 限制）
const BUF_TAU = 3;              // 緩衝量的平滑時間常數（秒）；K×τ＝0.45 → 阻尼比約 0.75，不會來回震盪
const RATE_WIN_S = 20;          // 伺服器推進速度用最近 20 秒的影格估
const RATE_DEADBAND = 0.04;     // 估出來跟 1 差不到 4% 就當作 1（到達時間抖動造成的估計誤差；伺服器真的變慢時頂部列在 0.95 以下才提示）

export interface ClockState {
  bt: number;
  rate: number;
  /** 目前緩衝量（最新 ts1 − bt） */
  buffer: number;
  /** 平滑後的緩衝量 */
  smooth: number;
  /** 伺服器推進速度估計（戰鬥秒／實際秒） */
  serverRate: number;
  stalled: boolean;
  stalls: number;
  jumps: number;
}

export class BattleClock {
  bt: number | null = null;
  rate = 1;
  latest = -Infinity;
  smooth = BUFFER_S;
  serverRate = 1;
  stalled = false;
  stalls = 0;
  jumps = 0;
  /** 最近一次跳過的戰鬥時間區間 */
  skipped: [number, number] | null = null;
  replay = false;
  /** 開播前先存滿一個緩衝（第一格影格只涵蓋 0.5 秒，直接開播的話下一格稍晚到就會斷） */
  primed = false;
  paused = false;
  speed = 1;
  private arrivals: [number, number][] = [];   // [實際秒, latest]

  constructor(readonly buffer = BUFFER_S) {}

  /** 即時：收到一格影格。wallS＝收到的實際時間（秒）。 */
  push(ts0: number, ts1: number, wallS: number) {
    if (ts1 <= this.latest) return;
    this.latest = ts1;
    this.arrivals.push([wallS, ts1]);
    while (this.arrivals.length > 2 && wallS - this.arrivals[0][0] > RATE_WIN_S) this.arrivals.shift();
    // 推進速度＝最近 20 秒（到達時間, ts1）的最小平方斜率（只看頭尾的話，到達時間抖動 ±0.2 秒會有好幾 % 的誤差）
    if (this.arrivals.length >= 6 && wallS - this.arrivals[0][0] > 4) {
      const n = this.arrivals.length;
      let mw = 0, mt = 0;
      for (const [w, t] of this.arrivals) { mw += w / n; mt += t / n; }
      let num = 0, den = 0;
      for (const [w, t] of this.arrivals) { num += (w - mw) * (t - mt); den += (w - mw) ** 2; }
      if (den > 1e-9) {
        const r = Math.max(0.2, Math.min(1.5, num / den));
        this.serverRate = Math.abs(r - 1) < RATE_DEADBAND ? 1 : r;
      }
    }
    if (this.bt === null) this.bt = Math.max(ts0, ts1 - this.buffer);
    if (!this.primed && this.latest - this.bt >= this.buffer - 1e-9) {
      this.primed = true;
      this.smooth = this.latest - this.bt;
    }
  }

  /** 回放：從 t 開始，speed 倍速 */
  startReplay(t: number, speed = 1) {
    this.replay = true;
    this.primed = true;
    this.bt = t;
    this.speed = speed;
    this.paused = false;
  }

  seek(t: number) { this.bt = t; }

  /** 前進 dtS 實際秒；回傳新的 bt（還沒有任何影格 → null） */
  tick(dtS: number): number | null {
    if (this.bt === null) return null;
    if (this.replay) {
      if (!this.paused) this.bt += dtS * this.speed;
      this.rate = this.paused ? 0 : this.speed;
      return this.bt;
    }
    if (!this.primed) { this.rate = 0; return this.bt; }
    let buf = this.latest - this.bt;
    if (buf > JUMP_S) {
      const to = this.latest - this.buffer;
      this.skipped = [this.bt, to];
      this.bt = to;
      this.smooth = this.buffer;
      this.jumps++;
      buf = this.buffer;
      this.arrivals = this.arrivals.slice(-1);   // 斷線期間的影格是一起到的，不能拿來估伺服器速度
    }
    this.smooth += (buf - this.smooth) * (1 - Math.exp(-dtS / BUF_TAU));
    const k = Math.max(0.9, Math.min(1.1, 1 + K * (this.smooth - this.buffer)));
    this.rate = this.serverRate * k;
    const next = this.bt + dtS * this.rate;
    if (next >= this.latest) {
      if (!this.stalled) this.stalls++;
      this.stalled = true;
      this.bt = this.latest;
    } else {
      this.stalled = false;
      this.bt = next;
    }
    return this.bt;
  }

  state(): ClockState {
    return { bt: this.bt ?? 0, rate: this.rate, buffer: this.latest - (this.bt ?? this.latest), smooth: this.smooth,
      serverRate: this.serverRate, stalled: this.stalled, stalls: this.stalls, jumps: this.jumps };
  }
}
