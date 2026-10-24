"use strict";
// 新戰術畫面（web/tac 建置的 dist/tac.js，PixiJS）的接頭：伺服器設定 renderer＝pixi 時載入；載入失敗就繼續用舊畫面（Canvas）。
// 接點：openWorld（換視角時清掉播放狀態）、onWorld（推送）、drawWorld（每幀同步跟隨、工具、大小，畫面交給新畫面）、
// 點選與放置（回到原本的處理）、縮放按鈕、迷你作戰圖的視窗框、鍵盤快捷鍵（1～6 工具、Esc 取消，新舊畫面都有）。
// 回放還是用舊畫面（S6 換掉），回放時新畫面停下來。

const TACX = { loading: null, ok: false, host: null, follow: undefined, tool: undefined, shown: null, sel: undefined };

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
        TACX.ok = true;
      } catch (e) {
        console.error("新戰術畫面載入失敗，改用舊畫面", e);
        TACX.ok = false;
      }
      return TACX.ok;
    })();
  }
  return TACX.loading;
}

function tacxActive() {
  const t = G.tac;
  return !!(TACX.ok && t && t.world && !t.replay);
}

function tacxSize() {
  if (!TACX.host) return;
  const w = `${TC.c.width}px`, h = `${TC.c.height}px`;
  if (TACX.host.style.width !== w) TACX.host.style.width = w;
  if (TACX.host.style.height !== h) TACX.host.style.height = h;
}

// 每幀（drawTac）：該用哪個畫面
function tacxShow(on) {
  if (!TACX.ok || TACX.shown === on) return;
  TACX.shown = on;
  Tac.visible(on);
  TC.c.style.visibility = on ? "hidden" : "";
  $("tac-legend").style.display = on ? "none" : "";     // 新畫面有自己的圖例（畫面左下的 ?）
}

// 每幀（新畫面在用時）：大小、跟隨、工具、選取、作戰 tick 時間列
function tacxSync() {
  const t = G.tac;
  tacxSize();
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

// 換視角（開啟、GM 真相、觀戰、回到即時）
function tacxOpen(gm, focus) {
  if (!TACX.ok) return;
  Tac.reset(!!gm);
  TACX.follow = undefined;
  TACX.tool = undefined;
  if (focus && /^[A-Z]\d$/.test(focus) && !(G.tac && G.tac.follow)) Tac.lookAtCell(focus);
}

function tacxPick(s) {
  const t = G.tac;
  if (!t) return;
  t.unit = s ? s.id : null;
  if (!s) { renderTacUnit(null); return; }
  const u = Object.assign({}, s.u, { down: s.down, side: s.side, name: s.name });
  if (s.side === "own") {
    const sq = (G.state.squads || []).find((x) => x.members.some((m) => m.name === s.name));
    if (sq) { t.markSquad = sq.id; G.selected = sq.id; renderWorldTools(); renderSquads(); }
  }
  renderTacUnit(u);
}

// 迷你作戰圖的視窗框（新畫面的鏡頭）
function tacxCamera() {
  return tacxActive() ? Tac.camera() : null;
}

// 鍵盤：1～6 選工具、Esc 取消（輸入框裡不算）
const TACX_TOOLS = ["rally", "flank", "fire", "airdrop", "smoke", "drone"];
window.addEventListener("keydown", (e) => {
  const tg = e.target;
  if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.tagName === "SELECT")) return;
  const t = G.tac;
  if (!t || !t.world || t.replay || readonly() || !ownActiveSquads().length) return;
  if (e.key === "Escape" && t.tool) { t.tool = null; renderWorldTools(); e.preventDefault(); return; }
  const i = "123456".indexOf(e.key);
  if (i >= 0 && !e.ctrlKey && !e.altKey && !e.metaKey) {
    t.tool = t.tool === TACX_TOOLS[i] ? null : TACX_TOOLS[i];
    renderWorldTools();
    e.preventDefault();
  }
});
