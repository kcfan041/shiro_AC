// S1：播放時鐘、個體軌跡、相機的驗收案例（方案 §15 S1 的完成條件）。
import { describe, expect, it } from "vitest";
import { Camera } from "../src/camera";
import { BattleClock, BUFFER_S } from "../src/clock";
import { Feed, walkFrames, type Walker } from "../src/feed";
import type { Frame, XY } from "../src/protocol";
import { easeFrac, Tracks } from "../src/tracks";

const FPS = 60;

/** 用模擬推送跑時鐘＋軌跡：回傳每一畫面幀的 (實際秒, bt, 樣本) */
function play(frames: Frame[], seconds: number, opt: ConstructorParameters<typeof Feed>[1] = {}, fps = FPS) {
  const feed = new Feed(frames, opt);
  const clock = new BattleClock();
  const tracks = new Tracks();
  const out: { wall: number; bt: number | null; rate: number; stalled: boolean; units: ReturnType<Tracks["sample"]> }[] = [];
  for (let i = 0; i <= seconds * fps; i++) {
    const wall = i / fps;
    for (const { frame, at } of feed.take(wall)) {
      clock.push(frame.ts0 ?? 0, frame.ts1 ?? 0, at);
      tracks.ingest(frame, clock.bt);
    }
    const bt = clock.tick(1 / fps);
    out.push({ wall, bt, rate: clock.rate, stalled: clock.stalled, units: bt === null ? [] : tracks.sample(bt) });
  }
  return { out, clock, tracks };
}

/** 一直往東走的人（一格 1.25 秒，網頁版的行軍速度） */
function marcher(id = "a", tiles = 80, waits?: Record<number, number>): Walker {
  return { id, route: Array.from({ length: tiles }, (_, i) => [10 + i, 20] as XY), tileS: 1.25, waits };
}

/** 一個人在相鄰兩幀之間最大的位移（格） */
function maxStep(out: ReturnType<typeof play>["out"], id: string, after = 0) {
  let worst = 0, prev: XY | null = null;
  for (const o of out) {
    if (o.wall < after) continue;
    const u = o.units.find((x) => x.id === id);
    if (!u) { prev = null; continue; }
    if (prev) worst = Math.max(worst, Math.hypot(u.x - prev[0], u.y - prev[1]));
    prev = [u.x, u.y];
  }
  return worst;
}

describe("播放時鐘", () => {
  it.each([1, 2, 3, 4, 5])("伺服器穩定、到達時間抖動 ±200 ms（種子 %i）：一分鐘的播放／戰鬥時間比在 1 ± 2% 內，速度不跟著抖、不斷", (seed) => {
    const frames = walkFrames([marcher()], 80);
    const { out, clock } = play(frames, 70, { jitter: 0.2, seed });
    const a = out.find((o) => o.wall >= 5)!, b = out.find((o) => o.wall >= 65)!;
    const ratio = (b.bt! - a.bt!) / (b.wall - a.wall);
    expect(Math.abs(ratio - 1)).toBeLessThan(0.02);
    const rates = out.filter((o) => o.wall >= 10 && o.wall <= 65).map((o) => o.rate);
    expect(Math.max(...rates)).toBeLessThan(1.03);       // 抖動只影響緩衝量，速度幾乎不變
    expect(Math.min(...rates)).toBeGreaterThan(0.97);
    expect(clock.stalls).toBe(0);
  });

  it("沒有抖動時速度收斂到 1.0、緩衝平均在 D 附近", () => {
    const { out, clock } = play(walkFrames([marcher()], 60), 50);
    const late = out.filter((o) => o.wall > 30);
    for (const o of late) expect(Math.abs(o.rate - 1)).toBeLessThan(0.01);
    expect(Math.abs(clock.smooth - BUFFER_S)).toBeLessThan(0.05);
  });

  it("伺服器變慢（時間倍率 0.8）：播放速度跟著變成約 0.8，不會一直卡住", () => {
    const { out } = play(walkFrames([marcher()], 60), 60, { timeScale: 0.8, jitter: 0.1 });
    const late = out.filter((o) => o.wall > 25 && o.wall < 55);
    const ratio = (late[late.length - 1].bt! - late[0].bt!) / (late[late.length - 1].wall - late[0].wall);
    expect(Math.abs(ratio - 0.8)).toBeLessThan(0.03);
    expect(out.filter((o) => o.wall > 15 && o.wall < 55 && o.stalled)).toHaveLength(0);   // 剛開始還沒估出速度時可能卡一下
  });

  it("斷線 5 秒後一次收到一堆影格：跳到最新 − D，不高速快轉", () => {
    const { out, clock } = play(walkFrames([marcher()], 40), 30, { outage: [10, 5] });
    expect(clock.jumps).toBe(1);
    expect(clock.skipped).not.toBeNull();
    const rates = out.filter((o) => o.wall > 1).map((o) => o.rate);
    expect(Math.max(...rates)).toBeLessThanOrEqual(1.1 + 1e-9);
  });

  it("影格停了：時間停在最新的 ts1，不越過也不倒退", () => {
    const frames = walkFrames([marcher()], 10);
    const { out, clock } = play(frames, 20);
    const last = frames[frames.length - 1].ts1!;
    let prev = -Infinity;
    for (const o of out) {
      if (o.bt === null) continue;
      expect(o.bt).toBeLessThanOrEqual(last + 1e-9);
      expect(o.bt).toBeGreaterThanOrEqual(prev);
      prev = o.bt;
    }
    expect(clock.stalled).toBe(true);
  });

  it("回放：照播放速度走、可以暫停", () => {
    const c = new BattleClock();
    c.startReplay(10, 2);
    c.tick(1);
    expect(c.bt).toBeCloseTo(12);
    c.paused = true;
    c.tick(1);
    expect(c.bt).toBeCloseTo(12);
  });
});

describe("個體軌跡", () => {
  it("移動連續：抖動 ±200 ms 下，相鄰兩幀的位移不超過 0.05 格（一格 1.25 秒，每幀約 0.013 格）", () => {
    for (const seed of [9, 10, 11]) {
      const { out } = play(walkFrames([marcher()], 60), 50, { jitter: 0.2, seed });
      expect(maxStep(out, "a", 2)).toBeLessThan(0.05);
    }
  });

  it("抵達時間那一刻剛好在那一格", () => {
    const frames = walkFrames([marcher()], 20);
    const tr = new Tracks();
    for (const f of frames) tr.ingest(f);
    // 第 3 格（x＝13）在 3×1.25＝3.75 秒抵達
    const u = tr.sample(3.75).find((x) => x.id === "a")!;
    expect(u.x).toBeCloseTo(13.5, 6);
    expect(u.y).toBeCloseTo(20.5, 6);
  });

  it("連續走的中間是等速，起步與停下才加減速", () => {
    const frames = walkFrames([{ id: "a", route: [[0, 0], [1, 0], [2, 0], [3, 0]], tileS: 1 }], 6);
    const tr = new Tracks();
    for (const f of frames) tr.ingest(f);
    const x = (t: number) => tr.sample(t).find((u) => u.id === "a")!.x - 0.5;
    expect(x(1.5) - x(1.4)).toBeCloseTo(0.1, 6);              // 第二格中間：等速 1 格／秒
    expect(x(0.05)).toBeLessThan(0.05 * 0.5);                   // 剛起步比等速慢
    expect(x(3) - x(2.95)).toBeLessThan(0.05 * 0.5);            // 快停下也比等速慢
  });

  it("被擋住停下等（伺服器不給 nx）：停在原地，之後再走也是連續的", () => {
    const { out } = play(walkFrames([marcher("a", 30, { 5: 3 })], 40), 35, { jitter: 0.15, seed: 4 });
    expect(maxStep(out, "a", 2)).toBeLessThan(0.05);
    const during = out.filter((o) => o.bt !== null && o.bt > 6.5 && o.bt < 9).map((o) => o.units.find((u) => u.id === "a")!.x);
    expect(Math.max(...during) - Math.min(...during)).toBeLessThan(0.01);   // 等待時不動
  });

  it("暫定的下一格沒走成（伺服器改判）：0.2 秒內滑回去，不瞬移", () => {
    const tr = new Tracks();
    const base = { side: "own", name: "a", posture: "stand", facing: [1, 0] as XY, down: false };
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [{ id: "a", ...base, from: [0, 0], pos: [0, 0], path: [], nx: [1, 0], nst: 0.2, nt: 1.45 }], shots: [], blasts: [], smokes: [] });
    const mid = tr.sample(0.9).find((u) => u.id === "a")!.x;
    expect(mid).toBeGreaterThan(0.6);                           // 已經往 x＝1 走了一段
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1.0, units: [{ id: "a", ...base, from: [0, 0], pos: [0, 0], path: [] }], shots: [], blasts: [], smokes: [] }, 0.9);
    expect(tr.sample(0.9).find((u) => u.id === "a")!.x).toBeCloseTo(mid, 6);   // 換掉的那一刻位置不跳
    expect(tr.sample(1.1).find((u) => u.id === "a")!.x).toBeCloseTo(0.5, 6);   // 0.2 秒後回到原地
  });

  it("跳超過 3 格（重生、換人）直接跳，不滑過去", () => {
    const tr = new Tracks();
    const u = (x: number) => ({ id: "a", side: "own", name: "a", posture: "stand", facing: [1, 0] as XY, down: false, from: [x, 0] as XY, pos: [x, 0] as XY, path: [] });
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [u(0)], shots: [], blasts: [], smokes: [] });
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1, units: [u(20)], shots: [], blasts: [], smokes: [] }, 0.6);
    expect(tr.sample(0.6).find((x) => x.id === "a")!.x).toBeCloseTo(20.5, 6);
  });

  it("壞的路徑點（一步差 25 格，伺服器偶爾會有）：不跟著跑，下一格影格再接回正確位置", () => {
    const tr = new Tracks();
    const base = { id: "e", side: "enemy", name: "敵兵", posture: "stand", facing: [1, 0] as XY, down: false };
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [{ ...base, from: [83, 75], pos: [83, 75], path: [], nx: [84, 101], nst: 0.4, nt: 1.65 }], shots: [], blasts: [], smokes: [] });
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1, units: [{ ...base, from: [83, 75], pos: [84, 101], path: [[84, 101]], pt: [0.9], pst: [0.4] }], shots: [], blasts: [], smokes: [] });
    tr.ingest({ t: 2, ts0: 1, ts1: 1.5, units: [{ ...base, from: [84, 76], pos: [84, 76], path: [] }], shots: [], blasts: [], smokes: [] });
    expect(tr.badSteps).toBe(2);
    for (const t of [0.3, 0.6, 0.79]) expect(tr.sample(t)[0].y).toBeCloseTo(75.5, 6);   // 一直留在 (83,75)
    expect(tr.sample(1.2)[0].y).toBeCloseTo(76.5, 6);                                    // 之後接回 (84,76)
  });

  it("朝向走最短的方向轉過去（170° → −170° 經過 180°）", () => {
    const tr = new Tracks();
    const deg = (d: number) => [Math.cos((d * Math.PI) / 180), Math.sin((d * Math.PI) / 180)] as XY;
    const u = (d: number) => ({ id: "a", side: "own", name: "a", posture: "stand", facing: deg(d), down: false, pos: [0, 0] as XY });
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [u(170)], shots: [], blasts: [], smokes: [] });
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1, units: [u(-170)], shots: [], blasts: [], smokes: [] });
    const mid = (tr.sample(0.5 + 0.075).find((x) => x.id === "a")!.facing * 180) / Math.PI;
    expect(Math.abs(Math.abs(mid) - 180)).toBeLessThan(1);
  });

  it("開槍前先轉向：這格有開槍時，朝向在開槍前 0.15 秒開始轉", () => {
    const tr = new Tracks();
    const u = (fx: number, fy: number) => ({ id: "a", side: "own", name: "a", posture: "stand", facing: [fx, fy] as XY, down: false, pos: [0, 0] as XY });
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [u(1, 0)], shots: [], blasts: [], smokes: [] });
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1, units: [u(0, 1)], shots: [{ shooter: "a", from: [0, 0], to: [0, 9], ts: 0.8 }], blasts: [], smokes: [] });
    expect(tr.sample(0.6).find((x) => x.id === "a")!.facing).toBeCloseTo(0, 6);
    expect(tr.sample(0.8).find((x) => x.id === "a")!.facing).toBeCloseTo(Math.PI / 2, 6);
  });

  it("看得到的敵人：出現時 0.2 秒淡入、失去目標時淡出", () => {
    const tr = new Tracks();
    const e = { id: "e", side: "enemy", name: "敵兵", posture: "stand", facing: [1, 0] as XY, down: false, pos: [5, 5] as XY };
    const f = (k: number, with_: boolean): Frame => ({ t: k, ts0: k * 0.5, ts1: k * 0.5 + 0.5, units: with_ ? [e] : [], shots: [], blasts: [], smokes: [] });
    tr.ingest(f(0, false));
    tr.ingest(f(1, true));
    tr.ingest(f(2, true));
    tr.ingest(f(3, false));
    const a = (t: number) => tr.sample(t).find((x) => x.id === "e")?.alpha ?? 0;
    expect(a(0.5)).toBeCloseTo(0, 6);
    expect(a(0.6)).toBeCloseTo(0.5, 6);
    expect(a(1.2)).toBeCloseTo(1, 6);
    expect(a(1.65)).toBeCloseTo(0.5, 6);
    expect(a(1.9)).toBe(0);
  });

  it("倒地照打倒他那一槍的時間，0.25 秒倒下", () => {
    const tr = new Tracks();
    const u = (down: boolean) => ({ id: "a", side: "own", name: "a", posture: "stand", facing: [1, 0] as XY, down, pos: [0, 0] as XY });
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [u(false)], shots: [], blasts: [], smokes: [] });
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1, units: [u(true)], shots: [], blasts: [], smokes: [], ev: [{ seq: 1, t: 1, ts: 0.7, type: "down", target: "a" }] });
    const s = (t: number) => tr.sample(t).find((x) => x.id === "a")!;
    expect(s(0.69).down).toBe(false);
    expect(s(0.7 + 0.125).downK).toBeCloseTo(0.5, 6);
    expect(s(1).downK).toBe(1);
  });

  it("手榴彈：飛行物的落地時間照爆炸事件修正（爆炸可能在下一格影格）", () => {
    const tr = new Tracks();
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, units: [], shots: [], blasts: [], smokes: [],
      ev: [{ seq: 1, t: 0, ts: 0.3, type: "throw", actor: "a", aid: "g1", data: { frm: [0, 0], at: [6, 0], item: "grenades" } }] });
    tr.ingest({ t: 1, ts0: 0.5, ts1: 1, units: [], shots: [], blasts: [{ item: "grenades", ts: 1.6, at: [6, 0] }], smokes: [],
      ev: [{ seq: 2, t: 1, ts: 1.6, type: "blast", actor: "a", aid: "g1" }] });
    const th = tr.fxBetween(0, 1).find((x) => x.kind === "throw");
    expect(th && th.kind === "throw" && th.land).toBeCloseTo(1.6, 6);
    expect(tr.fxBetween(1.5, 1.7).map((x) => x.kind)).toEqual(["blast"]);
  });
});

describe("加減速曲線", () => {
  it("從 0 到 1、單調、兩端連續", () => {
    for (const [a, b] of [[0, 0], [0.2, 0], [0, 0.3], [0.5, 0.5], [0.12, 0.12]]) {
      expect(easeFrac(0, a, b)).toBeCloseTo(0, 9);
      expect(easeFrac(1, a, b)).toBeCloseTo(1, 9);
      let prev = 0;
      for (let k = 0.01; k <= 1; k += 0.01) {
        const v = easeFrac(k, a, b);
        expect(v).toBeGreaterThanOrEqual(prev - 1e-12);
        expect(v - prev).toBeLessThan(0.05);
        prev = v;
      }
    }
  });
});

describe("相機", () => {
  it("跟隨的手感和更新率無關（60 Hz 與 144 Hz 跑 1 秒後位置相同）", () => {
    const run = (hz: number) => {
      const c = new Camera();
      c.follow = () => [10, 5];
      for (let i = 0; i < hz; i++) c.update(1 / hz);
      return [c.x, c.y];
    };
    const [a, b] = [run(60), run(144)];
    expect(a[0]).toBeCloseTo(b[0], 6);
    expect(a[1]).toBeCloseTo(b[1], 6);
  });

  it("滾輪以游標為中心縮放：游標下的世界點不動", () => {
    const c = new Camera();
    c.setViewport(1000, 800);
    c.jumpTo(50, 50);
    const before = c.toWorld(800, 200);
    c.zoomAt(800, 200, 2);
    for (let i = 0; i < 120; i++) c.update(1 / 60);
    expect(c.zoom).toBeCloseTo(28, 3);
    const after = c.toWorld(800, 200);
    expect(after[0]).toBeCloseTo(before[0], 4);
    expect(after[1]).toBeCloseTo(before[1], 4);
  });

  it("拖曳取消跟隨；世界容器位置是整數像素", () => {
    const c = new Camera();
    c.follow = () => [3.3, 7.7];
    c.update(0.1);
    c.pan(13.3, -4.1);
    expect(c.follow).toBeNull();
    const [ox, oy] = c.origin();
    expect(Number.isInteger(ox) && Number.isInteger(oy)).toBe(true);
  });
});
