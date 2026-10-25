// 地形高低差（伺服器 /api/world_terrain 的 relief，docs/new/terrain.md §10）：純邏輯，畫面與懸停說明共用。
// 每格一個地面高度、每條格界一個土堤高度（伺服器只列不是 0 的，高度單位 step m）。座標：全圖小格，y 向北。

import type { WorldTerrain } from "./protocol";

export type Side = "E" | "W" | "N" | "S";

export class Relief {
  readonly w: number;
  readonly h: number;
  /** 有沒有任何高低差（全平時畫面完全照舊） */
  readonly any: boolean;
  private z: Float32Array;
  private berms = new Map<string, { h: number; mat: string }>();

  constructor(t: Pick<WorldTerrain, "w" | "h" | "relief">) {
    this.w = t.w;
    this.h = t.h;
    this.z = new Float32Array(t.w * t.h);
    const r = t.relief;
    const step = r?.step ?? 0.2;
    for (const [x, y, u] of r?.ground ?? []) if (x >= 0 && y >= 0 && x < t.w && y < t.h) this.z[y * t.w + x] = u * step;
    for (const [x, y, side, u, mat] of r?.berm ?? []) this.berms.set(`${x},${y},${side}`, { h: u * step, mat });
    this.any = (r?.ground?.length ?? 0) > 0 || this.berms.size > 0;
  }

  /** 和另一份地形不同的格子（地面高度不同，或四邊的土堤不同）：[x, y] 清單 */
  diff(o: Relief): [number, number][] {
    const out = new Set<number>();
    for (let i = 0; i < this.z.length; i++) if (this.z[i] !== o.z[i]) out.add(i);
    const keys = new Set([...this.berms.keys(), ...o.berms.keys()]);
    for (const k of keys) {
      const a = this.berms.get(k), b = o.berms.get(k);
      if (a?.h === b?.h && a?.mat === b?.mat) continue;
      const [x, y, side] = k.split(",");
      const cx = +x, cy = +y;
      for (const [px, py] of [[cx, cy], side === "E" ? [cx + 1, cy] : [cx, cy + 1]])
        if (px >= 0 && py >= 0 && px < this.w && py < this.h) out.add(py * this.w + px);
    }
    return [...out].sort((a, b) => a - b).map((i) => [i % this.w, Math.floor(i / this.w)] as [number, number]);
  }

  /** 地面高度（m）；地圖外當 0 */
  at(x: number, y: number): number {
    return x >= 0 && y >= 0 && x < this.w && y < this.h ? this.z[y * this.w + x] : 0;
  }

  /** 格子 (x, y) 某一邊的土堤高度（m，從兩側較高的地面算起）；西、南邊換成鄰格的東、北邊（和 engine/tactical/tmap.py edge_key 一致） */
  berm(x: number, y: number, side: Side): number {
    const k = side === "E" ? `${x},${y},E` : side === "W" ? `${x - 1},${y},E` : side === "N" ? `${x},${y},N` : `${x},${y - 1},N`;
    return this.berms.get(k)?.h ?? 0;
  }

  /** 土堤頂端的絕對高度（m）：兩側較高的地面＋土堤；沒有土堤回傳 null */
  bermTop(x: number, y: number, side: Side): number | null {
    const b = this.berm(x, y, side);
    if (b <= 0) return null;
    const [nx, ny] = side === "E" ? [x + 1, y] : side === "W" ? [x - 1, y] : side === "N" ? [x, y + 1] : [x, y - 1];
    return Math.max(this.at(x, y), this.at(nx, ny)) + b;
  }

  /** 山體陰影（光從西北來）：面向西北的坡亮、面向東南的坡暗，回傳亮度修正（約 −0.3～0.3） */
  hillshade(x: number, y: number): number {
    const dzdx = (this.at(x + 1, y) - this.at(x - 1, y)) / 2;
    const dzdy = (this.at(x, y + 1) - this.at(x, y - 1)) / 2;   // 往北為正
    return Math.max(-0.3, Math.min(0.3, (dzdx - dzdy) * 1.1));
  }

  /** 高處亮、低處暗（約 −0.25～0.25；2 m 的緩丘頂比平地亮兩成多） */
  tint(x: number, y: number): number {
    return Math.max(-0.25, Math.min(0.25, this.at(x, y) * 0.12));
  }

  /** 西邊或北邊（地面或土堤頂）比這一格高多少（m，沒有就 0） */
  dropFrom(x: number, y: number, side: "W" | "N"): number {
    const [nx, ny] = side === "W" ? [x - 1, y] : [x, y + 1];
    const top = Math.max(this.at(nx, ny), this.bermTop(x, y, side) ?? -Infinity);
    return Math.max(0, top - this.at(x, y));
  }

  /** 從西邊、北邊的高處投到這一格的陰影寬度（格的比例，0～0.5）：高差越大越寬；光從西北來 */
  castFrom(x: number, y: number, side: "W" | "N"): number {
    const dh = this.dropFrom(x, y, side);
    return dh < 0.19 ? 0 : Math.min(0.5, 0.1 + dh * 0.3);
  }

  /** 陰影、台階邊的濃淡（0～1）：一階 0.2 m 很淡（像等高線），1 m 以上最濃 */
  strength(dh: number): number {
    return dh < 0.19 ? 0 : Math.min(1, 0.25 + (dh - 0.2) * 0.95);
  }

  /** 懸停說明用：「地面 +1.6 m」「坑內 −0.6 m（北側胸牆 0.4 m）」；全平且沒有土堤回傳空字串 */
  describe(x: number, y: number): string {
    const z = this.at(x, y);
    const nb = [this.at(x - 1, y), this.at(x + 1, y), this.at(x, y - 1), this.at(x, y + 1)];
    const rim = Math.min(...nb);
    const pit = z <= rim - 0.39;
    const sides: string[] = [];
    for (const [side, name] of [["N", "北"], ["S", "南"], ["E", "東"], ["W", "西"]] as [Side, string][]) {
      const b = this.berm(x, y, side);
      if (b > 0) sides.push(`${name}側${pit ? "胸牆" : "土堤"} ${b.toFixed(1)} m`);
    }
    if (Math.abs(z) < 0.05 && !sides.length) return "";
    const sign = z > 0 ? "+" : z < 0 ? "−" : "±";
    const head = `${pit ? "坑內" : "地面"} ${sign}${Math.abs(z).toFixed(1)} m`;
    return sides.length ? `${head}（${sides.join("、")}）` : head;
  }
}
