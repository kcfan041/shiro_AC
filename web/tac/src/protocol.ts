// 伺服器送給戰術畫面的資料型別（engine/views.py、engine/replay.py 的輸出）。
// 座標：全圖小格（1 格＝2 m），x 向東、y 向北，y＝0 在地圖最南邊；pos 的第三個值是樓層（0＝地面，可能沒有）。

export type XY = [number, number];
export type Pos = [number, number] | [number, number, number];

/** /api/world_terrain：整張全圖地形，只載一次。 */
export interface WorldTerrain {
  w: number;
  h: number;
  /** 一個作戰格是幾個小格（25） */
  cell: number;
  /** 一列一字串，terrain[0] 是 y＝0。# 牆 W 窗 | 隔牆 = 矮牆 % 瓦礫 " 植被 H 樓梯 . 空地 */
  terrain: string[];
  rows: string[];
  cols: string[];
  names: Record<string, string>;
  rooms: { id: number; rect: [number, number, number, number]; doors: XY[] }[];
  /** 地面類型（畫面用），一列一字串：r 柏油路 p 鋪面 c 水泥 d 泥土碎石 g 草地；舊伺服器沒有 */
  ground?: string[];
  /** 每個作戰格的地圖模板（street、crossroad、ruins…） */
  templates?: Record<string, string>;
}

export type Side = "own" | "ally" | "remnant" | "enemy";
export type Posture = "stand" | "crouch" | "prone";

export interface FrameUnit {
  id: string;
  side: Side | string;
  kind?: string | null;
  group?: string | null;
  squad?: string | null;
  name: string;
  from?: Pos;
  path?: Pos[];
  pos: Pos;
  posture: Posture | string;
  facing: XY;
  down: boolean;
  dead?: boolean;
  exited?: boolean;
  weapon?: string;
  suppressed?: boolean;
  pinned?: boolean;
  braced?: boolean;
  bracing?: boolean;
  dazzled?: boolean;
  stabilized?: boolean;
  supp?: number;
  why?: string;
  /** 路徑每格的抵達時間（戰鬥秒） */
  pt?: number[];
  /** 路徑每格的起步時間 */
  pst?: number[];
  /** 影格結束時正在走向的下一格、起步與預計抵達時間 */
  nx?: Pos;
  nst?: number;
  nt?: number;
}

export interface Shot {
  shooter: string | null;
  from: XY;
  to: XY;
  kind?: string;
  hit?: string | null;
  aid?: string | null;
  ow?: boolean;
  mode?: string | null;
  ts?: number | null;
  /** 看不到的射手：只給彈道後段 */
  unseen?: boolean;
  /** 彈著材質：wall 牆、low 矮牆、rubble 瓦礫、ground 地面、body 人、air 沒打到東西、unk 不明（打中看不到的人） */
  mat?: string;
}

export interface Blast { thrower?: string | null; item: string; ts?: number | null; at: XY }
export interface Smoke { x: number; y: number; r: number; density?: number }
export interface Ghost { id: string; pos: Pos; age: number }

export interface StructEvent {
  seq: number;
  t: number;
  ts?: number;
  phase?: string;
  type: string;
  actor?: string | null;
  target?: string | null;
  aid?: string | null;
  grp?: string | null;
  data?: Record<string, unknown> | null;
}

/** 一個執行步的影格（迷霧過濾後） */
export interface Frame {
  t: number;
  world_tick?: number;
  ts0?: number | null;
  ts1?: number | null;
  units: FrameUnit[];
  ghosts?: Ghost[];
  shots: Shot[];
  blasts: Blast[];
  smokes: Smoke[];
  events?: string[];
  supports?: Record<string, unknown>[];
  ev?: StructEvent[];
}

export interface ReplayMeta {
  id: string;
  cell: string;
  w: number;
  h: number;
  start_tick: number;
  end_tick: number;
  zone: boolean;
  bbox: [number, number, number, number] | null;
}

/** scripts/tac_fixture.py 產生的實驗頁資料：地形＋一場回放（已依觀看者過濾） */
export interface Fixture {
  terrain: WorldTerrain;
  replay: ReplayMeta;
  frames: Frame[];
}

/** 檢查影格的必要欄位；回傳問題清單（空＝沒問題）。 */
export function checkFrame(f: unknown): string[] {
  const out: string[] = [];
  if (!f || typeof f !== "object") return ["影格不是物件"];
  const o = f as Record<string, unknown>;
  if (typeof o.t !== "number") out.push("缺 t");
  for (const k of ["units", "shots", "blasts", "smokes"]) if (!Array.isArray(o[k])) out.push(`缺 ${k}`);
  if (o.ts0 != null && o.ts1 != null && (o.ts1 as number) < (o.ts0 as number)) out.push("ts1 早於 ts0");
  if (Array.isArray(o.units)) {
    o.units.forEach((u, i) => {
      const r = u as Record<string, unknown>;
      if (typeof r.id !== "string") out.push(`units[${i}] 缺 id`);
      if (!Array.isArray(r.pos) || r.pos.length < 2) out.push(`units[${i}] pos 不對`);
      const path = Array.isArray(r.path) ? r.path : [];
      if (Array.isArray(r.pt) && r.pt.length !== path.length) out.push(`units[${i}] pt 與 path 長度不同`);
    });
  }
  return out;
}

/** 檢查地形：寬高與每一列的長度要一致。 */
export function checkTerrain(t: unknown): string[] {
  if (!t || typeof t !== "object") return ["地形不是物件"];
  const o = t as Partial<WorldTerrain>;
  if (typeof o.w !== "number" || typeof o.h !== "number") return ["缺 w／h"];
  if (!Array.isArray(o.terrain) || o.terrain.length !== o.h) return ["terrain 列數與 h 不同"];
  const bad = o.terrain.findIndex((row) => typeof row !== "string" || row.length !== o.w);
  return bad >= 0 ? [`terrain[${bad}] 長度與 w 不同`] : [];
}
