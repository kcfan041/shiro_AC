"use strict";
// 連續地圖：戰術圖常駐在左欄（跟隨選中的小隊），作戰圖改成彈出視窗；左上角迷你作戰圖。
// 回放、GM 真相仍用彈出視窗（把同一個戰術圖框搬過去，關掉再搬回來）。

const DOCK = { on: false, out: false, mini: { c: null, ctx: null } };

function dockWanted() { return !!(G.state && G.state.zone_mode); }

function setupDock() {
  if (DOCK.on || !dockWanted()) return;
  DOCK.on = true;
  document.body.classList.add("docked");
  $("tac-dock").classList.remove("hidden");
  $("tac-dock").appendChild($("tac-box"));
  $("op-slot").appendChild($("op-panel"));
  $("hd-op").classList.remove("hidden");
  $("tac-close").classList.add("hidden");
  $("minimap").classList.remove("hidden");
  DOCK.mini.c = $("minimap");
  DOCK.mini.ctx = DOCK.mini.c.getContext("2d");
  resizeTac();
  openWorld(null, false);
}

function resizeTac() {
  const c = TC.c;
  if (DOCK.on && !DOCK.out) {
    const w = Math.max(360, $("tac-dock").clientWidth - 18);
    const h = Math.max(360, Math.min(600, window.innerHeight - 430));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  } else if (c.width !== 700 || c.height !== 700) {
    c.width = 700; c.height = 700;
  }
}
window.addEventListener("resize", () => { if (DOCK.on) resizeTac(); });

// 回放／GM：把戰術圖框搬到彈出視窗
function tacShow(overlay) {
  if (DOCK.on && !overlay) return;
  if (DOCK.on && !DOCK.out) {
    DOCK.out = true;
    $("tac").appendChild($("tac-box"));
    $("tac-close").classList.remove("hidden");
    $("minimap").classList.add("hidden");
    resizeTac();
  }
  $("tac").classList.remove("hidden");
}

function redock() {
  if (!DOCK.on) return;
  $("tac").classList.add("hidden");
  if (DOCK.out) {
    DOCK.out = false;
    $("tac-dock").appendChild($("tac-box"));
    $("tac-close").classList.add("hidden");
    $("minimap").classList.remove("hidden");
    resizeTac();
  }
  openWorld(null, false);
}

function dockFollow(sid) {
  const t = G.tac;
  if (!DOCK.on || DOCK.out || !t || !t.world) return;
  t.markSquad = sid;
  t.follow = true;
  renderWorldTools();
}

// 鏡頭移到某一格（有自己的小隊在那裡就跟隨它）
function dockLook(cell) {
  const t = G.tac;
  if (!t || !t.world || !WV.terrain) return;
  const own = ((G.state && G.state.squads) || []).find((s) => s.pos === cell && s.status === "active");
  if (own) { G.selected = own.id; dockFollow(own.id); renderSquads(); return; }
  const T = WV.terrain;
  const r = T.rows.indexOf(cell[0]), c = T.cols.indexOf(cell.slice(1));
  if (r < 0 || c < 0) return;
  t.follow = false;
  if (typeof tacxActive === "function" && tacxActive()) { Tac.lookAtCell(cell); renderWorldTools(); return; }
  t.cam = [c * T.cell + T.cell / 2, r * T.cell + T.cell / 2];
  renderWorldTools();
}

// ------------------------------------------------------------------ 作戰圖彈窗

function opPopOpen(auto) {
  DOCK.opAuto = !!auto;
  $("op-pop").classList.remove("hidden");
}
function opPopClose() {
  $("op-pop").classList.add("hidden");
  DOCK.opAuto = false;
}
function opPopVisible() { return !$("op-pop").classList.contains("hidden"); }
$("hd-op").onclick = () => opPopOpen(false);
$("op-close").onclick = () => { if (G.opTool) { G.opTool = null; updateOpHint(); } opPopClose(); };
$("op-pop").addEventListener("click", (e) => { if (e.target === $("op-pop")) $("op-close").click(); });

// ------------------------------------------------------------------ 迷你作戰圖

function drawMini(now) {
  if (!DOCK.on || DOCK.out || !G.state || !DOCK.mini.ctx) return;
  const ctx = DOCK.mini.ctx, W = DOCK.mini.c.width;
  const m = G.state.map;
  const S = W / m.cols;
  ctx.clearRect(0, 0, W, W);
  ctx.fillStyle = "rgba(7,10,8,.85)";
  ctx.fillRect(0, 0, W, W);
  for (const c of m.cells) {
    const r = m.row_names.indexOf(c.id[0]), k = m.col_names.indexOf(c.id.slice(1));
    const x = k * S, y = (m.rows - 1 - r) * S;
    ctx.fillStyle = c.base ? "#10251a" : c.enemy_base ? "#2a1212" : "#0d150f";
    ctx.fillRect(x + 1, y + 1, S - 2, S - 2);
    if (m.combat.includes(c.id)) {
      ctx.strokeStyle = `rgba(224,88,77,${0.5 + 0.4 * Math.sin(now / 250)})`;
      ctx.lineWidth = 2; ctx.strokeRect(x + 2, y + 2, S - 4, S - 4); ctx.lineWidth = 1;
    }
  }
  for (const e of m.enemies) {
    const r = m.row_names.indexOf(e.cell[0]), k = m.col_names.indexOf(e.cell.slice(1));
    ctx.fillStyle = e.suspected ? "rgba(160,100,60,.8)" : "rgba(224,88,77,.9)";
    ctx.font = "bold 9px monospace";
    ctx.fillText(`${e.est}`, k * S + S - 9, (m.rows - 1 - r) * S + S - 3);
  }
  const span = 25 * m.cols;
  for (const a of m.allies) {
    const r = m.row_names.indexOf(a.pos[0]), k = m.col_names.indexOf(a.pos.slice(1));
    ctx.fillStyle = "#7fd4c4"; ctx.beginPath(); ctx.arc(k * S + S / 2, (m.rows - 1 - r) * S + S / 2, 3, 0, 7); ctx.fill();
  }
  for (const sq of G.state.squads) {
    if (sq.status !== "active" || !sq.gpos) continue;
    const x = sq.gpos[0] / span * W, y = W - sq.gpos[1] / span * W;
    ctx.fillStyle = sq.stale ? "#8a7a4a" : "#6fe37f";
    ctx.beginPath(); ctx.arc(x, y, G.selected === sq.id ? 4 : 3, 0, 7); ctx.fill();
  }
  const t = G.tac;
  if (t && t.world && WV.terrain) {
    const tc = typeof tacxCamera === "function" ? tacxCamera() : null;   // 新畫面的鏡頭
    const z = tc ? tc.zoom : t.zoom, Wc = tc ? tc.w : TC.c.width, Hc = tc ? tc.h : TC.c.height;
    const vw = Wc / z / span * W, vh = Hc / z / span * W;
    const cx = (tc ? tc.x : t.cam[0]) / span * W, cy = W - (tc ? tc.y : t.cam[1]) / span * W;
    ctx.strokeStyle = "rgba(255,255,255,.7)";
    ctx.strokeRect(cx - vw / 2, cy - vh / 2, vw, vh);
  }
  ctx.strokeStyle = "#2a4a33"; ctx.strokeRect(0.5, 0.5, W - 1, W - 1);
}

$("minimap").addEventListener("click", (e) => { e.stopPropagation(); opPopOpen(false); });
