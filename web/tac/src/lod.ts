// 縮放級距（不依賴 Pixi，測試可以直接用）。

/** 地形烘焙級距：每格幾 px */
export const TIERS = [8, 16, 32];

/** 依縮放（每格 CSS px × DPR）選烘焙級距：實際比級距大 25% 以上才升一級 */
export function tierFor(pxPerTile: number): number {
  for (const t of TIERS) if (pxPerTile <= t * 1.25) return t;
  return TIERS[TIERS.length - 1];
}
