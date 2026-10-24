// 伺服器的 world 推送：每個看得到的區域一份影格（同一場 Battle、同一個戰鬥時間），合成一格。
// 個體各區域不重複；子彈、爆炸可能跨區（起點在一區、落點在另一區）→ 去重；事件依 seq 去重；視野、意圖合併。
// GM 視角的影格沒有 own／ally／enemy（是真相），依 kind 補上。純邏輯，不依賴 Pixi。

import type { Frame, FrameUnit } from "./protocol";

export function mergeFrames(frames: Frame[], gm = false): Frame | null {
  if (!frames.length) return null;
  const f0 = frames[0];
  const out: Frame = { t: f0.t, world_tick: f0.world_tick, ts0: f0.ts0, ts1: f0.ts1, units: [], ghosts: [], shots: [], blasts: [], smokes: [], ev: [] };
  const ids = new Set<string>(), shotKeys = new Set<string>(), blastKeys = new Set<string>(), smokeKeys = new Set<string>(), seqs = new Set<number>();
  const ghostIds = new Set<string>();
  let vision: Record<string, [number, number][]> | undefined, intents: Frame["intents"] | undefined;
  for (const f of frames) {
    for (const u of f.units) if (!ids.has(u.id)) { ids.add(u.id); out.units.push(gm ? gmSide(u) : u); }
    for (const g of f.ghosts || []) if (!ghostIds.has(g.id)) { ghostIds.add(g.id); out.ghosts!.push(g); }
    for (const s of f.shots) {
      const k = `${s.aid}|${s.from}|${s.to}|${s.ts}`;
      if (!shotKeys.has(k)) { shotKeys.add(k); out.shots.push(s); }
    }
    for (const b of f.blasts) {
      const k = `${b.ts}|${b.at}`;
      if (!blastKeys.has(k)) { blastKeys.add(k); out.blasts.push(b); }
    }
    for (const s of f.smokes) {
      const k = `${s.x},${s.y}`;
      if (!smokeKeys.has(k)) { smokeKeys.add(k); out.smokes.push(s); }
    }
    for (const e of f.ev || []) if (!seqs.has(e.seq)) { seqs.add(e.seq); out.ev!.push(e); }
    if (f.vision) vision = { ...(vision || {}), ...f.vision };
    if (f.intents) intents = { ...(intents || {}), ...f.intents };
  }
  out.ev!.sort((a, b) => a.seq - b.seq);
  if (vision) out.vision = vision;
  if (intents) out.intents = intents;
  return out;
}

/** GM 視角：小隊當友軍、殘部當殘部、其他當敵人 */
export function gmSide(u: FrameUnit): FrameUnit {
  const side = u.kind === "s" ? "ally" : u.kind === "g" ? "remnant" : "enemy";
  return { ...u, side };
}
