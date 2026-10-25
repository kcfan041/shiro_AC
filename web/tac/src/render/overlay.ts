// 其他 overlay（方案 §7.6、§11）：活動區域外調暗、交戰格邊框呼吸、作戰層情報（區域外的「!3」「?2」）、
// 標定點（集結旗、包抄箭頭、集火準星，虛線連到小隊）、支援（預告準星＋倒數環、無人機掃描圈、空投降落傘與箱子）、
// 工具放置預覽（無人機 16 m 圈、空投散布圈、煙牆方向、集結／包抄的預計路線）、畫面外事件的邊緣箭頭。
// 座標：畫面方向（y 向下），一格＝1；線寬與字級固定幾個 CSS px。

import { Container, Graphics, Text } from "pixi.js";
import type { WorldTerrain, XY } from "../protocol";
import type { Theme } from "../theme";

export interface ZoneView {
  cells: string[];
  hot: string[];
  marks?: { squad: string; callsign: string; pos: XY; kind: string }[];
  supports?: { id?: string; kind: string; label?: string; state: string; x: number; y: number; eta?: number; life?: number }[];
}
export interface WorldView { zones: ZoneView[]; intel?: { cell: string; est: number; suspected: boolean; age: number }[]; tick?: number }

export const DRONE_R = 8, AIRDROP_SPREAD = 1.5, SMOKE_SPACING = 2.5;

export class OverlayLayer {
  /** 地面上（個體下面）：調暗、格框、標定點、支援圈 */
  readonly ground = new Container();
  /** 最上面：情報字、支援倒數、預覽、邊緣箭頭 */
  readonly top = new Container();
  private g = new Graphics();
  private gt = new Graphics();
  private texts: Text[] = [];
  private ti = 0;
  private etaMax = new Map<string, number>();
  gm = false;

  constructor(private th: Theme, private T: WorldTerrain) {
    this.ground.addChild(this.g);
    this.top.addChild(this.gt);
  }

  private cellRect(id: string): [number, number, number] | null {
    const m = /^([A-Z]+)(\d+)$/.exec(id);                 // 列字母＋欄數字（engine/cells.py）
    const r = m ? this.T.rows.indexOf(m[1]) : -1, c = m ? this.T.cols.indexOf(m[2]) : -1;
    if (r < 0 || c < 0) return null;
    const n = this.T.cell;
    return [c * n, this.T.h - (r + 1) * n, n];
  }

  private text(s: string, x: number, y: number, px: number, color: number, zoom: number, dpr: number, alpha = 1, anchor: XY = [0.5, 0.5]) {
    let t = this.texts[this.ti];
    if (!t) {
      t = new Text({ text: "", style: { fontFamily: "sans-serif", fontSize: 24, fill: 0xffffff, stroke: { color: 0x000000, width: 5 }, fontWeight: "bold" } });
      this.top.addChild(t);
      this.texts[this.ti] = t;
    }
    this.ti++;
    t.visible = true;
    t.text = s;
    t.style.fill = color;
    t.alpha = alpha;
    t.anchor.set(anchor[0], anchor[1]);
    t.resolution = Math.max(1, dpr * 2);
    t.scale.set(px / 24 / zoom);
    t.position.set(x, y);
  }

  /**
   * view：伺服器的區域畫面；squadAt：小隊 id → 小隊中心（畫面方向），標定點的虛線用；
   * tool／cursor：放置中的工具與游標位置（畫面方向）；arrows：畫面外的事件（畫面方向），view＝看得到的範圍 [x0,y0,x1,y1]
   */
  update(view: WorldView | null, now: number, zoom: number, dpr: number, squadAt: (sid: string) => XY | null,
    tool: { kind: string; from: XY | null } | null, cursor: XY | null, arrows: XY[], vis: [number, number, number, number]) {
    const g = this.g, gt = this.gt, px = 1 / zoom, th = this.th;
    g.clear();
    gt.clear();
    this.ti = 0;
    const sy = (y: number) => this.T.h - y;
    if (view) {
      const inZone = new Set(view.zones.flatMap((z) => z.cells));
      const hot = new Set(view.zones.flatMap((z) => z.hot));
      for (let r = 0; r < this.T.rows.length; r++) {
        for (let c = 0; c < this.T.cols.length; c++) {
          const id = this.T.rows[r] + this.T.cols[c];
          const rc = this.cellRect(id)!;
          if (!this.gm && !inZone.has(id)) g.rect(rc[0], rc[1], rc[2], rc[2]).fill({ color: th.outside, alpha: 0.5 });
          if (hot.has(id)) g.rect(rc[0] + 0.2, rc[1] + 0.2, rc[2] - 0.4, rc[2] - 0.4).stroke({ width: 3 * px, color: th.pinned, alpha: 0.45 + 0.3 * Math.sin(now * 4) });
        }
      }
      // 作戰層情報（活動區域外）：越舊越淡
      for (const it of view.intel || []) {
        const rc = this.cellRect(it.cell);
        if (!rc) continue;
        const a = Math.max(0.35, 1 - it.age / 120);
        const col = it.suspected ? 0xb08050 : th.side.enemy;
        const cx = rc[0] + rc[2] / 2, cy = rc[1] + rc[2] / 2;
        g.roundRect(cx - 2.2, cy - 1.4, 4.4, 2.8, 0.4).stroke({ width: 2 * px, color: col, alpha: a });
        this.text(`${it.suspected ? "?" : "!"}${it.est}`, cx, cy - 0.2, 16, col, zoom, dpr, a);
        this.text(`${it.age}s 前`, cx, cy + 1.9, 10, col, zoom, dpr, a);
      }
      for (const z of view.zones) {
        // 標定點
        for (const mk of z.marks || []) {
          const p: XY = [mk.pos[0] + 0.5, sy(mk.pos[1] + 0.5)];
          const col = th.side.own;
          const c = squadAt(mk.squad);
          if (c) {
            const d = Math.hypot(p[0] - c[0], p[1] - c[1]);
            for (let s = 0; s < d; s += 1.2) {
              const e = Math.min(d, s + 0.6);
              g.moveTo(c[0] + (p[0] - c[0]) * (s / d), c[1] + (p[1] - c[1]) * (s / d)).lineTo(c[0] + (p[0] - c[0]) * (e / d), c[1] + (p[1] - c[1]) * (e / d));
            }
            g.stroke({ width: 1.5 * px, color: col, alpha: 0.6 });
          }
          if (mk.kind === "fire") {
            g.circle(p[0], p[1], 0.8).moveTo(p[0] - 1.3, p[1]).lineTo(p[0] + 1.3, p[1]).moveTo(p[0], p[1] - 1.3).lineTo(p[0], p[1] + 1.3)
              .stroke({ width: 2 * px, color: th.pinned });
          } else if (mk.kind === "fortify") {
            // 陣地：半圓的胸牆＋坑
            g.moveTo(p[0] - 1.1, p[1] + 0.3).arc(p[0], p[1] + 0.3, 1.1, Math.PI, 0).stroke({ width: 2.5 * px, color: col });
            g.rect(p[0] - 0.6, p[1] + 0.1, 1.2, 0.5).fill({ color: col, alpha: 0.5 });
          } else if (mk.kind === "flank") {
            g.moveTo(p[0] - 1, p[1] + 0.6).quadraticCurveTo(p[0] - 1, p[1] - 0.8, p[0] + 0.8, p[1] - 0.8).stroke({ width: 2.5 * px, color: col });
            g.poly([p[0] + 0.8, p[1] - 1.2, p[0] + 1.3, p[1] - 0.8, p[0] + 0.8, p[1] - 0.4]).fill({ color: col });
          } else {
            g.moveTo(p[0] - 0.4, p[1] + 0.8).lineTo(p[0] - 0.4, p[1] - 1).stroke({ width: 2.5 * px, color: col });
            g.poly([p[0] - 0.4, p[1] - 1, p[0] + 0.9, p[1] - 0.6, p[0] - 0.4, p[1] - 0.2]).fill({ color: col });
          }
          this.text(mk.callsign, p[0] + 1, p[1] - 1.2, 10, col, zoom, dpr, 1, [0, 1]);
        }
        // 支援
        for (const sp of z.supports || []) {
          const p: XY = [sp.x + 0.5, sy(sp.y + 0.5)];
          const key = sp.id ?? `${sp.kind}:${sp.x},${sp.y}`;
          if (sp.state === "inbound") {
            const eta = sp.eta ?? 0;
            const m = Math.max(this.etaMax.get(key) ?? 0, eta);
            this.etaMax.set(key, m);
            const k = m > 0 ? eta / m : 0;
            const col = th.suppressed;
            g.circle(p[0], p[1], 0.8).moveTo(p[0] - 1.4, p[1]).lineTo(p[0] + 1.4, p[1]).moveTo(p[0], p[1] - 1.4).lineTo(p[0], p[1] + 1.4)
              .stroke({ width: 2 * px, color: col });
            g.circle(p[0], p[1], 1.2 + 2.5 * k).stroke({ width: 1.5 * px, color: col, alpha: 0.7 });
            if (sp.kind === "airdrop") {                                  // 降落傘：越接近落地越低
              const h = 1 + 5 * k;
              g.moveTo(p[0] - 0.8, p[1] - h - 0.6).quadraticCurveTo(p[0], p[1] - h - 1.6, p[0] + 0.8, p[1] - h - 0.6).stroke({ width: 2 * px, color: 0xe8e8e0 });
              g.rect(p[0] - 0.3, p[1] - h - 0.1, 0.6, 0.6).fill({ color: 0x5ab4e8 });
            }
            this.text(`${sp.label || sp.kind} ${Math.ceil(eta)}s`, p[0] + 1.6, p[1] - 1.2, 11, col, zoom, dpr, 1, [0, 1]);
          } else if (sp.kind === "drone" && sp.state === "active") {
            const col = 0x5ab4e8;
            g.circle(p[0], p[1], DRONE_R).stroke({ width: 1.5 * px, color: col, alpha: 0.6 });
            const a = now * 1.2;
            g.moveTo(p[0], p[1]).arc(p[0], p[1], DRONE_R, a, a + 0.6).lineTo(p[0], p[1]).fill({ color: col, alpha: 0.12 });
            g.poly([p[0] - 0.5, p[1], p[0], p[1] - 0.5, p[0] + 0.5, p[1], p[0], p[1] + 0.5]).fill({ color: col });
          } else if (sp.kind === "airdrop" && sp.state === "landed") {
            g.rect(p[0] - 0.35, p[1] - 0.35, 0.7, 0.7).fill({ color: 0x5ab4e8 }).rect(p[0] - 0.35, p[1] - 0.35, 0.7, 0.7).stroke({ width: 1.5 * px, color: 0x0a2030 });
          }
        }
      }
    }
    // 放置預覽
    if (tool && cursor) {
      const [x, y] = cursor, col = th.suppressed;
      if (tool.kind === "drone") {
        gt.circle(x, y, DRONE_R).stroke({ width: 2 * px, color: 0x5ab4e8, alpha: 0.8 }).circle(x, y, DRONE_R).fill({ color: 0x5ab4e8, alpha: 0.06 });
      } else if (tool.kind === "airdrop") {
        gt.circle(x, y, AIRDROP_SPREAD + 0.5).stroke({ width: 2 * px, color: col }).circle(x, y, 3).stroke({ width: 1 * px, color: col, alpha: 0.5 });
      } else if (tool.kind === "smoke") {
        const f = tool.from;
        const d = f ? Math.hypot(x - f[0], y - f[1]) : 0;
        const nx = f && d > 0.1 ? -(y - f[1]) / d : 1, ny = f && d > 0.1 ? (x - f[0]) / d : 0;   // 垂直於「小隊 → 指定點」
        for (const k of [-1, 0, 1]) gt.circle(x + nx * SMOKE_SPACING * k, y + ny * SMOKE_SPACING * k, 2).fill({ color: 0xc0c8c4, alpha: 0.25 });
      } else if (tool.kind === "fire") {
        gt.circle(x, y, 1).moveTo(x - 1.6, y).lineTo(x + 1.6, y).moveTo(x, y - 1.6).lineTo(x, y + 1.6).stroke({ width: 2 * px, color: th.pinned });
      }
      if ((tool.kind === "rally" || tool.kind === "flank" || tool.kind === "fortify") && tool.from) {
        const f = tool.from;
        const d = Math.hypot(x - f[0], y - f[1]);
        for (let s = 0; s < d; s += 1.2) {
          const e = Math.min(d, s + 0.6);
          gt.moveTo(f[0] + (x - f[0]) * (s / d), f[1] + (y - f[1]) * (s / d)).lineTo(f[0] + (x - f[0]) * (e / d), f[1] + (y - f[1]) * (e / d));
        }
        gt.circle(x, y, 0.7).stroke({ width: 2 * px, color: th.side.own });
      }
    }
    // 畫面外事件：邊緣箭頭（指向事件的方向）
    const cx = (vis[0] + vis[2]) / 2, cy = (vis[1] + vis[3]) / 2;
    const hw = (vis[2] - vis[0]) / 2 - 24 * px, hh = (vis[3] - vis[1]) / 2 - 24 * px;
    for (const a of arrows) {
      const dx = a[0] - cx, dy = a[1] - cy;
      if (Math.abs(dx) < hw && Math.abs(dy) < hh) continue;
      const k = Math.min(hw / Math.max(1e-6, Math.abs(dx)), hh / Math.max(1e-6, Math.abs(dy)));
      const ex = cx + dx * k, ey = cy + dy * k, ang = Math.atan2(dy, dx), s = 14 * px;
      gt.poly([ex + Math.cos(ang) * s, ey + Math.sin(ang) * s, ex + Math.cos(ang + 2.5) * s, ey + Math.sin(ang + 2.5) * s,
        ex + Math.cos(ang - 2.5) * s, ey + Math.sin(ang - 2.5) * s]).fill({ color: th.suppressed, alpha: 0.6 + 0.3 * Math.sin(now * 6) });
    }
    for (let i = this.ti; i < this.texts.length; i++) this.texts[i].visible = false;
  }
}
