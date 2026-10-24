// 特效與音效的「怎麼呈現」規則（不依賴 Pixi 與 Web Audio，測試可以直接用）。

/** 彈著材質（伺服器 shots[].mat）：wall 牆、low 矮牆、rubble 瓦礫、ground 地面、body 人、air 沒打到東西、unk 不明（打中看不到的人） */
export type Mat = "wall" | "low" | "rubble" | "ground" | "body" | "air" | "unk";

export interface ImpactStyle {
  /** 塵土顆粒數、顏色、大小（格）、存活秒數、往外噴的速度（格／秒） */
  dust: number;
  dustColor: number;
  dustSize: number;
  dustLife: number;
  speed: number;
  /** 碎屑（小而快、有重力感） */
  chips: number;
  chipColor: number;
  /** 火花或血點（加亮） */
  sparks: number;
  sparkColor: number;
  /** 聲音：彈著聲的音色（null＝沒有） */
  sound: "hard" | "soft" | "dirt" | "flesh" | null;
}

const STYLES: Record<Mat, ImpactStyle> = {
  wall: { dust: 6, dustColor: 0xc8c4b8, dustSize: 0.55, dustLife: 0.55, speed: 1.4, chips: 4, chipColor: 0x9a968c, sparks: 2, sparkColor: 0xffe0a0, sound: "hard" },
  low: { dust: 5, dustColor: 0xbab4a4, dustSize: 0.5, dustLife: 0.5, speed: 1.2, chips: 3, chipColor: 0x8c8678, sparks: 1, sparkColor: 0xffe0a0, sound: "hard" },
  rubble: { dust: 6, dustColor: 0xa08c70, dustSize: 0.55, dustLife: 0.6, speed: 1.2, chips: 4, chipColor: 0x7a6650, sparks: 0, sparkColor: 0, sound: "hard" },
  ground: { dust: 5, dustColor: 0x8a7a60, dustSize: 0.45, dustLife: 0.45, speed: 1.6, chips: 3, chipColor: 0x6a5a44, sparks: 0, sparkColor: 0, sound: "dirt" },
  body: { dust: 0, dustColor: 0, dustSize: 0, dustLife: 0, speed: 1.5, chips: 0, chipColor: 0, sparks: 4, sparkColor: 0xff4a3a, sound: "flesh" },
  air: { dust: 0, dustColor: 0, dustSize: 0, dustLife: 0, speed: 0, chips: 0, chipColor: 0, sparks: 0, sparkColor: 0, sound: null },
  unk: { dust: 4, dustColor: 0xa8a49a, dustSize: 0.45, dustLife: 0.45, speed: 1.2, chips: 0, chipColor: 0, sparks: 0, sparkColor: 0, sound: "soft" },
};

export function impactStyle(mat: string | null | undefined, hit = false): ImpactStyle {
  if (mat && mat in STYLES) return STYLES[mat as Mat];
  return hit ? STYLES.body : STYLES.unk;                  // 舊影格沒有 mat：命中就當人，其他當不明
}

/**
 * 曳光抽樣：同一個射手連發時每 2 發畫 1 條（不然一整片亮線蓋住畫面）；隔超過 gap 秒算新的一串。
 * 彈著與聲音照實際發數，只有曳光抽樣。
 */
export class TracerThinner {
  private last = new Map<string, { t: number; n: number }>();
  constructor(private every = 2, private gap = 0.35) {}

  /** 這一發要不要畫曳光（看不到的射手 shooter＝null：每發都畫） */
  take(shooter: string | null, t: number): boolean {
    if (!shooter) return true;
    const s = this.last.get(shooter);
    const n = s && t - s.t < this.gap ? s.n + 1 : 0;
    this.last.set(shooter, { t, n });
    return n % this.every === 0;
  }
}

/** 同一次射擊相鄰兩發的間隔（秒，畫面用）：規則上一次射擊（點放、壓制一波）的每一發同時結算、時間一樣，畫面依武器射速錯開 */
export const ROUND_GAP: Record<string, number> = { rifle: 0.12, lmg: 0.1, dmr: 0.3, shotgun: 0.45, pistol: 0.2 };
const SHOTGUN_PELLETS = 8;

/**
 * 每一發子彈在畫面上的時間：伺服器給第幾發（`r`，同一發霰彈的彈丸同號）就照 r × 間隔往後錯開。
 * 舊影格沒有 r：同一射手、同一時間、同一個 aid 的子彈依順序當第 0、1、2… 發（霰彈槍每 8 顆彈丸算一發）。
 */
export function roundTimes(shots: { shooter: string | null; ts?: number | null; aid?: string | null; r?: number }[],
  weaponOf: (shooter: string | null) => string | null, fallback: (i: number) => number): number[] {
  const legacy = !shots.some((s) => s.r != null);
  const seen = new Map<string, number>();
  return shots.map((s, i) => {
    if (s.ts == null) return fallback(i);
    const w = weaponOf(s.shooter);
    let r = s.r ?? 0;
    if (legacy) {
      const key = `${s.shooter ?? "?"}|${s.ts}|${s.aid ?? ""}`;
      const n = seen.get(key) ?? 0;
      seen.set(key, n + 1);
      r = w === "shotgun" ? Math.floor(n / SHOTGUN_PELLETS) : n;
    }
    return s.ts + r * (ROUND_GAP[w ?? "rifle"] ?? ROUND_GAP.rifle);
  });
}

/** 曳光的顏色與粗細：看不到的射手偏紅、警戒射擊青色、壓制射擊淡而細、一般射擊暖黃 */
export function tracerStyle(o: { unseen?: boolean; ow?: boolean; mode?: string | null }): { color: number; width: number; alpha: number } {
  if (o.unseen) return { color: 0xff7a64, width: 0.12, alpha: 0.85 };
  if (o.ow) return { color: 0x5adce8, width: 0.13, alpha: 0.95 };
  if (o.mode === "suppress") return { color: 0xf0c070, width: 0.09, alpha: 0.6 };
  return { color: 0xffe8a0, width: 0.13, alpha: 0.95 };
}

/** 畫面震動：附近（< 10 格）有爆炸而且有我方在 6 格內時才震，幅度隨距離減弱（格） */
export function shakeFor(blastDist: number, ownNear: boolean, flash: boolean): number {
  if (!ownNear || blastDist > 10) return 0;
  return (flash ? 0.15 : 0.35) * (1 - blastDist / 10);
}
