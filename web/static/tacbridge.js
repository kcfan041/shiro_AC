"use strict";
// 戰術圖新畫面（web/tac 建置的 dist/tac.js，PixiJS）的接頭。連續地圖（zone 模式）的戰術圖與回放都用它；
// 伺服器沒有建置好的 dist/tac.js（狀態 renderer＝missing）或瀏覽器載入失敗時，戰術圖顯示怎麼建置的提示。
// 接點：openWorld（換視角時重新開始播放）、onWorld（推送）、drawWorld（每幀同步大小、跟隨、工具、選取、時間列）、
// 點選與放置（回到 world.js 的處理）、縮放按鈕、迷你作戰圖的視窗框、戰鬥紀錄（新畫面開始播一格時寫）、
// 回放（時間軸與跳點、戰後摘要、播放控制）、鍵盤快捷鍵（1～6 工具、Esc 取消）。

const TACX = { loading: null, ok: false, failed: false, host: null, msg: null, follow: undefined, tool: undefined, shown: null, sel: undefined,
  replay: null };

async function tacxEnsure() {
  if (!G.state || G.state.renderer !== "pixi" || !WV.terrain) return false;
  if (TACX.ok) return true;
  if (!TACX.loading) {
    TACX.loading = (async () => {
      try {
        await import("/static/dist/tac.js");
        const host = document.createElement("div");
        host.id = "tac-pixi";
        host.style.cssText = "position:absolute;left:0;top:0;overflow:hidden";
        document.querySelector(".tac-canvas").insertBefore(host, $("minimap"));
        TACX.host = host;
        tacxSize();
        await window.Tac.mount(host, WV.terrain, {});
        Tac.on("pick", tacxPick);
        Tac.on("place", (k, x, y) => { if (G.tac) { G.tac.tool = null; renderWorldTools(); } worldPlace(k, x, y); });
        Tac.on("follow", (on) => { const t = G.tac; if (t && t.follow !== on) { t.follow = on; TACX.follow = on ? t.markSquad : null; renderWorldTools(); } });
        Tac.on("frame", tacxFrameLog);
        TACX.ok = true;
      } catch (e) {
        console.error("戰術圖新畫面載入失敗", e);
        TACX.failed = true;
      }
      return TACX.ok;
    })();
  }
  return TACX.loading;
}

function tacxActive() {
  const t = G.tac;
  return !!(TACX.ok && t && t.world);
}

function tacxSize() {
  if (!TACX.host) return;
  const w = `${TC.c.width}px`, h = `${TC.c.height}px`;
  if (TACX.host.style.width !== w) TACX.host.style.width = w;
  if (TACX.host.style.height !== h) TACX.host.style.height = h;
}

// 每幀（drawTac）：連續地圖用新畫面，單格模式用舊的 Canvas；新畫面不能用時顯示提示
function tacxShow(world) {
  const on = !!(world && TACX.ok);
  if (TACX.shown !== on) {
    TACX.shown = on;
    if (TACX.ok) Tac.visible(on);
    TC.c.style.visibility = world ? "hidden" : "";
    $("tac-legend").style.display = world ? "none" : "";     // 新畫面有自己的圖例（畫面左下的 ?）
  }
  const missing = world && !TACX.ok && (TACX.failed || (G.state && G.state.renderer === "missing"));
  if (missing && !TACX.msg) {
    TACX.msg = document.createElement("div");
    TACX.msg.className = "tac-missing";
    TACX.msg.style.cssText = "position:absolute;left:0;top:0;right:0;padding:40px 20px;text-align:center;color:var(--amber)";
    TACX.msg.innerHTML = TACX.failed ? "戰術圖的新畫面載入失敗（瀏覽器可能不支援 WebGL），請看瀏覽器主控台的錯誤。"
      : "戰術圖需要先建置新畫面：在伺服器上執行 <code>python scripts/build_web.py</code>（需要 Node 20.19 以上），再重新啟動伺服器。";
    document.querySelector(".tac-canvas").appendChild(TACX.msg);
  }
  if (TACX.msg) TACX.msg.style.display = missing ? "" : "none";
}

// 每幀（新畫面在用時）：大小、跟隨、工具、選取、作戰 tick 時間列；回放時更新時間軸
function tacxSync() {
  const t = G.tac;
  tacxSize();
  if (t.replay) return tacxReplaySync();
  const want = t.follow ? t.markSquad : null;
  if (want !== TACX.follow) {
    TACX.follow = want;
    Tac.follow(want, want ? () => { const sq = squadById(want); return sq && sq.gpos ? [sq.gpos[0] + 0.5, sq.gpos[1] + 0.5] : null; } : null);
  }
  const tool = t.tool || null;
  if (tool !== TACX.tool) { TACX.tool = tool; Tac.setTool(tool, t.markSquad); }
  if (t.unit !== TACX.sel) { TACX.sel = t.unit; Tac.select(t.unit || null); }
  if (G.lastTick && G.state) Tac.setTick(G.lastTick, G.state.tick_seconds || 5);
}

// 換視角（開啟、GM 真相、觀戰、回到即時、關掉回放）
function tacxOpen(gm, focus) {
  if (!TACX.ok) return;
  if (TACX.replay) { Tac.stopReplay(); TACX.replay = null; }
  Tac.reset(!!gm);
  TACX.follow = undefined;
  TACX.tool = undefined;
  TACX.sel = undefined;
  if (focus && /^[A-Z]\d$/.test(focus) && !(G.tac && G.tac.follow)) Tac.lookAtCell(focus);
}

function tacxPick(s) {
  const t = G.tac;
  if (!t) return;
  t.unit = s ? s.id : null;
  if (!s) { renderTacUnit(null); return; }
  const u = Object.assign({}, s.u, { down: s.down, side: s.side, name: s.name });
  if (s.side === "own" && !t.replay) {
    const sq = (G.state.squads || []).find((x) => x.members.some((m) => m.name === s.name));
    if (sq) { t.markSquad = sq.id; G.selected = sq.id; renderWorldTools(); renderSquads(); }
  }
  renderTacUnit(u);
}

// 戰鬥紀錄：新畫面開始播一格影格時才寫（和畫面同步）；即時標戰鬥時間，回放標這場的 m:ss
function tacxFrameLog(f) {
  const t = G.tac;
  if (!t || !t.world) return;
  const prefix = TACX.replay ? Tac.clockText(f.ts0 ?? 0, TACX.replay.t0) : battleClock(f.ts0 ?? 0);
  pushFrameLog(t, f, prefix);
  scheduleTacLog();
}

// 迷你作戰圖的視窗框（新畫面的鏡頭）
function tacxCamera() {
  return tacxActive() ? Tac.camera() : null;
}

// ------------------------------------------------------------------ 回放（時間軸、跳點、戰後摘要）

function tacxReplayUi(show) {
  $("tac-replay").classList.toggle("hidden", !show);
  $("rp-marks").innerHTML = "";
  $("rp-aar").innerHTML = "";
}

function tacxReplay(d) {
  const bb = d.replay.bbox || [0, 0, WV.terrain.w, WV.terrain.h];
  Tac.playReplay(d.frames, +$("rp-speed").value);
  const span = Math.max(bb[2] - bb[0], bb[3] - bb[1], 20);
  Tac.lookAt((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2);
  Tac.setZoom(Math.max(4, Math.min(30, (TC.c.width - 40) / span)));
  const a = Tac.aar(d.frames);
  TACX.replay = { t0: a.t0, t1: a.t1, frames: d.frames, aar: a };
  tacxReplayUi(true);
  const seek = $("rp-seek");
  seek.min = a.t0; seek.max = a.t1; seek.step = 0.05; seek.value = a.t0;
  // 時間軸上的標記：點了跳到前 1 秒、鏡頭移過去
  const color = { kia: "var(--red)", down: "var(--red)", aid: "var(--own)", drag: "var(--own)", support: "var(--blue)", intent: "#9ab", blast: "var(--amber)" };
  const span2 = Math.max(1e-6, a.t1 - a.t0);
  $("rp-marks").innerHTML = a.items.map((x, i) =>
    `<i data-i="${i}" title="${esc(Tac.clockText(x.ts, a.t0) + " " + x.icon + " " + x.text)}" style="left:${((x.ts - a.t0) / span2 * 100).toFixed(2)}%;background:${color[x.kind] || "#9ab"};height:${x.kind === "blast" ? 6 : 10}px"></i>`).join("");
  for (const el of $("rp-marks").querySelectorAll("i")) el.onclick = () => tacxJump(a.items[+el.dataset.i]);
  // 戰後摘要卡
  const s = a.stats;
  $("rp-aar").innerHTML = `<div class="panel-title small">戰後摘要｜${Tac.clockText(a.t1, a.t0)}</div>
    <div class="dim small">開火 ${s.shots} 發、命中 ${s.hits}、倒地 ${s.downs}、陣亡 ${s.kias}、手榴彈 ${s.grenades}、支援 ${s.supports}</div>
    ${a.headline.map((x, i) => `<div class="aar-row" data-i="${a.items.indexOf(x)}"><span class="t">${Tac.clockText(x.ts, a.t0)}</span> ${x.icon} ${esc(x.text)}</div>`).join("")
      || '<div class="dim small">這段沒有傷亡、救援或支援。</div>'}`;
  for (const el of $("rp-aar").querySelectorAll(".aar-row")) el.onclick = () => tacxJump(a.items[+el.dataset.i]);
  tacxReplayLog(a.t0);
}

function tacxJump(item) {
  if (!item || !TACX.replay) return;
  tacxSeek(item.ts - 1);
  if (item.at) Tac.lookAt(item.at[0] + 0.5, item.at[1] + 0.5);
}

function tacxSeek(t) {
  Tac.replaySeek(t);
  tacxReplayLog(t);
}

// 跳到某個時間：戰鬥紀錄重建到那裡
function tacxReplayLog(t) {
  const rp = TACX.replay, tac = G.tac;
  if (!rp || !tac) return;
  tac.log = [];
  tac.logCtx = {};
  for (const f of rp.frames) {
    if ((f.ts0 ?? 0) > t) break;
    pushFrameLog(tac, f, Tac.clockText(f.ts0 ?? 0, rp.t0));
  }
  renderTacLog();
}

function tacxReplaySync() {
  const st = Tac.replayState(), rp = TACX.replay;
  if (!st || !rp) return;
  const seek = $("rp-seek");
  if (document.activeElement !== seek) seek.value = st.t;
  $("rp-pos").textContent = `${Tac.clockText(st.t, rp.t0)} / ${Tac.clockText(rp.t1, rp.t0)}`;
  const label = st.playing ? "⏸" : "▶";
  if ($("rp-play").textContent !== label) $("rp-play").textContent = label;
}

// 回放控制：連續地圖的回放交給新畫面，單格模式照 app.js 原本的處理
(() => {
  const isWorld = () => !!(G.tac && G.tac.world && G.tac.replay && TACX.replay);
  const wrap = (id, prop, fn) => { const el = $(id), orig = el[prop]; el[prop] = (e) => (isWorld() ? fn(e) : orig && orig.call(el, e)); };
  wrap("rp-play", "onclick", () => { const st = Tac.replayState(); Tac.replayPlay(!st.playing); });
  wrap("rp-prev", "onclick", () => { Tac.replayPlay(false); tacxSeek(Tac.replayState().t - 0.5); });
  wrap("rp-next", "onclick", () => { Tac.replayPlay(false); tacxSeek(Tac.replayState().t + 0.5); });
  wrap("rp-seek", "oninput", () => { Tac.replayPlay(false); tacxSeek(+$("rp-seek").value); });
  wrap("rp-speed", "onchange", () => Tac.replaySpeed(+$("rp-speed").value));
})();

// 鍵盤：1～6 選工具、Esc 取消；回放時空白鍵播放／暫停、左右鍵前後 0.5 秒（輸入框裡不算）
const TACX_TOOLS = ["rally", "flank", "fire", "airdrop", "smoke", "drone"];
window.addEventListener("keydown", (e) => {
  const tg = e.target;
  if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.tagName === "SELECT")) return;
  const t = G.tac;
  if (!t || !t.world) return;
  if (t.replay && TACX.replay) {
    if (e.key === " ") { $("rp-play").onclick(); e.preventDefault(); }
    else if (e.key === "ArrowLeft") { $("rp-prev").onclick(); e.preventDefault(); }
    else if (e.key === "ArrowRight") { $("rp-next").onclick(); e.preventDefault(); }
    return;
  }
  if (readonly() || !ownActiveSquads().length) return;
  if (e.key === "Escape" && t.tool) { t.tool = null; renderWorldTools(); e.preventDefault(); return; }
  const i = "123456".indexOf(e.key);
  if (i >= 0 && !e.ctrlKey && !e.altKey && !e.metaKey) {
    t.tool = t.tool === TACX_TOOLS[i] ? null : TACX_TOOLS[i];
    renderWorldTools();
    e.preventDefault();
  }
});
