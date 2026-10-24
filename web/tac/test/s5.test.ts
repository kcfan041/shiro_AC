// S5：注意力佇列（接敵、倒地、命令中斷、支援到達）與多區域影格合併。
import { describe, expect, it } from "vitest";
import { Attention, CARD_MS, compass, CONTACT_QUIET_S, MAX_CARDS } from "../src/attention";
import { gmSide, mergeFrames } from "../src/merge";
import type { Frame, FrameUnit, SquadIntent } from "../src/protocol";

const own = (id: string, x: number, y: number, over: Partial<FrameUnit> = {}): FrameUnit =>
  ({ id, side: "own", name: id, group: "a", pos: [x, y], posture: "stand", facing: [1, 0], down: false, ...over });
const intent = (order?: SquadIntent["order"]): Record<string, SquadIntent> =>
  ({ a: { callsign: "Alpha", kind: "advance", goal: null, side: null, focus: null, room: null, toward: null, movers: [], cover: [], order } });
const frame = (ts0: number, units: FrameUnit[], ev: Frame["ev"] = [], intents = intent()): Frame =>
  ({ t: ts0 * 2, ts0, ts1: ts0 + 0.5, units, shots: [], blasts: [], smokes: [], ev, intents });

describe("注意力佇列", () => {
  it("方位：東、北、西南", () => {
    expect(compass(1, 0)).toBe("東");
    expect(compass(0, 1)).toBe("北");
    expect(compass(-1, -1)).toBe("西南");
  });

  it("接敵：算在離被發現的人最近的我方小隊；安靜 20 秒內再發現不重複提示", () => {
    const a = new Attention();
    const spot = (seq: number, ts: number) => ({ seq, t: 0, ts, type: "spot", target: `e${seq}`, data: { at: [20, 10] } });
    const got = a.feed(frame(0, [own("k", 10, 10)], [spot(1, 0.2)]), 0);
    expect(got.map((x) => x.text)).toEqual(["Alpha 接敵（東 20 m）"]);
    expect(got[0].at).toEqual([20, 10]);
    expect(a.feed(frame(5, [own("k", 10, 10)], [spot(2, 5)]), 100)).toHaveLength(0);
    expect(a.feed(frame(5 + CONTACT_QUIET_S + 1, [own("k", 10, 10)], [spot(3, 5 + CONTACT_QUIET_S + 1)]), 200)).toHaveLength(1);
  });

  it("我方倒地、陣亡要提示；敵人倒地不提示；同一個事件不重複", () => {
    const a = new Attention();
    const units = [own("k", 1, 1), { ...own("e1", 9, 9), side: "enemy", group: null }];
    const f = frame(0, units, [{ seq: 1, t: 0, ts: 0.3, type: "down", target: "k" }, { seq: 2, t: 0, ts: 0.3, type: "down", target: "e1" }]);
    expect(a.feed(f, 0).map((x) => [x.kind, x.text])).toEqual([["down", "Alpha k 倒地"]]);
    expect(a.feed(f, 10)).toHaveLength(0);                     // 同一格影格
    expect(a.feed({ ...f }, 20)).toHaveLength(0);              // 同樣的事件在另一格（重送）
  });

  it("命令從執行中變成中斷：提示並附原因", () => {
    const a = new Attention();
    a.feed(frame(0, [own("k", 1, 1)], [], intent({ status: "executing", since: 1 })), 0);
    const got = a.feed(frame(0.5, [own("k", 1, 1)], [], intent({ status: "interrupted", reason: "遇敵，改固守", since: 2 })), 10);
    expect(got.map((x) => x.text)).toEqual(["Alpha 命令中斷：遇敵，改固守"]);
  });

  it("支援從預告變成落地、作用中：提示", () => {
    const a = new Attention();
    a.feed(frame(0, []), 0, [{ id: "s1", kind: "airdrop", state: "inbound", x: 3, y: 4 }]);
    const got = a.feed(frame(0.5, []), 10, [{ id: "s1", kind: "airdrop", state: "landed", x: 3, y: 4 }]);
    expect(got.map((x) => x.text)).toEqual(["空投落地"]);
  });

  it("卡片 6 秒後收起、最多 4 張、可以手動關掉", () => {
    const a = new Attention();
    const evs = Array.from({ length: 6 }, (_, i) => ({ seq: i + 1, t: 0, ts: 0.1, type: "down", target: `k${i}` }));
    a.feed(frame(0, evs.map((e) => own(e.target!, 1, 1)), evs), 0);
    expect(a.cards(100)).toHaveLength(MAX_CARDS);
    a.dismiss(a.cards(100)[0].id);
    expect(a.items).toHaveLength(5);
    expect(a.cards(CARD_MS + 1)).toHaveLength(0);
  });
});

describe("多區域影格合併", () => {
  it("個體不重複、跨區的子彈與事件去重、視野與意圖合併、事件依 seq 排序", () => {
    const shot = { shooter: "k", from: [1, 1] as [number, number], to: [30, 1] as [number, number], ts: 0.2, aid: "a1" };
    const f1: Frame = { t: 1, ts0: 0, ts1: 0.5, units: [own("k", 1, 1)], shots: [shot], blasts: [], smokes: [],
      ev: [{ seq: 5, t: 1, type: "shot" }, { seq: 3, t: 1, type: "spot" }], vision: { k: [[0, 0]] }, intents: intent() };
    const f2: Frame = { t: 1, ts0: 0, ts1: 0.5, units: [own("m", 30, 1), own("k", 1, 1)], shots: [shot], blasts: [], smokes: [],
      ev: [{ seq: 5, t: 1, type: "shot" }], vision: { m: [[1, 1]] } };
    const m = mergeFrames([f1, f2])!;
    expect(m.units.map((u) => u.id)).toEqual(["k", "m"]);
    expect(m.shots).toHaveLength(1);
    expect(m.ev!.map((e) => e.seq)).toEqual([3, 5]);
    expect(Object.keys(m.vision!)).toEqual(["k", "m"]);
    expect(m.intents!.a.callsign).toBe("Alpha");
    expect(mergeFrames([])).toBeNull();
  });

  it("GM 視角：小隊當友軍、殘部當殘部、其他當敵人", () => {
    expect(gmSide({ ...own("a", 0, 0), kind: "s", side: "blue" }).side).toBe("ally");
    expect(gmSide({ ...own("b", 0, 0), kind: "g", side: "blue" }).side).toBe("remnant");
    expect(gmSide({ ...own("c", 0, 0), kind: "e", side: "red" }).side).toBe("enemy");
  });
});
