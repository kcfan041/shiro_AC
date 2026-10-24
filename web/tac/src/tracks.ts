// 個體軌跡與特效時間軸：把一格一格的影格接成每個人連續的「行程表」，畫面只要問「現在是戰鬥第幾秒」，
// 就知道每個人在哪、面向哪、什麼姿勢、有沒有倒地、該不該淡入淡出，以及這段時間該觸發哪些子彈與爆炸。
//
// 移動：路徑每格一段（pst 起步 → pt 抵達）；起步、停下各約 0.15 秒加減速，連續走時中間等速。
//   影格結束時還在走向的下一格（nx，nst～nt）先記成暫定的一段，下一格影格到了就換成實際的；
//   換掉時如果畫面上的位置變了（例如被擋住沒走成），0.2 秒內滑過去，差超過 3 格才直接跳。
// 朝向、姿勢、倒地：記成「在某個時間變成某個值」，朝向 0.15 秒轉過去（最短方向）、姿勢與倒地 0.25 秒過渡。
//   伺服器目前只給影格結束時的朝向與姿勢（S4 會補變化時間），先用：這格有開槍 → 開槍前 0.15 秒轉向；
//   有移動 → 起步時；都沒有 → 影格開始時。倒地照打倒他的 down 事件時間。
// 出現與消失：看得到的敵人消失（失去目標）、陣亡、離開，都淡出；新出現的淡入。

import type { Frame, FrameUnit, XY } from "./protocol";

export const EASE_S = 0.15;
export const TURN_S = 0.15;
export const POSTURE_S = 0.25;
export const CORRECT_S = 0.2;
export const SNAP_DIST = 3;
export const FADE_IN_S = 0.2;
export const FADE_OUT_S = 0.3;
export const DEATH_FADE_S = 0.5;
const KEEP_S = 15;              // 軌跡保留多久（秒，以戰鬥時間計）
const GAP_S = 0.05;             // 兩段之間的空檔小於這個算連續（不加減速）
export const CORNER_R = 0.35;   // 轉角圓滑的半徑（格）
export const MAX_STEP = 2;      // 路徑一步最多幾格（斜走 1.41）；超過是壞資料

interface Seg { t0: number; t1: number; a: XY; b: XY; prov: boolean; easeIn: boolean; easeOut: boolean }
interface Change<T> { t: number; v: T }
interface Snap { ts0: number; u: FrameUnit }

interface Track {
  id: string;
  segs: Seg[];
  facing: Change<number>[];
  posture: Change<string>[];
  down: Change<boolean>[];
  snaps: Snap[];
  /** 看得到的時間區間 [開始, 結束（null＝還在）] */
  present: [number, number | null][];
  deadAt: number | null;
  offset: { dx: number; dy: number; t: number } | null;
  home: XY;                     // 上一格影格結束時確定的位置（下一格的起點應該接在這裡）
}

export interface UnitSample {
  id: string;
  side: string;
  name: string;
  x: number;
  y: number;
  z: number;
  /** 朝向（弧度，0＝東、逆時針，世界座標） */
  facing: number;
  posture: string;
  /** 前一個姿勢與過渡進度（0～1；1＝已經是現在的姿勢） */
  postureFrom: string;
  postureK: number;
  down: boolean;
  /** 倒地過渡（0＝站著、1＝完全倒下） */
  downK: number;
  dead: boolean;
  alpha: number;
  moving: boolean;
  u: FrameUnit;
}

export type TimedFx =
  | { kind: "shot"; ts: number; from: XY; to: XY; hit: boolean; shooter: string | null; ow: boolean; mode: string | null; unseen: boolean }
  | { kind: "blast"; ts: number; at: XY; item: string }
  | { kind: "throw"; ts: number; from: XY; to: XY; land: number; item: string; aid: string | null };

/** 梯形速度曲線：前 a、後 b（佔整段的比例）加減速，中間等速；回傳走了全程的幾成。 */
export function easeFrac(k: number, a: number, b: number): number {
  k = Math.max(0, Math.min(1, k));
  const v = 1 / (1 - a / 2 - b / 2);
  if (a > 0 && k < a) return (v * k * k) / (2 * a);
  if (b > 0 && k > 1 - b) return 1 - (v * (1 - k) ** 2) / (2 * b);
  return v * (a / 2 + (k - a));
}

function angleLerp(a0: number, a1: number, k: number): number {
  let d = a1 - a0;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a0 + d * k;
}

function at<T>(list: Change<T>[], t: number): [Change<T> | null, Change<T> | null] {
  let cur: Change<T> | null = null, prev: Change<T> | null = null;
  for (const c of list) {
    if (c.t > t) break;
    prev = cur;
    cur = c;
  }
  return [cur, prev];
}

const xy = (p: ArrayLike<number>): XY => [p[0], p[1]];
const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export class Tracks {
  private tracks = new Map<string, Track>();
  private fx: TimedFx[] = [];
  private lastTs1 = -Infinity;
  private step = 0.5;
  /** 被丟掉的壞路徑點次數（診斷用） */
  badSteps = 0;

  /** walkable：某一格能不能站（給轉角圓滑用；不給就不圓滑） */
  constructor(private walkable?: (x: number, y: number) => boolean) {}

  get size(): number { return this.tracks.size; }

  /** 收進一格影格。bt＝畫面現在播到哪（換掉暫定移動時，用來算要不要平滑修正）。 */
  ingest(f: Frame, bt: number | null = null) {
    const ts0 = f.ts0 ?? (Number.isFinite(this.lastTs1) ? this.lastTs1 : 0);
    const ts1 = f.ts1 ?? ts0 + this.step;
    if (ts1 <= this.lastTs1 + 1e-6) return;          // 重送或舊的影格
    this.step = Math.max(0.05, ts1 - ts0);
    this.lastTs1 = ts1;
    const seen = new Set<string>();
    const evTs = (type: string, target: string) => {
      for (const e of f.ev || []) if (e.type === type && e.target === target && e.ts != null) return e.ts;
      return null;
    };
    const firedAt = new Map<string, number>();
    for (const s of f.shots) if (s.shooter && s.ts != null && !firedAt.has(s.shooter)) firedAt.set(s.shooter, s.ts);

    for (const u of f.units) {
      if (u.exited) continue;
      seen.add(u.id);
      let tr = this.tracks.get(u.id);
      const start = xy(u.from || u.pos);
      if (!tr) {
        tr = { id: u.id, segs: [], facing: [], posture: [], down: [], snaps: [], present: [], deadAt: null, offset: null, home: start };
        this.tracks.set(u.id, tr);
      }
      const last = tr.present[tr.present.length - 1];
      if (!last || last[1] !== null) tr.present.push([ts0, null]);
      const before = bt !== null && tr.segs.length ? this.pos(tr, bt) : null;

      // 移動段：拿掉暫定的，接上這一格實際走的
      const provs = tr.segs.filter((s) => s.prov);
      tr.segs = tr.segs.filter((s) => !s.prov);
      // 暫定的那一格已經走完了（這一格的起點就是它的終點，例如走到的那一刻剛好看不到）→ 照原本的時間保留
      const done = provs.find((p) => dist(p.b, start) < 1e-6 && dist(p.a, tr.home) < 1e-6);
      if (done) {
        done.prov = false;
        tr.segs.push(done);
        tr.home = done.b;
      }
      const path = (u.path || []).map(xy);
      const pts: XY[] = [start, ...path];
      if (!path.length && dist(start, xy(u.pos)) > 1e-6) pts.push(xy(u.pos));
      const nAll = pts.length - 1;
      // 一步超過 MAX_STEP 格的路徑點是壞資料（伺服器偶爾出現差一個作戰格的點），從那裡截斷、不跟著跑
      let n = nAll;
      if (path.length) for (let i = 1; i <= nAll; i++) if (dist(pts[i - 1], pts[i]) > MAX_STEP) { n = i - 1; break; }
      let end = n < nAll ? pts[n] : xy(u.pos);
      let nx = u.nx && u.nt != null && u.nst != null && u.nt > u.nst ? xy(u.nx) : null;
      if (nx && (n < nAll || dist(end, nx) > MAX_STEP)) nx = null;
      if (n < nAll || (u.nx && !nx)) this.badSteps++;
      const timed = (arr?: number[]) => !!arr && arr.length === nAll && (path.length > 0);
      const legs: Seg[] = [];
      let prevT = ts0;
      for (let i = 1; i <= n; i++) {
        const t1 = timed(u.pt) ? u.pt![i - 1] : ts0 + ((ts1 - ts0) * i) / nAll;
        const t0 = timed(u.pst) ? Math.min(u.pst![i - 1], t1) : timed(u.pt) ? prevT : ts0 + ((ts1 - ts0) * (i - 1)) / nAll;
        legs.push({ t0, t1, a: pts[i - 1], b: pts[i], prov: false, easeIn: true, easeOut: true });
        prevT = t1;
      }
      if (!legs.length && n < nAll) end = start;
      // 這一格的起點和軌跡最後的位置對不上（重新被發現的敵人、重生、整補換人）：
      // 在這一格開始時才移過去（影格會比畫面早 0.25～0.75 秒到，立刻換位置的話人會提早瞬移）；
      // 差 3 格以內在那之前 0.2 秒滑過去，超過就直接跳。
      const lastSeg = tr.segs[tr.segs.length - 1];
      const lastEnd = tr.home;
      const gap = dist(lastEnd, start);
      if (gap > 1e-6) {
        const tj = Math.min(ts0, legs.length ? legs[0].t0 : ts0);
        const t0 = gap > SNAP_DIST ? tj : Math.max(lastSeg ? lastSeg.t1 : -Infinity, tj - CORRECT_S);
        tr.segs.push({ t0: Math.min(t0, tj), t1: tj, a: lastEnd, b: start, prov: false, easeIn: false, easeOut: false });
      }
      const firstNew = tr.segs.length;
      tr.segs.push(...legs);
      if (nx) tr.segs.push({ t0: u.nst!, t1: u.nt!, a: end, b: nx, prov: true, easeIn: true, easeOut: true });
      for (let i = 0; i < tr.segs.length; i++) {
        const s = tr.segs[i], p = tr.segs[i - 1], q = tr.segs[i + 1];
        if (s.t1 - s.t0 < 1e-6) continue;                               // 瞬移段
        s.easeIn = !(p && p.t1 - p.t0 > 1e-6 && s.t0 - p.t1 < GAP_S);
        s.easeOut = !(q && q.t1 - q.t0 > 1e-6 && q.t0 - s.t1 < GAP_S);
      }
      tr.home = end;
      if (before && bt !== null) {
        const after = this.pos(tr, bt, false);
        const d = dist(before, after);
        tr.offset = d > 0.01 && d <= SNAP_DIST ? { dx: before[0] - after[0], dy: before[1] - after[1], t: bt } : null;
      }

      // 朝向、姿勢、倒地、陣亡
      if (u.facing && (u.facing[0] || u.facing[1])) {
        const a = Math.atan2(u.facing[1], u.facing[0]);
        const cur = tr.facing[tr.facing.length - 1];
        if (!cur || Math.abs(angleLerp(cur.v, a, 1) - cur.v) > 1e-3) {
          const moved = n > 0 ? tr.segs[firstNew] : null;
          const fire = firedAt.get(u.id);
          const t = fire != null ? Math.max(ts0, fire - TURN_S) : moved ? Math.max(ts0, moved.t0) : ts0;
          tr.facing.push({ t, v: a });
        }
      }
      const pc = tr.posture[tr.posture.length - 1];
      if (!pc || pc.v !== u.posture) tr.posture.push({ t: pc ? ts0 : -Infinity, v: u.posture });
      const dc = tr.down[tr.down.length - 1];
      if (!dc || dc.v !== !!u.down) tr.down.push({ t: dc ? (u.down ? evTs("down", u.id) ?? ts1 : ts0) : -Infinity, v: !!u.down });
      if (u.dead && tr.deadAt === null) {
        tr.deadAt = evTs("kia", u.id) ?? ts1;
        if (!tr.down.length || !tr.down[tr.down.length - 1].v) tr.down.push({ t: tr.deadAt, v: true });
      }
      tr.snaps.push({ ts0, u });
    }
    for (const tr of this.tracks.values()) {
      const last = tr.present[tr.present.length - 1];
      if (!seen.has(tr.id) && last && last[1] === null) last[1] = ts0;   // 看不到了／離開了
    }

    // 特效
    f.shots.forEach((s, i) => {
      const ts = s.ts ?? ts0 + ((ts1 - ts0) * (i + 1)) / (f.shots.length + 1);
      this.fx.push({ kind: "shot", ts, from: xy(s.from), to: xy(s.to), hit: !!s.hit, shooter: s.shooter, ow: !!s.ow, mode: s.mode ?? null, unseen: !!s.unseen });
    });
    for (const b of f.blasts) this.fx.push({ kind: "blast", ts: b.ts ?? ts1, at: xy(b.at), item: b.item });
    for (const e of f.ev || []) {
      const d = (e.data || {}) as Record<string, unknown>;
      if (e.type === "throw" && Array.isArray(d.frm) && Array.isArray(d.at) && e.ts != null) {
        this.fx.push({ kind: "throw", ts: e.ts, from: xy(d.frm as number[]), to: xy(d.at as number[]), land: e.ts + 1, item: String(d.item || ""), aid: e.aid ?? null });
      }
      if (e.type === "blast" && e.aid && e.ts != null) {                  // 爆炸到了 → 修正飛行物的落地時間
        for (const x of this.fx) if (x.kind === "throw" && x.aid === e.aid) x.land = e.ts;
      }
    }
    this.fx.sort((a, b) => a.ts - b.ts);
    this.prune(ts1 - KEEP_S);
  }

  private prune(before: number) {
    for (const [id, tr] of this.tracks) {
      while (tr.segs.length > 1 && tr.segs[0].t1 < before) tr.segs.shift();
      while (tr.snaps.length > 1 && tr.snaps[1].ts0 < before) tr.snaps.shift();
      for (const list of [tr.facing, tr.posture, tr.down] as Change<unknown>[][]) while (list.length > 1 && list[1].t < before) list.shift();
      while (tr.present.length > 1 && (tr.present[0][1] ?? Infinity) < before) tr.present.shift();
      const last = tr.present[tr.present.length - 1];
      if (last && last[1] !== null && last[1] < before) this.tracks.delete(id);
    }
    while (this.fx.length && this.fx[0].ts < before) this.fx.shift();
  }

  /** 位置（含修正的平滑） */
  private pos(tr: Track, t: number, withOffset = true): XY {
    let p: XY = tr.segs.length ? tr.segs[0].a : tr.home;
    const segs = tr.segs;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      if (t < s.t0) break;
      if (t >= s.t1) { p = s.b; continue; }
      const dur = Math.max(1e-6, s.t1 - s.t0);
      const a = s.easeIn ? Math.min(0.5, EASE_S / dur) : 0, b = s.easeOut ? Math.min(0.5, EASE_S / dur) : 0;
      const k = easeFrac((t - s.t0) / dur, a, b);
      p = [s.a[0] + (s.b[0] - s.a[0]) * k, s.a[1] + (s.b[1] - s.a[1]) * k];
      if (this.walkable) p = this.corner(segs, i, p);
      break;
    }
    if (withOffset && tr.offset && t >= tr.offset.t) {
      const k = 1 - (t - tr.offset.t) / CORRECT_S;
      if (k > 0) p = [p[0] + tr.offset.dx * k, p[1] + tr.offset.dy * k];
      else tr.offset = null;
    }
    return p;
  }

  /**
   * 轉角圓滑（方案 §5.3）：連續走過一個轉角時，轉角前後各 CORNER_R 格改走二次貝茲曲線（控制點是轉角那一格），
   * 不再是折線。只有轉角那一格和內側那一格（轉進去的對角格）都是空地才做，不讓人看起來穿過牆角或離開掩體。
   */
  private corner(segs: Seg[], i: number, p: XY): XY {
    const s = segs[i];
    const at = (j: number, sideB: boolean): XY | null => {       // segs[j] 與 segs[j+1] 在 segs[j].b 連續轉彎 → 回傳曲線
      const s1 = segs[j], s2 = segs[j + 1];
      if (!s1 || !s2) return null;
      if (s1.t1 - s1.t0 < 1e-6 || s2.t1 - s2.t0 < 1e-6 || s2.t0 - s1.t1 >= GAP_S || dist(s1.b, s2.a) > 1e-6) return null;
      const l1 = dist(s1.a, s1.b), l2 = dist(s2.a, s2.b);
      if (l1 < 1e-6 || l2 < 1e-6) return null;
      const d1: XY = [(s1.b[0] - s1.a[0]) / l1, (s1.b[1] - s1.a[1]) / l1], d2: XY = [(s2.b[0] - s2.a[0]) / l2, (s2.b[1] - s2.a[1]) / l2];
      if (d1[0] * d2[0] + d1[1] * d2[1] > 0.985) return null;      // 幾乎直走
      const b = s1.b;
      const inner: XY = [Math.round(b[0] - d1[0] + d2[0]), Math.round(b[1] - d1[1] + d2[1])];
      if (!this.walkable!(b[0], b[1]) || !this.walkable!(inner[0], inner[1])) return null;
      const r = Math.min(CORNER_R, l1 / 2, l2 / 2);
      const dp = dist(p, b);
      if (dp >= r) return null;
      const u = sideB ? 0.5 + 0.5 * (dp / r) : 0.5 * (1 - dp / r);
      const P0: XY = [b[0] - d1[0] * r, b[1] - d1[1] * r], P2: XY = [b[0] + d2[0] * r, b[1] + d2[1] * r];
      const w0 = (1 - u) * (1 - u), w1 = 2 * u * (1 - u), w2 = u * u;
      return [P0[0] * w0 + b[0] * w1 + P2[0] * w2, P0[1] * w0 + b[1] * w1 + P2[1] * w2];
    };
    const before = dist(p, s.b) < dist(p, s.a);
    return (before ? at(i, false) : at(i - 1, true)) ?? p;
  }

  private moving(tr: Track, t: number): boolean {
    return tr.segs.some((s) => t >= s.t0 && t < s.t1);
  }

  /** 戰鬥時間 t 的畫面狀態（座標是格中心：位置＋0.5） */
  sample(t: number): UnitSample[] {
    const out: UnitSample[] = [];
    for (const tr of this.tracks.values()) {
      let alpha = 0;
      for (const [a, b] of tr.present) {
        if (t < a) continue;
        const inA = Math.min(1, (t - a) / FADE_IN_S);
        const outA = b === null ? 1 : 1 - (t - b) / FADE_OUT_S;
        alpha = Math.max(alpha, Math.min(inA, outA));
      }
      if (tr.deadAt !== null && t > tr.deadAt) alpha = Math.min(alpha, 1 - (t - tr.deadAt) / DEATH_FADE_S);
      if (alpha <= 0) continue;
      let snap = tr.snaps[0];
      for (const s of tr.snaps) { if (s.ts0 > t) break; snap = s; }
      const p = this.pos(tr, t);
      const [fc, fp] = at(tr.facing, t);
      let facing = fc ? fc.v : 0;
      if (fc && fp && t - fc.t < TURN_S) facing = angleLerp(fp.v, fc.v, (t - fc.t) / TURN_S);
      const [pc, pp] = at(tr.posture, t);
      const posture = pc ? pc.v : snap.u.posture;
      const postureK = pc && pp ? Math.min(1, (t - pc.t) / POSTURE_S) : 1;
      const [dc, dp] = at(tr.down, t);
      const down = dc ? dc.v : false;
      let downK = down ? 1 : 0;
      if (dc && dp && t - dc.t < POSTURE_S) downK = down ? (t - dc.t) / POSTURE_S : 1 - (t - dc.t) / POSTURE_S;
      out.push({ id: tr.id, side: snap.u.side, name: snap.u.name, x: p[0] + 0.5, y: p[1] + 0.5, z: snap.u.pos[2] ?? 0,
        facing, posture, postureFrom: pp ? pp.v : posture, postureK, down, downK, dead: tr.deadAt !== null && t > tr.deadAt,
        alpha: Math.max(0, Math.min(1, alpha)), moving: this.moving(tr, t), u: snap.u });
    }
    return out;
  }

  /** ts 在 (t0, t1] 的特效 */
  fxBetween(t0: number, t1: number): TimedFx[] {
    return this.fx.filter((x) => x.ts > t0 && x.ts <= t1);
  }

  clear() {
    this.tracks.clear();
    this.fx = [];
    this.lastTs1 = -Infinity;
  }
}
