// 色調：地形、陣營、狀態、介面的顏色都從這裡拿，換色調只換這一份。
// slate（預設）：中性的灰藍戰術圖，我方綠、友軍青、敵人紅在灰藍底上都比較醒目（S2 由指揮官選定）；
// green：延續指揮室其他面板的深綠「軍用螢幕」風格，留作選項（實驗頁 &theme=green）。

export interface Theme {
  name: string;
  label: string;
  bg: number;
  ground: Record<"r" | "p" | "c" | "d" | "g", number>;
  /** 地面雜訊亮度（0～1，越大越斑駁） */
  noise: number;
  indoor: number;
  curb: number;
  /** 車道中線 */
  lane: number;
  wall: number;
  wallTop: number;
  wallSide: number;
  shadow: number;
  shadowAlpha: number;
  window: number;
  glass: number;
  door: number;
  low: number;
  lowTop: number;
  rubble: number;
  vegBase: number;
  canopy: number;
  stair: number;
  grid: number;
  gridFine: number;
  labelColor: number;
  outside: number;
  side: Record<"own" | "ally" | "remnant" | "enemy", number>;
  /** 機兵剪影的裝甲底色（陣營色塗在肩甲與感測器） */
  armor: number;
  visor: Record<"own" | "ally" | "remnant" | "enemy", number>;
  suppressed: number;
  pinned: number;
  casualty: number;
  stabilized: number;
  select: number;
  text: number;
}

export const THEMES: Record<string, Theme> = {
  green: {
    name: "green", label: "綠色指揮室",
    bg: 0x070a08,
    ground: { r: 0x1c2420, p: 0x0f1410, c: 0x131813, d: 0x15140d, g: 0x0d1a0f },
    noise: 0.07,
    indoor: 0x1a231d, curb: 0x2e3b30,
    lane: 0x4a5a44,
    wall: 0x3a4a3e, wallTop: 0x6f8574, wallSide: 0x24302a, shadow: 0x000000, shadowAlpha: 0.4,
    window: 0x2a4458, glass: 0x7fb0d0, door: 0xc8a060,
    low: 0x4f5a3a, lowTop: 0x7a8658, rubble: 0x4d4232,
    vegBase: 0x15291a, canopy: 0x1f4426, stair: 0x6a6a5e,
    grid: 0x50825f, gridFine: 0x1c2a20, labelColor: 0x8cbe96, outside: 0x000000,
    side: { own: 0x6fe37f, ally: 0x7fd4c4, remnant: 0xa6e3c8, enemy: 0xe0584d },
    armor: 0x2c3a30,
    visor: { own: 0xb8ffc4, ally: 0xb8f4ea, remnant: 0xd8f5e6, enemy: 0xffb0a0 },
    suppressed: 0xe8b04a, pinned: 0xe0584d, casualty: 0xff5a4a, stabilized: 0xffffff, select: 0xffffff, text: 0xcfeed6,
  },
  slate: {
    name: "slate", label: "灰藍戰術圖",
    bg: 0x0a0e13,
    ground: { r: 0x232a33, p: 0x141920, c: 0x181d24, d: 0x1c1b18, g: 0x121c17 },
    noise: 0.07,
    indoor: 0x232a34, curb: 0x37414e,
    lane: 0x5e6a78,
    wall: 0x485462, wallTop: 0x93a2b3, wallSide: 0x2c343f, shadow: 0x000000, shadowAlpha: 0.45,
    window: 0x2c4b66, glass: 0x8ccaf0, door: 0xd2a868,
    low: 0x56606c, lowTop: 0x8a96a4, rubble: 0x4e4840,
    vegBase: 0x18261e, canopy: 0x24402f, stair: 0x77808c,
    grid: 0x5a7590, gridFine: 0x1d2530, labelColor: 0xa8bccf, outside: 0x000000,
    side: { own: 0x5fe38a, ally: 0x5cc8e6, remnant: 0xa8e0c4, enemy: 0xff5a4e },
    armor: 0x2a3340,
    visor: { own: 0xb0ffd0, ally: 0xb4ecff, remnant: 0xdcf6ea, enemy: 0xffb4a8 },
    suppressed: 0xf0b44a, pinned: 0xff5a4e, casualty: 0xff5a4e, stabilized: 0xffffff, select: 0xffffff, text: 0xd6e2ee,
  },
};

export function getTheme(name: string | null | undefined): Theme {
  return THEMES[name || ""] || THEMES.slate;
}

export function sideKey(side: string): "own" | "ally" | "remnant" | "enemy" {
  return side === "ally" || side === "remnant" || side === "enemy" ? side : "own";
}

export function cssHex(c: number): string {
  return `#${c.toString(16).padStart(6, "0")}`;
}
