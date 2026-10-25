// 試聽頁：用遊戲的音效引擎播每種槍的單發、點放、壓制、遠處、看不到的射手，以及一段交火範例。
// 發數與節奏照引擎（engine/tactical/units.py 的 burst／supp_rounds、timeline 的壓制分兩波）與 fxplan.ROUND_GAP。

import { SoundEngine, type SoundReq } from "../audio";
import { mechAfter, ROUND_GAP } from "../fxplan";

const audio = new SoundEngine("/static/dist/sounds/");
const L = { x: 0, y: 0, halfWidth: 30 };

const WEAPONS: { key: string; name: string; burst: number; supp: number }[] = [
  { key: "rifle", name: "步槍", burst: 3, supp: 5 },
  { key: "lmg", name: "機槍", burst: 4, supp: 8 },
  { key: "dmr", name: "射手步槍", burst: 1, supp: 4 },
  { key: "shotgun", name: "霰彈槍", burst: 2, supp: 3 },
];
const SIDES = [{ key: "own", name: "我方", cls: "own" }, { key: "enemy", name: "敵方", cls: "enemy" }];

let seq = 0;
/** 一次射擊：n 發照射速錯開，每發接機械聲 */
function volley(at: number, weapon: string, side: string, n: number, x: number, y: number, shooter = `s${seq++}`) {
  for (let i = 0; i < n; i++) {
    const t = at + i * (ROUND_GAP[weapon] ?? ROUND_GAP.rifle);
    setTimeout(() => {
      const req: SoundReq = { kind: "gun", weapon, side, x, y, shooter };
      audio.play(req, L);
      const m = mechAfter(weapon);
      if (m) audio.play({ kind: "mech", mech: m.mech, after: m.after, x, y }, L);
    }, t * 1000);
  }
}

async function run(fn: () => void) {
  await audio.enable();
  await audio.samples;                     // 第一次要等錄音載入完，不然先播到合成音
  status(`聲音開啟（錄音 ${audio.loaded} 個）`);
  fn();
}

function status(s: string) { document.getElementById("status")!.textContent = s; }

const rows = SIDES.flatMap((s) => WEAPONS.map((w) => ({ s, w })));
const btn = (label: string, id: string) => `<button data-id="${id}">${label}</button>`;
document.getElementById("app")!.innerHTML = `
  <table>
    <tr><th>陣營</th><th>槍種</th><th>試聽</th></tr>
    ${rows.map(({ s, w }) => `<tr><td class="${s.cls}">${s.name}</td><td>${w.name}</td><td>
      ${btn("單發", `one:${s.key}:${w.key}`)}${w.burst > 1 ? btn(`點放 ${w.burst} 發`, `burst:${s.key}:${w.key}`) : ""}
      ${btn(`壓制一輪 ${w.supp} 發（兩波）`, `supp:${s.key}:${w.key}`)}${btn("40 格外、右邊", `far:${s.key}:${w.key}`)}
      ${s.key === "enemy" ? btn("看不到的射手（左前方 25 格）", `unseen:${s.key}:${w.key}`) : ""}</td></tr>`).join("")}
  </table>
  <p>連續比較：${btn("我方四種槍各一次點放", "cmp:own")}${btn("敵方四種槍各一次點放", "cmp:enemy")}${btn("同一種槍：我方 → 敵方", "cmpside")}</p>
  <p>交火範例：${btn("我方步槍＋機槍 對 敵方步槍＋射手步槍（約 8 秒）", "demo")}${btn("手榴彈", "boom")}</p>`;

function play(id: string) {
  const [k, side, w] = id.split(":");
  const W = WEAPONS.find((x) => x.key === w)!;
  if (k === "one") volley(0, w, side, 1, 3, 0);
  else if (k === "burst") volley(0, w, side, W.burst, 3, 0);
  else if (k === "supp") { const a = Math.ceil(W.supp / 2); volley(0, w, side, a, 3, 0); volley(1.0, w, side, W.supp - a, 3, 0); }
  else if (k === "far") volley(0, w, side, W.burst, 40, 0);
  else if (k === "unseen") volley(0, w, "enemy", W.burst, -15, 20);
  else if (k === "cmp") WEAPONS.forEach((x, i) => volley(i * 1.6, x.key, side, Math.max(1, x.burst), 3, 0));
  else if (k === "cmpside") WEAPONS.forEach((x, i) => { volley(i * 3, x.key, "own", Math.max(1, x.burst), -4, 0); volley(i * 3 + 1.4, x.key, "enemy", Math.max(1, x.burst), 4, 0); });
  else if (k === "boom") audio.play({ kind: "boom", x: 8, y: 0 }, L);
  else if (k === "demo") {
    for (let t = 0; t < 8; t += 2) {
      volley(t, "rifle", "own", 3, -6, 2);
      volley(t + 0.3, "lmg", "own", 6, -10, 4);
      volley(t + 0.9, "rifle", "enemy", 3, 18, -12);
      if (t % 4 === 0) volley(t + 1.4, "dmr", "enemy", 1, 30, -20);
    }
    setTimeout(() => audio.play({ kind: "boom", x: 12, y: -6 }, L), 5200);
  }
}

for (const b of document.querySelectorAll<HTMLButtonElement>("button[data-id]")) b.onclick = () => void run(() => play(b.dataset.id!));
