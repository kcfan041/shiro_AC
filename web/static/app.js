"use strict";
// Area Commander 網頁前端（原生 JS + Canvas）

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const G = {
  me: null, state: null, ws: null, wsRetry: 0,
  watch: null,            // 觀戰／GM 正在看的指揮官 id
  selected: null,         // 選中的小隊 id
  expanded: {},           // 展開的隊員 key
  typing: {},             // 小隊 id -> 開始「回覆中」的時間
  lastStep: 0,            // 上一個戰術影格到達的時間（倒數條）
  extras: {},             // 小隊 id -> 非對話的無線電項目（回執、系統、自主、提議、支援）
  opTool: null,           // 作戰圖工具 {kind, squad, via: []}
  opPos: {},              // 作戰圖小隊動畫位置
  hoverCell: null,
  tac: null,              // 戰術畫面狀態
};

// ------------------------------------------------------------------ util

function toast(text, err) {
  const d = document.createElement("div");
  d.textContent = text;
  if (err) d.className = "err";
  $("toast").appendChild(d);
  setTimeout(() => d.remove(), 4200);
}

async function api(path, body, method) {
  const opt = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let d = {};
  try { d = await r.json(); } catch (e) { d = { ok: false, error: `HTTP ${r.status}` }; }
  if (!r.ok && d.ok === undefined) d.ok = false;
  return d;
}

function show(id) {
  for (const s of ["login", "lobby", "hq"]) $(s).classList.toggle("hidden", s !== id);
}

function readonly() {
  return !!(G.watch && G.me && G.watch !== G.me.id);
}

function secs(ticks) { return ticks * ((G.state && G.state.tick_seconds) || 10); }

function cellName(id) {
  const c = G.state && G.state.map.cells.find((x) => x.id === id);
  return c ? `${id} ${c.name}` : id;
}

// 格子 ID＝列字母＋欄數字（A1、C3、AB12），和 engine/cells.py 一致；回傳 [列字母, 欄數字]，不合格式回傳 null
const CELL_RE = /^([A-Z]+)(\d+)$/;
function cellParts(id) {
  const m = CELL_RE.exec(String(id || ""));
  return m ? [m[1], m[2]] : null;
}

// 格子 ID → 地圖內的 [列, 欄]（rows、cols＝row_names、col_names）；找不到是 [-1, -1]
function cellIndex(rows, cols, id) {
  const p = cellParts(id);
  return p ? [rows.indexOf(p[0]), cols.indexOf(p[1])] : [-1, -1];
}

function adjacent(a, b) {
  const m = G.state.map;
  const [ra, ca] = cellIndex(m.row_names, m.col_names, a);
  const [rb, cb] = cellIndex(m.row_names, m.col_names, b);
  return Math.abs(ra - rb) + Math.abs(ca - cb) === 1;
}

function squadById(id) { return G.state && G.state.squads.find((s) => s.id === id); }

// ------------------------------------------------------------------ login / lobby

async function boot() {
  const d = await api("/api/me");
  G.me = d.me;
  if (!G.me) { show("login"); return; }
  await enter();
}

async function enter() {
  const d = await api("/api/state");
  if (!d.ok) { show("login"); return; }
  G.state = d.state;
  G.me = d.state.me;
  if (G.state.squads.length) { G.watch = null; openHQ(); return; }
  if (G.me.role === "gm") { await startWatching(); return; }
  showLobby();
}

async function showLobby() {
  show("lobby");
  const d = await api("/api/commanders");
  const list = (d.commanders || []).map((c) => `<div>· ${esc(c.name)}（${c.squads} 支小隊）</div>`).join("");
  $("lb-commanders").innerHTML = list ? `<div class="panel-title small">目前的指揮官</div>${list}` : `<div class="dim">目前沒有指揮官。</div>`;
}

$("lg-login").onclick = async () => {
  const d = await api("/api/login", { name: $("lg-name").value, password: $("lg-pw").value });
  if (!d.ok) { $("lg-msg").textContent = d.error; return; }
  boot();
};
$("lg-register").onclick = async () => {
  const d = await api("/api/register", { name: $("lg-name").value, password: $("lg-pw").value });
  if (!d.ok) { $("lg-msg").textContent = d.error; return; }
  boot();
};
$("lg-pw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("lg-login").click(); });
$("lb-enlist").onclick = async () => {
  const d = await api("/api/enlist", {});
  if (!d.ok) { $("lb-msg").textContent = d.error; return; }
  toast(d.message);
  await enter();
};
$("lb-spectate").onclick = () => startWatching();
$("lb-help").onclick = () => showHelp();
$("lb-logout").onclick = $("hd-logout").onclick = async () => { await api("/api/logout", {}); location.reload(); };

async function startWatching() {
  const d = await api("/api/commanders");
  const cs = d.commanders || [];
  const sel = $("watch-sel");
  sel.innerHTML = cs.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  if (!cs.length) {
    if (G.me.role === "gm") { G.watch = G.me.id; openHQ(); toast("目前沒有指揮官。GM 頁可以推進或重置。"); return; }
    toast("目前沒有指揮官可以觀戰", true); return;
  }
  G.watch = G.watch && cs.some((c) => c.id === G.watch) ? G.watch : cs[0].id;
  sel.value = G.watch;
  const s = await api(`/api/state?watch=${encodeURIComponent(G.watch)}`);
  if (s.ok) G.state = s.state;
  openHQ();
}
$("watch-sel").onchange = async () => {
  G.watch = $("watch-sel").value;
  G.extras = {};
  G.selected = null;
  if (G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify({ type: "watch", commander: G.watch }));
  const s = await api(`/api/state?watch=${encodeURIComponent(G.watch)}`);
  if (s.ok) { G.state = s.state; renderAll(); }
};

function openHQ() {
  show("hq");
  $("hd-gm").classList.toggle("hidden", G.me.role !== "gm");
  $("hd-watch").classList.toggle("hidden", !G.watch || G.watch === G.me.id && !G.state.squads.length && G.me.role !== "gm");
  if (G.watch && G.watch !== G.me.id) $("hd-watch").classList.remove("hidden");
  connect();
  renderAll();
  if (typeof setupDock === "function") setupDock();
  const h = location.hash;
  if (h.startsWith("#tac=")) openTactical(h.slice(5).toUpperCase(), G.me.role === "gm" && h.endsWith("!"));
  if (h === "#op" && typeof opPopOpen === "function" && DOCK.on) opPopOpen(false);
}

// ------------------------------------------------------------------ websocket

function connect() {
  if (G.ws && G.ws.readyState <= 1) return;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  G.ws = ws;
  ws.onopen = () => {
    $("conn").classList.add("ok");
    G.wsRetry = 0;
    if (G.watch) ws.send(JSON.stringify({ type: "watch", commander: G.watch }));
    if (G.tac && G.tac.gm) ws.send(JSON.stringify({ type: "gm_cell", cell: G.tac.cell }));
  };
  ws.onclose = () => {
    $("conn").classList.remove("ok");
    G.wsRetry = Math.min(10, G.wsRetry + 1);
    setTimeout(connect, 500 * G.wsRetry);
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "state") {
      G.state = m.state; G.lastTick = performance.now(); renderAll();
      if (typeof setupDock === "function" && !DOCK.on) setupDock();
      if (G.tac && !G.tac.world && !G.tac.replay && !G.tac.frame) refreshTacMeta();
    }
    else if (m.type === "radio") onRadio(m.items);
    else if (m.type === "tactical") onTactical(m.frames);
    else if (m.type === "world") { G.world = m.world; if (G.tac && G.tac.world) onWorld(m.world); }
    else if (m.type === "error") toast(m.error, true);
  };
}

function onRadio(items) {
  for (const it of items) {
    const sid = it.squad || "_";
    if (it.type === "typing") { G.typing[sid] = performance.now(); continue; }
    if (it.type === "report" || it.type === "receipt") delete G.typing[sid];
    if (it.type === "report" && G.state) {
      const sq = squadById(sid);
      if (sq) sq.radio = (sq.radio || []).concat([{ t: it.t, who: "leader", text: it.text }]).slice(-60);
    }
    if (it.type === "order" && G.state) {
      const sq = squadById(sid);
      if (sq) sq.radio = (sq.radio || []).concat([{ t: it.t, who: "commander", text: it.text }]).slice(-60);
      G.extras[sid] = (G.extras[sid] || []).filter((x) => !(x.local && x.text === it.text));
    }
    if (it.type === "ally" && !it.squad) toast(`📻 ${it.name}：${it.text}`);
    // 給小隊的友軍無線電也在伺服器的無線電紀錄裡（who: ally），跟回報一樣併進 sq.radio，不另外放 extras（不然會出現兩次）
    if (it.type === "ally" && it.squad && G.state) {
      const sq = squadById(sid);
      if (sq) sq.radio = (sq.radio || []).concat([{ t: it.t, who: "ally", text: `${it.name}：${it.text}` }]).slice(-60);
    }
    if (["receipt", "system", "auto", "proposal", "support", "replay"].includes(it.type)) {
      (G.extras[sid] = G.extras[sid] || []).push(it);
      if (G.extras[sid].length > 80) G.extras[sid].shift();
    }
    if (it.type === "auto") toast(`${it.callsign || ""} 自主行動：${it.action}（${it.reason}）`);
    if (it.type === "proposal") toast(`${it.callsign || ""} 提議：${it.proposal.text}`);
    if (it.type === "support") toast(`📡 ${it.text}`);
    if (it.type === "replay") toast(`🎞 ${it.replay.cell} 的交戰已存成回放`);
    if (it.type === "mission") toast(`📜 任務${it.event === "start" ? "開始" : it.event === "done" ? "達成" : "失敗"}：${it.mission.title}`);
  }
  if (items.some((it) => ["mission", "receipt", "support", "replay"].includes(it.type))) reloadState();
  renderSquads();
}

let reloadTimer = null;
function reloadState() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(async () => {
    const q = G.watch && G.watch !== G.me.id ? `?watch=${encodeURIComponent(G.watch)}` : "";
    const d = await api(`/api/state${q}`);
    if (d.ok) { G.state = d.state; renderAll(); }
  }, 150);
}

// ------------------------------------------------------------------ render: HQ

function renderAll() {
  if (!G.state) return;
  renderHeader();
  renderSquads();
  renderSide();
}

function renderHeader() {
  const s = G.state;
  const who = readonly() ? `觀戰：${esc(s.commander.name)}` : `指揮官 ${esc(s.commander.name)}`;
  const sup = s.commander.supply_wait > 0 ? `補給冷卻 ${secs(s.commander.supply_wait)}s` : "補給可請求";
  const slow = s.time_scale && s.time_scale < 1
    ? `｜<span class="slow" title="伺服器模擬跟不上，遊戲時間變慢（近 10 秒平均）">⏱ 時間 ×${s.time_scale.toFixed(2)}</span>` : "";
  $("hd-info").innerHTML = `${who}｜T${s.tick}${!s.running ? "（暫停）" : s.idle_paused ? "（沒有玩家在線，暫停）" : ""}${slow}｜預備兵力 ${s.reserve} 人｜${sup}`;
  $("hd-replays").classList.toggle("hidden", readonly() && G.me.role !== "gm");
}

function renderSide() {
  const s = G.state;
  $("summary").innerHTML = s.summary.map((l) => `<div>${esc(l)}</div>`).join("") || `<div class="dim">（沒有部隊）</div>`;
  $("events").innerHTML = s.events.slice().reverse().map((l) => `<div>${esc(l)}</div>`).join("");
  const adj = s.adjutant || {};
  $("adj-summary").innerHTML = esc(adj.summary || "尚無簡報。") + (adj.tick !== undefined ? ` <span class="dim">（T${adj.tick}）</span>` : "");
  $("adj-sugs").innerHTML = (adj.suggestions || []).map((g, i) => {
    const sq = squadById(g.squad);
    return `<button class="sug" data-i="${i}" ${readonly() ? "disabled" : ""}>▸ ${esc(g.label)} <span class="dim">→ ${esc(sq ? sq.callsign : "")}：${esc(g.order)}</span></button>`;
  }).join("");
  for (const b of $("adj-sugs").querySelectorAll("button")) b.onclick = () => useSuggestion(+b.dataset.i);
  const ms = s.missions;
  let html = ms.active.map((m) => {
    const left = m.left === null ? "無時限" : `剩 ${Math.floor(secs(m.left) / 60)} 分 ${secs(m.left) % 60} 秒`;
    const prog = m.progress ? `<div>守住進度 ${m.progress[0]}/${m.progress[1]}</div>` : "";
    const rem = m.remnant ? `<div class="amber">${esc(m.remnant)}</div>` : "";
    return `<div class="mission ${m.line}"><div class="mt">${m.line === "main" ? "主線" : "支線"}｜${esc(m.title)}</div>
      <div class="small">${esc(m.brief)}</div><div class="dim small">${esc(m.cell_name)}｜${left}</div>${prog}${rem}</div>`;
  }).join("") || `<div class="dim">目前沒有任務。</div>`;
  if (ms.history.length) {
    html += `<div class="panel-title small">最近結果</div>` + ms.history.map((h) =>
      `<div class="small">${h.success ? "✅" : "❌"} ${esc(h.title)}${!h.success && h.reason ? "：" + esc(h.reason) : ""}（T${h.t}）</div>`).join("");
  }
  $("missions").innerHTML = html;
}

async function useSuggestion(i) {
  const d = await api("/api/suggestion", { index: i });
  if (!d.ok) toast(d.error || "建議已失效", true);
}
$("adj-refresh").onclick = async () => { await api("/api/adjutant", {}); toast("副官整理中……"); };

// [按鈕, 行動, 目標]；@ 開頭＝在作戰圖點選目標
const QUICK_FIELD = [
  ["固守", "hold"], ["隱蔽", "hide"], ["回報", "report"],
  ["移動…", "@move"], ["偵察…", "@scout"], ["撤退…", "@retreat"],
];
const QUICK_COMBAT = [
  ["固守", "hold"], ["推進", "advance"], ["壓制推進", "supp_advance"],
  ["左包抄", "flank", "left"], ["右包抄", "flank", "right"], ["突擊", "assault"], ["伏擊", "hide"],
  ["破門", "breach"], ["撤退", "retreat"], ["回報", "report"],
];

async function quickOrder(sid, action, target, via) {
  const d = await api("/api/quick", { squad: sid, action, target: target || null, via: via || [] });
  if (!d.ok) toast(d.error || "命令沒有送達（失聯？）", true);
}

function radioItems(sq) {
  const out = [];
  for (const d of sq.radio || []) {
    if (d.who === "ally") { out.push({ t: d.t, k: 3, cls: "ally", text: `📻 ${d.text}` }); continue; }
    out.push({ t: d.t, k: d.who === "commander" ? 0 : 2, cls: d.who === "commander" ? "cmd" : "leader",
      text: d.who === "commander" ? `▶ ${d.text}` : `${sq.leader}：${d.text}` });
  }
  for (const it of G.extras[sq.id] || []) {
    if (it.type === "receipt") {
      const r = it.receipt;
      const mark = r.mark ? `｜標定 ${r.mark.pos.join(",")}` : "";
      out.push({ t: it.t, k: 1, cls: "receipt", text: `［回執］${r.label}${r.understanding ? "｜理解：" + r.understanding : ""}｜行動：${r.action}${r.low_confidence ? "（不太確定）" : ""}${r.autonomy ? "｜自主等級→" + r.autonomy : ""}${mark}` });
    } else if (it.type === "system") out.push({ t: it.t, k: 3, cls: "sys", text: it.text });
    else if (it.type === "auto") out.push({ t: it.t, k: 3, cls: "auto", text: `［自主］${it.action}：${it.reason}` });
    else if (it.type === "proposal") out.push({ t: it.t, k: 3, cls: "auto", text: `［提議］${it.proposal.text}` });
    else if (it.type === "support") out.push({ t: it.t, k: 3, cls: "support", text: `［支援］${it.text}` });
    else if (it.type === "local_order") out.push({ t: it.t, k: 0, cls: "cmd", text: `▶ ${it.text}` });
  }
  out.sort((a, b) => a.t - b.t || a.k - b.k);
  return out.slice(-80);
}

function bar(v, max, cls) {
  const p = Math.max(0, Math.min(100, Math.round(100 * v / Math.max(1, max))));
  const lvl = p < 25 ? "crit" : p < 50 ? "low" : "";
  return `<div class="bar ${cls || ""} ${lvl}"><b style="width:${p}%"></b></div>`;
}

function memberRow(sq, m) {
  const key = `${sq.id}|${m.name}`;
  const icons = `${m.leading ? "★" : ""}${m.rookie ? "🆕" : ""}`;
  const state = m.down ? (m.stabilized ? "倒地(穩定)" : "倒地") : (m.suppression !== "無" ? m.suppression : m.wounds.length ? "負傷" : "");
  let html = `<div class="mem ${m.down ? "down" : ""}" data-key="${esc(key)}">
    <span>${icons}${esc(m.name)}</span><span class="dim">${esc(m.weapon.label)}</span>
    <span title="完好度 ${m.hp}">${bar(m.hp, 100)}</span><span title="彈藥 ${m.ammo}/${m.ammo_cap}">${bar(m.ammo, m.ammo_cap, "ammo")}</span>
    <span class="small">💣${m.grenades}☁${m.smokes}✦${m.flashes} ${esc(state)}</span></div>`;
  if (G.expanded[key]) {
    const w = m.weapon;
    const other = Object.values(m.prof_other || {}).map((p) => `${p.label} ${p.level}(${p.value})`).join("、") || "—";
    const tac = m.stats ? `<tr><td>本場</td><td>開火 ${m.stats.shots}｜命中 ${m.stats.hits}｜擊倒 ${m.stats.kills}${m.braced ? "｜架槍中" : ""}${m.dazzled ? "｜光學過載" : ""}</td></tr>` : "";
    const pos = m.tpos ? `${sq.pos} 的 (${m.tpos[0]},${m.tpos[1]})` : sq.pos;
    html += `<div class="mem-detail"><table>
      <tr><td>武器</td><td>${esc(w.label)}（${esc(w.caliber)}）射程 ${w.range}m｜精度 ×${w.accuracy}｜連發 ${w.burst || "—"}</td></tr>
      <tr><td>彈藥</td><td>${m.ammo}/${m.ammo_cap} 發｜破片 ${m.grenades}｜煙霧 ${m.smokes}｜閃光 ${m.flashes}</td></tr>
      <tr><td>狀態</td><td>完好度 ${m.hp}｜${m.wounds.length ? "受傷：" + esc(m.wounds.join("、")) : "無外傷"}｜壓制：${esc(m.suppression)}｜姿勢：${esc(m.posture)}</td></tr>
      <tr><td>熟練度</td><td>${esc(w.label)} ${m.prof.level}（${m.prof.value}）｜其他：${esc(other)}</td></tr>
      <tr><td>經歷</td><td>參戰 ${m.battles} 場${m.led_before ? "｜曾帶隊" : ""}${m.rookie ? "｜新兵" : ""}</td></tr>
      <tr><td>位置</td><td>${esc(pos)}</td></tr>${tac}</table></div>`;
  }
  return html;
}

function renderSquads() {
  if (!G.state) return;
  const ro = readonly();
  const box = $("squads");
  const focused = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.radio;
  const draft = {};
  for (const inp of box.querySelectorAll("input[data-radio]")) draft[inp.dataset.radio] = inp.value;
  const scrolls = {};
  for (const el of box.querySelectorAll(".radio-log")) scrolls[el.dataset.sid] = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
  if (!G.selected && G.state.squads.length) G.selected = G.state.squads.find((s) => s.status === "active")?.id || G.state.squads[0].id;
  box.innerHTML = G.state.squads.map((sq) => {
    const dead = sq.status !== "active";
    const tags = [];
    if (dead) tags.push(`<span class="sq-tag red">全滅</span>`);
    if (sq.in_combat) tags.push(`<span class="sq-tag combat">⚔ 交戰中</span>`);
    if (sq.stale) tags.push(`<span class="sq-tag offline">📵 失聯｜T${sq.tick} 的資料</span>`);
    if (sq.action.auto) tags.push(`<span class="sq-tag auto">自主行動</span>`);
    tags.push(`<span class="sq-tag">${esc(sq.autonomy_label)}</span>`);
    const act = esc(sq.action.text) + (sq.action.auto ? ` <span class="amber">（自主：${esc(sq.action.reason)}）</span>` +
      (ro ? "" : ` <button class="tiny warn" data-act="cancel" data-sid="${esc(sq.id)}">取消</button>`) : "");
    const prop = sq.proposal ? `<div class="proposal">提議：${esc(sq.proposal.text)} ${ro ? "" :
      `<button class="tiny primary" data-act="accept" data-sid="${esc(sq.id)}">同意</button>`}</div>` : "";
    const auto = ro ? "" : `<div class="autonomy row">自主：${["standby", "defend", "free"].map((lv) =>
      `<button class="${sq.autonomy === lv ? "on" : ""}" data-act="autonomy" data-lv="${lv}" data-sid="${esc(sq.id)}">${{ standby: "待命", defend: "自衛", free: "自由" }[lv]}</button>`).join("")}</div>`;
    const quick = (sq.in_combat ? QUICK_COMBAT : QUICK_FIELD).map(([label, action, target]) =>
      `<button data-act="quick" data-sid="${esc(sq.id)}" data-action="${action}" data-target="${target || ""}" ${ro || dead || sq.stale ? "disabled" : ""}>${label}</button>`).join("");
    const typing = (G.typing[sq.id] && performance.now() - G.typing[sq.id] < 30000)
      ? `<div class="rl typing">${esc(sq.leader)} 回覆中<span class="dots">…</span></div>` : "";
    const radio = radioItems(sq).map((r) => `<div class="rl ${r.cls}"><span class="t">T${r.t}</span>${esc(r.text)}</div>`).join("") + typing;
    return `<div class="squad ${G.selected === sq.id ? "selected" : ""} ${sq.stale ? "stale" : ""}" data-sid="${esc(sq.id)}">
      <div class="sq-head" data-act="select" data-sid="${esc(sq.id)}">
        <span class="sq-name">${esc(sq.callsign)}</span><span>${esc(sq.pos)} ${esc(sq.pos_name)}</span>${tags.join("")}
        <span class="spacer"></span><span class="dim">帶隊 ${esc(sq.leader)}｜${sq.alive}/${sq.total} 人｜彈藥 ${sq.ammo}%｜士氣 ${esc(sq.morale_label)}</span>
      </div>
      <div class="sq-body">
        <div class="sq-status">
          <div>行動：${act}</div>${orderLine(sq)}
          ${sq.rest ? `<div class="dim">🔧 ${esc(sq.rest)}</div>` : ""}
          ${sq.medevac ? `<div class="small" style="color:var(--blue)">🚑 友軍正在趕來後送</div>`
            : sq.members.some((m) => m.down) && !sq.in_combat ? `<div class="amber small">🩹 帶著倒地的隊員（${esc(sq.members.filter((m) => m.down).map((m) => m.name).join("、"))}）行軍放慢，回 A3 整補後歸隊</div>` : ""}
          ${(sq.away || []).length ? `<div class="dim small">🏥 後送中／在 A3 休養：${esc(sq.away.join("、"))}（回 A3 整補時歸隊）</div>` : ""}
          ${prop}${auto}
          <div class="row">
            <button data-act="tac" data-sid="${esc(sq.id)}" ${sq.stale ? "disabled" : ""}>🗺 戰術畫面</button>
            ${ro ? "" : `<button data-act="supply" data-sid="${esc(sq.id)}" ${dead ? "disabled" : ""}>📦 請求補給</button>`}
            ${ro || !sq.members.some((m) => m.down) ? "" : `<button data-act="medevac" data-sid="${esc(sq.id)}" ${dead ? "disabled" : ""} title="派友軍把倒地的隊員拖回 A3；原小隊下次回 A3 整補時歸隊">🚑 請求後送</button>`}
          </div>
        </div>
        <div class="members">${sq.members.map((m) => memberRow(sq, m)).join("") || `<div class="dim">（無人）</div>`}</div>
        <div class="radio">
          <div class="radio-log" data-sid="${esc(sq.id)}">${radio || `<div class="dim">（無線電靜默）</div>`}</div>
          ${ro ? "" : `<div class="radio-input"><input data-radio="${esc(sq.id)}" placeholder="對 ${esc(sq.callsign)} 下令（Enter 送出）" ${dead ? "disabled" : ""}>
            <button data-act="send" data-sid="${esc(sq.id)}" ${dead ? "disabled" : ""}>送出</button></div>
          <div class="quick">${quick}</div>`}
        </div>
      </div></div>`;
  }).join("") || `<div class="panel dim">沒有小隊。</div>`;
  for (const inp of box.querySelectorAll("input[data-radio]")) {
    if (draft[inp.dataset.radio]) inp.value = draft[inp.dataset.radio];
    if (focused === inp.dataset.radio) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") sendOrder(inp.dataset.radio, inp.value, inp); });
  }
  for (const el of box.querySelectorAll(".radio-log")) {
    if (scrolls[el.dataset.sid] !== false) el.scrollTop = el.scrollHeight;
  }
  for (const el of box.querySelectorAll("[data-act]")) el.onclick = (e) => squadAction(el, e);
  for (const el of box.querySelectorAll(".mem")) el.onclick = () => { G.expanded[el.dataset.key] = !G.expanded[el.dataset.key]; renderSquads(); };
}

// 最近一道命令的狀態（engine/orders.py）：送出 → 隊長思考中 → 執行中／拒絕… → 完成／中斷（原因）；結束的狀態顯示 2 個作戰 tick
// （和 web/tac/src/intentplan.ts 的 orderLabel 同一套規則，戰術圖小隊旁也有）
const ORDER_STATE = { sent: ["送出", "dim"], thinking: ["隊長思考中…", ""], executing: ["執行中", "ok"], done: ["完成", "ok"],
  interrupted: ["中斷", "amber"], refused: ["拒絕", "red"], unclear: ["無法對應", "red"], failed: ["無法執行", "red"],
  kept: ["維持原行動", "amber"], confirm: ["要求確認", "amber"], info: ["回應", "dim"] };
function orderLine(sq) {
  const o = sq.order;
  if (!o || !G.state) return "";
  const ongoing = ["sent", "thinking", "executing"].includes(o.status);
  if (!ongoing && G.state.tick - o.since > 2) return "";
  const [name, cls] = ORDER_STATE[o.status] || [o.status, "dim"];
  const what = o.status === "executing" ? (o.understanding || o.action || o.text)
    : ongoing ? o.text : (o.reason || o.label);
  const color = cls === "ok" ? ' style="color:var(--own)"' : "";
  return `<div class="small ${cls === "ok" ? "" : cls}"${color}>📋 命令${name}${what ? "：" + esc(what) : ""}</div>`;
}

async function sendOrder(sid, text, inp) {
  text = (text || "").trim();
  if (!text) return;
  if (inp) inp.value = "";
  const sq = squadById(sid);
  if (sq) {
    (G.extras[sid] = G.extras[sid] || []).push({ type: "local_order", t: G.state.tick, text, local: true });
    G.typing[sid] = performance.now();
    renderSquads();
  }
  const d = await api("/api/order", { squad: sid, text });
  if (!d.ok) { delete G.typing[sid]; toast(d.error || "命令沒有送達（失聯？）", true); renderSquads(); }
}

async function squadAction(el, e) {
  const sid = el.dataset.sid;
  const act = el.dataset.act;
  if (act === "select") {
    G.selected = sid; renderSquads();
    if (typeof dockFollow === "function") dockFollow(sid);
    return;
  }
  e.stopPropagation();
  if (act === "send") { const inp = document.querySelector(`input[data-radio="${CSS.escape(sid)}"]`); sendOrder(sid, inp.value, inp); }
  else if (act === "quick") {
    const a = el.dataset.action;
    if (a.startsWith("@")) { startOpTool(a.slice(1), sid); return; }
    quickOrder(sid, a, el.dataset.target || null);
  }
  else if (act === "cancel") { await api("/api/cancel_auto", { squad: sid }); }
  else if (act === "accept") { await api("/api/accept", { squad: sid }); }
  else if (act === "autonomy") { await api("/api/autonomy", { squad: sid, level: el.dataset.lv }); }
  else if (act === "medevac") { const d = await api("/api/medevac", { squad: sid }); toast(d.ok ? d.result.message : d.error, !d.ok); }
  else if (act === "supply") { const d = await api("/api/supply", { squad: sid }); toast(d.result ? d.result.message : d.error, !(d.result && d.result.ok)); }
  else if (act === "tac") {
    const sq = squadById(sid); G.selected = sid;
    if (typeof DOCK !== "undefined" && DOCK.on && !DOCK.out) { dockFollow(sid); renderSquads(); return; }
    openTactical(sq.pos);
  }
}

// ------------------------------------------------------------------ 作戰圖

const OP = { c: $("opmap"), size: 104 };
OP.ctx = OP.c.getContext("2d");

function cellXY(id) {
  const m = G.state.map;
  const [r, c] = cellIndex(m.row_names, m.col_names, id);
  return { x: c * OP.size, y: (m.rows - 1 - r) * OP.size };
}

function cellAt(px, py) {
  const m = G.state.map;
  const c = Math.floor(px / OP.size), rr = Math.floor(py / OP.size);
  const r = m.rows - 1 - rr;
  if (c < 0 || c >= m.cols || r < 0 || r >= m.rows) return null;
  return m.row_names[r] + m.col_names[c];
}

function startOpTool(kind, sid) {
  const sq = squadById(sid);
  if (sq.in_combat && kind !== "retreat" && !(G.state.zone_mode && kind === "move")) { toast("交戰中只能撤退到相鄰格", true); return; }
  G.opTool = { kind, squad: sid, via: [] };
  updateOpHint();
  if (typeof DOCK !== "undefined" && DOCK.on) opPopOpen(true);
}

function opToolDone() {
  if (typeof DOCK !== "undefined" && DOCK.on && DOCK.opAuto) opPopClose();
}

function updateOpHint() {
  const t = G.opTool;
  if (!t) { $("op-hint").innerHTML = ""; return; }
  const sq = squadById(t.squad);
  const label = { move: "移動", scout: "偵察", retreat: "撤退" }[t.kind];
  if (t.kind === "move") {
    $("op-hint").innerHTML = `${esc(sq.callsign)} ${label}：依序點經過格與目的地 ${esc(t.via.join("→"))}
      <button class="tiny primary" id="op-go" ${t.via.length ? "" : "disabled"}>送出</button> <button class="tiny" id="op-cancel">取消</button>`;
    $("op-go").onclick = () => finishMove();
  } else {
    $("op-hint").innerHTML = `${esc(sq.callsign)} ${label}：點一個相鄰格 <button class="tiny" id="op-cancel">取消</button>`;
  }
  $("op-cancel").onclick = () => { G.opTool = null; updateOpHint(); opToolDone(); };
}

function finishMove() {
  const t = G.opTool;
  const dest = t.via[t.via.length - 1];
  const via = t.via.slice(0, -1);
  quickOrder(t.squad, "move", dest, via);
  G.opTool = null;
  updateOpHint();
  opToolDone();
}

OP.c.addEventListener("mousemove", (e) => { const r = OP.c.getBoundingClientRect(); G.hoverCell = cellAt(e.clientX - r.left, e.clientY - r.top); });
OP.c.addEventListener("mouseleave", () => { G.hoverCell = null; });
OP.c.addEventListener("click", (e) => {
  if (!G.state) return;
  const r = OP.c.getBoundingClientRect();
  const id = cellAt(e.clientX - r.left, e.clientY - r.top);
  if (!id) return;
  const t = G.opTool;
  if (t) {
    const sq = squadById(t.squad);
    if (t.kind === "move") {
      if (t.via[t.via.length - 1] === id) { finishMove(); return; }
      if (id !== sq.pos) t.via.push(id);
      updateOpHint();
      return;
    }
    if (!adjacent(sq.pos, id)) { toast("要選相鄰的格子", true); return; }
    quickOrder(t.squad, t.kind, id);
    G.opTool = null;
    updateOpHint();
    opToolDone();
    return;
  }
  showCellInfo(id);
  if (typeof dockLook === "function" && DOCK.on && !DOCK.out) dockLook(id);
});

function showCellInfo(id) {
  const s = G.state, m = s.map;
  const c = m.cells.find((x) => x.id === id);
  const foe = m.enemies.find((e) => e.cell === id);
  const mine = s.squads.filter((q) => q.pos === id && q.status === "active").map((q) => q.callsign);
  const allies = m.allies.filter((a) => a.pos === id).map((a) => `${a.label}(${a.alive})`);
  const mis = m.missions.filter((x) => x.cell === id).map((x) => x.title);
  const combat = m.combat.includes(id);
  const coming = m.allies.filter((a) => a.computer && a.task === "support" && a.goal === id).map((a) => a.label);
  const asked = m.allies.find((a) => a.computer && a.mine && a.task === "support");
  const canAsk = !readonly() && m.allies.some((a) => a.computer && !a.hot && (a.task === "guard" || a.task === "return")) && !asked;
  $("cell-info").innerHTML = `<b>${esc(id)} ${esc(c.name)}</b>｜${esc(c.terrain)}｜掩蔽 ${esc(c.cover)}${c.base ? "｜後方據點（整補）" : ""}${c.enemy_base ? "｜敵方據點" : ""}<br>
    ${mine.length ? "我方：" + esc(mine.join("、")) + "　" : ""}${allies.length ? "友軍：" + esc(allies.join("、")) + "　" : ""}
    ${foe ? `敵情：約 ${foe.est}${foe.suspected ? "（疑似）" : ""}，${secs(foe.age)} 秒前` : "敵情：無確切情報"}
    ${mis.length ? "<br>任務：" + esc(mis.join("、")) : ""}
    ${coming.length ? "<br>🤝 友軍 " + esc(coming.join("、")) + " 正在支援這一格" : ""}
    ${(mine.length || G.me.role === "gm") ? ` <button class="tiny" id="ci-tac">🗺 戰術畫面${combat ? "（交戰中）" : ""}</button>` : ""}
    ${!readonly() && !mine.length ? ` <button class="tiny" id="ci-drone">🛩 派無人機偵察</button>` : ""}
    ${canAsk ? ` <button class="tiny" id="ci-ally" title="派一支空閒的友方電腦小隊到這一格，守一陣子（有交戰就壓制推進）">🤝 請求友軍支援</button>` : ""}
    ${asked && !readonly() ? `<br><span class="dim small">🤝 你請的友軍 ${esc(asked.label)} 正在支援 ${esc(asked.goal)}</span>` : ""}`;
  const b = $("ci-tac");
  if (b) b.onclick = () => openTactical(id, G.me.role === "gm" && !mine.length);
  const d = $("ci-drone");
  if (d) d.onclick = async () => { const r = await api("/api/support", { kind: "drone", cell: id, point: [12, 12] }); toast(r.ok ? r.message : r.error, !r.ok); };
  const al = $("ci-ally");
  if (al) al.onclick = async () => { const r = await api("/api/ally_support", { cell: id }); toast(r.ok ? r.message : r.error, !r.ok); if (r.ok) reloadState(); };
}

function drawOp(now) {
  if (!G.state || $("hq").classList.contains("hidden")) return;
  const ctx = OP.ctx, S = OP.size, m = G.state.map;
  ctx.clearRect(0, 0, OP.c.width, OP.c.height);
  for (const c of m.cells) {
    const p = cellXY(c.id);
    ctx.fillStyle = c.base ? "#10251a" : c.enemy_base ? "#2a1212" : "#0b120d";
    ctx.fillRect(p.x + 1, p.y + 1, S - 2, S - 2);
    if (G.hoverCell === c.id) { ctx.strokeStyle = "#3d6b48"; ctx.strokeRect(p.x + 1.5, p.y + 1.5, S - 3, S - 3); }
    ctx.fillStyle = "#4f7a5a"; ctx.font = "11px monospace";
    ctx.fillText(c.id, p.x + 5, p.y + 13);
    ctx.fillStyle = "#3a5a44"; ctx.font = "10px sans-serif";
    ctx.fillText(c.name.slice(0, 8), p.x + 5, p.y + S - 6);
  }
  if (G.opTool && G.opTool.kind === "move") {
    const sq = squadById(G.opTool.squad);
    let prev = cellXY(sq.pos);
    ctx.strokeStyle = "#e8b04a"; ctx.setLineDash([6, 4]); ctx.lineWidth = 2;
    for (const id of G.opTool.via) { const p = cellXY(id); ctx.beginPath(); ctx.moveTo(prev.x + S / 2, prev.y + S / 2); ctx.lineTo(p.x + S / 2, p.y + S / 2); ctx.stroke(); prev = p; }
    ctx.setLineDash([]); ctx.lineWidth = 1;
  }
  for (const id of m.combat) { const p = cellXY(id); ctx.strokeStyle = `rgba(224,88,77,${0.5 + 0.4 * Math.sin(now / 250)})`; ctx.lineWidth = 3; ctx.strokeRect(p.x + 3, p.y + 3, S - 6, S - 6); ctx.lineWidth = 1; ctx.fillStyle = "#e0584d"; ctx.fillText("⚔", p.x + S - 18, p.y + 14); }
  for (const x of m.missions) { const p = cellXY(x.cell); ctx.fillStyle = x.line === "main" ? "#e8b04a" : "#5ab4e8"; ctx.font = "16px sans-serif"; ctx.fillText("★", p.x + S - 20, p.y + S - 8); }
  for (const x of m.drops) { const p = cellXY(x.cell); ctx.fillStyle = "#5ab4e8"; ctx.font = "16px sans-serif"; ctx.fillText("↓", p.x + S / 2 - 4, p.y + 18); }
  for (const e of m.enemies) {
    const p = cellXY(e.cell);
    const a = Math.max(0.25, 1 - e.age / 60);
    ctx.fillStyle = e.suspected ? `rgba(160,100,60,${a})` : `rgba(224,88,77,${a})`;
    ctx.font = "bold 14px monospace";
    ctx.fillText(`${e.suspected ? "?" : "!"}${e.est}`, p.x + S - 34, p.y + S / 2 + 5);
  }
  const groups = {};
  for (const a of m.allies) (groups[a.pos] = groups[a.pos] || []).push({ ally: true, label: a.label });
  for (const sq of G.state.squads) if (sq.status === "active") (groups[sq.pos] = groups[sq.pos] || []).push({ sq });
  const seen = new Set();
  for (const [cell, list] of Object.entries(groups)) {
    const p = cellXY(cell);
    list.forEach((it, i) => {
      let tx = p.x + 22 + (i % 3) * 26, ty = p.y + 36 + Math.floor(i / 3) * 26;
      if (it.sq && it.sq.gpos && G.state.zone_mode) {
        const span = 25 * m.cols;
        tx = it.sq.gpos[0] / span * m.cols * S;
        ty = (m.rows * 25 - it.sq.gpos[1]) / (m.rows * 25) * m.rows * S;
      }
      if (it.ally) {
        ctx.fillStyle = "#7fd4c4"; ctx.beginPath(); ctx.arc(tx, ty, 8, 0, 7); ctx.fill();
        ctx.fillStyle = "#06120c"; ctx.font = "bold 10px monospace"; ctx.fillText(it.label[0], tx - 3, ty + 4);
        return;
      }
      const sq = it.sq;
      seen.add(sq.id);
      const cur = G.opPos[sq.id] || { x: tx, y: ty };
      cur.x += (tx - cur.x) * 0.08; cur.y += (ty - cur.y) * 0.08;
      G.opPos[sq.id] = cur;
      if (sq.action.path && sq.action.path.length) {
        ctx.strokeStyle = sq.action.auto ? "#e8b04a" : "#6fe37f"; ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(cur.x, cur.y);
        for (const id of sq.action.path) { const q = cellXY(id); ctx.lineTo(q.x + S / 2, q.y + S / 2); }
        ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.fillStyle = sq.stale ? "#8a7a4a" : "#6fe37f";
      ctx.beginPath(); ctx.arc(cur.x, cur.y, 10, 0, 7); ctx.fill();
      if (G.selected === sq.id) { ctx.strokeStyle = "#fff"; ctx.beginPath(); ctx.arc(cur.x, cur.y, 13, 0, 7); ctx.stroke(); }
      ctx.fillStyle = "#06120c"; ctx.font = "bold 11px monospace"; ctx.fillText(sq.callsign[0], cur.x - 4, cur.y + 4);
      if (sq.action.auto) { ctx.fillStyle = "#e8b04a"; ctx.fillText("A", cur.x + 9, cur.y - 7); }
    });
  }
}

// ------------------------------------------------------------------ 戰術畫面

const TC = { c: $("tacmap") };
TC.ctx = TC.c.getContext("2d");
const TERRAIN_COLOR = { "#": "#2b362e", "W": "#2a4458", "|": "#2b362e", "=": "#55603e", "%": "#4d4232", '"': "#1d3a20", "H": "#555" };
function animMs() {
  const st = G.state;
  const s = st && st.step_seconds ? st.step_seconds : 2.5;
  return Math.max(300, s * 1000 * 0.92);
}

async function openTactical(cell, gm) {
  if (G.state && G.state.zone_mode) return openWorld(cell, gm);
  G.tac = { cell, gm: !!gm, meta: null, frame: null, frameAt: 0, log: [], tool: null, replay: null, unit: null, markSquad: null };
  $("tac").classList.remove("hidden");
  $("tac-replay").classList.add("hidden");
  $("tac-log").innerHTML = "";
  $("tac-unit").innerHTML = "";
  if (gm && G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify({ type: "gm_cell", cell }));
  await refreshTacMeta(true);
}

async function refreshTacMeta(first) {
  const t = G.tac;
  if (!t || t.replay) return;
  const q = [];
  if (t.gm) q.push("gm=1");
  if (G.watch && G.watch !== G.me.id) q.push(`watch=${encodeURIComponent(G.watch)}`);
  const d = await api(`/api/tactical/${t.cell}${q.length ? "?" + q.join("&") : ""}`);
  if (!d.ok) { if (first) { toast(d.error, true); closeTac(); } return; }
  t.meta = d.tactical;
  if (d.tactical.frame && first) setFrame(d.tactical.frame, true);
  renderTacTools();
}

function closeTac() {
  if (G.tac && G.tac.gm && G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify({ type: "gm_cell", cell: null }));
  if (G.tac && G.tac.world && G.tac.gm && G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify({ type: "gm_truth", on: false }));
  G.tac = null;
  $("tac").classList.add("hidden");
  if (typeof DOCK !== "undefined" && DOCK.on) redock();
}
$("tac-close").onclick = closeTac;

function onTactical(frames) {
  const t = G.tac;
  if (!t || t.replay) return;
  const f = frames[t.cell];
  if (!f) return;
  if (!t.meta || !t.meta.combat) refreshTacMeta();
  setFrame(f);
}

function setFrame(f, instant) {
  const t = G.tac;
  if (t.frame && t.frame.t === f.t && !instant) return;
  if (!t.replay) f._logPrefix = f.ts0 != null ? battleClock(f.ts0) : `T${f.t}`;
  enqueueFrame(t, f, instant);
  if (!t.replay) G.lastStep = performance.now();
}

// 有結構化事件 → 用事件產生的紀錄（HTML）；沒有 → 舊的文字事件
function frameLogLines(f, prefix, ctx) {
  const lines = typeof evLogLines === "function" ? evLogLines(f, prefix, ctx) : null;
  if (lines) return lines;
  return (f.events || []).map((e) => `<div>${esc(prefix + " " + e)}</div>`);
}

function pushFrameLog(t, f, prefix) {
  for (const l of frameLogLines(f, prefix, t.logCtx || (t.logCtx = {}))) t.log.push(l);
  if (t.log.length > 300) t.log.splice(0, t.log.length - 300);
}

function renderTacLog(lines) {
  const el = $("tac-log");
  const list = lines || (G.tac ? G.tac.log : []);
  el.innerHTML = list.slice(-200).join("");
  el.scrollTop = el.scrollHeight;
}

function ownSquadsInCell() {
  if (!G.tac || !G.state) return [];
  return G.state.squads.filter((s) => s.pos === G.tac.cell && s.status === "active" && !s.stale);
}

function renderTacTools() {
  const t = G.tac;
  if (!t) return;
  if (t.world) return renderWorldTools();
  const title = t.replay ? `回放｜${t.replay.meta.cell}（T${t.replay.meta.start_tick}～T${t.replay.meta.end_tick}）`
    : `${t.meta ? t.meta.name : t.cell}${t.gm ? "（GM 真相）" : ""}${t.meta && t.meta.combat ? "｜⚔ 交戰中" : ""}`;
  $("tac-title").textContent = title;
  const own = ownSquadsInCell();
  if (t.replay || readonly() || !own.length) { $("tac-tools").innerHTML = ""; return; }
  if (!t.markSquad || !own.some((s) => s.id === t.markSquad)) t.markSquad = (own.find((s) => s.id === G.selected) || own[0]).id;
  const sel = own.length > 1 ? `<select id="tt-squad">${own.map((s) => `<option value="${esc(s.id)}" ${s.id === t.markSquad ? "selected" : ""}>${esc(s.callsign)}</option>`).join("")}</select>` : "";
  const tool = (k, label, title) => `<button class="tool ${t.tool === k ? "on" : ""}" data-tool="${k}" title="${esc(title)}">${label}</button>`;
  $("tac-tools").innerHTML = `${sel}<span class="dim">標定</span>${tool("rally", "🚩集結", "要小隊到這一點（仍經隊長判斷）")}${tool("flank", "↪包抄", "從這一點方向包抄")}${tool("fire", "🎯集火", "優先打這附近的敵人")}
    <span class="dim">支援</span>${tool("airdrop", "📦空投", "約 5 秒後落地，有散布；附近的人自動補給")}${tool("smoke", "☁煙幕", "約 2.5 秒後拉一道煙牆")}${tool("drone", "🛩無人機", "揭露半徑約 16m 內的敵人約 20 秒，可能被擊落")}`;
  for (const b of $("tac-tools").querySelectorAll("button[data-tool]")) b.onclick = () => { t.tool = t.tool === b.dataset.tool ? null : b.dataset.tool; renderTacTools(); };
  const s = $("tt-squad");
  if (s) s.onchange = () => { t.markSquad = s.value; };
}

function tacGeom() {
  const t = G.tac;
  const meta = t.replay ? t.replay.meta : t.meta;
  if (!meta) return null;
  const s = Math.floor(Math.min(TC.c.width / meta.w, TC.c.height / meta.h));
  return { meta, s, w: meta.w, h: meta.h };
}

TC.c.addEventListener("click", async (e) => {
  const t = G.tac;
  if (!t) return;
  if (t.world) return;                         // 連續地圖的點選由新畫面處理（tacbridge.js）
  const g = tacGeom();
  if (!g) return;
  const r = TC.c.getBoundingClientRect();
  const px = (e.clientX - r.left) * (TC.c.width / r.width), py = (e.clientY - r.top) * (TC.c.height / r.height);
  const x = Math.floor(px / g.s), y = g.h - 1 - Math.floor(py / g.s);
  if (x < 0 || y < 0 || x >= g.w || y >= g.h) return;
  if (t.tool && !t.replay) {
    const k = t.tool;
    t.tool = null;
    renderTacTools();
    if (["rally", "flank", "fire"].includes(k)) {
      const d = await api("/api/mark", { squad: t.markSquad, kind: k, point: [x, y] });
      if (!d.ok) toast(d.error || "命令沒有送達", true);
      else if (d.result && d.result.receipt) toast(`回執：${d.result.receipt.label}｜${d.result.receipt.action}`);
    } else {
      const d = await api("/api/support", { kind: k, cell: t.cell, point: [x, y] });
      toast(d.ok ? d.message : d.error, !d.ok);
    }
    return;
  }
  const units = currentUnits();
  let best = null, bd = 1.2;
  for (const u of units) { const d = Math.hypot(u.x - (x + 0.5), u.y - (y + 0.5)); if (d < bd) { bd = d; best = u; } }
  t.unit = best ? best.id : null;
  renderTacUnit(best);
});

// 樓層：pos[2]（0＝地面）。目前只有中繼塔平台有二樓；俯視圖上樓上樓下會畫在同一點，所以樓上的人另外標示。
function unitLevel(u) { return (u && u.pos && u.pos[2]) || 0; }
function byLevel(units) { return units.map((u, i) => [u, i]).sort((a, b) => unitLevel(a[0]) - unitLevel(b[0]) || a[1] - b[1]).map((x) => x[0]); }
function drawUpstairs(ctx, u, px, py, r) {
  if (unitLevel(u) <= 0) return;
  ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5;
  ctx.strokeRect(px - r - 2, py - r - 2, 2 * r + 4, 2 * r + 4);
  ctx.lineWidth = 1; ctx.fillStyle = "#fff"; ctx.font = "9px monospace";
  ctx.fillText(`${unitLevel(u) + 1}F`, px - r - 2, py - r - 4);
}

function renderTacUnit(u) {
  if (!u) { $("tac-unit").innerHTML = ""; return; }
  const floor = unitLevel(u) > 0 ? `｜在 ${unitLevel(u) + 1} 樓` : "";
  if (u.side === "enemy") { $("tac-unit").innerHTML = `<b class="red">敵兵</b>｜${esc(u.weapon || "")}｜姿勢 ${esc(u.posture || "")}${u.down ? "｜倒地" : ""}${floor}`; return; }
  const now = typeof unitStatusHtml === "function" && !u.down ? unitStatusHtml(u) : "";
  for (const sq of (G.state && G.state.squads) || []) {
    const m = sq.members.find((x) => x.name === u.name);
    if (m) { $("tac-unit").innerHTML = `<b>${esc(sq.callsign)}</b>${floor}` + now + memberRowStatic(sq, m); return; }
  }
  $("tac-unit").innerHTML = `<b>${esc(u.name)}</b>（${u.side === "ally" ? "友軍" : u.side === "own" ? "我方" : "殘部"}）${u.down ? "｜倒地" : ""}${floor}${now}`;
}

function memberRowStatic(sq, m) {
  const key = `${sq.id}|${m.name}`;
  const was = G.expanded[key];
  G.expanded[key] = true;
  const html = memberRow(sq, m);
  G.expanded[key] = was;
  return html;
}

function lerpPath(pts, q) {
  if (pts.length === 1) return pts[0];
  const n = pts.length - 1;
  const f = Math.min(n, q * n);
  const i = Math.min(n - 1, Math.floor(f));
  const k = f - i;
  return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k];
}

// ------------------------------------------------------------------ 播放時鐘（模擬重構第 11 步）
// 每格影格涵蓋一段戰鬥時間（ts0～ts1）；路徑每格、子彈、爆炸、事件都帶自己的時間（秒），照實播放。
// 即時：新影格排進佇列，等正在播的那格播完才接著播（延後一個更新，動作連續不跳）；每格的播放長度跟著實際收到的間隔走
// （伺服器變慢就跟著慢），佇列積太多就稍微加速追上。回放：每格長度＝它涵蓋的戰鬥時間 ÷ 播放速度。

function frameDur(f) {
  const t = G.tac;
  const span = f && f.ts0 != null && f.ts1 != null ? (f.ts1 - f.ts0) * 1000 : null;
  if (t.replay) return (span || 2400) / t.replay.speed;
  const base = span || animMs();
  let d = t.playMs ? Math.max(base * 0.8, Math.min(base * 3, t.playMs)) : base;
  const n = t.queue ? t.queue.length : 0;        // 佇列多一格是正常的（延後一個更新）；積更多才稍微加速
  if (n >= 3) d *= 0.7;
  else if (n >= 2) d *= 0.9;
  return d;
}

function startFrame(t, f, instant) {
  t.prev = t.frame;
  t.frame = f;
  t.frameDur = frameDur(f);
  t.frameAt = instant ? performance.now() - t.frameDur : performance.now();
  if (!t.replay && f._logPrefix) {               // 影格開始播時才寫戰鬥紀錄（跟畫面同步）
    pushFrameLog(t, f, f._logPrefix);
    f._logPrefix = null;
    scheduleTacLog();
  }
}

// 戰鬥紀錄最多每秒重畫一次（不然每 0.5 秒跳一次）
function scheduleTacLog() {
  if (G._logTimer) return;
  const wait = Math.max(0, 1000 - (performance.now() - (G._logAt || 0)));
  G._logTimer = setTimeout(() => { G._logTimer = null; G._logAt = performance.now(); renderTacLog(); }, wait);
}

// 戰鬥時間（秒）→ 紀錄前面的時間標記 m:ss
function battleClock(sec) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function enqueueFrame(t, f, instant) {
  const now = performance.now();
  if (t.lastArrive) {
    const gap = now - t.lastArrive;
    t.playMs = t.playMs ? t.playMs * 0.8 + gap * 0.2 : gap;
  }
  t.lastArrive = now;
  if (instant || !t.frame || t.replay) { t.queue = []; startFrame(t, f, instant); return; }
  (t.queue || (t.queue = [])).push(f);
  while (t.queue.length > 3) startFrame(t, t.queue.shift());   // 分頁在背景（不畫）時積太多 → 直接跳過去
  pumpFrames(t);
}

// 正在播的影格播完了 → 換下一格（畫每一格前呼叫）
function pumpFrames(t) {
  if (!t || t.replay || !t.queue || !t.queue.length) return;
  if (!t.frame || animP() >= 1) startFrame(t, t.queue.shift());
}

function animP() {
  const t = G.tac;
  const dur = t.frameDur || (t.replay ? 2400 / t.replay.speed : animMs());
  return Math.max(0, Math.min(1, (performance.now() - t.frameAt) / dur));
}

// 影格進度 p（0～1）→ 戰鬥時間（秒）；舊影格沒有時間 → null
function frameBT(f, p) {
  return f && f.ts0 != null && f.ts1 != null ? f.ts0 + p * (f.ts1 - f.ts0) : null;
}

// 這一刻還看得到的子彈、爆炸：本格與上一格（剛好跨格的效果不會被切掉）
function recentFx(f, key) {
  const t = G.tac;
  const out = (f[key] || []).map((x) => [x, f]);
  if (t && t.prev && t.prev !== f && t.prev.ts1 != null) for (const x of t.prev[key] || []) out.push([x, t.prev]);
  return out;
}

const BLAST_S = 0.6;   // 爆炸畫多久（秒）

function drawBlasts(ctx, f, p, P, z) {
  const bt = frameBT(f, p);
  for (const [bl, src] of recentFx(f, "blasts")) {
    let q;
    if (bt != null && bl.ts != null) q = (bt - bl.ts) / BLAST_S;
    else if (src === f) q = (p - 0.5) / 0.5;
    else continue;
    if (q < 0 || q > 1) continue;
    const [px, py] = P(bl.at[0], bl.at[1]);
    const flash = bl.item === "flashes";
    ctx.fillStyle = flash ? `rgba(255,255,255,${0.8 * (1 - q)})` : `rgba(255,150,60,${0.7 * (1 - q)})`;
    ctx.beginPath(); ctx.arc(px, py, (1 + q * (flash ? 3 : 2)) * z, 0, 7); ctx.fill();
  }
}

// 依起步與抵達時間內插：第 i 格在 pst[i] 起步、pt[i] 抵達（起步可能在上一格影格）；之間停在原地。
// 影格結束時還在走向下一格（nx，nst 起步、nt 抵達）→ 繼續往前推（最多 95%，等伺服器確認抵達），動作跨影格連續。
function lerpMoves(u, pts, f, bt) {
  const mix = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
  let pos = pts[0];
  for (let i = 1; i < pts.length; i++) {
    const t1 = u.pt[i - 1];
    const t0 = u.pst ? Math.min(u.pst[i - 1], t1) : (i === 1 ? f.ts0 : u.pt[i - 2]);
    if (bt < t0) return pos;
    if (bt < t1) return mix(pos, pts[i], (bt - t0) / Math.max(1e-6, t1 - t0));
    pos = pts[i];
  }
  if (u.nx && u.nt > u.nst && bt > u.nst) return mix(pos, [u.nx[0], u.nx[1]], Math.min(0.95, (bt - u.nst) / (u.nt - u.nst)));
  return pos;
}

// 沿路徑依時間內插：from 在 ts0，第 i 格在 pt[i] 抵達
function lerpTimed(pts, times, bt) {
  if (bt <= times[0]) return pts[0];
  for (let i = 1; i < pts.length; i++) {
    if (bt <= times[i]) {
      const span = Math.max(1e-6, times[i] - times[i - 1]);
      const k = (bt - times[i - 1]) / span;
      return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k];
    }
  }
  return pts[pts.length - 1];
}

// 這一步倒地的人：倒在打倒他的那一槍之後（有結構化事件才算得出來）
function downTimes(f) {
  if (f._downAt) return f._downAt;
  const out = {};
  if (f.ev && f.ts0 != null && f.ts1 != null) {
    for (const e of f.ev) if (e.type === "down" && e.ts != null) out[e.target] = (e.ts - f.ts0) / Math.max(1e-6, f.ts1 - f.ts0);
  } else if (f.ev && typeof shotTimes === "function") {
    const times = f._times || (f._times = shotTimes(f));
    for (const e of f.ev) {
      if (e.type !== "down") continue;
      const tm = e.aid && times[e.aid];
      out[e.target] = tm ? tm.t : 0.9;
    }
  }
  f._downAt = out;
  return out;
}

function currentUnits() {
  const t = G.tac;
  if (!t) return [];
  if (t.frame) {
    const p = animP();
    const mv = Math.min(1, p / 0.9);
    const f = t.frame;
    if (f._maxSub === undefined) f._maxSub = typeof frameMaxSub === "function" ? frameMaxSub(f) : 1;
    if (typeof EV !== "undefined") EV.maxSub = f._maxSub;
    const downAt = downTimes(f);
    const bt = frameBT(f, p);
    return f.units.filter((u) => !u.dead && !u.exited).map((u) => {
      const pts = [u.from || u.pos, ...(u.path || [])].map((n) => [n[0], n[1]]);
      if (pts.length === 1 && u.from && (u.from[0] !== u.pos[0] || u.from[1] !== u.pos[1])) pts.push([u.pos[0], u.pos[1]]);
      const timed = bt != null && ((u.pt && u.pt.length === pts.length - 1) || (u.nx && pts.length === 1));
      const [x, y] = timed ? lerpMoves(Object.assign({ pt: [] }, u), pts, f, bt)
        : u.psub && typeof lerpPathSub === "function" ? lerpPathSub(u, pts, mv) : lerpPath(pts, mv);
      let downNow = u.down && p > 0.6;
      if (f.ev) downNow = u.down && (downAt[u.id] === undefined || p > downAt[u.id] + 0.05);
      return Object.assign({}, u, { x: x + 0.5, y: y + 0.5, downNow });
    });
  }
  const meta = t.meta;
  if (!meta) return [];
  return (meta.units || []).map((u) => Object.assign({}, u, { x: u.pos[0] + 0.5, y: u.pos[1] + 0.5, downNow: u.down }));
}

function drawTac(now) {
  const t = G.tac;
  if (!t) return;
  tacxShow(!!t.world);
  if (t.world) return drawWorld(now);            // 連續地圖：新畫面
  pumpFrames(t);
  const g = tacGeom();
  const ctx = TC.ctx;
  ctx.fillStyle = "#070a08";
  ctx.fillRect(0, 0, TC.c.width, TC.c.height);
  if (!g) return;
  const { meta, s, w, h } = g;
  const P = (x, y) => [x * s, (h - y) * s];
  ctx.strokeStyle = "#0f1a12";
  for (let i = 0; i <= w; i++) { ctx.beginPath(); ctx.moveTo(i * s, 0); ctx.lineTo(i * s, h * s); ctx.stroke(); }
  for (let j = 0; j <= h; j++) { ctx.beginPath(); ctx.moveTo(0, j * s); ctx.lineTo(w * s, j * s); ctx.stroke(); }
  for (let y = 0; y < h; y++) {
    const row = meta.terrain[y];
    for (let x = 0; x < w; x++) {
      const ch = row[x];
      if (ch === ".") continue;
      ctx.fillStyle = TERRAIN_COLOR[ch] || "#333";
      const [px, py] = P(x, y + 1);
      if (ch === "=" || ch === "|") ctx.fillRect(px + s * 0.15, py + s * 0.35, s * 0.7, s * 0.3);
      else if (ch === "%") { ctx.fillRect(px + s * 0.2, py + s * 0.2, s * 0.6, s * 0.6); }
      else if (ch === '"') { ctx.globalAlpha = 0.8; ctx.fillRect(px + 1, py + 1, s - 2, s - 2); ctx.globalAlpha = 1; }
      else ctx.fillRect(px, py, s, s);
      if (ch === "W") { ctx.strokeStyle = "#7fb0d0"; ctx.beginPath(); ctx.moveTo(px + 2, py + s / 2); ctx.lineTo(px + s - 2, py + s / 2); ctx.stroke(); }
    }
  }
  for (const r of meta.rooms || []) {
    const [x0, y0, x1, y1] = r.rect;
    const [cx, cy] = P((x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2);
    ctx.fillStyle = "#3d5444"; ctx.font = `${Math.max(9, s * 0.4)}px monospace`;
    ctx.fillText(`建${r.id}`, cx - s * 0.5, cy + 4);
    for (const d of r.doors) { const [px, py] = P(d[0], d[1] + 1); ctx.strokeStyle = "#c8a060"; ctx.strokeRect(px + 3, py + 3, s - 6, s - 6); }
  }
  const f = t.frame;
  const p = f ? animP() : 1;
  const smokes = f ? f.smokes : (meta.smokes || []);
  for (const sm of smokes) {
    const [px, py] = P(sm.x, sm.y);
    ctx.fillStyle = `rgba(180,190,185,${0.45 * (sm.density ?? 1)})`;
    ctx.beginPath(); ctx.arc(px, py, sm.r * s, 0, 7); ctx.fill();
  }
  for (const gh of (f ? f.ghosts : meta.ghosts) || []) {
    const [px, py] = P(gh.pos[0] + 0.5, gh.pos[1] + 0.5);
    ctx.strokeStyle = `rgba(224,88,77,${Math.max(0.15, 1 - gh.age / 90)})`;
    ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.arc(px, py, s * 0.4, 0, 7); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "rgba(224,88,77,.6)"; ctx.font = "9px monospace"; ctx.fillText(`${gh.age}s`, px + s * 0.4, py - s * 0.3);
  }
  const supports = f ? (f.supports || []) : (meta.supports || []);
  for (const sp of supports) {
    const [px, py] = P(sp.x + 0.5, sp.y + 0.5);
    if (sp.state === "inbound") {
      ctx.strokeStyle = "#e8b04a"; ctx.beginPath(); ctx.arc(px, py, s * 0.8, 0, 7); ctx.moveTo(px - s, py); ctx.lineTo(px + s, py); ctx.moveTo(px, py - s); ctx.lineTo(px, py + s); ctx.stroke();
      ctx.fillStyle = "#e8b04a"; ctx.font = "10px monospace"; ctx.fillText(`${sp.label} ${Math.round(sp.eta)}s`, px + s, py - s * 0.6);
    } else if (sp.kind === "drone" && sp.state === "active") {
      ctx.strokeStyle = "rgba(90,180,232,.7)"; ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.arc(px, py, 8 * s, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#5ab4e8"; ctx.font = "14px sans-serif"; ctx.fillText("🛩", px - 7, py + 5);
    } else if (sp.kind === "airdrop" && sp.state === "landed") {
      ctx.fillStyle = "#5ab4e8"; ctx.fillRect(px - s * 0.3, py - s * 0.3, s * 0.6, s * 0.6);
    }
  }
  if (!t.replay && G.state) {
    for (const sq of G.state.squads) {
      if (sq.pos !== t.cell || !sq.action.mark) continue;
      const [px, py] = P(sq.action.mark[0] + 0.5, sq.action.mark[1] + 0.5);
      ctx.fillStyle = "#6fe37f"; ctx.font = "14px sans-serif";
      ctx.fillText({ rally: "🚩", flank: "↪", fire: "🎯" }[sq.action.mark_kind] || "🚩", px - 7, py + 5);
      ctx.font = "9px monospace"; ctx.fillText(sq.callsign, px + 8, py - 6);
    }
  }
  if (f) {
    drawHeard(ctx, f, p, P, s);
    drawShots(ctx, f, p, P, s);
    drawBlasts(ctx, f, p, P, s);
  }
  for (const u of byLevel(currentUnits())) {
    const [px, py] = P(u.x, u.y);
    const col = { own: "#6fe37f", ally: "#7fd4c4", remnant: "#a6e3c8", enemy: "#e0584d" }[u.side] || "#ccc";
    const r = s * (u.posture === "prone" ? 0.28 : u.posture === "stand" ? 0.4 : 0.34);
    if (u.downNow) {
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(px - r, py - r); ctx.lineTo(px + r, py + r); ctx.moveTo(px + r, py - r); ctx.lineTo(px - r, py + r); ctx.stroke();
      ctx.lineWidth = 1;
      if (u.stabilized) { ctx.fillStyle = "#fff"; ctx.font = "9px monospace"; ctx.fillText("+", px + r, py - r); }
    } else {
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(px, py, r, 0, 7); ctx.fill();
      if (u.facing) { ctx.strokeStyle = col; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + u.facing[0] * s * 0.6, py - u.facing[1] * s * 0.6); ctx.stroke(); }
      if (u.pinned) { ctx.strokeStyle = "#e0584d"; ctx.beginPath(); ctx.arc(px, py, r + 3, 0, 7); ctx.stroke(); }
      else if (u.suppressed) { ctx.strokeStyle = "#e8b04a"; ctx.beginPath(); ctx.arc(px, py, r + 3, 0, 7); ctx.stroke(); }
      if (u.dazzled) { ctx.strokeStyle = "#fff"; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.arc(px, py, r + 5, 0, 7); ctx.stroke(); ctx.setLineDash([]); }
      if (u.braced) { ctx.fillStyle = col; ctx.fillRect(px - 2, py + r + 1, 4, 3); }
    }
    drawUpstairs(ctx, u, px, py, r);
    if (u.side !== "enemy") { ctx.fillStyle = u.side === "own" ? "#cfe" : "#8bb"; ctx.font = "9px monospace"; ctx.fillText(u.name, px + r + 2, py + 3); }
    if (G.tac.unit === u.id) { ctx.strokeStyle = "#fff"; ctx.strokeRect(px - r - 4, py - r - 4, 2 * r + 8, 2 * r + 8); }
  }
  drawReasons(ctx, currentUnits(), P, s * 1.4, G.tac.unit);
  if (t.tool) {
    ctx.fillStyle = "rgba(232,176,74,.9)"; ctx.font = "12px monospace";
    ctx.fillText(`點選位置：${{ rally: "集結點", flank: "包抄點", fire: "集火點", airdrop: "空投落點", smoke: "煙幕位置", drone: "無人機中心" }[t.tool]}`, 8, 16);
  }
  if (!t.replay && t.frame && G.lastTick) {          // 到下一個作戰 tick（隊長、作戰層、完整狀態）
    const total = ((G.state && G.state.tick_seconds) || 5) * 1000;
    const left = Math.max(0, 1 - (performance.now() - G.lastTick) / total);
    ctx.fillStyle = "#1a2a1e"; ctx.fillRect(0, h * s - 4, w * s, 4);
    ctx.fillStyle = left > 0 ? "#6fe37f" : "#e8b04a"; ctx.fillRect(0, h * s - 4, w * s * left, 4);
  }
  if (t.replay) stepReplay();
}

// ------------------------------------------------------------------ 回放

async function openReplay(rid) {
  const d = await api(`/api/replay/${encodeURIComponent(rid)}${G.me.role === "gm" ? "?gm=1" : ""}`);
  if (!d.ok) { toast(d.error, true); return; }
  closeModal();
  if (d.replay.zone) return openWorldReplay(d);
  G.tac = { cell: d.replay.cell, meta: null, frame: null, frameAt: 0, log: [], tool: null, unit: null,
    replay: { meta: d.replay, frames: d.frames, idx: 0, playing: true, speed: +$("rp-speed").value } };
  $("tac").classList.remove("hidden");
  $("tac-replay").classList.remove("hidden");
  $("rp-marks").innerHTML = ""; $("rp-aar").innerHTML = "";
  $("rp-seek").min = 0; $("rp-seek").step = 1;     // 連續地圖的回放用戰鬥秒當刻度（tacbridge.js），這裡是影格編號
  $("rp-seek").max = d.frames.length - 1;
  renderTacTools();
  gotoFrame(0);
}

function gotoFrame(i) {
  const t = G.tac;
  const rp = t.replay;
  rp.idx = Math.max(0, Math.min(rp.frames.length - 1, i));
  t.prev = rp.idx > 0 ? rp.frames[rp.idx - 1] : null;
  t.frame = rp.frames[rp.idx];
  t.frameDur = frameDur(t.frame);
  t.frameAt = performance.now();
  $("rp-seek").value = rp.idx;
  $("rp-pos").textContent = `第 ${rp.idx + 1}/${rp.frames.length} 步（T${t.frame.world_tick}）`;
  const lines = [];
  const ctx = {};
  for (let k = 0; k <= rp.idx; k++) {
    const fr = rp.frames[k];
    lines.push(...frameLogLines(fr, fr.ts0 != null ? battleClock(fr.ts0) : `T${fr.world_tick}`, ctx));
  }
  renderTacLog(lines);
  if (t.unit) renderTacUnit(currentUnits().find((u) => u.id === t.unit) || null);
}

function stepReplay() {
  const rp = G.tac.replay;
  if (!rp.playing) return;
  if (animP() >= 1) {
    if (rp.idx >= rp.frames.length - 1) { rp.playing = false; $("rp-play").textContent = "▶"; return; }
    gotoFrame(rp.idx + 1);
  }
}

$("rp-play").onclick = () => { const rp = G.tac && G.tac.replay; if (!rp) return; rp.playing = !rp.playing; if (rp.playing && rp.idx >= rp.frames.length - 1) gotoFrame(0); $("rp-play").textContent = rp.playing ? "⏸" : "▶"; };
$("rp-prev").onclick = () => { const rp = G.tac && G.tac.replay; if (rp) { rp.playing = false; gotoFrame(rp.idx - 1); } };
$("rp-next").onclick = () => { const rp = G.tac && G.tac.replay; if (rp) { rp.playing = false; gotoFrame(rp.idx + 1); } };
$("rp-seek").oninput = () => { const rp = G.tac && G.tac.replay; if (rp) { rp.playing = false; gotoFrame(+$("rp-seek").value); } };
$("rp-speed").onchange = () => {
  const t = G.tac, rp = t && t.replay;
  if (!rp) return;
  const p = animP();
  rp.speed = +$("rp-speed").value;
  t.frameDur = frameDur(t.frame);
  t.frameAt = performance.now() - p * t.frameDur;   // 換速度時保持目前進度
};

$("hd-replays").onclick = () => {
  const list = (G.state.replays || []).map((r) => `<tr><td>${esc(r.cell)} ${esc(cellName(r.cell).split(" ")[1] || "")}</td><td>T${r.start_tick}～T${r.end_tick}</td><td>${r.frames} 步</td>
    <td><button data-rid="${esc(r.id)}">▶ 播放</button></td></tr>`).join("");
  openModal(`<h3>交戰回放</h3>${list ? `<table>${list}</table>` : `<div class="dim">還沒有結束的交戰。</div>`}`);
  for (const b of $("modal-body").querySelectorAll("button[data-rid]")) b.onclick = () => openReplay(b.dataset.rid);
};

// ------------------------------------------------------------------ modal / help / GM

function openModal(html) { $("modal-body").innerHTML = html; $("modal").classList.remove("hidden"); }
function closeModal() { $("modal").classList.add("hidden"); }
$("modal-close").onclick = closeModal;

function showHelp() {
  openModal(`<h3>你是誰</h3><p>地區指揮官。你不在戰場上，只能用無線電指揮機器人小隊。小隊長有自己的個性、信念與判斷：
    可能照辦、變通，甚至拒絕。每道命令都會回一張<b class="amber">回執</b>，告訴你他怎麼理解、實際做了什麼。</p>
    <h3>畫面</h3><table>
    <tr><td>作戰圖</td><td>5×5 城區。綠點是你的小隊（通訊正常時即時位置），青色是其他指揮官的友軍，紅色數字是情報中的敵軍（可能過時，越淡越舊）。點格子看資訊。</td></tr>
    <tr><td>小隊卡片</td><td>行動、自主等級、隊員（點名字看裝備、熟練度、傷勢）、無線電。失聯時資料停在最後回報。</td></tr>
    <tr><td>戰術畫面</td><td>50m 見方的格內地圖：每個隊員的位置、子彈、爆炸、煙霧。敵人只畫看得到的；看不到的畫最後位置（虛線、標幾秒前）。</td></tr>
    <tr><td>回放</td><td>每場交戰結束後可以倒帶、逐步看。</td></tr></table>
    <h3>下令</h3><table>
    <tr><td>打字</td><td>任何話都可以，例如「去 D3，避開 C3」「左邊包抄」「回 A3 整補」。</td></tr>
    <tr><td>快捷鍵</td><td>交戰外：固守、隱蔽、移動…（在作戰圖依序點經過格與目的地）、偵察…、撤退…。交戰中：推進、壓制推進、包抄、突擊、伏擊、破門、撤退。</td></tr>
    <tr><td>標定點</td><td>戰術畫面上點集結／包抄／集火位置（也是命令，隊長會判斷）。</td></tr></table>
    <h3>自主等級</h3><table>
    <tr><td>待命</td><td>沒有命令不自己移動；被打時只能就地保命。</td></tr>
    <tr><td>自衛</td><td>受威脅時可以隱蔽、撤退，不會主動進攻或轉移。</td></tr>
    <tr><td>自由</td><td>依個性自行判斷。</td></tr></table>
    <p>例行狀況（整補完成、空投到達）小隊不會自己出門，只會<b class="amber">提議</b>，你按「同意」才執行。小隊自己改的行動標「自主」，可以按「取消」。說「待命」「不准亂跑」也會自動切成待命。</p>
    <h3>指揮部支援（戰術畫面工具列）</h3><table>
    <tr><td>📦 空投</td><td>約 5 秒後落地，有散布；落點 3 格內的我方自動補彈藥與投擲物。敵人比較近會被搶走。</td></tr>
    <tr><td>☁ 煙幕</td><td>約 2.5 秒後拉一道三發煙牆，掩護推進或撤退（熱感測仍可穿透一部分）。</td></tr>
    <tr><td>🛩 無人機</td><td>揭露半徑約 16m 內的敵人約 20 秒，與同格友軍共享；可能被擊落。在作戰圖點沒有小隊的格子也能派去偵察。</td></tr></table>
    <h3>整補與補給</h3><p>小隊回 A3（後方據點）固守或隱蔽約 30 秒：彈藥補滿、傷員修復、缺員時從預備兵池補人。前線可用「請求補給」叫一般空投（冷卻 5 分鐘）。</p>`);
}
$("hd-help").onclick = showHelp;

$("hd-gm").onclick = async () => {
  const d = await api("/api/gm/status", {});
  if (!d.ok) { toast(d.error, true); return; }
  const st = d.status;
  const rows = st.squads.map((s) => `<tr><td>${esc(s.callsign)}<div class="dim small">${esc(s.id)}</div></td><td>${esc(s.pos)} ${esc(s.status)}<br>${esc(s.action)}｜${esc(s.autonomy)}</td>
    <td class="small">服從 ${s.obedience}｜${esc(s.tendencies)}<br>${esc(s.beliefs.join("／"))}</td>
    <td><button data-link="${esc(s.id)}" data-on="${s.linked ? 0 : 1}">${s.linked ? "📶 切斷" : "📵 恢復"}</button></td></tr>`).join("");
  const enemies = st.enemies.map((e) => `${esc(e.id)}(${esc(e.tag)}) ${esc(e.pos)}×${e.strength}${e.order ? " " + esc(e.order) + "→" + esc(e.goal) : ""}`).join("、");
  openModal(`<h3>GM｜T${st.tick}｜背景 tick ${st.running ? "on" : "off"}</h3>
    <div class="row"><button data-gm="tick1">推進 1 tick</button><button data-gm="tick5">推進 5 tick</button>
    <button data-gm="run">${st.running ? "暫停" : "繼續"}</button><button data-gm="reserve">補滿預備兵池</button>
    <button class="warn" data-gm="reset">重置戰場</button><button class="danger" data-gm="wipe">清除存檔</button></div>
    <div class="row">看真相：<input id="gm-cell" size="4" placeholder="C3"><button data-gm="truth">開啟戰術畫面</button>
      ${G.watch !== null ? "" : ""}<button data-gm="watch">切換觀看指揮官</button></div>
    <h3>小隊</h3><table>${rows}</table>
    <h3>敵軍（總兵力 ${st.enemy_total}）</h3><div class="small">${enemies || "—"}</div>
    <div class="small dim">交戰中：${esc(st.engagements.join("、") || "無")}｜${esc(st.enemy_log.join("；"))}</div>`);
  const body = $("modal-body");
  for (const b of body.querySelectorAll("button[data-link]")) b.onclick = async () => { await api("/api/gm/link", { squad: b.dataset.link, on: b.dataset.on === "1" }); $("hd-gm").click(); };
  for (const b of body.querySelectorAll("button[data-gm]")) b.onclick = async () => {
    const k = b.dataset.gm;
    let r;
    if (k === "tick1") r = await api("/api/gm/tick", { count: 1 });
    else if (k === "tick5") r = await api("/api/gm/tick", { count: 5 });
    else if (k === "run") r = await api("/api/gm/run", { on: !st.running });
    else if (k === "reserve") r = await api("/api/gm/reserve", {});
    else if (k === "reset") { if (!confirm("重置戰場？")) return; r = await api("/api/gm/reset", {}); }
    else if (k === "wipe") { if (!confirm("清除整個存檔？所有指揮官要重新就任。")) return; r = await api("/api/gm/wipe", {}); location.reload(); return; }
    else if (k === "truth") { const c = $("gm-cell").value.trim().toUpperCase(); if (!c) return; closeModal(); openTactical(c, true); return; }
    else if (k === "watch") { closeModal(); await startWatching(); return; }
    if (r) toast(r.ok ? r.message : r.error, !r.ok);
    $("hd-gm").click();
  };
};

// ------------------------------------------------------------------ loop

function loop(now) {
  try {
    const docked = typeof DOCK !== "undefined" && DOCK.on;
    if (!docked || opPopVisible()) drawOp(now);
    drawTac(now);
    if (docked) drawMini(now);
  } catch (e) { console.error(e); }
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
setInterval(() => { if (G.ws && G.ws.readyState === 1) G.ws.send('{"type":"ping"}'); }, 25000);
boot();
