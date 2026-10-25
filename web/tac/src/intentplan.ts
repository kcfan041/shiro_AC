// 小隊意圖層的呈現規則（不依賴 Pixi，測試可以直接用）：戰術的圖示與文字、命令狀態標籤、包抄弧線。

import type { OrderState, SquadIntent, XY } from "./protocol";

export const KIND: Record<string, [string, string]> = {
  hold: ["■", "固守"], advance: ["↑", "推進"], supp_advance: ["⇈", "壓制推進"], flank: ["↪", "包抄"],
  ambush: ["◎", "伏擊"], assault: ["⇑", "突擊"], retreat: ["↩", "撤退"], breach: ["▣", "破門"], march: ["→", "行軍"],
  fortify: ["⛏", "構工"],
};

export function intentLabel(it: Pick<SquadIntent, "kind" | "side">): string {
  const [icon, text] = KIND[it.kind] || ["·", it.kind];
  const side = it.kind === "flank" ? (it.side === "right" ? "右翼" : "左翼") : "";
  return `${icon} ${text}${side}`;
}

export type Tone = "muted" | "info" | "ok" | "warn" | "bad";

const ORDER: Record<string, [string, Tone]> = {
  sent: ["送出", "muted"], thinking: ["隊長思考中…", "info"], executing: ["執行中", "ok"], done: ["完成", "ok"],
  interrupted: ["中斷", "warn"], refused: ["拒絕", "bad"], unclear: ["無法對應", "bad"], failed: ["無法執行", "bad"],
  kept: ["維持原行動", "warn"], confirm: ["要求確認", "warn"], info: ["回應", "muted"],
};

/** 命令標籤顯示多久：送出、思考中、執行中一直顯示；結束的狀態顯示 ORDER_KEEP_TICKS 個作戰 tick（約 10 秒） */
export const ORDER_KEEP_TICKS = 2;

/** 命令狀態 → 標籤文字與色調；不該顯示（太舊、沒有命令）→ null。worldTick＝目前的作戰 tick */
export function orderLabel(o: OrderState | null | undefined, worldTick: number): { text: string; tone: Tone } | null {
  if (!o) return null;
  const [name, tone] = ORDER[o.status] || [o.status, "muted"];
  const ongoing = o.status === "sent" || o.status === "thinking" || o.status === "executing";
  if (!ongoing && worldTick - o.since > ORDER_KEEP_TICKS) return null;
  const what = o.status === "executing" ? o.understanding || o.action || o.text
    : o.status === "sent" || o.status === "thinking" ? o.text : o.reason || o.label;
  const short = what ? `：${what.length > 18 ? what.slice(0, 17) + "…" : what}` : "";
  return { text: `${name}${short}`, tone };
}

/** 包抄的弧線：從 a 到 b 的二次曲線控制點，往左或右（畫面方向，y 向下）偏 0.35 倍距離 */
export function flankControl(a: XY, b: XY, side: string | null): XY {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const k = 0.35 * (side === "right" ? 1 : -1);
  // 畫面 y 向下：往前方向 (dx, dy) 的「右手邊」是 (-dy, dx)
  return [(a[0] + b[0]) / 2 - dy * k, (a[1] + b[1]) / 2 + dx * k];
}
