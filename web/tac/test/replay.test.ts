// 用真實回放檢查播放（有 web/tac/fixtures/replay.json 才跑，沒有就略過；先跑 python scripts/tac_fixture.py）：
// 照伺服器節奏、到達時間抖動 ±200 ms，從頭播到尾，畫面上每個人相鄰兩幀的位移不超過 0.5 格
// （伺服器資料本身超過 3 格的瞬移除外：重生、整批換位置），影格還在來的期間也不會卡住。
import { describe, expect, it } from "vitest";
import { BattleClock } from "../src/clock";
import { Feed } from "../src/feed";
import type { Fixture } from "../src/protocol";
import { SNAP_DIST, Tracks } from "../src/tracks";

const fsName = "node:fs";
const fs = (await import(/* @vite-ignore */ fsName)) as { existsSync(p: string): boolean; readFileSync(p: string, e: string): string };
const PATH = "fixtures/replay.json";
const fixture = fs.existsSync(PATH) ? (JSON.parse(fs.readFileSync(PATH, "utf8")) as Fixture) : null;

describe.skipIf(!fixture)("真實回放", () => {
  it.each([[0, 1], [0.2, 2]])("抖動 ±%s 秒：單幀位移 ≤ 0.5 格、不卡住", (jitter, seed) => {
    const frames = fixture!.frames;
    const feed = new Feed(frames, { jitter, seed });
    const clock = new BattleClock(), tr = new Tracks();
    const last = new Map<string, [number, number]>();
    let worst = 0, worstAt = "", lastJumps = 0, stallsWhileFeeding = 0;
    for (let i = 0; !feed.done || (clock.bt ?? 0) < (frames[frames.length - 1].ts1 ?? 0) - 0.01; i++) {
      const wall = i / 60;
      for (const { frame, at } of feed.take(wall)) { clock.push(frame.ts0 ?? 0, frame.ts1 ?? 0, at); tr.ingest(frame, clock.bt); }
      const bt = clock.tick(1 / 60);
      if (bt === null) continue;
      if (!feed.done) stallsWhileFeeding = clock.stalls;           // 播到最後沒有影格了會停，那不算
      const jumped = clock.jumps !== lastJumps;
      lastJumps = clock.jumps;
      const cur = tr.sample(bt);
      for (const id of [...last.keys()]) if (!cur.some((u) => u.id === id)) last.delete(id);
      for (const u of cur) {
        const p = last.get(u.id);
        if (p && !jumped) {
          const d = Math.hypot(u.x - p[0], u.y - p[1]);
          if (d <= SNAP_DIST && d > worst) { worst = d; worstAt = `${u.id} @ ${bt.toFixed(2)}`; }
        }
        last.set(u.id, [u.x, u.y]);
      }
      if (i > 60 * 3600) throw new Error("播不完");
    }
    expect(worst, worstAt).toBeLessThan(0.5);
    expect(stallsWhileFeeding).toBe(0);
  });
});
