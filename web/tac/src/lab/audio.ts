// 實驗頁音效：用程式產生的噪音當槍聲與爆炸聲（S0 只驗證排程與負載，正式素材在 S3）。
// 以鏡頭中心為聽者：距離越遠越小聲、越悶；左右聲道依方向；同時最多 MAX_VOICES 個聲音，同一射手 0.25 秒內只響一次。

const MAX_VOICES = 12;
const MERGE_S = 0.25;

export class LabAudio {
  private ctx: AudioContext | null = null;
  private gun: AudioBuffer | null = null;
  private boom: AudioBuffer | null = null;
  private voices = 0;
  private lastBy = new Map<string, number>();
  scheduleMs = 0;      // 最近一次排程花的時間（量負載用）
  played = 0;
  dropped = 0;

  get on(): boolean { return !!this.ctx && this.ctx.state === "running"; }
  get active(): number { return this.voices; }

  /** 瀏覽器規定要使用者先互動一次才能出聲：由按鈕呼叫 */
  async enable() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.gun = this.noise(0.18, 38);
      this.boom = this.noise(1.2, 4);
    }
    await this.ctx.resume();
  }

  async disable() { if (this.ctx) await this.ctx.suspend(); }

  private noise(sec: number, decay: number): AudioBuffer {
    const ctx = this.ctx!;
    const n = Math.floor(ctx.sampleRate * sec);
    const buf = ctx.createBuffer(1, n, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.exp((-decay * i) / ctx.sampleRate);
    return buf;
  }

  /** dx、dy：聲源相對聽者的位置（格） */
  play(kind: "gun" | "boom", dx: number, dy: number, by = "") {
    if (!this.on) return;
    const t0 = performance.now();
    const ctx = this.ctx!;
    if (by) {
      const last = this.lastBy.get(by);
      if (last !== undefined && ctx.currentTime - last < MERGE_S) return;
      this.lastBy.set(by, ctx.currentTime);
    }
    if (this.voices >= MAX_VOICES) { this.dropped++; return; }
    const dist = Math.hypot(dx, dy);
    const src = ctx.createBufferSource();
    src.buffer = kind === "gun" ? this.gun : this.boom;
    src.playbackRate.value = kind === "gun" ? 0.9 + Math.random() * 0.2 : 0.7;
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = Math.max(600, 9000 / (1 + dist / 15));
    const pan = ctx.createStereoPanner();
    pan.pan.value = Math.max(-1, Math.min(1, dx / 40));
    const gain = ctx.createGain();
    gain.gain.value = (kind === "gun" ? 0.25 : 0.6) / (1 + dist / 20);
    src.connect(lp).connect(pan).connect(gain).connect(ctx.destination);
    this.voices++;
    this.played++;
    src.onended = () => { this.voices--; };
    src.start();
    this.scheduleMs = performance.now() - t0;
  }
}
