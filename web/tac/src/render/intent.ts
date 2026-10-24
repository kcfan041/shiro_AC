// 小隊意圖層（方案 §7.5）：看的是小隊在做什麼，不是每個人在哪。
// 每個自己的小隊（友軍較淡）：小隊旁的戰術標籤；推進與行軍的目標虛線＋目標點、包抄的弧形箭頭、撤退方向箭頭、
// 集火準星、破門的目標房間；交互掩護時前進組腳下一圈、掩護組朝目標的淡扇形；命令狀態標籤（送出→思考中→執行中→完成／中斷）。
// 座標：畫面方向（y 向下），一格＝1；線寬與字級固定幾個 CSS px。

import { Container, Graphics, Text } from "pixi.js";
import { flankControl, intentLabel, orderLabel, type Tone } from "../intentplan";
import type { SquadIntent, WorldTerrain, XY } from "../protocol";
import type { UnitSample } from "../tracks";
import type { Theme } from "../theme";

interface Labels { title: Text; order: Text }

function dashed(g: Graphics, a: XY, b: XY, dash: number, gap: number) {
  const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (d < 1e-6) return;
  const ux = (b[0] - a[0]) / d, uy = (b[1] - a[1]) / d;
  for (let s = 0; s < d; s += dash + gap) {
    const e = Math.min(d, s + dash);
    g.moveTo(a[0] + ux * s, a[1] + uy * s).lineTo(a[0] + ux * e, a[1] + uy * e);
  }
}

function arrowHead(g: Graphics, tip: XY, dir: XY, size: number) {
  const d = Math.hypot(dir[0], dir[1]) || 1;
  const ux = dir[0] / d, uy = dir[1] / d;
  g.moveTo(tip[0], tip[1]).lineTo(tip[0] - ux * size - uy * size * 0.55, tip[1] - uy * size + ux * size * 0.55);
  g.moveTo(tip[0], tip[1]).lineTo(tip[0] - ux * size + uy * size * 0.55, tip[1] - uy * size - ux * size * 0.55);
}

export class IntentLayer {
  /** 地面上（個體下面）：路線、圈、扇形 */
  readonly ground = new Container();
  /** 標籤（最上面） */
  readonly labels = new Container();
  private g = new Graphics();
  private texts = new Map<string, Labels>();
  enabled = true;

  constructor(private th: Theme, private T: WorldTerrain) {
    this.ground.addChild(this.g);
  }

  private tone(t: Tone): number {
    return { muted: 0x9aa6b2, info: 0xdfe8f0, ok: this.th.side.own, warn: this.th.suppressed, bad: this.th.pinned }[t];
  }

  /**
   * intents：這一刻的小隊意圖；units：這一刻的個體（畫面方向座標）；sy：世界 y → 畫面 y；
   * worldTick：目前的作戰 tick（命令標籤多久收起）；ownIds：自己的小隊（其他當友軍，畫淡一點）
   */
  update(intents: Record<string, SquadIntent>, units: UnitSample[], sy: (y: number) => number, zoom: number, dpr: number, worldTick: number) {
    const g = this.g;
    g.clear();
    this.ground.visible = this.labels.visible = this.enabled;
    const used = new Set<string>();
    if (!this.enabled) { for (const l of this.texts.values()) { l.title.visible = l.order.visible = false; } return; }
    const px = 1 / zoom;
    for (const [sid, it] of Object.entries(intents)) {
      const mem = units.filter((u) => (u.u.group || u.u.squad) === sid && !u.dead && u.alpha > 0.3);
      if (!mem.length) continue;
      const live = mem.filter((u) => !u.down);
      const base = live.length ? live : mem;
      const c: XY = [base.reduce((a, u) => a + u.x, 0) / base.length, base.reduce((a, u) => a + u.y, 0) / base.length];
      const own = mem[0].side === "own";
      const col = own ? this.th.side.own : this.th.side.ally;
      const a = own ? 0.85 : 0.45;
      const P = (p: XY): XY => [p[0] + 0.5, sy(p[1] + 0.5)];
      const goal = it.goal ? P(it.goal) : null;
      const dir: XY | null = goal ? [goal[0] - c[0], goal[1] - c[1]] : it.focus ? [P(it.focus)[0] - c[0], P(it.focus)[1] - c[1]] : null;
      // 交互掩護：前進組腳下一圈、掩護組朝目標的淡扇形
      if (it.movers.length && it.cover.length) {
        for (const u of mem) {
          if (it.movers.includes(u.id)) g.circle(u.x, u.y, 0.95).stroke({ width: 2 * px, color: col, alpha: a * 0.8 });
          else if (it.cover.includes(u.id) && dir) {
            const ang = Math.atan2(dir[1], dir[0]), r = 4;
            g.moveTo(u.x, u.y).arc(u.x, u.y, r, ang - 0.45, ang + 0.45).lineTo(u.x, u.y).fill({ color: col, alpha: 0.1 });
          }
        }
      }
      // 目標與路線
      if (goal && (it.kind === "flank")) {
        const k = flankControl(c, goal, it.side);
        g.moveTo(c[0], c[1]).quadraticCurveTo(k[0], k[1], goal[0], goal[1]).stroke({ width: 2 * px, color: col, alpha: a });
        arrowHead(g, goal, [goal[0] - k[0], goal[1] - k[1]], 12 * px);
        g.stroke({ width: 2 * px, color: col, alpha: a });
      } else if (goal && it.kind !== "hold" && it.kind !== "ambush") {
        dashed(g, c, goal, 8 * px, 6 * px);
        g.stroke({ width: 2 * px, color: col, alpha: a });
        g.circle(goal[0], goal[1], 0.7).stroke({ width: 2 * px, color: col, alpha: a });
      }
      if (it.kind === "retreat" && it.toward) {
        const d = Math.hypot(it.toward[0], it.toward[1]) || 1;
        const tip: XY = [c[0] + (it.toward[0] / d) * 6, c[1] - (it.toward[1] / d) * 6];
        g.moveTo(c[0], c[1]).lineTo(tip[0], tip[1]);
        arrowHead(g, tip, [tip[0] - c[0], tip[1] - c[1]], 12 * px);
        g.stroke({ width: 3 * px, color: this.th.suppressed, alpha: a });
      }
      if (it.focus) {
        const f = P(it.focus);
        g.circle(f[0], f[1], 0.9).moveTo(f[0] - 1.4, f[1]).lineTo(f[0] + 1.4, f[1]).moveTo(f[0], f[1] - 1.4).lineTo(f[0], f[1] + 1.4)
          .stroke({ width: 2 * px, color: this.th.pinned, alpha: a });
      }
      if (it.kind === "breach" && it.room != null) {
        const room = this.T.rooms.find((r) => r.id === it.room);
        if (room) {
          const [x0, y0, x1, y1] = room.rect;
          g.rect(x0, sy(y1 + 1), x1 - x0 + 1, y1 - y0 + 1).stroke({ width: 2 * px, color: col, alpha: a });
        }
      }
      // 標籤：小隊名＋戰術，下面一行命令狀態
      let l = this.texts.get(sid);
      if (!l) {
        const style = { fontFamily: "sans-serif", fontSize: 24, fill: 0xffffff, stroke: { color: 0x000000, width: 5 }, fontWeight: "bold" as const };
        l = { title: new Text({ text: "", style }), order: new Text({ text: "", style: { ...style, fontWeight: "normal" as const, fontSize: 22 } }) };
        l.title.anchor.set(0.5, 1);
        l.order.anchor.set(0.5, 0);
        this.labels.addChild(l.title, l.order);
        this.texts.set(sid, l);
      }
      used.add(sid);
      const top = Math.min(...mem.map((u) => u.y)) - 1.1;
      l.title.visible = true;
      l.title.text = `${it.callsign}  ${intentLabel(it)}`;
      l.title.style.fill = col;
      l.title.alpha = own ? 1 : 0.7;
      l.title.resolution = Math.max(1, dpr * 2);
      l.title.scale.set(12 / 24 / zoom);
      l.title.position.set(c[0], top - 13 / zoom);
      const ol = own ? orderLabel(it.order, worldTick) : null;
      l.order.visible = !!ol;
      if (ol) {
        l.order.text = ol.text;
        l.order.style.fill = this.tone(ol.tone);
        l.order.resolution = Math.max(1, dpr * 2);
        l.order.scale.set(11 / 22 / zoom);
        l.order.position.set(c[0], top - 12 / zoom);
      }
    }
    for (const [sid, l] of this.texts) if (!used.has(sid)) { l.title.visible = false; l.order.visible = false; }
  }
}
