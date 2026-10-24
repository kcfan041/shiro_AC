// S4：伺服器的呈現資料接進軌跡（逐格面向、瞄準、換姿勢時間），小隊意圖層的標籤與命令狀態、包抄弧線。
import { describe, expect, it } from "vitest";
import { flankControl, intentLabel, orderLabel, ORDER_KEEP_TICKS } from "../src/intentplan";
import type { Frame, FrameUnit, XY } from "../src/protocol";
import { TURN_S, Tracks } from "../src/tracks";

const base = (over: Partial<FrameUnit>): FrameUnit => ({ id: "a", side: "own", name: "a", pos: [0, 0], posture: "stand", facing: [1, 0], down: false, ...over });
const frame = (t: number, units: FrameUnit[], ev: Frame["ev"] = []): Frame => ({ t, ts0: t * 0.5, ts1: t * 0.5 + 0.5, units, shots: [], blasts: [], smokes: [], ev });
const deg = (r: number) => (r * 180) / Math.PI;

describe("伺服器給的時間", () => {
  it("逐格面向（pf）：走每一格時照伺服器的面向，在起步那一刻開始轉（邊走邊面向威脅）", () => {
    const tr = new Tracks();
    tr.ingest(frame(0, [base({ pos: [0, 0] })]));
    // 往東走一格（0.6 起步、1.2 抵達），但面向北（[0,1]）
    tr.ingest(frame(1, [base({ from: [0, 0], pos: [1, 0], path: [[1, 0]], pst: [0.6], pt: [0.95], pf: [[0, 1]], facing: [0, 1] })]));
    const s = (t: number) => tr.sample(t)[0];
    expect(deg(s(0.59).facing)).toBeCloseTo(0, 3);
    expect(deg(s(0.6 + TURN_S).facing)).toBeCloseTo(90, 3);
  });

  it("瞄準事件：開始時轉向目標、進度從 0 到 1（到開火），開火後 0.1 秒結束", () => {
    const tr = new Tracks();
    tr.ingest(frame(0, [base({ pos: [5, 5] })]));
    tr.ingest(frame(1, [base({ pos: [5, 5], facing: [0, -1] })], [{ seq: 1, t: 1, ts: 0.6, type: "aim", actor: "a", data: { fire: 1.0, at: [5, 0] } }]));
    const s = (t: number) => tr.sample(t)[0];
    expect(s(0.55).aim).toBeNull();
    expect(s(0.8).aim).toBeCloseTo(0.5, 6);
    expect(s(1.0).aim).toBe(1);
    expect(s(1.2).aim).toBeNull();
    expect(deg(s(0.6 + TURN_S).facing)).toBeCloseTo(-90, 3);   // 轉向南邊的目標
  });

  it("換姿勢照伺服器事件的時間（不是影格開始）", () => {
    const tr = new Tracks();
    tr.ingest(frame(0, [base({ posture: "stand" })]));
    tr.ingest(frame(1, [base({ posture: "prone" })], [{ seq: 1, t: 1, ts: 0.8, type: "posture", actor: "a", data: { frm: "stand", to: "prone" } }]));
    const s = (t: number) => tr.sample(t)[0];
    expect(s(0.79).postureK === 1 ? s(0.79).posture : s(0.79).postureFrom).toBe("stand");
    expect(s(0.8 + 0.125).postureK).toBeCloseTo(0.5, 6);
  });

  it("沒有新欄位的舊影格照 S1 的估法（不會壞）", () => {
    const tr = new Tracks();
    tr.ingest(frame(0, [base({})]));
    tr.ingest(frame(1, [base({ facing: [0, 1] })]));
    expect(deg(tr.sample(0.5 + TURN_S)[0].facing)).toBeCloseTo(90, 3);
  });
});

describe("小隊意圖層", () => {
  it("戰術標籤：圖示＋文字，包抄帶方向", () => {
    expect(intentLabel({ kind: "supp_advance", side: null })).toBe("⇈ 壓制推進");
    expect(intentLabel({ kind: "flank", side: "right" })).toBe("↪ 包抄右翼");
    expect(intentLabel({ kind: "flank", side: null })).toBe("↪ 包抄左翼");
    expect(intentLabel({ kind: "weird", side: null })).toBe("· weird");
  });

  it("命令狀態：進行中一直顯示、結束的顯示幾個 tick 後收起；文字太長截斷", () => {
    expect(orderLabel({ status: "thinking", text: "推進到 C3", since: 1 }, 99)).toEqual({ text: "隊長思考中…：推進到 C3", tone: "info" });
    const ex = orderLabel({ status: "executing", understanding: "前往 C3 中央十字路口，沿南大街西側建築推進", since: 1 }, 50)!;
    expect(ex.tone).toBe("ok");
    expect(ex.text.endsWith("…")).toBe(true);
    expect(orderLabel({ status: "interrupted", reason: "遇敵，改固守", since: 10 }, 10 + ORDER_KEEP_TICKS)).toEqual({ text: "中斷：遇敵，改固守", tone: "warn" });
    expect(orderLabel({ status: "done", reason: "抵達", since: 10 }, 11 + ORDER_KEEP_TICKS)).toBeNull();
    expect(orderLabel({ status: "refused", label: "拒絕", since: 5 }, 5)?.tone).toBe("bad");
    expect(orderLabel(null, 5)).toBeNull();
  });

  it("包抄弧線往指定的一側彎（畫面 y 向下：往東走時右邊是南）", () => {
    const a: XY = [0, 0], b: XY = [10, 0];
    expect(flankControl(a, b, "right")[1]).toBeGreaterThan(0);
    expect(flankControl(a, b, "left")[1]).toBeLessThan(0);
    expect(flankControl(a, b, "left")[0]).toBeCloseTo(5, 6);
  });
});
