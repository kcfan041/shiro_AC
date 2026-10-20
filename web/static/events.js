"use strict";
// 結構化事件（Phase E）：戰鬥紀錄、行動理由、射擊時序。伺服器已依情報過濾（看不到的敵人沒有 ID）。

const EV = {
  names: {},
  label(id, f) {
    if (!id) return "不明位置";
    const u = (f.units || []).find((x) => x.id === id);
    if (u && u.side !== "enemy") return u.name;
    return "敵兵";
  },
  part: { head: "頭部", torso: "軀幹", larm: "左臂", rarm: "右臂", lleg: "左腿", rleg: "右腿" },
  item: { grenades: "手榴彈", smokes: "煙霧彈", flashes: "閃光彈" },
  mode: { aimed: "瞄準射擊", burst: "連發", suppress: "壓制射擊" },
  sup: { drone: "偵察無人機", airdrop: "精準空投", smoke: "煙幕彈幕" },
};

// 一條事件 → {text, cls}；回傳 null 表示不列在紀錄裡（太瑣碎）
function evLine(e, f) {
  const d = e.data || {};
  const A = EV.label(e.actor, f), T = EV.label(e.target, f);
  switch (e.type) {
    case "shot": {
      const who = e.actor ? A : "看不到的敵人";
      const at = e.target ? `→ ${T}` : "";
      const hits = (d.impact || []).filter((k) => k === "body").length;
      const ow = d.ow ? "警戒射擊" : (EV.mode[d.mode] || "射擊");
      return { text: `${who} ${ow}${at ? " " + at : ""}${d.rounds > 1 ? `（${d.rounds} 發）` : ""}${hits ? "" : (d.impact || []).includes("cover") ? "，打在掩體上" : ""}`,
        cls: d.ow ? "ev-ow" : e.actor && isOwnOrAlly(e.actor, f) ? "ev-own" : "ev-foe" };
    }
    case "hit": return { text: `　${T} ${EV.part[d.part] || d.part}中彈`, cls: isOwnOrAlly(e.target, f) ? "ev-hurt" : "ev-good" };
    case "down": return { text: `　${T} 倒地`, cls: isOwnOrAlly(e.target, f) ? "ev-hurt b" : "ev-good b" };
    case "kia": return { text: `${T} 陣亡（${d.why || ""}）`, cls: isOwnOrAlly(e.target, f) ? "ev-hurt b" : "ev-good" };
    case "suppress":
      if (d.level === d.was || !isOwnOrAlly(e.target, f)) return null;
      if (d.level === "pinned") return { text: `${T} 被火力壓得抬不起頭${e.actor ? `（${A}）` : ""}`, cls: "ev-warn" };
      if (d.level === "suppressed") return { text: `${T} 被壓制${e.actor ? `（${A}）` : ""}`, cls: "ev-warn" };
      return null;
    case "spot": return { text: `發現敵兵${d.how === "drone" ? "（無人機）" : ""}`, cls: "ev-intel" };
    case "hear": return { text: d.how === "gunfire" ? `聽到槍聲，大約在 (${d.at[0]},${d.at[1]}) 附近（誤差約 ${Math.round(d.err || 0)} m）` : `聽到動靜`, cls: "ev-intel" };
    case "lost": return null;
    case "throw": return { text: `${e.actor ? A : "敵兵"} 投擲${EV.item[d.item] || ""}`, cls: "ev-throw" };
    case "blast": return null;
    case "dazzled": return { text: `${T} 被閃光彈致盲`, cls: "ev-warn" };
    case "melee": return { text: `${A} 與 ${T} 近身搏鬥${d.winner ? `，${EV.label(d.winner, f)} 佔上風` : ""}`, cls: "ev-warn" };
    case "brace":
      if (d.state === "ready") return { text: `${A} 架好槍${d.rest ? "（有依託）" : ""}`, cls: "ev-dim" };
      if (d.state === "broken") return { text: `${A} 架槍中斷（${d.why}）`, cls: "ev-dim" };
      return null;
    case "hold_fire": return { text: `${A} 射線上有隊友，暫停射擊`, cls: "ev-dim" };
    case "flanked": return { text: `${T} 遭到側翼攻擊`, cls: "ev-hurt" };
    case "aid": return { text: `${A || "隊友"} 為 ${T} 止血，傷勢穩定`, cls: "ev-good" };
    case "drag": return { text: `${A} 拖著 ${T} 移動`, cls: "ev-dim" };
    case "exit": return { text: `${A} 撤出`, cls: "ev-dim" };
    case "surrender": return { text: `${A} 投降`, cls: "ev-good" };
    case "transfer": return { text: `${A} ${d.mode === "take" ? "撿起" : "交給"} ${T} ${(d.items || []).join("、")}`, cls: "ev-dim" };
    case "support": return { text: `指揮部：${EV.sup[d.what] || d.what} ${{ landed: "落地", done: "完成", active: "抵達", lost: "被擊落" }[d.state] || d.state}${d.result ? `（${d.result}）` : ""}`, cls: "ev-sup" };
    default: return null;
  }
}

function isOwnOrAlly(id, f) {
  const u = (f.units || []).find((x) => x.id === id);
  return !!u && u.side !== "enemy";
}

// 影格 → 紀錄行（HTML）。同一個同時結算群組的多次射擊標「同時」。
function evLogLines(f, prefix) {
  const evs = f.ev || [];
  if (!evs.length) return null;
  const grpN = {};
  for (const e of evs) if (e.type === "shot" && e.grp) grpN[e.grp] = (grpN[e.grp] || 0) + 1;
  const out = [];
  let lastGrp = null;
  for (const e of evs) {
    const l = evLine(e, f);
    if (!l) continue;
    let tag = "";
    if (e.type === "shot" && grpN[e.grp] > 1) {
      tag = e.grp === lastGrp ? `<span class="ev-sim">　┆</span>` : `<span class="ev-sim" title="同一瞬間結算，沒有先後">同時┐</span>`;
      lastGrp = e.grp;
    } else if (e.type !== "hit" && e.type !== "down") lastGrp = null;
    out.push(`<div class="${l.cls}"><span class="dim">${esc(prefix)}</span> ${tag}${esc(l.text)}</div>`);
  }
  return out;
}

// 射擊在畫面上的時間點（0～1）：警戒射擊依移動小步（與移動同步），一般射擊在移動結束後；同一群組同時出現。
function shotTimes(f) {
  const evs = (f.ev || []).filter((e) => e.type === "shot");
  const map = {};
  if (!evs.length) return map;
  let maxSub = 1;
  for (const u of f.units || []) for (const s of u.psub || []) maxSub = Math.max(maxSub, s);
  const fireGrps = [...new Set(evs.filter((e) => e.phase !== "overwatch").map((e) => e.grp))];
  for (const e of evs) {
    let t;
    if (e.phase === "overwatch") t = 0.9 * Math.min(1, (e.sub || 1) / maxSub);
    else t = 0.88 + 0.08 * (fireGrps.indexOf(e.grp) / Math.max(1, fireGrps.length));
    if (e.aid) map[e.aid] = { t, ow: !!(e.data && e.data.ow), mode: e.data && e.data.mode };
  }
  return map;
}

// 依移動小步內插（psub：每一步路徑是在第幾個移動小步走的），讓警戒射擊打在移動途中。
function lerpPathSub(u, pts, q) {
  if (!u.psub || !u.psub.length || pts.length < 2) return lerpPath(pts, q);
  const maxSub = EV.maxSub || Math.max(...u.psub);
  const s = q * maxSub;
  let i = 0;
  while (i < u.psub.length && u.psub[i] <= s) i++;
  if (i >= u.psub.length) return pts[pts.length - 1];
  const k = Math.max(0, Math.min(1, s - (u.psub[i] - 1)));
  return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k];
}

function frameMaxSub(f) {
  let m = 1;
  for (const u of f.units || []) for (const s of u.psub || []) m = Math.max(m, s);
  return m;
}

// 畫射擊：警戒射擊青色虛線、壓制射擊細琥珀、看不到的射手紅色；命中時目標閃一下
function drawShots(ctx, f, p, P, z) {
  const times = f._times || (f._times = shotTimes(f));
  const hasEv = Object.keys(times).length > 0;
  f.shots.forEach((sh, i) => {
    const tm = hasEv && sh.aid && times[sh.aid];
    const start = tm ? tm.t : 0.3 + 0.5 * (i / Math.max(1, f.shots.length));
    const age = p - start;
    if (age < 0 || age > 0.22) return;
    const a = 1 - age / 0.22;
    const [x0, y0] = P(sh.from[0], sh.from[1]);
    const [x1, y1] = P(sh.to[0], sh.to[1]);
    const ow = sh.ow || (tm && tm.ow);
    const supp = (sh.mode || (tm && tm.mode)) === "suppress";
    ctx.strokeStyle = sh.hit ? `rgba(255,120,90,${a})` : sh.unseen ? `rgba(255,80,80,${a * 0.7})`
      : ow ? `rgba(90,220,232,${a * 0.9})` : supp ? `rgba(232,176,74,${a * 0.5})` : `rgba(255,230,150,${a * 0.6})`;
    ctx.lineWidth = sh.hit ? 2 : supp ? 0.8 : 1;
    if (ow) ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineWidth = 1;
    if (sh.hit && age < 0.15) {
      ctx.strokeStyle = `rgba(255,90,70,${1 - age / 0.15})`;
      ctx.beginPath(); ctx.arc(x1, y1, Math.max(4, z * 0.7) * (1 + age * 4), 0, 7); ctx.stroke();
    }
  });
}

// 聽到槍聲：大概位置畫虛線圈＋「?」（誤差圈）
function drawHeard(ctx, f, p, P, z) {
  for (const e of f.ev || []) {
    if (e.type !== "hear" || !e.data || !e.data.at) continue;
    const a = Math.max(0, 1 - p) * 0.8 + 0.2;
    const [px, py] = P(e.data.at[0] + 0.5, e.data.at[1] + 0.5);
    const r = Math.max(6, ((e.data.err || 2) / 2) * z);
    ctx.strokeStyle = `rgba(232,176,74,${a})`;
    ctx.setLineDash([2, 4]); ctx.beginPath(); ctx.arc(px, py, r, 0, 7); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = `rgba(232,176,74,${a})`; ctx.font = "bold 12px monospace"; ctx.fillText("?", px - 3, py + 4);
  }
}

// 我方個體的理由標籤（放大時）
function drawReasons(ctx, units, P, z, selected) {
  if (z < 12) return;
  ctx.font = "9px monospace";
  for (const u of units) {
    if (u.side !== "own" || !u.why || u.downNow) continue;
    if (z < 18 && u.id !== selected) continue;
    const [px, py] = P(u.x, u.y);
    ctx.fillStyle = "rgba(10,20,12,.75)";
    const w = ctx.measureText(u.why).width;
    ctx.fillRect(px - w / 2 - 2, py + z * 0.5 + 2, w + 4, 11);
    ctx.fillStyle = "#bfe8c8";
    ctx.fillText(u.why, px - w / 2, py + z * 0.5 + 11);
  }
}

function suppLabel(v) {
  if (v == null) return "";
  if (v >= 80) return `<span class="red">壓制 ${v}（抬不起頭）</span>`;
  if (v >= 50) return `<span class="amber">壓制 ${v}（被壓制）</span>`;
  if (v > 0) return `<span class="dim">壓制 ${v}</span>`;
  return "";
}

function unitStatusHtml(u) {
  const bits = [];
  if (u.why) bits.push(`<b>正在：</b>${esc(u.why)}`);
  const s = suppLabel(u.supp);
  if (s) bits.push(s);
  if (u.bracing) bits.push(`<span class="dim">架槍中（下一步就緒）</span>`);
  else if (u.braced) bits.push(`<span class="dim">已架槍</span>`);
  if (u.dazzled) bits.push(`<span class="amber">光學過載</span>`);
  return bits.length ? `<div class="unit-now">${bits.join("｜")}</div>` : "";
}
