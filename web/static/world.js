"use strict";
// 連續地圖（zone 模式）的戰術畫面：畫面本體是新畫面（web/tac 建置的 dist/tac.js，PixiJS，接頭在 tacbridge.js）；
// 這裡只管：載入全圖地形、開啟／換視角、收伺服器推送、標題與工具列、工具放置後送出命令或支援。
// 舊的 Canvas 連續地圖畫面已拿掉（戰術地圖重做 S6）；單格模式（zone_mode 關閉）仍用 app.js 的舊畫面。

const WV = { terrain: null, loading: null, reliefLoading: false };

async function loadWorldTerrain() {
  if (WV.terrain) return WV.terrain;
  if (!WV.loading) {
    WV.loading = api("/api/world_terrain").then((d) => {
      if (!d.ok) throw new Error(d.error || "terrain");
      WV.terrain = d.terrain;
      return WV.terrain;
    });
  }
  return WV.loading;
}

async function openWorld(focus, gm) {
  await loadWorldTerrain();
  let sid = G.selected;
  if (focus && cellParts(focus)) {
    const own = (G.state.squads || []).find((s) => s.pos === focus && s.status === "active");
    sid = own ? own.id : (gm ? null : sid);
  }
  const keepLog = G.tac && G.tac.world && !G.tac.replay && !G.tac.gm && !gm ? G.tac.log : [];
  G.tac = { world: true, gm: !!gm, log: keepLog, tool: null, replay: null, unit: null,
    markSquad: sid, follow: !!sid && !(focus && gm), view: null };
  if (await tacxEnsure()) tacxOpen(gm, focus);      // 換視角：新畫面重新開始播放
  if (typeof tacShow === "function") tacShow(!!gm); else $("tac").classList.remove("hidden");
  tacxReplayUi(false);
  renderTacLog();
  $("tac-unit").innerHTML = "";
  if (G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify({ type: "gm_truth", on: !!gm }));
  const q = [];
  if (gm) q.push("gm=1");
  if (G.watch && G.watch !== G.me.id) q.push(`watch=${encodeURIComponent(G.watch)}`);
  const d = await api(`/api/world${q.length ? "?" + q.join("&") : ""}`);
  if (d.ok) onWorld(d.world);
  renderWorldTools();
}

// 連續地圖的回放：新畫面播（時間軸、跳點、戰後摘要在 tacbridge.js）
async function openWorldReplay(d) {
  await loadWorldTerrain();
  G.tac = { world: true, gm: false, log: [], tool: null, unit: null, follow: false, view: null, cell: d.replay.cell,
    replay: { meta: d.replay, frames: d.frames } };
  if (typeof tacShow === "function") tacShow(true); else $("tac").classList.remove("hidden");
  renderWorldTools();
  renderTacLog([]);
  $("tac-unit").innerHTML = "";
  if (!(await tacxEnsure())) { toast("戰術圖的新畫面沒有載入，無法播放回放", true); return; }
  tacxReplay(d);
}

// 地形變了（地圖變更層：挖坑、爆炸、地形回復）：推送的 relief_v 和手上的不同就拿新的高低差，戰術圖只重烘改到的塊
function syncRelief(world) {
  const v = world && world.relief_v;
  if (v == null || !WV.terrain || v === WV.terrain.relief_v || WV.reliefLoading) return;
  WV.reliefLoading = true;
  api("/api/world_relief").then((d) => {
    WV.reliefLoading = false;
    if (!d.ok) return;
    WV.terrain.relief = d.relief;
    WV.terrain.relief_v = d.v;
    if (tacxActive()) Tac.setRelief(d.relief);
  }).catch(() => { WV.reliefLoading = false; });
}

function onWorld(world) {
  const t = G.tac;
  G.world = world;
  syncRelief(world);
  if (!t || !t.world || t.replay) return;
  t.view = world;
  if (tacxActive()) Tac.pushWorld(world);
  G.lastStep = performance.now();
  renderWorldTools();
  if (t.unit) {                                      // 選取中的人：資料跟著更新
    for (const z of world.zones || []) {
      const u = (z.units || []).find((x) => x.id === t.unit);
      if (u) { renderTacUnit(u); break; }
    }
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
    const keys = { rally: 1, flank: 2, fire: 3, airdrop: 4, smoke: 5, drone: 6, fortify: 7 };
    const tool = (k, label, title) => `<button class="tool ${t.tool === k ? "on" : ""}" data-tool="${k}" title="${esc(label + "：" + title + `（快捷鍵 ${keys[k]}，Esc 取消）`)}">${docked ? [...label][0] : label}</button>`;
    $("tac-tools").innerHTML = `${follow}${zoom}${sel}<span class="dim">標定</span>${tool("rally", "🚩集結", "要小隊到這一點（仍經隊長判斷）")}${tool("flank", "↪包抄", "從這一點方向包抄")}${tool("fire", "🎯集火", "優先打這附近的敵人")}${tool("fortify", "⛏陣地", "到這一點挖散兵坑構築陣地（仍經隊長判斷）")}
      <span class="dim">支援</span>${tool("airdrop", "📦空投", "約 5 秒後落地，有散布；附近的人自動補給")}${tool("smoke", "☁煙幕", "約 2.5 秒後拉一道煙牆")}${tool("drone", "🛩無人機", "揭露半徑約 16m 內的敵人，可能被擊落")}`;
    for (const b of $("tac-tools").querySelectorAll("button[data-tool]")) b.onclick = () => { t.tool = t.tool === b.dataset.tool ? null : b.dataset.tool; renderWorldTools(); };
    const s = $("tt-squad");
    if (s) s.onchange = () => { t.markSquad = s.value; G.selected = s.value; t.follow = true; renderWorldTools(); renderSquads(); };
  }
  const f = $("wt-follow");
  if (f) f.onclick = () => { t.follow = !t.follow; renderWorldTools(); };
  const zi = $("wt-zin"), zo = $("wt-zout");
  if (zi) zi.onclick = () => { if (tacxActive()) Tac.zoomBy(1.4); };
  if (zo) zo.onclick = () => { if (tacxActive()) Tac.zoomBy(1 / 1.4); };
}

// 每幀（app.js 的 drawTac）：連續地圖交給新畫面
function drawWorld() {
  if (tacxActive()) tacxSync();
}

// 工具點了位置（全圖小格）：標定點是給小隊的命令、支援直接由指揮部執行
async function worldPlace(k, x, y) {
  const t = G.tac;
  if (!t) return;
  if (["rally", "flank", "fire", "fortify"].includes(k)) {
    const d = await api("/api/mark", { squad: t.markSquad, kind: k, global: [x, y] });
    if (!d.ok) toast(d.error || "命令沒有送達", true);
    else if (d.result && d.result.receipt) toast(`回執：${d.result.receipt.label}｜${d.result.receipt.action}`);
  } else {
    const d = await api("/api/support", { kind: k, global: [x, y] });
    toast(d.ok ? d.message : d.error, !d.ok);
  }
}
