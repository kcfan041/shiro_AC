// 個體圖層（方案 §7.2）：依縮放自動換外觀（也可以固定）。
//   戰況（< 6 px／格）：我方、友軍、殘部每個小隊縮成一個標牌（人數／可作戰人數）；敵人照畫小符號。
//   小隊（6～16）：符號（我方圓、友軍圓角方、殘部虛線外圈、敵人菱形）＋朝向楔形；名字（避開重疊）。
//   個體（> 16）：程式畫的俯視機兵（肩甲形狀依陣營、槍的長度依武器、走路肩膀擺動、開槍後座），行動理由。
// 狀態：被壓制（琥珀圈輕微抖動）、釘死（紅色粗圈）、光學過載（白色虛線圈）、架槍（槍口兩腳架，架槍中閃爍）、
// 倒地（倒成臥姿、變暗、紅十字；已穩定白十字）、2 樓以上（白框＋2F）、選取（白圈）。
// 座標：畫面方向（x 向東、y 向下＝世界 y 翻轉），一格＝1。

import { Container, Sprite, Text, type Texture } from "pixi.js";
import type { UnitSample } from "../tracks";
import { sideKey, type Theme } from "../theme";
import { U, type UnitTextures } from "./unitTextures";

export type Look = "auto" | "symbol" | "mech";
export const ZOOM_SQUAD = 6, ZOOM_MECH = 16, ZOOM_NAMES = 9, ZOOM_WHY = 18;
/** 機兵剪影比實際比例大一點（一格 2 m，照比例畫只有 0.3 格寬，看不清楚） */
const MECH_SCALE = 1.4;

const GUN_LEN: Record<string, [number, number]> = { rifle: [0.55, 1], lmg: [0.62, 1.5], dmr: [0.78, 0.8], shotgun: [0.45, 1.3] };
const POSTURE_K: Record<string, number> = { stand: 1, crouch: 0.9, prone: 0.82 };

function hashId(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
}

function mix(a: number, b: number, k: number): number {
  const f = (s: number) => Math.round(((a >> s) & 255) * (1 - k) + ((b >> s) & 255) * k);
  return (f(16) << 16) | (f(8) << 8) | f(0);
}

const S = 1 / U;

function sp(tex: Texture, parent: Container, ax = 0.5, ay = 0.5): Sprite {
  const s = new Sprite(tex);
  s.anchor.set(ax, ay);
  s.scale.set(S);
  parent.addChild(s);
  return s;
}

class UnitView {
  readonly root = new Container();
  private shadow: Sprite;
  private base: Sprite;
  private body = new Container();
  private legs: Sprite;
  private pack: Sprite;
  private shoulders: Sprite;
  private torso: Sprite;
  private head: Sprite;
  private visor: Sprite;
  private gun: Sprite;
  private bipod: Sprite;
  private symbol: Sprite;
  private ringSupp: Sprite;
  private ringPin: Sprite;
  private ringDaz: Sprite;
  private ringSel: Sprite;
  private cross: Sprite;
  private frame: Sprite;
  label: Text | null = null;
  why: Text | null = null;
  floorTag: Text | null = null;
  readonly phase: number;
  lastShot = -Infinity;
  seen = 0;

  constructor(readonly id: string, private tex: UnitTextures) {
    this.phase = hashId(id) * Math.PI * 2;
    this.shadow = sp(tex.shadow, this.root);
    this.shadow.tint = 0x000000;
    this.base = sp(tex.base.own, this.root);   // 機兵腳下的陣營底盤：顏色＋形狀（圓／圓角方／菱形／虛線圓）分敵我
    this.root.addChild(this.body);
    this.legs = sp(tex.legs, this.body);
    this.pack = sp(tex.pack, this.body);
    this.shoulders = sp(tex.shoulders.own, this.body);
    this.torso = sp(tex.torso, this.body);
    this.gun = sp(tex.gun, this.body, 0, 0.5);
    this.bipod = sp(tex.bipod, this.body, 0, 0.5);
    this.head = sp(tex.head, this.body);
    this.visor = sp(tex.visor, this.body);
    this.symbol = sp(tex.symbol.own, this.root);
    this.ringSupp = sp(tex.ring, this.root);
    this.ringPin = sp(tex.ringThick, this.root);
    this.ringDaz = sp(tex.ringDash, this.root);
    this.ringSel = sp(tex.ring, this.root);
    this.cross = sp(tex.cross, this.root);
    this.frame = sp(tex.frame, this.root);
  }

  update(u: UnitSample, th: Theme, zoom: number, look: Look, t: number, selected: boolean) {
    const side = sideKey(u.side);
    const col = th.side[side];
    const f = u.u;
    const mech = look === "mech" || (look === "auto" && zoom >= ZOOM_MECH);
    const down = u.downK >= 0.5;
    const pa = POSTURE_K[u.postureFrom] ?? 1, pb = POSTURE_K[u.posture] ?? 1;
    const pk = pa + (pb - pa) * u.postureK;
    const prone = (u.posture === "prone" ? u.postureK : u.postureFrom === "prone" ? 1 - u.postureK : 0) || 0;
    const lie = Math.max(prone, u.downK);
    this.root.x = u.x;
    this.root.y = u.y;
    this.root.alpha = u.alpha;
    this.root.zIndex = (u.z || 0) * 1000 + (down ? 0 : 1);

    // 機兵剪影
    this.body.visible = mech;
    this.shadow.visible = mech;
    this.base.visible = mech && !down;
    this.symbol.visible = !mech;
    if (mech) {
      this.body.rotation = -u.facing;
      const sway = u.moving && !down ? Math.sin(t * 9 + this.phase) * 0.13 : 0;
      const recoil = Math.max(0, 1 - (t - this.lastShot) / 0.09) * 0.07;
      const dark = down ? 0.45 : 0;
      const armor = mix(th.armor, 0x000000, dark);
      this.shoulders.texture = this.tex.shoulders[side];
      this.shoulders.tint = mix(col, 0x000000, 0.08 + dark);
      this.shoulders.rotation = sway;
      this.shoulders.scale.set(S * pk * (1 - lie * 0.15), S * pk);
      this.torso.tint = armor;
      this.torso.scale.set(S * pk);
      this.torso.rotation = sway * 0.5;
      this.pack.tint = mix(armor, 0x000000, 0.25);
      this.pack.x = -0.16 * pk;
      this.legs.visible = lie > 0.05;
      this.legs.alpha = lie;
      this.legs.tint = armor;
      this.legs.x = -0.12;
      this.head.tint = mix(th.armor, 0xffffff, 0.12 - dark * 0.3);
      this.head.x = 0.05 + (u.posture === "crouch" ? 0.04 : 0);
      this.visor.x = this.head.x;
      this.visor.tint = down ? 0x553333 : f.dazzled ? 0xffffff : th.visor[side];
      const [len, thick] = GUN_LEN[f.weapon || "rifle"] || GUN_LEN.rifle;
      this.gun.tint = down ? 0x2a2a2a : 0x8a949a;
      // 舉槍瞄準（伺服器的 aim 事件）：槍從右手側收到正前方、往前伸一點，開火時後座
      const aimK = u.aim ?? 0;
      this.gun.x = 0.04 + aimK * 0.06 - recoil;
      this.gun.y = 0.12 - aimK * 0.08;
      this.gun.scale.set(len * (1 + aimK * 0.12) * S, S * thick);        // 貼圖寬 U＝一格
      this.gun.visible = !down;
      this.bipod.visible = !down && !!(f.braced || f.bracing) && (!f.bracing || Math.sin(t * 12) > 0);
      this.bipod.x = 0.04 + len - 0.06;
      this.bipod.y = 0.12;
      this.bipod.tint = mix(col, 0xffffff, 0.3);
      this.bipod.scale.set(S * 0.5);
      this.shadow.alpha = 0.35 * (1 - lie * 0.4);
      this.shadow.scale.set(S * 1.1 * MECH_SCALE);
      this.body.scale.set(MECH_SCALE);
      this.base.texture = this.tex.base[side];
      this.base.tint = col;
      this.base.alpha = 0.6;
      this.base.scale.set(S * MECH_SCALE * 1.05);
    } else {
      this.symbol.texture = down ? this.tex.symbolDown : this.tex.symbol[side];
      this.symbol.tint = down ? mix(col, 0x000000, 0.35) : col;
      this.symbol.rotation = -u.facing;
      const k = zoom < ZOOM_SQUAD ? 1.6 : 1;                   // 拉很遠時符號不要小到看不見
      this.symbol.scale.set(S * 1.9 * pk * k, S * 1.9 * pk * k * (1 - prone * 0.25));   // 約 0.95 格
    }

    // 狀態圈與標記（機兵比較大，圈也跟著放大）
    const rk = mech ? MECH_SCALE * 1.15 : 1;
    const supp = !down && !!f.suppressed && !f.pinned;
    this.ringSupp.visible = supp;
    if (supp) {
      this.ringSupp.tint = th.suppressed;
      this.ringSupp.scale.set(S * rk * (1.05 + Math.sin(t * 31 + this.phase) * 0.04));
    }
    this.ringPin.visible = !down && !!f.pinned;
    this.ringPin.tint = th.pinned;
    this.ringPin.scale.set(S * rk * 1.05);
    this.ringDaz.visible = !down && !!f.dazzled;
    this.ringDaz.tint = 0xffffff;
    this.ringDaz.rotation = t * 2;
    this.ringDaz.scale.set(S * rk * 1.2);
    this.ringSel.visible = selected;
    this.ringSel.tint = th.select;
    this.ringSel.scale.set(S * rk * 1.45);
    this.cross.visible = down && !u.dead;
    this.cross.tint = f.stabilized ? th.stabilized : th.casualty;
    this.cross.x = 0.42 * rk;
    this.cross.y = -0.42 * rk;
    this.cross.scale.set(S * 0.55);
    const upstairs = (u.z || 0) > 0;
    this.frame.visible = upstairs;
    this.frame.scale.set(S * rk * 1.3);
    if (upstairs) {
      if (!this.floorTag) {
        this.floorTag = new Text({ text: "2F", style: { fontFamily: "monospace", fontSize: 22, fill: 0xffffff, fontWeight: "bold" } });
        this.floorTag.anchor.set(0, 1);
        this.root.addChild(this.floorTag);
      }
      this.floorTag.text = `${Math.round(u.z) + 1}F`;
      this.floorTag.position.set(0.55, -0.45);
      this.floorTag.scale.set(10 / 22 / zoom);
    } else if (this.floorTag) this.floorTag.visible = false;
    if (this.floorTag && upstairs) this.floorTag.visible = true;
  }

  destroy() { this.root.destroy({ children: true }); }
}

class SquadBadge {
  readonly root = new Container();
  private bg: Sprite;
  private text: Text;
  constructor(tex: UnitTextures) {
    this.bg = sp(tex.badge, this.root);
    this.text = new Text({ text: "", style: { fontFamily: "monospace", fontSize: 30, fill: 0x000000, fontWeight: "bold" } });
    this.text.anchor.set(0.5);
    this.root.addChild(this.text);
  }
  update(x: number, y: number, col: number, label: string, zoom: number, dpr: number) {
    this.root.visible = true;
    this.root.position.set(x, y);
    this.bg.tint = col;
    this.bg.scale.set((28 / zoom) / U);           // 固定約 28 CSS px 寬
    this.text.text = label;
    this.text.resolution = Math.max(1, dpr * 2);
    this.text.scale.set(10 / 30 / zoom);
  }
}

export class UnitLayer {
  readonly root = new Container();
  readonly labels = new Container();
  private views = new Map<string, UnitView>();
  private badges: SquadBadge[] = [];
  private ghosts: { s: Sprite; t: Text }[] = [];
  private frame = 0;
  look: Look = "auto";
  selected: string | null = null;

  constructor(private tex: UnitTextures, private th: Theme) {
    this.root.sortableChildren = true;
  }

  setTheme(th: Theme) { this.th = th; }

  /** 這一刻開了槍的人（畫後座） */
  shot(id: string, t: number) {
    const v = this.views.get(id);
    if (v) v.lastShot = t;
  }

  /**
   * units：tracks.sample() 的結果（座標已經是畫面方向）；ghosts：最後已知位置（畫面方向、幾秒前）。
   * 回傳畫了幾個個體。
   */
  update(units: UnitSample[], ghosts: { id: string; x: number; y: number; age: number }[], zoom: number, dpr: number, t: number): number {
    this.frame++;
    const squadMode = this.look === "auto" && zoom < ZOOM_SQUAD;
    const groups = new Map<string, { xs: number; ys: number; n: number; up: number; side: string }>();
    let drawn = 0;
    for (const u of units) {
      let v = this.views.get(u.id);
      if (!v) {
        v = new UnitView(u.id, this.tex);
        this.views.set(u.id, v);
        this.root.addChild(v.root);
      }
      v.seen = this.frame;
      const friendly = u.side !== "enemy";
      const grp = (u.u.group || u.u.squad || "") as string;
      if (squadMode && friendly && grp && !u.dead) {
        v.root.visible = false;
        const g = groups.get(grp) || { xs: 0, ys: 0, n: 0, up: 0, side: u.side };
        g.xs += u.x; g.ys += u.y; g.n++;
        if (!u.down) g.up++;
        groups.set(grp, g);
        continue;
      }
      v.root.visible = true;
      v.update(u, this.th, zoom, this.look, t, this.selected === u.id);
      drawn++;
    }
    for (const [id, v] of this.views) {
      if (v.seen !== this.frame) {
        if (this.frame - v.seen > 600) { v.destroy(); this.views.delete(id); }
        else v.root.visible = false;
      }
    }
    // 小隊標牌
    let bi = 0;
    for (const [, g] of groups) {
      const b = this.badges[bi] || (this.badges[bi] = new SquadBadge(this.tex));
      if (!b.root.parent) this.labels.addChild(b.root);
      b.update(g.xs / g.n, g.ys / g.n, this.th.side[sideKey(g.side)], g.up === g.n ? `${g.n}` : `${g.up}/${g.n}`, zoom, dpr);
      bi++;
    }
    for (; bi < this.badges.length; bi++) this.badges[bi].root.visible = false;
    // 最後已知位置
    for (let i = 0; i < Math.max(ghosts.length, this.ghosts.length); i++) {
      const g = ghosts[i];
      let e = this.ghosts[i];
      if (!g) { if (e) { e.s.visible = false; e.t.visible = false; } continue; }
      if (!e) {
        e = { s: sp(this.tex.ghost, this.root), t: new Text({ text: "", style: { fontFamily: "monospace", fontSize: 20, fill: 0xffffff } }) };
        this.labels.addChild(e.t);
        this.ghosts[i] = e;
      }
      e.s.visible = true;
      e.s.position.set(g.x, g.y);
      e.s.tint = this.th.side.enemy;
      e.s.alpha = Math.max(0.2, 1 - g.age / 90);
      e.s.scale.set(S * 1.2 * (zoom < ZOOM_SQUAD ? 1.6 : 1));
      e.t.visible = zoom >= ZOOM_NAMES;
      e.t.text = `${g.age}s`;
      e.t.style.fill = this.th.side.enemy;
      e.t.alpha = e.s.alpha;
      e.t.position.set(g.x + 0.45, g.y - 0.6);
      e.t.resolution = Math.max(1, dpr * 2);
      e.t.scale.set(10 / 20 / zoom);
    }
    this.placeLabels(units, zoom, dpr, squadMode);
    return drawn;
  }

  /** 名字（小隊級距以上）與行動理由（個體級距的我方），由上往下放，和已經放的重疊就不放 */
  private placeLabels(units: UnitSample[], zoom: number, dpr: number, squadMode: boolean) {
    const placed: [number, number, number, number][] = [];
    const hit = (r: [number, number, number, number]) => placed.some((p) => r[0] < p[2] && r[2] > p[0] && r[1] < p[3] && r[3] > p[1]);
    const showNames = !squadMode && zoom >= ZOOM_NAMES;
    const px = 11 / zoom;                      // 字高（世界單位）
    const sorted = units.filter((u) => u.side !== "enemy").sort((a, b) => a.y - b.y || (a.id < b.id ? -1 : 1));
    const used = new Set<string>();
    for (const u of sorted) {
      const v = this.views.get(u.id);
      if (!v || !v.root.visible) continue;
      used.add(u.id);
      const wantName = showNames && u.alpha > 0.3;
      if (wantName) {
        if (!v.label) {
          v.label = new Text({ text: u.name, style: { fontFamily: "monospace", fontSize: 22, fill: 0xffffff, stroke: { color: 0x000000, width: 4 } } });
          this.labels.addChild(v.label);
        }
        const L = v.label;
        L.text = u.name;
        L.style.fill = u.side === "own" ? this.th.text : this.th.side[sideKey(u.side)];
        L.resolution = Math.max(1, dpr * 2);
        L.scale.set(px / 22);
        const w = (L.width / L.scale.x) * L.scale.x;
        const r: [number, number, number, number] = [u.x + 0.55, u.y - 0.55 - px * 0.5, u.x + 0.55 + w, u.y - 0.55 + px * 0.7];
        L.visible = !hit(r);
        if (L.visible) { placed.push(r); L.position.set(r[0], r[1]); L.alpha = u.alpha; }
      } else if (v.label) v.label.visible = false;
      const wantWhy = !squadMode && u.side === "own" && !!u.u.why && !u.down && (zoom >= ZOOM_WHY || this.selected === u.id) && zoom >= ZOOM_NAMES;
      if (wantWhy) {
        if (!v.why) {
          v.why = new Text({ text: "", style: { fontFamily: "sans-serif", fontSize: 20, fill: 0xffffff, stroke: { color: 0x000000, width: 4 } } });
          v.why.anchor.set(0.5, 0);
          this.labels.addChild(v.why);
        }
        const W = v.why;
        W.text = u.u.why!;
        W.style.fill = this.th.text;
        W.resolution = Math.max(1, dpr * 2);
        W.scale.set((10 / zoom) / 20);
        const w = W.width;
        const r: [number, number, number, number] = [u.x - w / 2, u.y + 0.55, u.x + w / 2, u.y + 0.55 + 12 / zoom];
        W.visible = !hit(r);
        if (W.visible) { placed.push(r); W.position.set(u.x, u.y + 0.55); W.alpha = u.alpha; }
      } else if (v.why) v.why.visible = false;
    }
    for (const [id, v] of this.views) if (!used.has(id)) { if (v.label) v.label.visible = false; if (v.why) v.why.visible = false; }
  }
}
