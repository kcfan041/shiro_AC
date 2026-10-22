# 程式架構、時間模型、效能

程式怎麼分層、一拍裡發生什麼、時間怎麼算、效能瓶頸在哪，以及之後的模擬重構方向。
相關：[戰術層](tactical.md)、[作戰層](operations.md)、[網頁介面](interface.md)。

## 1. 目錄

| 路徑 | 內容 |
|---|---|
| `server.py`、`web/` | 網頁版：aiohttp 伺服器（`app.py`）、帳號（`accounts.py`）、前端 `static/` |
| `bot.py`、`cmds/` | Discord 版（凍結） |
| `engine/` | 遊戲核心，不依賴介面：`sim.py`（作戰 tick、命令、事件、反思）、`models.py`、`store.py`、`scenario.py`、`missions.py`、`enemy_ai.py`、`enemy_tactics.py`、`allies.py`、`reserve.py`、`remnants.py`、`roster.py`、`support.py`、`obedience.py`、`prompts.py`、`persona.py`、`sop.py`、`views.py`（玩家視圖）、`replay.py`（回放、事件過濾）、`engagement.py`、`zone.py` |
| `engine/tactical/` | 戰術層，見 [tactical.md §20](tactical.md#20-檔案) |
| `engine/scenarios/` | 場景資料 |
| `lib/llm.py` | LLM 客戶端 |
| `scripts/` | 測試 `*_test.py`、批次 `*_batch.py`、效能 `*_bench.py`、`lag_probe.py`、`encounter.py`、`ab_batch.py`、`aid_check.py` |
| `setting/`、`data/`、`log/`、`.env` | 本機設定、存檔、紀錄（不進 git；範例在 `setting example/`） |

## 2. 分層與資料流

```
世界真相（engine，World）
  ├─ 作戰層：5×5 格、小隊、敵軍單位、任務、補給、兵源
  └─ 戰術層：每個活動區域一場 Battle（個體位置、彈道、情報）
        ↕ sync_in／寫回（位置、傷勢、彈藥、投擲物、熟練度、壓制、倒地、士氣）
指揮官情報表（每位指揮官一份）
  ↓ views.py／replay.filter_events（迷霧過濾）
前端（只拿得到情報表與過濾後的事件）
```

- **真相與認知分離**：引擎只模擬真相；每位指揮官（含副官）只能讀自己的情報表，透過回報、偵察、無人機更新。「有限資訊」由架構保證，不靠前端隱藏。
- **作戰層和戰術層要同步**：改了其中一邊的數值（整補、空投、HP、編制），要確認另一邊也一致。
- 每支玩家小隊的戰術 side 是 `blue|<小隊 id>`；同格我方共用一份戰術情報。

## 3. 一拍（作戰 tick）

1. 收命令（命令進佇列，下一拍生效；快捷按鈕當場擲服從判定）。
2. 敵方作戰層 AI、友軍、支線、空投、增援。
3. `_run_zones()`：重建活動區域，每個區域的 Battle 跑 `tac_steps` 個戰術步（網頁版 2 步）；每步 AI 規劃 → `Battle.step()` → 錄影 → 寫回。
4. 交戰事件 → 排隊觸發隊長 LLM（背景執行）。
5. 更新指揮官情報、推送前端、存檔。

- **LLM 一律在背景跑**，不在 tick 鎖內同步等待。打字下令、副官更新呼叫 LLM 時也不佔住全域鎖，兩位指揮官同時下令不互相排隊。
- 一拍超過 1 秒寫 log（`scripts/lag_probe.py` 量測）。

## 4. 時間模型

| 名詞 | 意義 | 現況 |
|---|---|---|
| 作戰 tick | 作戰層一拍 | 網頁版 5 秒（`tick_seconds`），Discord 版 10 秒 |
| 戰術步 | 位置、彈道、傷害結算一次 | 2.5 秒（`step_seconds = tick_seconds / tac_steps`） |
| 網路推送 | 伺服器多久推一次 | 跟戰術步走 |
| AI 決策 | AI 多久重新規劃 | 每個戰術步都完整規劃 |
| 畫面更新 | 客戶端每秒畫幾次 | 前端自己決定 |

問題：2.5 秒同時被當成模擬粒度、遊戲時間、推送間隔與播放時長；而接敵 2 AP、行軍 6 AP 是從舊的 10 秒 tick 沿用下來的。
以 tick 計的作戰層規則（任務時限、整補、冷卻）在網頁版沒有換算，實際時間是 Discord 版的一半。
這四件事應該分開定義，但要等實測資料（用結構化事件量）再決定，見 [待定問題](open-questions.md#時間與節奏)。

## 5. 效能

| 項目 | 數值 | 來源 |
|---|---|---|
| `Battle.step` | 平均 4.1 ms、p95 8.9 ms、最慢 13.8 ms | 固定遭遇（`scripts/encounter.py`） |
| 整步（AI 規劃＋step＋錄影） | 平均 111 ms、p95 280 ms、最慢 855 ms（第一次接敵） | 同上 |
| 連續地圖約 36～40 人 | 每作戰 tick 平均 0.14～0.2 秒、最慢約 0.6 秒 | `scripts/zone_bench.py` |
| 存檔每拍（加友軍後） | 約 250 ms（加友軍前約 100 ms） | TODO P2 |
| 單一指揮官 world 推送 | 平均 7.2 KB、最大 10 KB | baseline |

結論：**瓶頸在 AI 規劃（路徑搜尋），不在戰鬥結算。** 單純提高模擬頻率或只最佳化 `Battle.step` 效果有限。
已做的最佳化：視線用空間網格、人體碰撞體快取、AI 暴露判定每步快取、路徑搜尋限制在本步走得到的範圍、伏擊轉移搜尋縮小、啟動先建全圖、反思閒聊改背景。

**容量估計**（現行架構、每步要在 2.5 秒內做完，推估不是量測）：同一 Battle 40 人以下有資料支持；60～80 人是合理的第一版上限；80～120 要實測；150 以上很可能超時。多個 Battle 依序執行，要算總和。

## 6. 已知問題

- **戰區合併、分裂會遺失狀態**：合併時只保留一個舊 Battle，其他的丟掉，下一步從世界資料重建；分裂時只有一邊沿用。
  會保留：位置、姿勢、傷勢、彈藥、投擲物、熟練度、壓制、倒地、小隊士氣。
  會遺失：情報（`spotted`、`last_known`、`believed_seen`）、煙霧、架槍、敵方士氣、AI 內部狀態（耐心等）、回報去重、尚未結算的經驗、事件時間線。回放也會被切成好幾段。
- **跨 Battle 看不到、打不到**：偵測、射擊、壓制、情報、士氣都只查同一個 Battle。沒合併的兩個戰區至少隔一格（50 m），但偵測 90 m、射程 80 m，所以 50～90 m 可能有漏洞（未用精確距離測試確認）。
- 轉隊改 uid 時，情報裡舊 uid 的 key 沒一起改；單位離開時沒清 `believed_seen`。
- `_morale_tick` 的兵力比、「沒人看得到」沒有距離上限，連續地圖時算整個戰區。
- `zone_test.test_two_players_zones_merge_and_split` 只測了合併，沒測分裂與狀態延續。
- 連續地圖行軍速度：以前的紀錄是「約 1.5 m/s、一格約 35 秒」，但程式規則是 6 AP／步（上限約 12 m／2.5 秒），實際速度受縱隊等待影響，需要重新量。

## 7. 重構方向（提案，尚未實作）

原文：[archive/simulation-architecture-proposal.md](archive/simulation-architecture-proposal.md)。

共同原則（參考 FPS、RTS lockstep、MMO 興趣管理、EVE 時間膨脹）：**便宜、玩家敏感的東西高頻跑；昂貴、可以沿用的東西低頻或有事件時才跑。**
不照搬的原因：FPS 的「決定要做什麼」由玩家大腦做，這裡是伺服器替大量士兵做；lockstep 會讓客戶端持有完整世界、洩漏迷霧，且 Python 難保證跨平台確定性；CPython 有 GIL，平行要靠多行程。

| 階段 | 內容 | 改行為嗎 |
|---|---|---|
| 0 | 量測：40～200 人、各種情境，分開計時規劃、step、detect、sync、過濾、序列化 | 否 |
| 1 | 空間索引（例如 25 m 一格，只查附近）＋修明顯浪費（`trace` 每條彈道排序全部單位、`update_beliefs` 重複計算） | 否（`_morale_tick` 加距離上限除外） |
| 2 | 拆開網路迴圈與模擬，規劃花 500 ms 時仍能收命令、推上一個狀態 | 否 |
| 3 | 沿用計畫：只有新命令、新發現、目標消失、路徑被擋、壓制跨門檻、傷亡、計畫過期才重新規劃 | 是 |
| 4 | **單一 Battle**，戰區只當「誰要完整模擬」的啟用範圍；順便解決 §6 的遺失與漏洞；回放改以交火切段 | 是 |
| 5 | 規劃改成「讀不可變快照、輸出純計畫」：移除規劃時暫改 `u.pos`、`u.exited`、共用快取 | 否 |
| 6 | 常駐工作行程平行規劃（粒度到小隊／敵方群組）＋每步時間預算 | 否（超時降級除外） |
| 7 | 多頻率戰術執行（較短的固定步長） | 是，要重調平衡 |
| 8 | AI 分級（近戰逐人完整 AI／附近小隊級／遠方作戰層）＋過載處理（降級或時間變慢） | 是 |

提議的頻率（待實測）：畫面 60 Hz、網路 10～20 Hz、戰術執行 5～10 Hz、感知 2～5 Hz、AI 規劃 0.5～1 Hz＋事件觸發、世界 0.2～1 Hz。

規範：不改行為的階段，`scripts/event_test.py` 的 digest 必須不變、`ab_batch.py` 新舊統計一致；改行為的階段要明確更新 golden 並記錄差異。

要補的測試：跨戰區距離邊界（59/60/61、79/80/81、89/90/91 m）、合併→分裂→再合併狀態延續、離開並重新進入啟用範圍、`ab_batch` 輸出信賴區間並在超出容許差異時失敗。

初步驗收目標：命令 200 ms 內確認、不受規劃影響；單一 Battle 80 人 p95 < 1.0 秒、最大 < 2.5 秒；擴充目標 150～200 人 p95 < 1.25 秒。

## 8. 測試與工具

- 離線測試：`python scripts/run_tests.py`（不需 LLM、Discord）。清單在 `OFFLINE`：multi、mission、layout、belief、map、enemy、tactical、tworld、engage、enemy_tactics、awareness、breach、reserve、persona、remnant、handover、sop、multi_engage、web_engine、web、zone、event、allies。新增 `*_test.py` 要加進去。
- 真實 LLM 測試：`python scripts/run_llm_tests.py`（輸出在 `data/llm_runs/`；只檢查能跑完，內容要看輸出）。連續地圖、網頁版節奏的是 `zone_play_test.py`，其他腳本是單格舊模式。
- 固定遭遇：`python scripts/encounter.py`（Alpha 4 人從 B3 進 C3 打 3 名守軍，seed 9007）；`event_test.py` 檢查逐步狀態摘要不受 `PYTHONHASHSEED` 影響。
- 行為改動做 A/B：`python scripts/ab_batch.py --root=<舊版 worktree>`。
- 批次：`tactical_batch`、`engage_batch`、`breach_batch`、`sop_batch`、`remnant_batch`、`pace_batch`、`ff_batch`、`zone_batch`、`support_batch`；救援統計 `aid_check.py`；效能 `zone_bench`、`tactical_bench`、`lag_probe`。
- 網頁示範（不需 LLM）：`python scripts/web_demo.py 8090`；截圖：`pwsh scripts/web_shot.ps1 -Hash "#tac=C3"`。
