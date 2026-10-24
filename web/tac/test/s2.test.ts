// S2：轉角圓滑、地形烘焙級距。
import { describe, expect, it } from "vitest";
import { walkFrames } from "../src/feed";
import { tierFor } from "../src/lod";
import { CORNER_R, Tracks } from "../src/tracks";
import type { XY } from "../src/protocol";

/** 往東走兩格、再往北走兩格（轉角在 (2,0)） */
const route: XY[] = [[0, 0], [1, 0], [2, 0], [2, 1], [2, 2]];

function run(walkable?: (x: number, y: number) => boolean) {
  const tr = new Tracks(walkable);
  for (const f of walkFrames([{ id: "a", route, tileS: 1 }], 6)) tr.ingest(f);
  return (t: number) => {
    const u = tr.sample(t).find((x) => x.id === "a")!;
    return [u.x - 0.5, u.y - 0.5] as XY;
  };
}

describe("轉角圓滑", () => {
  it("轉角兩邊都是空地：轉彎時往內側切（不經過轉角那一點）", () => {
    const at = run(() => true);
    const [x, y] = at(2);                       // 2 秒抵達轉角那一格
    expect(x).toBeLessThan(2 - 0.05);
    expect(y).toBeGreaterThan(0.05);
    expect(Math.hypot(x - 2, y)).toBeLessThan(CORNER_R);
  });

  it("內側那一格是牆：照折線走、經過轉角那一點", () => {
    const at = run((x, y) => !(x === 1 && y === 1));
    const [x, y] = at(2);
    expect(x).toBeCloseTo(2, 6);
    expect(y).toBeCloseTo(0, 6);
  });

  it("沒有給地形：不圓滑（和 S1 一樣）", () => {
    const [x, y] = run()(2);
    expect(x).toBeCloseTo(2, 6);
    expect(y).toBeCloseTo(0, 6);
  });

  it("圓滑的曲線是連續的：轉角附近每 1/60 秒的位移不超過直走的 1.5 倍，離轉角遠的地方不受影響", () => {
    const at = run(() => true);
    let prev = at(1.5), worst = 0;
    for (let t = 1.5 + 1 / 60; t <= 2.5; t += 1 / 60) {
      const p = at(t);
      worst = Math.max(worst, Math.hypot(p[0] - prev[0], p[1] - prev[1]));
      prev = p;
    }
    expect(worst).toBeLessThan((1 / 60) * 1.5);
    const far = at(1.5);
    expect(far[0]).toBeCloseTo(1.5, 6);
    expect(far[1]).toBeCloseTo(0, 6);
  });

  it("直走不受影響", () => {
    const tr = new Tracks(() => true);
    for (const f of walkFrames([{ id: "a", route: [[0, 0], [1, 0], [2, 0], [3, 0]], tileS: 1 }], 5)) tr.ingest(f);
    const u = tr.sample(2).find((x) => x.id === "a")!;
    expect(u.x - 0.5).toBeCloseTo(2, 6);
    expect(u.y - 0.5).toBeCloseTo(0, 6);
  });
});

describe("地形烘焙級距", () => {
  it("每格畫面像素（含 DPR）決定烘焙級距：太大才升一級，最大 32", () => {
    expect(tierFor(4)).toBe(8);
    expect(tierFor(10)).toBe(8);
    expect(tierFor(14)).toBe(16);
    expect(tierFor(21)).toBe(32);
    expect(tierFor(60)).toBe(32);
  });
});
