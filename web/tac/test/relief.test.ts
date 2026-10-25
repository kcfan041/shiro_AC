// 地形高低差（src/relief.ts）：解析伺服器的 relief、土堤的邊（和 engine/tactical/tmap.py edge_key 一致）、明暗、投影、懸停說明。
import { describe, expect, it } from "vitest";
import { Relief } from "../src/relief";

const T = (ground: [number, number, number][], berm: [number, number, string, number, string][] = []) =>
  new Relief({ w: 10, h: 10, relief: { step: 0.2, ground, berm } });

describe("Relief", () => {
  it("全平或舊伺服器沒有 relief：沒有高低差、說明是空的", () => {
    const flat = new Relief({ w: 10, h: 10 });
    expect(flat.any).toBe(false);
    expect(flat.at(3, 3)).toBe(0);
    expect(flat.describe(3, 3)).toBe("");
    expect(T([]).any).toBe(false);
  });

  it("高度單位換成公尺、地圖外當 0", () => {
    const r = T([[2, 3, 8], [4, 4, -3]]);
    expect(r.any).toBe(true);
    expect(r.at(2, 3)).toBeCloseTo(1.6);
    expect(r.at(4, 4)).toBeCloseTo(-0.6);
    expect(r.at(-1, 0)).toBe(0);
    expect(r.at(10, 0)).toBe(0);
  });

  it("土堤：西、南邊是鄰格的東、北邊；頂端從兩側較高的地面算起", () => {
    const r = T([[5, 5, -3]], [[5, 4, "N", 2, "soil"], [4, 5, "E", 3, "soil"]]);
    expect(r.berm(5, 5, "S")).toBeCloseTo(0.4);       // (5,4) 的北邊＝(5,5) 的南邊
    expect(r.berm(5, 4, "N")).toBeCloseTo(0.4);
    expect(r.berm(5, 5, "W")).toBeCloseTo(0.6);       // (4,5) 的東邊
    expect(r.berm(5, 5, "N")).toBe(0);
    expect(r.bermTop(5, 5, "S")).toBeCloseTo(0.4);    // 兩側 max(−0.6, 0)＋0.4
    expect(r.bermTop(5, 5, "N")).toBeNull();
  });

  it("山體陰影：光從西北來，朝西北的坡亮、朝東南的坡暗；高處亮低處暗", () => {
    const up = T([[6, 5, 5]]);                         // (6,5) 高 1.0
    expect(up.hillshade(5, 5)).toBeGreaterThan(0);     // 東邊高：坡朝西 → 亮
    expect(up.hillshade(7, 5)).toBeLessThan(0);        // 西邊高：坡朝東 → 暗
    expect(up.tint(6, 5)).toBeGreaterThan(0);
    expect(T([[6, 5, -5]]).tint(6, 5)).toBeLessThan(0);
  });

  it("投影：西邊、北邊的高處（含土堤頂）投到這一格，高差越大越寬；太小不畫", () => {
    const r = T([[4, 5, 5], [8, 8, 1]]);
    const w1 = r.castFrom(5, 5, "W");
    expect(w1).toBeGreaterThan(0);
    expect(T([[4, 5, 10]]).castFrom(5, 5, "W")).toBeGreaterThan(w1);
    expect(r.castFrom(9, 8, "W")).toBeGreaterThan(0);   // 一階 0.2 m（路緣）也有一條細影
    expect(r.castFrom(9, 8, "W")).toBeLessThan(w1);
    expect(r.castFrom(7, 8, "W")).toBe(0);              // 沒有高差不畫
    expect(r.castFrom(8, 8, "N")).toBe(0);              // 比北邊高：不會被投影
    expect(r.castFrom(5, 5, "N")).toBe(0);
    const pit = T([[5, 5, -3]], [[5, 5, "N", 2, "soil"]]);
    expect(pit.castFrom(5, 5, "N")).toBeGreaterThan(0); // 坑北緣的胸牆投進坑裡
  });

  it("濃淡：一階很淡、高差越大越濃、沒有高差不畫", () => {
    const r = T([]);
    expect(r.strength(0)).toBe(0);
    expect(r.strength(0.2)).toBeGreaterThan(0);
    expect(r.strength(0.2)).toBeLessThan(r.strength(0.6));
    expect(r.strength(1.2)).toBe(1);
    expect(T([[4, 5, 5]]).dropFrom(5, 5, "W")).toBeCloseTo(1.0);
  });

  it("懸停說明：地面高度、坑內、土堤方向", () => {
    expect(T([[3, 3, 8]]).describe(3, 3)).toBe("地面 +1.6 m");
    expect(T([[3, 3, -3]], [[3, 2, "N", 2, "soil"]]).describe(3, 3)).toBe("坑內 −0.6 m（南側胸牆 0.4 m）");
    expect(T([], [[3, 3, "E", 2, "soil"]]).describe(3, 3)).toBe("地面 ±0.0 m（東側土堤 0.4 m）");
    expect(T([[3, 3, 1]]).describe(5, 5)).toBe("");
  });
});
