// 戰術圖上的 HTML 疊層（方案 §10、§11）：交火格清單、注意力提示卡、工具提示、懸停說明、圖層開關、聲音、圖例、
// 到下一個作戰 tick 的時間列與播放落後、F3 診斷。全部疊在畫布上，只有按鈕和卡片吃滑鼠。

import type { Attn } from "./attention";
import { cssHex, type Theme } from "./theme";

const CSS = (th: Theme) => `
.tac-hud{position:absolute;inset:0;pointer-events:none;font:12px/1.4 system-ui,"Microsoft JhengHei",sans-serif;color:${cssHex(th.text)};overflow:hidden}
.tac-hud .box{background:rgba(8,12,16,.78);border:1px solid rgba(140,170,200,.25);border-radius:4px}
.tac-hud button{pointer-events:auto;background:rgba(8,12,16,.78);color:inherit;border:1px solid rgba(140,170,200,.3);border-radius:3px;font:inherit;padding:1px 6px;cursor:pointer}
.tac-hud button.on{background:rgba(60,110,80,.55);border-color:rgba(120,220,160,.5)}
.tac-hud .fights{position:absolute;top:6px;left:50%;transform:translateX(-50%);display:flex;gap:4px}
.tac-hud .fights button{border-color:${cssHex(th.pinned)}88;color:${cssHex(th.pinned)}}
.tac-hud .hint{position:absolute;top:34px;left:50%;transform:translateX(-50%);padding:2px 10px;color:${cssHex(th.suppressed)}}
.tac-hud .cards{position:absolute;top:6px;right:6px;display:flex;flex-direction:column;gap:4px;align-items:flex-end;max-width:46%}
.tac-hud .card{pointer-events:auto;cursor:pointer;padding:4px 8px;animation:tacIn .25s ease-out}
.tac-hud .card.down,.tac-hud .card.kia{border-color:${cssHex(th.casualty)}aa}
.tac-hud .card.contact{border-color:${cssHex(th.pinned)}aa}
.tac-hud .card.interrupted{border-color:${cssHex(th.suppressed)}aa}
.tac-hud .card .go{opacity:.7;margin-left:6px}
.tac-hud .layers{position:absolute;left:6px;bottom:10px;display:flex;gap:3px;flex-wrap:wrap;align-items:center}
.tac-hud .q{opacity:.7;margin-left:4px}
.tac-hud .bar{position:absolute;left:0;right:0;bottom:0;height:4px;background:rgba(40,60,80,.6)}
.tac-hud .bar i{display:block;height:100%;background:${cssHex(th.side.own)}}
.tac-hud .lag{position:absolute;right:6px;bottom:8px;padding:1px 6px;color:${cssHex(th.suppressed)}}
.tac-hud .tip{position:absolute;padding:3px 7px;white-space:nowrap;transform:translate(14px,14px)}
.tac-hud .legend{position:absolute;left:6px;bottom:36px;padding:6px 8px;pointer-events:auto;max-width:300px}
.tac-hud .legend .s{display:inline-block;width:10px;height:10px;margin:0 4px 0 8px;vertical-align:-1px}
.tac-hud .diag{position:absolute;left:132px;top:6px;padding:4px 8px;font:11px/1.4 monospace;white-space:pre}
@keyframes tacIn{from{opacity:0;transform:translateX(12px)}to{opacity:1;transform:none}}
`;

export interface HudCallbacks {
  fight(cell: string): void;
  card(a: Attn): void;
  toggle(name: string, on: boolean): void;
  sound(): Promise<boolean>;
}

export class Hud {
  readonly root = document.createElement("div");
  private fights = document.createElement("div");
  private hint = document.createElement("div");
  private cardsEl = document.createElement("div");
  private layers = document.createElement("div");
  private bar = document.createElement("div");
  private lag = document.createElement("div");
  private tip = document.createElement("div");
  private legend = document.createElement("div");
  private diag = document.createElement("div");
  private fightKey = "";
  private cardKey = "";

  constructor(host: HTMLElement, th: Theme, private cb: HudCallbacks, toggles: Record<string, [string, boolean]>, quality: () => string) {
    if (!document.getElementById("tac-hud-css")) {
      const st = document.createElement("style");
      st.id = "tac-hud-css";
      st.textContent = CSS(th);
      document.head.appendChild(st);
    }
    this.root.className = "tac-hud";
    this.fights.className = "fights";
    this.hint.className = "hint box";
    this.cardsEl.className = "cards";
    this.layers.className = "layers";
    this.bar.className = "bar";
    this.bar.innerHTML = "<i></i>";
    this.lag.className = "lag box";
    this.tip.className = "tip box";
    this.legend.className = "legend box";
    this.diag.className = "diag box";
    for (const e of [this.hint, this.lag, this.tip, this.legend, this.diag]) e.style.display = "none";
    this.root.append(this.fights, this.hint, this.cardsEl, this.layers, this.bar, this.lag, this.tip, this.legend, this.diag);
    host.appendChild(this.root);
    for (const [k, [label, on]] of Object.entries(toggles)) {
      const b = document.createElement("button");
      b.textContent = label;
      b.classList.toggle("on", on);
      b.title = `顯示／隱藏${label}`;
      b.onclick = () => { const v = !b.classList.contains("on"); b.classList.toggle("on", v); cb.toggle(k, v); };
      this.layers.appendChild(b);
    }
    const snd = document.createElement("button");
    snd.textContent = "🔇";
    snd.title = "聲音";
    snd.onclick = async () => { const on = await cb.sound(); snd.textContent = on ? "🔊" : "🔇"; snd.classList.toggle("on", on); };
    this.layers.appendChild(snd);
    const lg = document.createElement("button");
    lg.textContent = "?";
    lg.title = "圖例";
    lg.onclick = () => { this.legend.style.display = this.legend.style.display === "none" ? "block" : "none"; };
    this.layers.appendChild(lg);
    const q = document.createElement("button");
    q.className = "q";
    q.title = "畫質：自動（跟不上時降低解析度與粒子）→ 固定高 → 固定低";
    q.onclick = () => { cb.toggle("quality", true); q.textContent = quality(); };
    this.layers.appendChild(q);
    setInterval(() => { q.textContent = quality(); }, 1000);
    const s = (c: number, shape: string) => `<i class="s" style="background:${cssHex(c)};${shape}"></i>`;
    this.legend.innerHTML = `<b>圖例</b><br>
      ${s(th.side.own, "border-radius:50%")}我方 ${s(th.side.ally, "border-radius:2px")}友軍 ${s(th.side.enemy, "transform:rotate(45deg)")}敵人（看得到）<br>
      ${s(th.side.enemy, "background:none;border:1px dashed " + cssHex(th.side.enemy) + ";transform:rotate(45deg)")}敵人最後位置（越淡越舊）<br>
      <span style="color:${cssHex(th.suppressed)}">◌ ?</span> 聽到槍聲的大概位置　<span style="color:#5adce8">━</span> 警戒射擊<br>
      圈：<span style="color:${cssHex(th.suppressed)}">琥珀</span> 被壓制、<span style="color:${cssHex(th.pinned)}">紅粗</span> 抬不起頭、白虛線 光學過載<br>
      <span style="color:${cssHex(th.casualty)}">✚</span> 倒地（白色＝已止血）　2F 在樓上<br>
      地形：高處亮、低處暗，影子越寬高差越大；<span style="color:${cssHex(th.bermTop)}">▬</span> 土堤／胸牆（滑鼠停在地上看高度）<br>
      視野：附近清楚、遠處半亮、看不到的地方變暗（大致範圍，視野內不代表一定發現）<br>
      小隊旁：戰術與命令狀態；虛線＝目標、弧線＝包抄、圈＝正在前進、扇形＝掩護`;
  }

  setFights(cells: string[]) {
    const key = cells.join(",");
    if (key === this.fightKey) return;
    this.fightKey = key;
    this.fights.innerHTML = "";
    for (const c of cells) {
      const b = document.createElement("button");
      b.textContent = `⚔ ${c}`;
      b.title = "鏡頭移到這場交火";
      b.onclick = () => this.cb.fight(c);
      this.fights.appendChild(b);
    }
  }

  setCards(cards: Attn[]) {
    const key = cards.map((a) => a.id).join("|");
    if (key === this.cardKey) return;
    this.cardKey = key;
    this.cardsEl.innerHTML = "";
    for (const a of cards) {
      const d = document.createElement("div");
      d.className = `card box ${a.kind}`;
      d.innerHTML = `${a.icon} ${a.text.replace(/[<>&]/g, "")}${a.at ? '<span class="go">→ 查看</span>' : ""}`;
      d.onclick = () => this.cb.card(a);
      this.cardsEl.appendChild(d);
    }
  }

  setHint(text: string | null) {
    this.hint.style.display = text ? "block" : "none";
    if (text) this.hint.textContent = text;
  }

  /** k：到下一個作戰 tick 還剩幾成（0～1）；lagS：播放落後幾秒 */
  setBar(k: number | null, lagS: number) {
    this.bar.style.display = k === null ? "none" : "block";
    if (k !== null) (this.bar.firstChild as HTMLElement).style.width = `${Math.max(0, Math.min(1, k)) * 100}%`;
    const show = lagS > 1.5;
    this.lag.style.display = show ? "block" : "none";
    if (show) this.lag.textContent = `畫面落後 ${lagS.toFixed(1)} 秒`;
  }

  setTip(html: string | null, x = 0, y = 0) {
    this.tip.style.display = html ? "block" : "none";
    if (html) { this.tip.innerHTML = html; this.tip.style.left = `${x}px`; this.tip.style.top = `${y}px`; }
  }

  setDiag(text: string | null) {
    this.diag.style.display = text ? "block" : "none";
    if (text) this.diag.textContent = text;
  }

  destroy() { this.root.remove(); }
}
