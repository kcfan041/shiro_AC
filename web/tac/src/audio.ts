// 音效（interface.md §3.4）：槍聲依陣營與武器、槍的機械聲（霰彈槍泵動、射手步槍彈殼落地）、爆炸、閃光彈、彈著、無線電提示音、環境底噪。
// 分得出槍種靠三件事：節奏（fxplan.ROUND_GAP，機槍快、射手步槍慢）、錄音（我方與友軍一套、敵人一套，機槍是同一把步槍降調）、機械聲。
// 以鏡頭中心為聽者：距離越遠越小聲、越悶（低通）、越晚到（聲速 343 m/s）；左右聲道依方向。
// 同時最多 MAX_VOICES 個聲音（無線電與警示不受限，彈著聲只用一半、讓給槍聲）；同一射手 MERGE_S 秒內只響一次（連發聽起來還是連發）。
// 情報精度：看不到的射手，聲音位置用「聽到槍聲」事件的誤差位置，不用真實位置；聽得出槍種（伺服器給 wpn），陣營當敵人。
// 聲音先用程式合成；web/tac/public/sounds/sounds.json 有列的檔案會取代對應的合成音（素材來源與授權記在同目錄的 README.md）。
// 分頁在背景時暫停；瀏覽器規定要使用者先互動一次才能出聲。

export const MAX_VOICES = 12;
export const MERGE_S = 0.05;                 // 同一發的重複聲音（霰彈彈丸）合併；機槍一發 0.075 秒，要比它短
const SOUND_MPS = 343, TILE_M = 2;

export type SoundKind = "gun" | "mech" | "boom" | "flash" | "impact" | "radio" | "alert" | "ambient";
export type Category = "weapons" | "explosions" | "radio" | "ambient";
const CATEGORY: Record<SoundKind, Category> = { gun: "weapons", mech: "weapons", impact: "weapons", boom: "explosions", flash: "explosions", radio: "radio", alert: "radio", ambient: "ambient" };
const BASE_GAIN: Record<SoundKind, number> = { gun: 0.45, mech: 0.08, impact: 0.18, boom: 0.5, flash: 0.8, radio: 0.35, alert: 0.4, ambient: 0.06 };

export interface Listener { x: number; y: number; /** 畫面半寬（格），決定左右聲道 */ halfWidth: number }
export interface SoundReq {
  kind: SoundKind; x?: number; y?: number; weapon?: string; mat?: string; shooter?: string | null;
  /** 槍聲的陣營：enemy 用敵方那套錄音，其他（我方、友軍、殘部）用我方那套 */
  side?: string | null;
  /** 機械聲：pump 泵動、casing 彈殼落地 */
  mech?: "pump" | "casing";
  /** 晚多少秒才響（機械聲接在槍聲後面） */
  after?: number;
}
export interface Spatial { gain: number; pan: number; delay: number; cutoff: number }

/** 位置 → 音量、聲道、延遲、低通（純計算）。沒有位置（無線電、警示）＝不空間化。 */
export function spatial(req: SoundReq, L: Listener): Spatial {
  const base = BASE_GAIN[req.kind];
  if (req.x === undefined || req.y === undefined) return { gain: base, pan: 0, delay: 0, cutoff: 18000 };
  const dx = req.x - L.x, dy = req.y - L.y;
  const d = Math.hypot(dx, dy);
  return {
    gain: base / (1 + d / (req.kind === "mech" ? 4 : 18)),        // 機械聲很小，遠一點就聽不到
    pan: Math.max(-1, Math.min(1, dx / Math.max(1, L.halfWidth))) * 0.9,
    delay: Math.min(0.6, (d * TILE_M) / SOUND_MPS),
    cutoff: Math.max(500, 16000 / (1 + d / 12)),
  };
}

/** 同時發聲上限與同一射手合併（純計算） */
export class VoiceLimiter {
  active = 0;
  merged = 0;
  dropped = 0;
  private last = new Map<string, number>();
  constructor(readonly max = MAX_VOICES, readonly mergeS = MERGE_S) {}

  admit(req: SoundReq, now: number): boolean {
    if (req.kind === "radio" || req.kind === "alert" || req.kind === "ambient") return true;
    if (req.shooter) {
      const l = this.last.get(req.shooter);
      if (l !== undefined && now - l < this.mergeS) { this.merged++; return false; }
      this.last.set(req.shooter, now);
    }
    const cap = req.kind === "impact" || req.kind === "mech" ? Math.floor(this.max / 2) : this.max;   // 彈著聲、機械聲讓給槍聲：只用一半的聲音數
    if (this.active >= cap) { this.dropped++; return false; }
    this.active++;
    return true;
  }

  release() { this.active = Math.max(0, this.active - 1); }
}

// ---------------------------------------------------------------- 合成音

type Synth = (sr: number) => Float32Array;

function noise(seed: number): () => number {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2147483648 - 1; };
}

/** 槍聲：高頻爆音（crack）＋低頻悶響（thump）＋尾音；參數依武器 */
function gunSynth(crack: number, thump: number, tail: number, hp: number, seed: number): Synth {
  return (sr) => {
    const n = Math.floor(sr * (0.05 + tail));
    const out = new Float32Array(n);
    const rnd = noise(seed);
    let lp = 0, prev = 0;
    for (let i = 0; i < n; i++) {
      const t = i / sr;
      const w = rnd();
      lp += (w - lp) * hp;                        // 簡單低通，crack 用 w − lp 當高通
      const c = (w - lp) * crack * Math.exp(-t * 90);
      const b = Math.sin(2 * Math.PI * (110 - t * 300) * t) * thump * Math.exp(-t * 30);
      const r = lp * 0.35 * Math.exp(-t / Math.max(0.02, tail * 0.4));
      const v = c + b + r;
      out[i] = v * 0.7 + prev * 0.3;
      prev = v;
    }
    return out;
  };
}

const SYNTH: Record<string, Synth> = {
  "gun:rifle": gunSynth(1, 0.6, 0.25, 0.25, 1),
  "gun:lmg": gunSynth(0.9, 0.9, 0.3, 0.18, 2),
  "gun:dmr": gunSynth(1.3, 0.7, 0.45, 0.3, 3),
  "gun:shotgun": gunSynth(0.8, 1.1, 0.35, 0.12, 4),
  boom: (sr) => {
    const n = Math.floor(sr * 1.6), out = new Float32Array(n), rnd = noise(5);
    let lp = 0;
    for (let i = 0; i < n; i++) {
      const t = i / sr;
      lp += (rnd() - lp) * 0.04;
      out[i] = (lp * 3 * Math.exp(-t * 2.2) + Math.sin(2 * Math.PI * (55 - t * 20) * t) * 0.8 * Math.exp(-t * 4)) * Math.min(1, t * 400);
    }
    return out;
  },
  flash: (sr) => {
    const n = Math.floor(sr * 1.0), out = new Float32Array(n), rnd = noise(6);
    for (let i = 0; i < n; i++) {
      const t = i / sr;
      out[i] = rnd() * Math.exp(-t * 40) + Math.sin(2 * Math.PI * 3100 * t) * 0.12 * Math.exp(-t * 3);
    }
    return out;
  },
  "mech:pump": (sr) => {                       // 泵動：往後拉、往前推兩聲金屬聲
    const n = Math.floor(sr * 0.45), o = new Float32Array(n), r = noise(11);
    for (let i = 0; i < n; i++) {
      const t = i / sr, a = t < 0.2 ? Math.exp(-t * 60) : Math.exp(-(t - 0.3) * 50) * (t > 0.3 ? 1 : 0);
      o[i] = (r() * 0.6 + Math.sin(2 * Math.PI * 2300 * t) * 0.4) * a * 0.6;
    }
    return o;
  },
  "mech:casing": (sr) => {                     // 彈殼落地：一下短而悶的小金屬聲，彈一下（不要像鈴聲）
    const n = Math.floor(sr * 0.2), o = new Float32Array(n), r = noise(12);
    let lp = 0;
    for (let i = 0; i < n; i++) {
      const t = i / sr, hit = (t0: number, k: number) => (t >= t0 ? Math.exp(-(t - t0) * 120) * k : 0);
      lp += (r() - lp) * 0.35;
      o[i] = (lp * 0.7 + Math.sin(2 * Math.PI * 3100 * t) * 0.3) * (hit(0, 1) + hit(0.07, 0.35)) * 0.35;
    }
    return o;
  },
  "impact:hard": (sr) => { const n = Math.floor(sr * 0.05), o = new Float32Array(n), r = noise(7); for (let i = 0; i < n; i++) o[i] = r() * Math.exp(-i / sr * 120) * (i % 3 ? 1 : -1); return o; },
  "impact:dirt": (sr) => { const n = Math.floor(sr * 0.08), o = new Float32Array(n), r = noise(8); let lp = 0; for (let i = 0; i < n; i++) { lp += (r() - lp) * 0.08; o[i] = lp * 3 * Math.exp(-i / sr * 60); } return o; },
  "impact:flesh": (sr) => { const n = Math.floor(sr * 0.07), o = new Float32Array(n); for (let i = 0; i < n; i++) { const t = i / sr; o[i] = Math.sin(2 * Math.PI * 180 * t) * Math.exp(-t * 70); } return o; },
  "impact:soft": (sr) => { const n = Math.floor(sr * 0.04), o = new Float32Array(n), r = noise(9); for (let i = 0; i < n; i++) o[i] = r() * 0.5 * Math.exp(-i / sr * 100); return o; },
  radio: (sr) => {
    const n = Math.floor(sr * 0.22), out = new Float32Array(n);
    for (let i = 0; i < n; i++) { const t = i / sr; const f = t < 0.09 ? 1250 : 1650; out[i] = Math.sign(Math.sin(2 * Math.PI * f * t)) * 0.25 * (t < 0.09 || t > 0.11 ? 1 : 0) * Math.min(1, (0.22 - t) * 40); }
    return out;
  },
  alert: (sr) => {
    const n = Math.floor(sr * 0.5), out = new Float32Array(n);
    for (let i = 0; i < n; i++) { const t = i / sr; const on = (t % 0.16) < 0.09; out[i] = on ? Math.sin(2 * Math.PI * 880 * t) * 0.35 : 0; }
    return out;
  },
  ambient: (sr) => {
    const n = Math.floor(sr * 4), out = new Float32Array(n), rnd = noise(10);
    let b = 0;
    for (let i = 0; i < n; i++) { b = (b + rnd() * 0.02) * 0.995; out[i] = b * (0.8 + 0.2 * Math.sin((2 * Math.PI * i) / n)); }
    return out;
  },
};

/** 槍聲的錄音組：敵人一套，其他（我方、友軍、殘部）一套 */
export function gunFamily(side: string | null | undefined): "own" | "enemy" {
  return side === "enemy" ? "enemy" : "own";
}

/** 依序找得到哪個就用哪個：槍聲先找陣營那套錄音（gun:enemy:lmg），沒有就用合成的（gun:lmg） */
export function soundKeys(req: SoundReq): string[] {
  if (req.kind === "gun") {
    const k = soundKey(req);
    return [`gun:${gunFamily(req.side)}:${k.slice(4)}`, k];
  }
  return [soundKey(req)];
}

export function soundKey(req: SoundReq): string {
  if (req.kind === "gun") return `gun:${req.weapon && SYNTH[`gun:${req.weapon}`] ? req.weapon : "rifle"}`;
  if (req.kind === "mech") return `mech:${req.mech || "casing"}`;
  if (req.kind === "impact") return `impact:${req.mat || "soft"}`;
  return req.kind;
}

// ---------------------------------------------------------------- Web Audio

const STORE = "tac.audio";

export class SoundEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private buses: Partial<Record<Category, GainNode>> = {};
  private buffers = new Map<string, AudioBuffer>();
  private ambient: AudioBufferSourceNode | null = null;
  readonly limiter = new VoiceLimiter();
  played = 0;
  /** 從 sounds.json 載入的錄音數；samples：載入完成（試聽頁等它） */
  loaded = 0;
  samples: Promise<void> = Promise.resolve();
  volume: Record<"master" | Category, number> = { master: 0.8, weapons: 1, explosions: 1, radio: 1, ambient: 1 };
  wanted = false;

  constructor(private assetBase = "sounds/") {
    try {
      const v = JSON.parse(localStorage.getItem(STORE) || "null");
      if (v && typeof v === "object") {
        Object.assign(this.volume, v.volume || {});
        this.wanted = !!v.on;
      }
    } catch { /* 沒有儲存空間（無痕視窗等）就用預設值 */ }
    document.addEventListener("visibilitychange", () => { void this.sync(); });
  }

  get on(): boolean { return !!this.ctx && this.ctx.state === "running"; }

  private save() {
    try { localStorage.setItem(STORE, JSON.stringify({ on: this.wanted, volume: this.volume })); } catch { /* 略過 */ }
  }

  /** 要由使用者的動作（點擊、按鍵）呼叫 */
  async enable() {
    this.wanted = true;
    this.save();
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
      for (const c of ["weapons", "explosions", "radio", "ambient"] as Category[]) {
        const g = this.ctx.createGain();
        g.connect(this.master);
        this.buses[c] = g;
      }
      for (const [k, f] of Object.entries(SYNTH)) {
        const data = f(this.ctx.sampleRate);
        const buf = this.ctx.createBuffer(1, data.length, this.ctx.sampleRate);
        buf.getChannelData(0).set(data);
        this.buffers.set(k, buf);
      }
      this.samples = this.loadSamples();
      this.applyVolume();
    }
    await this.sync();
  }

  async disable() {
    this.wanted = false;
    this.save();
    await this.sync();
  }

  setVolume(k: "master" | Category, v: number) {
    this.volume[k] = Math.max(0, Math.min(1, v));
    this.applyVolume();
    this.save();
  }

  private applyVolume() {
    if (!this.master) return;
    this.master.gain.value = this.volume.master;
    for (const [c, g] of Object.entries(this.buses)) g!.gain.value = this.volume[c as Category];
  }

  private async sync() {
    if (!this.ctx) return;
    const run = this.wanted && !document.hidden;
    if (run && this.ctx.state !== "running") await this.ctx.resume();
    if (!run && this.ctx.state === "running") await this.ctx.suspend();
    if (run && !this.ambient) this.startAmbient();
  }

  /** sounds/sounds.json：{ "gun:rifle": "rifle.ogg", ... }，有的話取代合成音 */
  private async loadSamples() {
    try {
      const r = await fetch(`${this.assetBase}sounds.json`);
      if (!r.ok) return;
      const list = (await r.json()) as Record<string, string>;
      for (const [k, file] of Object.entries(list)) {
        const a = await fetch(`${this.assetBase}${file}`);
        if (a.ok && this.ctx) { this.buffers.set(k, await this.ctx.decodeAudioData(await a.arrayBuffer())); this.loaded++; }
      }
    } catch { /* 沒有素材就用合成音 */ }
  }

  private startAmbient() {
    const buf = this.buffers.get("ambient");
    if (!this.ctx || !buf) return;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    const g = this.ctx.createGain();
    g.gain.value = BASE_GAIN.ambient;
    src.connect(g).connect(this.buses.ambient!);
    src.start();
    this.ambient = src;
  }

  play(req: SoundReq, L: Listener) {
    if (!this.on || !this.ctx) return;
    if (!this.limiter.admit(req, this.ctx.currentTime)) return;
    const buf = soundKeys(req).map((k) => this.buffers.get(k)).find((b) => b);
    const limited = req.kind !== "radio" && req.kind !== "alert" && req.kind !== "ambient";
    if (!buf) { if (limited) this.limiter.release(); return; }
    const sp = spatial(req, L);
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    // 音高隨機 ±2%：一樣的聲音重複不會太死板，又不會讓不同的槍音高重疊
    src.playbackRate.value = req.kind === "gun" || req.kind === "impact" || req.kind === "mech" ? 0.98 + Math.random() * 0.04 : 1;
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = req.kind === "boom" ? Math.min(sp.cutoff, 5000) : sp.cutoff;   // 爆炸錄音的金屬碎片聲很刺耳：高頻再收掉一些
    const pan = ctx.createStereoPanner();
    pan.pan.value = sp.pan;
    const g = ctx.createGain();
    g.gain.value = sp.gain;
    src.connect(lp).connect(pan).connect(g).connect(this.buses[CATEGORY[req.kind]]!);
    if (limited) src.onended = () => this.limiter.release();
    src.start(ctx.currentTime + sp.delay + (req.after ?? 0));
    this.played++;
  }
}
