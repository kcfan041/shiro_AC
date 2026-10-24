// 注意力佇列（方案 §10）：從播到的影格挑出指揮官該知道的事，變成畫面邊緣的提示卡（點了鏡頭移過去）。
// 接敵（小隊安靜一陣子後第一次發現敵人）、我方倒地、陣亡、命令中斷、支援到達（煙幕生效、空投落地、無人機開始偵察）。
// 不自動搶鏡頭；同一件事不重複提示；落後跳過的影格照樣處理（事件不會因為畫面跳過去就漏掉）。純邏輯，不依賴 Pixi。

import type { Frame, SquadIntent, XY } from "./protocol";

export type AttnKind = "contact" | "down" | "kia" | "interrupted" | "support";

export interface Attn {
  id: string;
  kind: AttnKind;
  icon: string;
  text: string;
  /** 事件的位置（全圖小格，世界座標：y 向北） */
  at: XY | null;
  /** 戰鬥時間 */
  ts: number;
  /** 收到的實際時間（毫秒，卡片多久收起） */
  wall: number;
  squad?: string;
}

/** 接敵：小隊安靜這麼久（戰鬥秒）之後再發現敵人才提示 */
export const CONTACT_QUIET_S = 20;
/** 卡片顯示多久（毫秒） */
export const CARD_MS = 6000;
export const MAX_CARDS = 4;

const DIRS = ["東", "東北", "北", "西北", "西", "西南", "南", "東南"];

/** 方向（世界座標，y 向北）→ 八方位 */
export function compass(dx: number, dy: number): string {
  const a = Math.atan2(dy, dx);
  return DIRS[((Math.round(a / (Math.PI / 4)) % 8) + 8) % 8];
}

export class Attention {
  items: Attn[] = [];
  private lastSpot = new Map<string, number>();       // 小隊 → 最後一次發現敵人的戰鬥時間
  private seenEv = new Set<string>();
  private orderState = new Map<string, string>();
  private supportState = new Map<string, string>();
  private done = new Set<Frame>();

  /** 處理一格剛開始播的影格（同一格只處理一次）；supports：這一刻的支援（狀態變了才提示） */
  feed(f: Frame, wallMs: number, supports: { id?: string; kind: string; state: string; label?: string; x: number; y: number }[] = []): Attn[] {
    if (this.done.has(f)) return [];
    this.done.add(f);
    if (this.done.size > 400) this.done = new Set([...this.done].slice(-200));
    if (this.seenEv.size > 5000) this.seenEv = new Set([...this.seenEv].slice(-2000));
    const out: Attn[] = [];
    const ts = f.ts0 ?? 0;
    const unit = new Map(f.units.map((u) => [u.id, u]));
    const intents = f.intents || {};
    const sqOf = (id: string | null | undefined) => { const u = id ? unit.get(id) : undefined; return u ? (u.group || u.squad || null) as string | null : null; };
    const call = (sid: string | null) => (sid && intents[sid]?.callsign) || "";
    const push = (a: Omit<Attn, "wall">) => { out.push({ ...a, wall: wallMs }); };
    for (const e of f.ev || []) {
      const key = `${e.seq}`;
      if (this.seenEv.has(key)) continue;
      this.seenEv.add(key);
      const t = e.ts ?? ts;
      if ((e.type === "down" || e.type === "kia") && e.target) {
        const u = unit.get(e.target);
        if (!u || u.side !== "own") continue;
        const sid = sqOf(e.target);
        push({ id: `${e.type}:${e.target}`, kind: e.type, icon: e.type === "kia" ? "✝" : "🩸", ts: t, squad: sid ?? undefined,
          text: `${call(sid)} ${u.name} ${e.type === "kia" ? "陣亡" : "倒地"}`.trim(), at: [u.pos[0], u.pos[1]] });
      } else if (e.type === "spot" && e.target) {
        // 偵測是整個陣營一起算的（事件沒有發現者）：算在離被發現的人最近的我方小隊
        const d = (e.data || {}) as { at?: number[] };
        const tg = unit.get(e.target);
        const at: XY | null = Array.isArray(d.at) ? [d.at[0], d.at[1]] : tg ? [tg.pos[0], tg.pos[1]] : null;
        if (!at) continue;
        let o: (typeof f.units)[number] | null = null, best = Infinity;
        for (const u of f.units) {
          if (u.side !== "own" || u.dead) continue;
          const dd = Math.hypot(u.pos[0] - at[0], u.pos[1] - at[1]);
          if (dd < best) { best = dd; o = u; }
        }
        if (!o) continue;
        const sid = sqOf(o.id);
        if (!sid) continue;
        const last = this.lastSpot.get(sid);
        this.lastSpot.set(sid, t);
        if (last !== undefined && t - last < CONTACT_QUIET_S) continue;
        const dx = at[0] - o.pos[0], dy = at[1] - o.pos[1];
        const m = Math.round(Math.hypot(dx, dy) * 2);
        push({ id: `contact:${sid}:${Math.round(t)}`, kind: "contact", icon: "⚔", ts: t, squad: sid, at,
          text: `${call(sid)} 接敵（${compass(dx, dy)} ${m} m）` });
      }
    }
    for (const [sid, it] of Object.entries(intents) as [string, SquadIntent][]) {
      const st = it.order?.status;
      const prev = this.orderState.get(sid);
      if (st) this.orderState.set(sid, st);
      if (st === "interrupted" && prev !== undefined && prev !== "interrupted") {
        const mem = f.units.filter((u) => (u.group || u.squad) === sid);
        const at: XY | null = mem.length ? [mem.reduce((a, u) => a + u.pos[0], 0) / mem.length, mem.reduce((a, u) => a + u.pos[1], 0) / mem.length] : null;
        push({ id: `order:${sid}:${it.order!.since}`, kind: "interrupted", icon: "⚠", ts, squad: sid, at,
          text: `${it.callsign} 命令中斷${it.order!.reason ? "：" + it.order!.reason : ""}` });
      }
    }
    for (const s of supports) {
      const key = s.id ?? `${s.kind}:${s.x},${s.y}`;
      const prev = this.supportState.get(key);
      this.supportState.set(key, s.state);
      if (prev === "inbound" && (s.state === "active" || s.state === "landed")) {
        const what = s.kind === "airdrop" ? "空投落地" : s.kind === "drone" ? "無人機開始偵察" : "煙幕生效";
        push({ id: `support:${key}`, kind: "support", icon: s.kind === "airdrop" ? "📦" : s.kind === "drone" ? "🛩" : "☁", ts, at: [s.x, s.y], text: what });
      }
    }
    for (const a of out) if (!this.items.some((x) => x.id === a.id)) this.items.push(a);
    return out;
  }

  /** 還要顯示的卡片（最新的在前，最多 MAX_CARDS 張） */
  cards(wallMs: number): Attn[] {
    this.items = this.items.filter((a) => wallMs - a.wall < CARD_MS);
    return [...this.items].reverse().slice(0, MAX_CARDS);
  }

  dismiss(id: string) { this.items = this.items.filter((a) => a.id !== id); }
}
