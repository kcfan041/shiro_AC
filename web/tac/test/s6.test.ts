// S6：戰後摘要與時間軸標記。
import { describe, expect, it } from "vitest";
import { clockText, headline, summarize } from "../src/aar";
import type { Frame, FrameUnit, SquadIntent } from "../src/protocol";

const u = (id: string, side: string, name = id): FrameUnit => ({ id, side, name, group: side === "own" ? "a" : null, pos: [5, 5], posture: "stand", facing: [1, 0], down: false });
const intent = (kind: string): Record<string, SquadIntent> =>
  ({ a: { callsign: "Alpha", kind, goal: null, side: null, focus: null, room: null, toward: null, movers: [], cover: [] } });

describe("戰後摘要", () => {
  const frames: Frame[] = [
    { t: 1, ts0: 100, ts1: 100.5, units: [u("k", "own", "K-07"), u("r", "own", "R-12"), u("e1", "enemy", "敵兵")], intents: intent("hold"),
      shots: [{ shooter: "e1", from: [0, 0], to: [5, 5], mat: "body" }, { shooter: "k", from: [5, 5], to: [9, 9], mat: "wall" }], blasts: [], smokes: [],
      ev: [{ seq: 1, t: 1, ts: 100.2, type: "down", actor: "e1", target: "r" }, { seq: 2, t: 1, ts: 100.3, type: "down", actor: null, target: "k" }] },
    { t: 2, ts0: 130, ts1: 130.5, units: [u("k", "own", "K-07"), u("r", "own", "R-12")], intents: intent("supp_advance"), shots: [], blasts: [], smokes: [],
      ev: [{ seq: 3, t: 2, ts: 130.1, type: "aid", actor: "k", target: "r", data: { state: "done" } },
        { seq: 4, t: 2, ts: 130.2, type: "drag", actor: "k", target: "r" },
        { seq: 5, t: 2, ts: 130.3, type: "support", data: { what: "airdrop", state: "landed", at: [3, 4] } },
        { seq: 6, t: 2, ts: 130.4, type: "blast", actor: "e1", data: { item: "grenades", at: [6, 6] } },
        { seq: 1, t: 2, ts: 100.2, type: "down", actor: "e1", target: "r" }] },              // 重複的事件（seq 一樣）只算一次
  ];
  const a = summarize(frames);

  it("誰被誰打倒、看不到的射手、誰救了誰、支援、戰術改變，依時間排序", () => {
    expect(a.items.map((x) => x.text)).toEqual([
      "R-12 倒地（敵兵打中）",
      "K-07 倒地（看不到的射手打中）",
      "Alpha：固守 → 壓制推進",
      "K-07 替 R-12 止血",
      "K-07 把 R-12 拖往掩護",
      "精準空投落地",
      "手榴彈爆炸",
    ]);
    expect(a.items.find((x) => x.kind === "support")!.at).toEqual([3, 4]);
  });

  it("統計：開火、命中、倒地、手榴彈、支援；開始與結束時間", () => {
    expect(a.stats).toEqual({ shots: 2, hits: 1, downs: 2, kias: 0, grenades: 1, supports: 1 });
    expect([a.t0, a.t1]).toEqual([100, 130.5]);
  });

  it("摘要卡不列爆炸；太多時先留陣亡、倒地，再照時間排", () => {
    expect(headline(a).some((x) => x.kind === "blast")).toBe(false);
    const many = { ...a, items: [...Array.from({ length: 20 }, (_, i) => ({ ts: i, kind: "intent" as const, icon: "", text: `i${i}`, at: null })),
      { ts: 50, kind: "kia" as const, icon: "", text: "kia", at: null }] };
    const h = headline(many, 5);
    expect(h).toHaveLength(5);
    expect(h.map((x) => x.text)).toContain("kia");
    expect(h.map((x) => x.ts)).toEqual([...h.map((x) => x.ts)].sort((x, y) => x - y));
  });

  it("時間標記 m:ss 從這場開始算", () => {
    expect(clockText(100, 100)).toBe("0:00");
    expect(clockText(175.9, 100)).toBe("1:15");
  });
});
