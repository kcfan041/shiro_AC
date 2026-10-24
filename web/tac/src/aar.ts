// 戰後摘要（方案 §12；TODO 的「戰後復盤」回放部分）：從一場回放的結構化事件整理出
// 誰被誰打倒、誰陣亡、誰救了誰（止血、拖到掩護）、用了哪些支援、小隊什麼時候換戰術，每一條都能跳到那個時間；
// 時間軸上的標記（倒地、陣亡、爆炸、支援、戰術改變）也從這裡來。
// 只用玩家視圖的資料（回放已經依觀看者過濾）：看不到的射手寫「看不到的射手」。純邏輯，不依賴 Pixi。

import { KIND } from "./intentplan";
import type { Frame, XY } from "./protocol";

export type AarKind = "down" | "kia" | "aid" | "drag" | "support" | "intent" | "blast";

export interface AarItem { ts: number; kind: AarKind; icon: string; text: string; at: XY | null }

export interface Aar {
  t0: number;
  t1: number;
  items: AarItem[];
  stats: { shots: number; hits: number; downs: number; kias: number; grenades: number; supports: number };
}

const SUPPORT: Record<string, string> = { airdrop: "精準空投", smoke: "煙幕彈幕", drone: "偵察無人機" };
const SUPPORT_STATE: Record<string, string> = { active: "開始作用", landed: "落地", lost: "被擊落" };

/** 戰鬥秒 → m:ss（以這場的開始為 0） */
export function clockText(ts: number, t0: number): string {
  const s = Math.max(0, Math.floor(ts - t0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function summarize(frames: Frame[]): Aar {
  const t0 = frames.length ? frames[0].ts0 ?? 0 : 0;
  const t1 = frames.length ? frames[frames.length - 1].ts1 ?? t0 : t0;
  const names = new Map<string, { name: string; side: string; pos: XY }>();
  const items: AarItem[] = [];
  const stats = { shots: 0, hits: 0, downs: 0, kias: 0, grenades: 0, supports: 0 };
  const seen = new Set<number>();
  const lastKind = new Map<string, string>();
  const who = (id: string | null | undefined) => {
    if (!id) return "看不到的射手";
    const n = names.get(id);
    return n ? (n.side === "enemy" ? "敵兵" : n.name) : "敵兵";
  };
  for (const f of frames) {
    for (const u of f.units) names.set(u.id, { name: u.name, side: String(u.side), pos: [u.pos[0], u.pos[1]] });
    stats.shots += f.shots.length;
    stats.hits += f.shots.filter((s) => s.mat === "body" || (!s.mat && s.hit)).length;
    for (const e of f.ev || []) {
      if (seen.has(e.seq)) continue;
      seen.add(e.seq);
      const ts = e.ts ?? f.ts0 ?? 0;
      const d = (e.data || {}) as Record<string, unknown>;
      const tgt = e.target ? names.get(e.target) : undefined;
      const at: XY | null = tgt ? tgt.pos : null;
      if (e.type === "down" && e.target) {
        stats.downs++;
        items.push({ ts, kind: "down", icon: "🩸", at, text: `${who(e.target)} 倒地（${who(e.actor)}打中）` });
      } else if (e.type === "kia" && e.target) {
        stats.kias++;
        items.push({ ts, kind: "kia", icon: "✝", at, text: `${who(e.target)} 陣亡${e.actor ? `（${who(e.actor)}）` : ""}` });
      } else if (e.type === "aid" && e.actor && e.target) {
        items.push({ ts, kind: "aid", icon: "✚", at, text: `${who(e.actor)} 替 ${who(e.target)} 止血` });
      } else if (e.type === "drag" && e.actor && e.target) {
        items.push({ ts, kind: "drag", icon: "⇢", at, text: `${who(e.actor)} 把 ${who(e.target)} 拖往掩護` });
      } else if (e.type === "support" && SUPPORT_STATE[String(d.state)]) {
        stats.supports++;
        const a = Array.isArray(d.at) ? [Number(d.at[0]), Number(d.at[1])] as XY : null;
        items.push({ ts, kind: "support", icon: d.what === "airdrop" ? "📦" : d.what === "drone" ? "🛩" : "☁", at: a,
          text: `${SUPPORT[String(d.what)] || d.what}${SUPPORT_STATE[String(d.state)]}` });
      } else if (e.type === "blast") {
        if (d.item === "grenades" || d.item === undefined) stats.grenades++;
        const a = Array.isArray(d.at) ? [Number(d.at[0]), Number(d.at[1])] as XY : null;
        items.push({ ts, kind: "blast", icon: "💥", at: a, text: d.item === "flashes" ? "閃光彈" : "手榴彈爆炸" });
      }
    }
    for (const [sid, it] of Object.entries(f.intents || {})) {
      const prev = lastKind.get(sid);
      lastKind.set(sid, it.kind);
      if (prev !== undefined && prev !== it.kind) {
        const mem = f.units.filter((u) => (u.group || u.squad) === sid);
        const at: XY | null = mem.length ? [mem.reduce((a, u) => a + u.pos[0], 0) / mem.length, mem.reduce((a, u) => a + u.pos[1], 0) / mem.length] : null;
        const label = (k: string) => (KIND[k] || ["", k])[1];
        items.push({ ts: f.ts0 ?? 0, kind: "intent", icon: "⇄", at, text: `${it.callsign}：${label(prev)} → ${label(it.kind)}` });
      }
    }
  }
  items.sort((a, b) => a.ts - b.ts);
  return { t0, t1, items, stats };
}

/** 摘要卡要列的（爆炸只當時間軸標記，不列進摘要；太多時只留重要的） */
export function headline(a: Aar, max = 12): AarItem[] {
  const pri: Record<AarKind, number> = { kia: 0, down: 1, aid: 2, drag: 3, support: 4, intent: 5, blast: 9 };
  const keep = a.items.filter((x) => x.kind !== "blast");
  if (keep.length <= max) return keep;
  return [...keep].sort((x, y) => pri[x.kind] - pri[y.kind] || x.ts - y.ts).slice(0, max).sort((x, y) => x.ts - y.ts);
}
