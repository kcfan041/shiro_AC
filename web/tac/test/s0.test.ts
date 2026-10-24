// S0：資料格式檢查、壓力測試場景、幀時間統計。
import { describe, expect, it } from "vitest";
import { checkFrame, checkTerrain } from "../src/protocol";
import { makeRng } from "../src/rng";
import { FrameTimer } from "../src/stats";
import { makeTerrain, StressScene, walkable } from "../src/stress";

describe("protocol", () => {
  it("合法的影格沒有問題", () => {
    const f = { t: 3, ts0: 1, ts1: 1.5, units: [{ id: "a", pos: [1, 2], path: [[1, 3]], pt: [1.2] }], shots: [], blasts: [], smokes: [] };
    expect(checkFrame(f)).toEqual([]);
  });
  it("缺欄位、時間倒過來、pt 與 path 長度不同都會被抓到", () => {
    const f = { t: 3, ts0: 2, ts1: 1, units: [{ id: "a", pos: [1, 2], path: [[1, 3]], pt: [] }], shots: [] };
    const p = checkFrame(f);
    expect(p).toContain("缺 blasts");
    expect(p).toContain("ts1 早於 ts0");
    expect(p.some((x) => x.includes("pt 與 path"))).toBe(true);
  });
  it("地形每列長度要等於寬度", () => {
    const t = makeTerrain(1, 20, 10);
    expect(checkTerrain(t)).toEqual([]);
    expect(checkTerrain({ ...t, terrain: [...t.terrain.slice(0, 9), "#"] })).toHaveLength(1);
  });
});

describe("亂數與壓力測試場景", () => {
  it("同一個種子得到同一串亂數", () => {
    const a = makeRng(42), b = makeRng(42);
    expect([a(), a(), a()]).toEqual([b(), b(), b()]);
  });
  it("同一個種子跑出同樣的事件（量測才比得起來）", () => {
    const t = makeTerrain(7);
    const run = () => {
      const s = new StressScene(t, 60, 3);
      let shots = 0, blasts = 0;
      for (let i = 0; i < 300; i++) for (const e of s.step(1 / 60)) e.kind === "shot" ? shots++ : e.kind === "blast" ? blasts++ : 0;
      return { shots, blasts, pos: s.units.map((u) => [u.x.toFixed(3), u.y.toFixed(3)]) };
    };
    const r1 = run(), r2 = run();
    expect(r1).toEqual(r2);
    expect(r1.shots).toBeGreaterThan(50);       // 5 秒 60 人，負載要像交火
    expect(r1.blasts).toBeGreaterThan(0);
  });
  it("個體不會走進牆裡或走出地圖", () => {
    const t = makeTerrain(7);
    const s = new StressScene(t, 120, 5);
    for (let i = 0; i < 600; i++) s.step(1 / 30);
    for (const u of s.units) expect(walkable(t, Math.floor(u.x), Math.floor(u.y))).toBe(true);
  });
});

describe("幀時間統計", () => {
  it("平均、p95、最大值與卡頓幀數", () => {
    const tm = new FrameTimer(100);
    for (let i = 0; i < 95; i++) tm.push(16, 1);
    for (let i = 0; i < 5; i++) tm.push(50, 3);
    const s = tm.stats();
    expect(s.frames).toBe(100);
    expect(s.maxMs).toBe(50);
    expect(s.p95Ms).toBe(50);
    expect(s.slow).toBe(5);
    expect(s.avgMs).toBeCloseTo(17.7, 5);
    expect(s.updateMs).toBeCloseTo(1.1, 5);
  });
  it("只保留最近 N 幀", () => {
    const tm = new FrameTimer(10);
    for (let i = 0; i < 10; i++) tm.push(100);
    for (let i = 0; i < 10; i++) tm.push(10);
    expect(tm.stats().maxMs).toBe(10);
  });
});
