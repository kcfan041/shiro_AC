# 程式架構、時間模型、效能

程式怎麼分層、一拍裡發生什麼、時間怎麼算、效能瓶頸在哪，以及已採用的模擬重構路線。
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

- **LLM 一律在背景跑**，不在 tick 鎖內同步等待（`scripts/lock_test.py` 檢查）：網頁版（`background`）把「準備資料（鎖內）→ LLM（鎖外）→ 寫回（鎖內）」拆開，
  包括事件反應、反思、換人帶隊、閒聊、增援（先抽人、回來才編成）、殘部無線電、殘部自成小隊。打字下令、副官更新也不佔住全域鎖。
- 一拍目前直接在網頁伺服器的事件迴圈上同步執行：慢的一拍跑完前，所有連線都會等（重構第 3 步要拆開）。
- 一拍超過 1 秒寫 log，列出各段耗時（含「等鎖」與「未分類」）；`scripts/lag_probe.py` 量測。

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
| 連續地圖約 36～40 人 | 每作戰 tick 平均 0.14～0.2 秒、最慢約 0.6 秒 | `scripts/zone_bench.py`（較早） |
| 同一 Battle 36／72／108 人，每步 | 平均 0.34／0.91／2.39 秒，最慢 0.91／1.83／4.06 秒（接近平方成長；108 人跟不上 2.5 秒一步） | `zone_bench.py` 加倍人數，2026-10-23，32 核 AMD、Python 3.10 |
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

## 7. 模擬重構（已採用，分支 `sim_v3`）

原始提案：[archive/simulation-architecture-proposal.md](archive/simulation-architecture-proposal.md)。進度見 [TODO.md](TODO.md) 的「模擬重構」。

**目標**：一次改到好，不做過渡性的假動畫。
1. **不卡**：LLM 不在鎖內等；模擬不在網頁事件迴圈上跑；AI 規劃不必每步都做。
2. **動作有真實時間**：現在一步 2.5 秒內所有人同時決定、所有一般射擊以同一個快照一起結算，前端只能把射擊擠在每步 88～96% 的瞬間播，看起來是「所有人同一瞬間動作」。
   改成每個動作有持續時間（瞄準、點放、連發、裝填、換姿勢、走一段路，以秒計），各自的計時器；執行步縮短（初定 0.5 秒）、AI 規劃約每秒一次＋事件觸發；先完成的先生效（被打倒的人打不出後面那一槍）。
   「每步 2 AP」改成「動作花幾秒」，戰術層約 20 個以步計的常數與每步比率（倒地 12 步、煙霧 6 步、架槍 2 步、耐心、伏擊轉移、破門等待、敵方重評、脫離接觸、壓制消退、士氣 ±、投降機率）全部換成秒。
3. **前端照實播放**：客戶端延後一個更新、在兩次更新之間連續內插，依事件的真實時間戳播放。
4. **單一 Battle**：戰區只當「誰要完整模擬」的範圍，解決 §6 的合併分裂遺失與 50～90 m 漏洞。

共同原則（參考 FPS、RTS lockstep、MMO 興趣管理、EVE 時間膨脹）：**便宜、玩家敏感的東西高頻跑；昂貴、可以沿用的東西低頻或有事件時才跑。**
不照搬的原因：FPS 的「決定要做什麼」由玩家大腦做，這裡是伺服器替大量士兵做；lockstep 會讓客戶端持有完整世界、洩漏迷霧，且 Python 難保證跨平台確定性；CPython 有 GIL，平行要靠多行程。

| 步 | 內容 | 規模 | 改戰鬥結果 |
|---|---|---|---|
| 1 | LLM 移出 tick 鎖、補齊計時（在 `web_v2` 完成） | S | 否 |
| 2 | 量測工具：40～120 人、多種情境，分開計時規劃、結算、偵測、同步、過濾、序列化 | S | 否 |
| 3 | 模擬移出網頁事件迴圈：網路照常收命令、推上一個完成的狀態 | M | 否 |
| 4 | 沿用計畫、事件觸發重新規劃（新命令、新發現、目標消失、被擋、壓制跨門檻、傷亡、計畫過期） | M～L | 是 |
| 5 | 空間索引（只查附近）＋修明顯浪費（`trace` 每條彈道排序全部單位、`update_beliefs` 重複計算） | M | 否（`_morale_tick` 加距離上限除外） |
| 6 | 有時間的動作＋短執行步（上面目標 2） | L | 是 |
| 7 | 單一 Battle，回放改以交火切段 | L | 是 |
| 8 | 前端：延後一個更新內插＋依時間戳播放 | M | 否 |
| 9 | 統一調一次平衡，整份重寫 `tactical.md` | M～L | 是 |
| 之後 | 規劃快照化、多行程平行規劃、AI 分級、過載處理 | L | 視情況 |

做法：
- 4、6、7 會改戰鬥結果。中間每一步只保證正確性（測試、確定性、不重生不回血），**不調數值**；全部到位後第 9 步用 `ab_batch`、批次情境一次調完。
- 不改結果的步驟：`scripts/event_test.py` 的摘要不變、`ab_batch.py` 新舊統計一致。改結果的步驟：明確更新摘要並記錄差異。
- 大改期間平衡會走樣，`web_v2` 保持可玩；P1 的介面項目（命令進度、AAR）等合回來再做，避免做兩次。
- 確定性：動作有時間後，同一時刻的平手要有明確規則，不能依處理順序或 hash 順序。
- 存檔：進行中交戰的格式會變，舊存檔可能要重置世界。

要補的測試：跨戰區距離邊界（59/60/61、79/80/81、89/90/91 m）、合併→分裂→再合併狀態延續、離開並重新進入啟用範圍、`ab_batch` 輸出信賴區間並在超出容許差異時失敗。

初步驗收目標：命令 200 ms 內確認、不受模擬耗時影響；單一 Battle 80 人 p95 < 1.0 秒、最大 < 2.5 秒（換成短執行步後依新步長重訂）。

開工前要決定的見 [待定問題](open-questions.md#模擬架構)。

## 8. 測試與工具

- 離線測試：`python scripts/run_tests.py`（不需 LLM、Discord）。清單在 `OFFLINE`：multi、mission、layout、belief、map、enemy、tactical、tworld、engage、enemy_tactics、awareness、breach、reserve、persona、remnant、handover、sop、multi_engage、web_engine、web、zone、event、allies。新增 `*_test.py` 要加進去。
- 真實 LLM 測試：`python scripts/run_llm_tests.py`（輸出在 `data/llm_runs/`；只檢查能跑完，內容要看輸出）。連續地圖、網頁版節奏的是 `zone_play_test.py`，其他腳本是單格舊模式。
- 固定遭遇：`python scripts/encounter.py`（Alpha 4 人從 B3 進 C3 打 3 名守軍，seed 9007）；`event_test.py` 檢查逐步狀態摘要不受 `PYTHONHASHSEED` 影響。
- 行為改動做 A/B：`python scripts/ab_batch.py --root=<舊版 worktree>`。
- 批次：`tactical_batch`、`engage_batch`、`breach_batch`、`sop_batch`、`remnant_batch`、`pace_batch`、`ff_batch`、`zone_batch`、`support_batch`；救援統計 `aid_check.py`；效能 `zone_bench`、`tactical_bench`、`lag_probe`。
- 網頁示範（不需 LLM）：`python scripts/web_demo.py 8090`；截圖：`pwsh scripts/web_shot.ps1 -Hash "#tac=C3"`。
