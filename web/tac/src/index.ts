// 指揮室用的入口（建置成 web/static/dist/tac.js）：window.Tac 給舊前端（world.js）呼叫。
// 伺服器設定 renderer＝pixi 而且有建置好的 dist/tac.js 時，app.js 動態載入這個檔案；載入失敗就繼續用舊畫面。

import { headline, summarize, clockText } from "./aar";
import type { Attn } from "./attention";
import type { Frame, WorldTerrain, XY } from "./protocol";
import type { WorldView } from "./render/overlay";
import type { UnitSample } from "./tracks";
import { TacView, type TacEvents, type TacOptions } from "./view";

let view: TacView | null = null;
let creating: Promise<TacView> | null = null;
const handlers: Partial<TacEvents> = {};

const Tac = {
  get ready(): boolean { return !!view; },

  /** 在 host 裡建立畫面（只建一次；之後再呼叫回傳同一個） */
  async mount(host: HTMLElement, terrain: WorldTerrain, opt: TacOptions = {}): Promise<boolean> {
    if (view) return true;
    if (!creating) creating = TacView.create(host, terrain, opt);
    view = await creating;
    view.on = handlers;
    return true;
  },

  pushWorld(w: WorldView & { frames?: Frame[] }) { view?.pushWorld(w); },
  /** 換視角（GM 真相、觀戰、回到即時）：清掉播放狀態 */
  reset(gm = false) { view?.reset(gm); },
  follow(sid: string | null, fallback: (() => XY | null) | null = null) { view?.follow(sid, fallback); },
  lookAt(x: number, y: number) { view?.lookAt(x, y); },
  lookAtCell(cell: string) { view?.lookAtCell(cell); },
  zoomBy(f: number) { view?.zoomBy(f); },
  setZoom(z: number) { view?.cam.setZoom(z); },
  setTool(kind: string | null, squad: string | null = null) { view?.setTool(kind, squad); },
  setTick(atMs: number, secs: number) { view?.setTick(atMs, secs); },
  camera() { return view?.camera() ?? null; },
  /** 選取的個體（畫白圈） */
  select(id: string | null) { if (view) view.units.selected = id; },
  /** 顯示或暫停（回放還在用舊畫面時把新畫面停下來，不浪費電） */
  visible(on: boolean) {
    if (!view) return;
    view.host.style.display = on ? "" : "none";
    if (on) view.app.ticker.start(); else view.app.ticker.stop();
  },
  on<K extends keyof TacEvents>(ev: K, fn: TacEvents[K]) { handlers[ev] = fn; },
  stats() { return view ? { ...view.timer.stats(), clock: view.clock.state(), units: view.samples.length } : null; },
  // 回放（方案 §12）
  playReplay(frames: Frame[], speed = 1) { view?.startReplay(frames, speed); },
  stopReplay() { view?.stopReplay(); },
  replaySeek(t: number) { view?.replaySeek(t); },
  replayPlay(on: boolean) { view?.replayPlay(on); },
  replaySpeed(v: number) { view?.replaySpeed(v); },
  replayState() { return view?.replayState() ?? null; },
  /** 戰後摘要：全部項目（時間軸標記用）、摘要卡要列的、統計 */
  aar(frames: Frame[]) { const a = summarize(frames); return { ...a, headline: headline(a) }; },
  clockText,
  /** 測試與除錯用：畫面本體 */
  _view() { return view; },
};

export type { Attn, UnitSample };
(window as unknown as { Tac: typeof Tac }).Tac = Tac;
export default Tac;
