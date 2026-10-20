# Phase 0 基線與 Phase E 結構化事件

狀態：已實作（`web_v2` 分支）
相關：[Web 強化執行方案](web-enhancement-plan.md)、[引擎與畫面資料缺口](engine-data-gap.md)

## 1. 固定遭遇

`scripts/encounter.py`（不需要 LLM）：

- Alpha 4 人從 B3 行軍進 C3，C3 有 3 名守軍；接敵後下令左翼包抄；接敵後第 3 個作戰 tick 在敵我之間叫一次煙幕支援。
- 連續地圖、`tac_steps=2`（每步 2.5 秒），seed 9007。
- 結果：拿下、我方 1 陣亡 2 傷、倒地 3 次、命中 19 次，約 46 個戰術步。
- `python scripts/encounter.py` 跑一場、存回放到 `data/encounter_replays/`、印出基線。
- `python scripts/encounter.py scan 9000 16` 掃其他 seed。

已知限制：掃過的 16 場都沒有急救或拖人（AI 只在「看不到敵人」或羈絆高時才去救），也沒有警戒射擊與看不到的射手。這兩類事件改用合成資料測試；之後若要讓固定遭遇涵蓋，需要另選場景。

## 2. 基線數據

| 項目 | 記錄事件前 | 記錄事件後 |
|---|---|---|
| `Battle.step` 平均／p95／最慢 | 4.1／8.8／13.9 ms | 4.1／8.9／13.8 ms |
| 整步（AI 規劃＋step＋錄影）平均／p95／最慢 | 112／275／858 ms | 111／280／855 ms |
| 單一指揮官 `world` 推送平均／最大 | 5.0／6.8 KB | 7.2／10.0 KB |
| 每步玩家可見事件平均／最多 | 0.9／4（文字） | 6.3／15（結構化） |

結論：

- 瓶頸在 AI 規劃（路徑搜尋），不是 `Battle.step`。最慢的一步約 0.85 秒，出現在第一次接敵，仍在 2.5 秒內。
- 記錄事件沒有增加可量測的耗時；推送量多約 2～3 KB。

## 3. 理解度題目（實測用，出自方案 §11.1）

看完固定遭遇的回放，不看說明，回答：

1. 敵人第一次從哪個方向開火？
2. 第一名倒地的隊員是被誰（或從哪個方向）打倒的？
3. 包抄時，哪些人在掩護、哪些人在移動？
4. 煙幕落在哪裡？擋住了誰的視線？
5. 有沒有人因為射線上有隊友而停火？
6. 戰鬥結束時，我方傷亡多少？敵人還剩幾個？

目標：主要問題答對 80%。

## 4. 核心事件（玩家最需要看懂的）

| 事件 | 引擎來源 | 畫面 |
|---|---|---|
| 發現敵人／聽到槍聲 | `detect`、`reveal_fire` | 紀錄＋大概位置「?」誤差圈 |
| 警戒射擊（移動中挨打） | `step` 移動迴圈 | 青色虛線，在移動途中出現 |
| 一般射擊／壓制射擊 | `fire`、`apply` | 一般黃線、壓制細琥珀線；同一群組「同時」標記 |
| 命中→倒地 | `_damage`、`_down`（同一 action id） | 倒在打倒他的那一槍之後；命中點閃紅圈 |
| 被壓制／抬不起頭 | `apply` 壓制量 | 紀錄（只在級別改變時）＋個體面板 |
| 投擲（手榴彈、煙、閃光） | `throw`、`throw_smoke` | 紀錄 |
| 急救／拖人 | 快照 2 的 care 階段 | 紀錄 |
| 行動理由 | AI `Plan.why`、結算阻擋 `Battle.why` | 個體面板「正在：」、放大時的標籤 |

## 5. 結構化事件格式

```json
{"seq": 812, "t": 31, "phase": "fire", "sub": 0, "type": "shot",
 "actor": "s1.alpha.2", "target": "e3", "aid": "a31.7", "grp": "t31-fire",
 "data": {"mode": "suppress", "ow": false, "rounds": 4, "weapon": "dmr",
          "frm": [52.3, 40.1], "to": [[60.2, 47.9]], "impact": ["cover"]}}
```

- `seq`：整場遞增。`t`：戰術 tick。`phase`：`pre`／`move`／`overwatch`／`post_move`／`fire`／`throw`／`care`／`end`／`support`。`sub`：移動小步。
- `aid`：因果 ID，串起射擊→命中→倒地／陣亡。ID 裡不含個體 id。
- `grp`：同時結算群組。一般射擊為 `t<n>-fire`；警戒射擊為 `t<n>-ow<小步>-braced|free`（已架槍的先結算）。
- 類型：`shot hit down kia suppress spot hear lost throw blast dazzled melee brace hold_fire flanked aid drag exit surrender transfer support posture`。
- 影格另外加上：`units[].why`（行動理由）、`units[].supp`（壓制值）、`units[].bracing`、`units[].psub`（每一步路徑在第幾個移動小步）、`shots[].aid/ow/mode`。舊欄位都保留。

## 6. 迷霧過濾（`engine/replay.filter_events`）

- 看不到的敵人：行動者和目標的 ID 拿掉；射擊只留彈道後段、去掉武器；看不到的人被命中、倒地、陣亡不送；看不到的人架槍、姿勢等不送。
- 聽到槍聲：只送引擎寫進情報的誤差位置；目標 ID 只在已有最後已知位置時保留（ghost 本來就帶 ID）。
- 敵方的情報事件不送。壓制來源只留看得到的。別人的支援不送，煙幕都送。
- 敵人的 `why`、`supp` 不送。GM 拿完整資料。

## 7. 測試

`scripts/event_test.py`（已加進 `run_tests.py`）：

- 固定遭遇的逐步狀態摘要和記錄事件前（c4ec33e）相同，且不受 `PYTHONHASHSEED` 影響。
- 事件涵蓋、序號遞增、同時結算群組、命中／倒地串到射擊、迷霧過濾（實際場次＋合成資料）。

已知的舊問題（與這次無關）：`zone_test.test_enemy_enters_and_leaves_zone` 在某些 `PYTHONHASHSEED` 下失敗（例如 2），c4ec33e 也一樣。

## 8. 還沒做的

- 2.5 秒與行動點的調整（等實測）。
- 急救／拖人、警戒射擊的代表性場景。
- 前端換 PixiJS（Phase 1）。
