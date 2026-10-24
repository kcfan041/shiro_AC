"use strict";
// 連續地圖（zone 模式）的戰術畫面：整張 125×125 全圖一次載入，鏡頭跟隨小隊、可拖曳／縮放。

const WV = { terrain: null, off: null, px: 8, loading: null };

async function loadWorldTerrain() {
  if (WV.terrain) return WV.terrain;
  if (!WV.loading) {
    WV.loading = api("/api/world_terrain").then((d) => {
      if (!d.ok) throw new Error(d.error || "terrain");
      WV.terrain = d.terrain;
      const T = WV.terrain;
      const c = document.createElement("canvas");
      c.width = T.w * WV.px;
      c.height = T.h * WV.px;
      const g = c.getContext("2d");
      g.fillStyle = "#070a08";
      g.fillRect(0, 0, c.width, c.height);
      const s = WV.px;
      for (let y = 0; y < T.h; y++) {
        const row = T.terrain[y];
        for (let x = 0; x < T.w; x++) {
          const ch = row[x];
          if (ch === ".") continue;
          g.fillStyle = TERRAIN_COLOR[ch] || "#333";
          const px = x * s, py = (T.h - 1 - y) * s;
          if (ch === "=" || ch === "|") g.fillRect(px + s * 0.15, py + s * 0.35, s * 0.7, s * 0.3);
          else if (ch === "%") g.fillRect(px + s * 0.2, py + s * 0.2, s * 0.6, s * 0.6);
          else if (ch === '"') { g.globalAlpha = 0.8; g.fillRect(px, py, s, s); g.globalAlpha = 1; }
          else g.fillRect(px, py, s, s);
        }
      }
      g.strokeStyle = "#c8a060";
      for (const r of T.rooms) for (const d of r.doors) g.strokeRect(d[0] * s + 1, (T.h - 1 - d[1]) * s + 1, s - 2, s - 2);
      WV.off = c;
      return T;
    });
  }
  return WV.loading;
}

function worldSquadCenter(sid) {
  const t = G.tac;
  if (t && t.frame) {
    const us = currentUnits().filter((u) => u.squad === sid || u.group === sid);
    const live = us.filter((u) => !u.downNow);
    const list = live.length ? live : us;
    if (list.length) return [list.reduce((a, u) => a + u.x, 0) / list.length, list.reduce((a, u) => a + u.y, 0) / list.length];
  }
  const sq = squadById(sid);
  if (sq && sq.gpos) return [sq.gpos[0] + 0.5, sq.gpos[1] + 0.5];
  return null;
}

async function openWorld(focus, gm) {
  await loadWorldTerrain();
  const T = WV.terrain;
  let sid = G.selected;
  let cam = [T.w / 2, T.h / 2];
  if (focus && focus.length <= 3 && /^[A-Z]\d$/.test(focus)) {
    const r = T.rows.indexOf(focus[0]), c = T.cols.indexOf(focus.slice(1));
    cam = [c * T.cell + T.cell / 2, r * T.cell + T.cell / 2];
    const own = (G.state.squads || []).find((s) => s.pos === focus && s.status === "active");
    sid = own ? own.id : (gm ? null : sid);
  }
  const keepLog = G.tac && G.tac.world && !G.tac.replay && !G.tac.gm && !gm ? G.tac.log : [];
  G.tac = { world: true, gm: !!gm, frame: null, frameAt: 0, log: keepLog, tool: null, replay: null, unit: null,
    markSquad: sid, follow: !!sid && !(focus && gm), cam, zoom: 14, view: null };
  const c = sid && worldSquadCenter(sid);
  if (c && G.tac.follow) G.tac.cam = c;
  if (typeof tacxEnsure === "function" && await tacxEnsure()) tacxOpen(gm, focus);     // 新畫面（PixiJS）：換視角時重新開始播放
  if (typeof tacShow === "function") tacShow(!!gm); else $("tac").classList.remove("hidden");
  $("tac-replay").classList.add("hidden");
  renderTacLog();
  $("tac-unit").innerHTML = "";
  if (G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify({ type: "gm_truth", on: !!gm }));
  const q = [];
  if (gm) q.push("gm=1");
  if (G.watch && G.watch !== G.me.id) q.push(`watch=${encodeURIComponent(G.watch)}`);
  const d = await api(`/api/world${q.length ? "?" + q.join("&") : ""}`);
  if (d.ok) onWorld(d.world, true);
  renderWorldTools();
}

async function openWorldReplay(d) {
  await loadWorldTerrain();
  const bb = d.replay.bbox || [0, 0, WV.terrain.w, WV.terrain.h];
  const span = Math.max(bb[2] - bb[0], bb[3] - bb[1], 20);
  G.tac = { world: true, gm: false, frame: null, frameAt: 0, log: [], tool: null, unit: null, follow: false,
    cam: [(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2], zoom: Math.max(4, Math.min(30, 680 / span)), view: null,
    cell: d.replay.cell,
    replay: { meta: d.replay, frames: d.frames, idx: 0, playing: true, speed: +$("rp-speed").value } };
  if (typeof tacShow === "function") tacShow(true); else $("tac").classList.remove("hidden");
  $("tac-replay").classList.remove("hidden");
  $("rp-seek").max = d.frames.length - 1;
  renderWorldTools();
  gotoFrame(0);
}

function mergeFrames(frames) {
  const out = { t: 0, units: [], ghosts: [], shots: [], blasts: [], smokes: [], supports: [], events: [], ev: [] };
  let anyEv = false;
  for (const f of frames) {
    out.t = Math.max(out.t, f.t || 0);
    if (f.ts0 != null) { out.ts0 = f.ts0; out.ts1 = f.ts1; }   // 同一場 Battle：每個區域的時間一樣
    if (f.ev) anyEv = true;
    for (const k of ["units", "ghosts", "shots", "blasts", "smokes", "supports", "events", "ev"]) out[k] = out[k].concat(f[k] || []);
  }
  if (!anyEv) delete out.ev;
  return out;
}

function onWorld(world, instant) {
  const t = G.tac;
  G.world = world;
  if (!t || !t.world || t.replay) return;
  t.view = world;
  if (typeof tacxActive === "function" && tacxActive()) Tac.pushWorld(world);
  const f = mergeFrames(world.frames || []);
  f.t = world.tick * 1000 + f.t;
  if (!world.frames || !world.frames.length) {
    f.units = [];
    for (const z of world.zones || []) for (const u of z.units) f.units.push(Object.assign({}, u, { from: u.pos, path: [] }));
    f.ghosts = [].concat(...(world.zones || []).map((z) => z.ghosts));
    f.smokes = [].concat(...(world.zones || []).map((z) => z.smokes));
  }
  f.supports = [].concat(...(world.zones || []).map((z) => z.supports || []));
  const last = t.queue && t.queue.length ? t.queue[t.queue.length - 1] : t.frame;
  if (last && last.t === f.t && !instant) return;
  f._logPrefix = f.ts0 != null ? battleClock(f.ts0) : `T${world.tick}`;   // 開始播這一格時才寫紀錄
  enqueueFrame(t, f, instant);
  G.lastStep = performance.now();
  renderWorldTools();
  if (t.unit) {
    const u = f.units.find((x) => x.id === t.unit);
    if (u) renderTacUnit(Object.assign({}, u, { x: u.pos[0], y: u.pos[1] }));
  }
}

function ownActiveSquads() {
  return (G.state && G.state.squads || []).filter((s) => s.status === "active" && !s.stale);
}

function renderWorldTools() {
  const t = G.tac;
  if (!t || !t.world) return;
  const hot = t.view ? [].concat(...(t.view.zones || []).map((z) => z.hot)) : [];
  const cells = t.view ? [].concat(...(t.view.zones || []).map((z) => z.cells)) : [];
  $("tac-title").textContent = t.replay
    ? `回放｜${t.replay.meta.cell}（T${t.replay.meta.start_tick}～T${t.replay.meta.end_tick}）`
    : `戰術地圖｜活動區域 ${cells.length ? cells[0] + "～" + cells[cells.length - 1] : "（無）"}${hot.length ? "｜⚔ " + hot.join("、") : ""}${t.gm ? "（GM 真相）" : ""}`;
  const own = ownActiveSquads();
  const docked = typeof DOCK !== "undefined" && DOCK.on && !DOCK.out;
  if (docked && !t.replay) $("tac-title").textContent = `戰術｜${cells.length ? cells[0] + "～" + cells[cells.length - 1] : "—"}${hot.length ? "｜⚔ " + hot.join("、") : ""}`;
  const follow = `<button id="wt-follow" class="${t.follow ? "on" : ""}" title="鏡頭跟隨選中小隊">${docked ? "🎯" : "🎯 跟隨"}</button>`;
  const zoom = `<button id="wt-zin" title="放大">＋</button><button id="wt-zout" title="縮小">－</button>`;
  if (t.replay || readonly() || !own.length) {
    $("tac-tools").innerHTML = t.replay ? zoom : follow + zoom;
  } else {
    if (!t.markSquad || !own.some((s) => s.id === t.markSquad)) t.markSquad = (own.find((s) => s.id === G.selected) || own[0]).id;
    const sel = `<select id="tt-squad">${own.map((s) => `<option value="${esc(s.id)}" ${s.id === t.markSquad ? "selected" : ""}>${esc(s.callsign)}</option>`).join("")}</select>`;
    const compact = typeof DOCK !== "undefined" && DOCK.on && !DOCK.out;
    const keys = { rally: 1, flank: 2, fire: 3, airdrop: 4, smoke: 5, drone: 6 };
    const tool = (k, label, title) => `<button class="tool ${t.tool === k ? "on" : ""}" data-tool="${k}" title="${esc(label + "：" + title + `（快捷鍵 ${keys[k]}，Esc 取消）`)}">${compact ? [...label][0] : label}</button>`;
    $("tac-tools").innerHTML = `${follow}${zoom}${sel}<span class="dim">標定</span>${tool("rally", "🚩集結", "要小隊到這一點（仍經隊長判斷）")}${tool("flank", "↪包抄", "從這一點方向包抄")}${tool("fire", "🎯集火", "優先打這附近的敵人")}
      <span class="dim">支援</span>${tool("airdrop", "📦空投", "約 5 秒後落地，有散布；附近的人自動補給")}${tool("smoke", "☁煙幕", "約 2.5 秒後拉一道煙牆")}${tool("drone", "🛩無人機", "揭露半徑約 16m 內的敵人，可能被擊落")}`;
    for (const b of $("tac-tools").querySelectorAll("button[data-tool]")) b.onclick = () => { t.tool = t.tool === b.dataset.tool ? null : b.dataset.tool; renderWorldTools(); };
    const s = $("tt-squad");
    if (s) s.onchange = () => { t.markSquad = s.value; G.selected = s.value; t.follow = true; renderWorldTools(); renderSquads(); };
  }
  const f = $("wt-follow");
  if (f) f.onclick = () => { t.follow = !t.follow; renderWorldTools(); };
  const zi = $("wt-zin"), zo = $("wt-zout");
  const tx = typeof tacxActive === "function" && tacxActive();
  if (zi) zi.onclick = () => { if (tx) Tac.zoomBy(1.4); else t.zoom = Math.min(40, t.zoom * 1.4); };
  if (zo) zo.onclick = () => { if (tx) Tac.zoomBy(1 / 1.4); else t.zoom = Math.max(3, t.zoom / 1.4); };
}

function worldXY(t) {
  const W = TC.c.width, H = TC.c.height;
  return {
    P: (x, y) => [(x - t.cam[0]) * t.zoom + W / 2, (t.cam[1] - y) * t.zoom + H / 2],
    inv: (sx, sy) => [(sx - W / 2) / t.zoom + t.cam[0], t.cam[1] - (sy - H / 2) / t.zoom],
  };
}

function drawWorld(now) {
  const t = G.tac;
  if (typeof tacxActive === "function" && tacxActive()) { tacxSync(); return; }   // 新畫面在畫
  const ctx = TC.ctx;
  const W = TC.c.width, H = TC.c.height;
  ctx.fillStyle = "#070a08";
  ctx.fillRect(0, 0, W, H);
  const T = WV.terrain;
  if (!T) return;
  if (t.follow && t.markSquad && !t.replay) {
    const c = worldSquadCenter(t.markSquad);
    if (c) { t.cam[0] += (c[0] - t.cam[0]) * 0.12; t.cam[1] += (c[1] - t.cam[1]) * 0.12; }
  }
  const z = t.zoom;
  const { P } = worldXY(t);
  ctx.imageSmoothingEnabled = false;
  const [left, top] = P(0, T.h);
  ctx.drawImage(WV.off, left, top, T.w * z, T.h * z);
  const cellsIn = new Set(t.view ? [].concat(...(t.view.zones || []).map((zz) => zz.cells)) : []);
  const hot = new Set(t.view ? [].concat(...(t.view.zones || []).map((zz) => zz.hot)) : []);
  for (let r = 0; r < T.rows.length; r++) {
    for (let c = 0; c < T.cols.length; c++) {
      const id = T.rows[r] + T.cols[c];
      const [x0, y0] = P(c * T.cell, (r + 1) * T.cell);
      const sz = T.cell * z;
      if (!t.replay && !cellsIn.has(id)) { ctx.fillStyle = "rgba(0,0,0,.6)"; ctx.fillRect(x0, y0, sz, sz); }
      ctx.strokeStyle = hot.has(id) ? `rgba(224,88,77,${0.5 + 0.3 * Math.sin(now / 250)})` : "rgba(80,130,95,.45)";
      ctx.lineWidth = hot.has(id) ? 2 : 1;
      ctx.strokeRect(x0, y0, sz, sz);
      ctx.lineWidth = 1;
      ctx.fillStyle = "rgba(140,190,150,.75)"; ctx.font = `${Math.max(10, Math.min(16, z))}px monospace`;
      ctx.fillText(`${id} ${T.names[id] || ""}`, x0 + 4, y0 + Math.max(12, Math.min(18, z + 2)));
    }
  }
  if (t.view && !t.replay) {
    for (const it of t.view.intel || []) {
      const r = T.rows.indexOf(it.cell[0]), c = T.cols.indexOf(it.cell.slice(1));
      const [px, py] = P(c * T.cell + T.cell / 2, r * T.cell + T.cell / 2);
      const a = Math.max(0.3, 1 - it.age / 120);
      ctx.fillStyle = it.suspected ? `rgba(160,100,60,${a})` : `rgba(224,88,77,${a})`;
      ctx.font = `bold ${Math.max(12, z * 1.2)}px monospace`;
      ctx.fillText(`${it.suspected ? "?" : "!"}${it.est}`, px - z, py);
      ctx.font = "10px monospace";
      ctx.fillText(`${it.age}s 前`, px - z, py + Math.max(12, z));
    }
  }
  const f = t.frame;
  const p = f ? animP() : 1;
  for (const sm of (f && f.smokes) || []) {
    const [px, py] = P(sm.x, sm.y);
    ctx.fillStyle = `rgba(180,190,185,${0.45 * (sm.density ?? 1)})`;
    ctx.beginPath(); ctx.arc(px, py, sm.r * z, 0, 7); ctx.fill();
  }
  for (const gh of (f && f.ghosts) || []) {
    const [px, py] = P(gh.pos[0] + 0.5, gh.pos[1] + 0.5);
    ctx.strokeStyle = `rgba(224,88,77,${Math.max(0.15, 1 - gh.age / 90)})`;
    ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.arc(px, py, Math.max(3, z * 0.45), 0, 7); ctx.stroke(); ctx.setLineDash([]);
    if (z >= 8) { ctx.fillStyle = "rgba(224,88,77,.6)"; ctx.font = "9px monospace"; ctx.fillText(`${gh.age}s`, px + z * 0.4, py - z * 0.3); }
  }
  for (const sp of (f && f.supports) || []) {
    const [px, py] = P(sp.x + 0.5, sp.y + 0.5);
    if (sp.state === "inbound") {
      ctx.strokeStyle = "#e8b04a"; ctx.beginPath(); ctx.arc(px, py, z * 0.8, 0, 7); ctx.moveTo(px - z, py); ctx.lineTo(px + z, py); ctx.moveTo(px, py - z); ctx.lineTo(px, py + z); ctx.stroke();
      ctx.fillStyle = "#e8b04a"; ctx.font = "10px monospace"; ctx.fillText(`${sp.label} ${Math.round(sp.eta)}s`, px + z, py - z * 0.6);
    } else if (sp.kind === "drone" && sp.state === "active") {
      ctx.strokeStyle = "rgba(90,180,232,.7)"; ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.arc(px, py, 8 * z, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#5ab4e8"; ctx.font = "14px sans-serif"; ctx.fillText("🛩", px - 7, py + 5);
    } else if (sp.kind === "airdrop" && sp.state === "landed") {
      ctx.fillStyle = "#5ab4e8"; ctx.fillRect(px - z * 0.3, py - z * 0.3, z * 0.6, z * 0.6);
    }
  }
  if (t.view && !t.replay) {
    for (const zz of t.view.zones || []) for (const mk of zz.marks || []) {
      const [px, py] = P(mk.pos[0] + 0.5, mk.pos[1] + 0.5);
      ctx.fillStyle = "#6fe37f"; ctx.font = "14px sans-serif";
      ctx.fillText({ rally: "🚩", flank: "↪", fire: "🎯" }[mk.kind] || "🚩", px - 7, py + 5);
      ctx.font = "9px monospace"; ctx.fillText(mk.callsign, px + 8, py - 6);
    }
  }
  if (f) {
    drawHeard(ctx, f, p, P, z);
    drawShots(ctx, f, p, P, z);
    drawBlasts(ctx, f, p, P, z);
  }
  const units = currentUnits();
  for (const u of byLevel(units)) {
    const [px, py] = P(u.x, u.y);
    if (px < -20 || py < -20 || px > W + 20 || py > H + 20) continue;
    const col = { own: "#6fe37f", ally: "#7fd4c4", remnant: "#a6e3c8", enemy: "#e0584d" }[u.side] || "#ccc";
    const r = Math.max(3, z * (u.posture === "prone" ? 0.28 : u.posture === "stand" ? 0.4 : 0.34));
    if (u.downNow) {
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(px - r, py - r); ctx.lineTo(px + r, py + r); ctx.moveTo(px + r, py - r); ctx.lineTo(px - r, py + r); ctx.stroke();
      ctx.lineWidth = 1;
    } else {
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(px, py, r, 0, 7); ctx.fill();
      if (u.facing && z >= 6) { ctx.strokeStyle = col; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + u.facing[0] * z * 0.6, py - u.facing[1] * z * 0.6); ctx.stroke(); }
      if (u.pinned) { ctx.strokeStyle = "#e0584d"; ctx.beginPath(); ctx.arc(px, py, r + 3, 0, 7); ctx.stroke(); }
      else if (u.suppressed) { ctx.strokeStyle = "#e8b04a"; ctx.beginPath(); ctx.arc(px, py, r + 3, 0, 7); ctx.stroke(); }
    }
    drawUpstairs(ctx, u, px, py, r);
    if (u.side !== "enemy" && z >= 9) { ctx.fillStyle = u.side === "own" ? "#cfe" : "#8bb"; ctx.font = "9px monospace"; ctx.fillText(u.name, px + r + 2, py + 3); }
    if (t.unit === u.id) { ctx.strokeStyle = "#fff"; ctx.strokeRect(px - r - 4, py - r - 4, 2 * r + 8, 2 * r + 8); }
  }
  drawReasons(ctx, units, P, z, t.unit);
  if (!t.follow && t.markSquad && !t.replay) {
    const c = worldSquadCenter(t.markSquad);
    if (c) {
      const [px, py] = P(c[0], c[1]);
      if (px < 0 || py < 0 || px > W || py > H) {
        const ang = Math.atan2(py - H / 2, px - W / 2);
        const ex = W / 2 + Math.cos(ang) * (W / 2 - 20), ey = H / 2 + Math.sin(ang) * (H / 2 - 20);
        ctx.fillStyle = "#6fe37f";
        ctx.beginPath(); ctx.moveTo(ex + Math.cos(ang) * 12, ey + Math.sin(ang) * 12);
        ctx.lineTo(ex + Math.cos(ang + 2.5) * 10, ey + Math.sin(ang + 2.5) * 10);
        ctx.lineTo(ex + Math.cos(ang - 2.5) * 10, ey + Math.sin(ang - 2.5) * 10); ctx.fill();
      }
    }
  }
  if (t.tool) {
    ctx.fillStyle = "rgba(232,176,74,.9)"; ctx.font = "12px monospace";
    ctx.fillText(`點選位置：${{ rally: "集結點", flank: "包抄點", fire: "集火點", airdrop: "空投落點", smoke: "煙幕位置", drone: "無人機中心" }[t.tool]}`, 8, 16);
  }
  ctx.fillStyle = "rgba(140,190,150,.8)"; ctx.font = "10px monospace";
  ctx.fillText(`比例 1 格＝2m｜畫面約 ${Math.round(W / z * 2)}m`, 8, H - 10);
  if (!t.replay && t.frame && G.lastTick) {          // 到下一個作戰 tick（隊長、作戰層、完整狀態）
    const total = ((G.state && G.state.tick_seconds) || 5) * 1000;
    const left2 = Math.max(0, 1 - (performance.now() - G.lastTick) / total);
    ctx.fillStyle = "#1a2a1e"; ctx.fillRect(0, H - 4, W, 4);
    ctx.fillStyle = left2 > 0 ? "#6fe37f" : "#e8b04a"; ctx.fillRect(0, H - 4, W * left2, 4);
  }
  if (t.replay) stepReplay();
}

const WDRAG = { on: false, moved: false, x: 0, y: 0 };

function canvasXY(e) {
  const r = TC.c.getBoundingClientRect();
  return [(e.clientX - r.left) * (TC.c.width / r.width), (e.clientY - r.top) * (TC.c.height / r.height)];
}

TC.c.addEventListener("mousedown", (e) => {
  if (!G.tac || !G.tac.world) return;
  const [x, y] = canvasXY(e);
  Object.assign(WDRAG, { on: true, moved: false, x, y });
});
window.addEventListener("mousemove", (e) => {
  if (!WDRAG.on || !G.tac || !G.tac.world) return;
  const [x, y] = canvasXY(e);
  const dx = x - WDRAG.x, dy = y - WDRAG.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) WDRAG.moved = true;
  if (WDRAG.moved) {
    const t = G.tac;
    t.cam[0] -= dx / t.zoom;
    t.cam[1] += dy / t.zoom;
    if (t.follow) { t.follow = false; renderWorldTools(); }
    WDRAG.x = x; WDRAG.y = y;
  }
});
window.addEventListener("mouseup", () => { setTimeout(() => { WDRAG.on = false; }, 0); });
TC.c.addEventListener("wheel", (e) => {
  if (!G.tac || !G.tac.world) return;
  e.preventDefault();
  const t = G.tac;
  t.zoom = Math.max(3, Math.min(40, t.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
}, { passive: false });

// 工具點了位置（全圖小格）：標定點是給小隊的命令、支援直接由指揮部執行（新舊畫面共用）
async function worldPlace(k, x, y) {
  const t = G.tac;
  if (!t) return;
  if (["rally", "flank", "fire"].includes(k)) {
    const d = await api("/api/mark", { squad: t.markSquad, kind: k, global: [x, y] });
    if (!d.ok) toast(d.error || "命令沒有送達", true);
    else if (d.result && d.result.receipt) toast(`回執：${d.result.receipt.label}｜${d.result.receipt.action}`);
  } else {
    const d = await api("/api/support", { kind: k, global: [x, y] });
    toast(d.ok ? d.message : d.error, !d.ok);
  }
}

async function worldClick(e) {
  const t = G.tac;
  if (WDRAG.moved) { WDRAG.moved = false; return; }
  const [sx, sy] = canvasXY(e);
  const [gx, gy] = worldXY(t).inv(sx, sy);
  const x = Math.floor(gx), y = Math.floor(gy);
  if (t.tool && !t.replay) {
    const k = t.tool;
    t.tool = null;
    renderWorldTools();
    return worldPlace(k, x, y);
  }
  let best = null, bd = Math.max(1.2, 8 / t.zoom);
  for (const u of currentUnits()) { const d = Math.hypot(u.x - gx, u.y - gy); if (d < bd) { bd = d; best = u; } }
  t.unit = best ? best.id : null;
  if (best && best.side === "own") {
    const sq = (G.state.squads || []).find((s) => s.members.some((m) => m.name === best.name));
    if (sq) { t.markSquad = sq.id; G.selected = sq.id; renderWorldTools(); }
  }
  renderTacUnit(best);
}
