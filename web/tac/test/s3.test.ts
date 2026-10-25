// S3：特效與音效的呈現規則、聲音的空間化與發聲上限、軌跡帶彈著材質與聽到的槍聲。
import { describe, expect, it } from "vitest";
import { gunFamily, MAX_VOICES, MERGE_S, soundKey, soundKeys, spatial, VoiceLimiter } from "../src/audio";
import { impactStyle, mechAfter, ROUND_GAP, roundOffset, roundTimes, SUPP_JITTER, shakeFor, TracerThinner, tracerStyle } from "../src/fxplan";
import { Tracks } from "../src/tracks";

describe("彈著與曳光", () => {
  it("彈著依材質：牆有塵土與碎屑、地面是土色、人只有紅點、沒打到東西什麼都沒有", () => {
    const wall = impactStyle("wall"), ground = impactStyle("ground"), body = impactStyle("body"), air = impactStyle("air");
    expect(wall.dust).toBeGreaterThan(0);
    expect(wall.chips).toBeGreaterThan(0);
    expect(ground.dustColor).not.toBe(wall.dustColor);
    expect(body.dust).toBe(0);
    expect(body.sparkColor).toBe(0xff4a3a);
    expect(air.dust + air.chips + air.sparks).toBe(0);
  });

  it("舊影格沒有材質：命中當人、其他當不明（不假裝打在牆上）", () => {
    expect(impactStyle(undefined, true)).toEqual(impactStyle("body"));
    expect(impactStyle(undefined, false)).toEqual(impactStyle("unk"));
    expect(impactStyle("unk").sparks).toBe(0);
  });

  it("連發的曳光每 2 發畫 1 條；隔一段時間是新的一串；看不到的射手每發都畫", () => {
    const th = new TracerThinner();
    const take = (t: number) => th.take("a", t);
    expect([take(0), take(0.1), take(0.2), take(0.3)]).toEqual([true, false, true, false]);
    expect(take(2)).toBe(true);                 // 新的一串從頭算
    expect([th.take(null, 0), th.take(null, 0.05)]).toEqual([true, true]);
  });

  it("曳光顏色：警戒青色、看不到的射手偏紅、壓制較淡較細", () => {
    expect(tracerStyle({ ow: true }).color).toBe(0x5adce8);
    expect(tracerStyle({ unseen: true }).color).toBe(0xff7a64);
    const sup = tracerStyle({ mode: "suppress" }), aim = tracerStyle({ mode: "aimed" });
    expect(sup.alpha).toBeLessThan(aim.alpha);
    expect(sup.width).toBeLessThan(aim.width);
  });

  it("畫面震動只在我方附近的爆炸，越近越大，閃光彈較小", () => {
    expect(shakeFor(3, false, false)).toBe(0);
    expect(shakeFor(12, true, false)).toBe(0);
    expect(shakeFor(2, true, false)).toBeGreaterThan(shakeFor(8, true, false));
    expect(shakeFor(2, true, true)).toBeLessThan(shakeFor(2, true, false));
  });
});

describe("聲音", () => {
  const L = { x: 50, y: 50, halfWidth: 40 };
  it("越遠越小聲、越悶、越晚到（2 m 一格、聲速 343 m/s）", () => {
    const near = spatial({ kind: "gun", x: 52, y: 50 }, L), far = spatial({ kind: "gun", x: 110, y: 50 }, L);
    expect(far.gain).toBeLessThan(near.gain / 3);
    expect(far.cutoff).toBeLessThan(near.cutoff);
    expect(far.delay).toBeCloseTo(Math.min(0.6, (60 * 2) / 343), 6);
    expect(near.delay).toBeCloseTo((2 * 2) / 343, 6);
  });

  it("左右聲道依方向；無線電與警示不空間化", () => {
    expect(spatial({ kind: "gun", x: 30, y: 50 }, L).pan).toBeLessThan(0);
    expect(spatial({ kind: "gun", x: 70, y: 50 }, L).pan).toBeGreaterThan(0);
    expect(spatial({ kind: "gun", x: 500, y: 50 }, L).pan).toBeLessThanOrEqual(1);
    const r = spatial({ kind: "radio" }, L);
    expect([r.pan, r.delay]).toEqual([0, 0]);
  });

  it("同時最多 12 個聲音；同一射手 0.05 秒內只響一次（比機槍一發短）；無線電與警示不受限", () => {
    const v = new VoiceLimiter();
    for (let i = 0; i < MAX_VOICES; i++) expect(v.admit({ kind: "gun", shooter: `s${i}` }, 0)).toBe(true);
    expect(v.admit({ kind: "gun", shooter: "x" }, 0)).toBe(false);
    expect(v.dropped).toBe(1);
    expect(v.admit({ kind: "alert" }, 0)).toBe(true);
    v.release();
    expect(v.admit({ kind: "gun", shooter: "s0" }, 0.03)).toBe(false);   // 同一射手太近：合併
    expect(MERGE_S).toBeLessThan(ROUND_GAP.lmg);
    expect(v.merged).toBe(1);
    expect(v.admit({ kind: "gun", shooter: "s0" }, 0.2)).toBe(true);
  });

  it("彈著聲只用一半的聲音數，讓給槍聲", () => {
    const v = new VoiceLimiter();
    for (let i = 0; i < MAX_VOICES / 2; i++) expect(v.admit({ kind: "impact" }, 0)).toBe(true);
    expect(v.admit({ kind: "impact" }, 0)).toBe(false);
    expect(v.admit({ kind: "gun", shooter: "a" }, 0)).toBe(true);
  });

  it("槍聲依武器；不認得的武器用步槍；彈著依材質", () => {
    expect(soundKey({ kind: "gun", weapon: "lmg" })).toBe("gun:lmg");
    expect(soundKey({ kind: "gun", weapon: "railgun" })).toBe("gun:rifle");
    expect(soundKey({ kind: "gun" })).toBe("gun:rifle");
    expect(soundKey({ kind: "impact", mat: "dirt" })).toBe("impact:dirt");
  });
});

describe("軌跡帶的特效資料", () => {
  it("子彈帶材質與射手的武器；聽到的槍聲帶誤差位置", () => {
    const tr = new Tracks();
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, smokes: [], blasts: [],
      units: [{ id: "s1", side: "own", name: "s1", pos: [0, 0], posture: "stand", facing: [1, 0], down: false, weapon: "dmr" }],
      shots: [{ shooter: "s1", from: [0, 0], to: [9, 0], ts: 0.2, mat: "low" }, { shooter: null, from: [20, 5], to: [3, 1], ts: 0.3, unseen: true }],
      ev: [{ seq: 1, t: 0, ts: 0.3, type: "hear", data: { at: [24, 6], err: 4 } }] });
    const fx = tr.fxBetween(0, 0.5);
    const s1 = fx.find((x) => x.kind === "shot" && x.shooter === "s1");
    expect(s1 && s1.kind === "shot" && [s1.mat, s1.weapon]).toEqual(["low", "dmr"]);
    const un = fx.find((x) => x.kind === "shot" && x.unseen);
    expect(un && un.kind === "shot" && [un.weapon, un.side]).toEqual([null, "enemy"]);   // 舊影格沒有槍種：不知道武器，陣營當敵人
    const h = fx.find((x) => x.kind === "hear");
    expect(h && h.kind === "hear" && [h.at, h.err]).toEqual([[24, 6], 4]);
  });
});

describe("同一次射擊的每一發依射速錯開", () => {
  const unit = (id: string, weapon: string) => ({ id, side: "own", name: id, pos: [0, 0] as [number, number], posture: "stand", facing: [1, 0] as [number, number], down: false, weapon });
  const shot = (shooter: string | null, ts: number, r?: number, aid = "x") => ({ shooter, from: [0, 0] as [number, number], to: [9, 0] as [number, number], ts, aid, ...(r ? { r } : {}) });
  const times = (tr: Tracks, who: string | null) => tr.fxBetween(0, 5).filter((x) => x.kind === "shot" && x.shooter === who).map((x) => +x.ts.toFixed(3));

  it("壓制四發：伺服器同一個時間 → 畫面一發一發出去（步槍 0.17 秒、機槍 0.075 秒）", () => {
    const tr = new Tracks();
    tr.ingest({ t: 0, ts0: 0, ts1: 1, smokes: [], blasts: [], ev: [], units: [unit("a", "rifle"), unit("m", "lmg")],
      shots: [shot("a", 0.4), shot("a", 0.4, 1), shot("a", 0.4, 2), shot("a", 0.4, 3), shot("m", 0.4), shot("m", 0.4, 1, "y")] });
    expect(times(tr, "a")).toEqual([0.4, 0.57, 0.74, 0.91]);
    expect(times(tr, "m")).toEqual([0.4, 0.475]);
  });

  it("霰彈槍同一發的彈丸同號 → 同時；第二發才往後", () => {
    const pel = (r: number) => Array.from({ length: 8 }, () => shot("g", 1, r));
    expect(roundTimes([...pel(0), ...pel(1)], () => "shotgun", () => 0)).toEqual([...Array(8).fill(1), ...Array(8).fill(1 + ROUND_GAP.shotgun)]);
  });

  it("舊影格沒有序號：同一射手、時間、aid 依順序當第幾發，霰彈每 8 顆算一發；不同射手不互相影響", () => {
    const old = [shot("a", 1), shot("a", 1), shot("b", 1), shot("a", 1)];
    expect(roundTimes(old, () => "rifle", () => 0).map((x) => +x.toFixed(3))).toEqual([1, 1.17, 1, 1.34]);
    const sg = Array.from({ length: 16 }, () => shot("g", 2));
    const t = roundTimes(sg, () => "shotgun", () => 0);
    expect(new Set(t.slice(0, 8)).size).toBe(1);
    expect(t[8] - t[0]).toBeCloseTo(ROUND_GAP.shotgun, 6);
  });

  it("看不到的射手（不知道武器）照步槍錯開；沒有時間的子彈照原本平均分散", () => {
    expect(roundTimes([shot(null, 1), shot(null, 1, 1)], () => null, () => 0)).toEqual([1, 1 + ROUND_GAP.rifle]);
    expect(roundTimes([{ shooter: "a" }], () => "rifle", (i) => 7 + i)).toEqual([7]);
  });

  it("連發時槍舉到最後一發之後才放下", () => {
    const tr = new Tracks();
    tr.ingest({ t: 0, ts0: 0, ts1: 1, smokes: [], blasts: [], units: [unit("a", "rifle")],
      ev: [{ seq: 1, t: 0, ts: 0.1, type: "aim", actor: "a", data: { fire: 0.4, at: [9, 0] } }],
      shots: [shot("a", 0.4), shot("a", 0.4, 1), shot("a", 0.4, 2), shot("a", 0.4, 3)] });
    const aim = (t: number) => tr.sample(t).find((u) => u.id === "a")!.aim;
    expect(aim(0.8)).toBe(1);                   // 第 4 發在 0.91
    expect(aim(0.99)).toBe(1);
    expect(aim(1.05)).toBeNull();
  });
});

describe("分得出槍種與陣營", () => {
  it("槍聲先找陣營那套錄音：敵人一套，我方、友軍、殘部一套；沒有錄音就用合成的", () => {
    expect(soundKeys({ kind: "gun", weapon: "lmg", side: "enemy" })).toEqual(["gun:enemy:lmg", "gun:lmg"]);
    expect(soundKeys({ kind: "gun", weapon: "dmr", side: "ally" })).toEqual(["gun:own:dmr", "gun:dmr"]);
    expect(soundKeys({ kind: "gun", weapon: "railgun", side: "own" })).toEqual(["gun:own:rifle", "gun:rifle"]);
    expect([gunFamily("own"), gunFamily("remnant"), gunFamily(null), gunFamily("enemy")]).toEqual(["own", "own", "own", "enemy"]);
    expect(soundKeys({ kind: "mech", mech: "pump" })).toEqual(["mech:pump"]);
  });

  it("節奏：機槍最快、射手步槍最慢、霰彈槍中間留得下泵動", () => {
    expect(ROUND_GAP.lmg).toBeLessThan(ROUND_GAP.rifle);
    expect(ROUND_GAP.rifle).toBeLessThan(ROUND_GAP.dmr);
    const pump = mechAfter("shotgun")!;
    expect(pump.mech).toBe("pump");
    expect(pump.after + 0.3).toBeLessThan(ROUND_GAP.shotgun);
    expect(mechAfter("dmr")!.mech).toBe("casing");
    expect([mechAfter("rifle"), mechAfter("lmg"), mechAfter(null)]).toEqual([null, null, null]);
  });

  it("機械聲比槍聲衰減得快：10 格外不到槍聲衰減的一半", () => {
    const L = { x: 0, y: 0, halfWidth: 30 };
    const k = (kind: "gun" | "mech") => spatial({ kind, x: 10, y: 0 }, L).gain / spatial({ kind, x: 0, y: 0 }, L).gain;
    expect(k("mech")).toBeLessThan(k("gun") / 2);
  });

  it("看不到的射手：沒有 ID，但帶伺服器給的槍種，陣營當敵人；舊影格沒有槍種就不知道", () => {
    const tr = new Tracks();
    tr.ingest({ t: 0, ts0: 0, ts1: 0.5, smokes: [], blasts: [], ev: [], units: [],
      shots: [{ shooter: null, from: [20, 5], to: [3, 1], ts: 0.2, unseen: true, wpn: "lmg" }, { shooter: null, from: [20, 5], to: [3, 1], ts: 0.3, unseen: true }] });
    const s = tr.fxBetween(0, 1).filter((x) => x.kind === "shot");
    expect(s.map((x) => x.kind === "shot" && [x.weapon, x.side])).toEqual([["lmg", "enemy"], [null, "enemy"]]);
  });
});

describe("壓制射擊的間隔隨機", () => {
  it("壓制：每個間隔在 ±SUPP_JITTER 內、一定往後；同一發每次算都一樣，不同次射擊不一樣", () => {
    for (const w of ["rifle", "lmg", "dmr", "shotgun"]) {
      const t = [0, 1, 2, 3, 4, 5].map((r) => roundOffset(r, w, "suppress", "a12.3"));
      for (let r = 1; r < t.length; r++) {
        const gap = t[r] - t[r - 1];
        expect(gap).toBeGreaterThanOrEqual(ROUND_GAP[w] * (1 - SUPP_JITTER[w]) - 1e-9);
        expect(gap).toBeLessThanOrEqual(ROUND_GAP[w] * (1 + SUPP_JITTER[w]) + 1e-9);
      }
      expect(roundOffset(3, w, "suppress", "a12.3")).toBe(t[3]);
    }
    const a = [1, 2, 3].map((r) => roundOffset(r, "rifle", "suppress", "x1"));
    const b = [1, 2, 3].map((r) => roundOffset(r, "rifle", "suppress", "x2"));
    expect(a).not.toEqual(b);
    const gaps = [1, 2, 3, 4].map((r) => roundOffset(r, "rifle", "suppress", "x1") - roundOffset(r - 1, "rifle", "suppress", "x1"));
    expect(new Set(gaps.map((g) => g.toFixed(4))).size).toBeGreaterThan(1);    // 不是等距
  });

  it("點放與瞄準射擊照射速等距；機槍壓制最快的一發也不會被合併掉", () => {
    expect([1, 2, 3].map((r) => +roundOffset(r, "rifle", "burst", "x").toFixed(3))).toEqual([0.17, 0.34, 0.51]);
    expect(ROUND_GAP.lmg * (1 - SUPP_JITTER.lmg)).toBeGreaterThan(MERGE_S);
  });
});
