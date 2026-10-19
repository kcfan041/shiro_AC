# shiro_AC — Area Commander

以 Discord 為介面的戰爭指揮遊戲：你是地區指揮官，透過通訊對由 LLM 扮演的機器人小隊下令。
設計文件見 [docs/design.md](docs/design.md)。

## 安裝

```
pip install -r requirements.txt
```

## 設定

1. 建立 `.env`：
   ```
   TOKEN=你的bot token
   application_id=你的application id
   LLM_BASE_URL=http://localhost:20128/v1
   LLM_API_KEY=你的key
   LLM_MODEL=gpt
   ```
2. 將 `setting example` 複製為 `setting`，並在 `setting/setting.json` 填入 `owner`（Discord 使用者 ID，GM 權限）。
3. 小隊角色卡放在 `setting/squads/<id>.json`；遊戲參數在 `setting/ac_game.json`。

## 執行

```
python bot.py      # Discord 版（凍結維護）
python server.py   # 網頁版：http://localhost:8080
```

### 網頁版

- 設定：`setting/web.json`（範例在 `setting example/web.json`）：`port`、`gm`（GM 暱稱清單）、`starter_cards`、`max_commanders`。
- 存檔：`data/web_world.json`、帳號 `data/accounts.json`、回放 `data/replays/`（與 Discord 版分開，兩者不要同時開同一份存檔）。
- 註冊暱稱＋密碼 → 報到處「就任指揮官」→ 指揮室：作戰圖（點格子移動／偵察）、小隊卡片（隊員裝備與熟練度、自主等級、無線電與快捷鍵、回執）、
  戰術畫面（25×25 即時動畫、迷霧、標定點、指揮部支援：空投／煙幕／無人機）、交戰回放、副官、任務、說明、GM 頁。
- 示範（不需 LLM）：`python scripts/web_demo.py 8090`，印出甲／乙／boss 的登入連結。
- 截圖（開發用）：`pwsh scripts/web_shot.ps1 -Hash "#tac=C3"`（伺服器與無頭瀏覽器會自動關閉）。

## MVP 玩法

1. GM 執行 `/ac_lobby` 建立 `#報到處`。玩家按「🎖 就任指揮官」：建立自己的分類（`#指揮所` + `#<呼號>-小隊`），
   並領一支起始小隊（依 `starter_cards` 順序挑別人沒在用的角色卡）。GM 也可用 `/ac_squad_spawn <卡> [commander]` 指定。
   - `setting/ac_game.json`：`open_enlist`（false＝只有 GM 能按）、`starter_cards`、`max_commanders`（預設 4）。
   - 多位指揮官共用戰場與預備兵池；同格友軍共享敵情、互相急救，副官也會看到其他指揮官小隊的位置（不能指揮）。
2. 指揮官在自己的小隊頻道直接打字下令（其他人的訊息不會被當成命令）。
3. `#指揮所` 的副官面板提供現況、建議行動按鈕、更新、🔗 情報共享（開放唯讀給指定的人）。
4. 任何人可在 `#報到處` 按「觀戰」取得唯讀觀戰身分。
5. `#指揮所`（指揮官唯讀）的訊息順序固定為：📜 任務看板 → 副官面板 → 任務短通知（60 秒後自動刪除）。
   看板與面板只會被編輯；被刪除或順序亂掉時 bot 會自動重排（啟動時也會檢查）。
6. **整補**：下令小隊回 A3 南側集結點（後方據點），抵達後原地固守／隱蔽約 6 tick（1 分鐘），期間逐步修復，
   完成時彈藥補滿、傷員修復、士氣回穩，缺員時補充新兵。副官面板顯示「🔧 整補中 n/6」。
   在據點接敵或改做其他行動會中斷整補。
7. **前線補給空投**：副官面板「📦 請求補給」或在小隊頻道說「幫你們叫補給」。約 1 分鐘後空投到小隊所在格；
   附近有敵軍可能被攔截；冷卻約 10 分鐘。
8. **任務看板**含戰情圖（依你的情報）、兵力摘要、最近戰況。
9. **小隊長會成長**：重大事件後會反思，形成自己的信念與行事傾向（例如開始對可疑命令要求確認）。
   GM 可用 `/ac_status <squad>` 查看信念、傾向、經歷與內心想法。
10. **增援**：小隊全滅或人數過半損失時，指揮部會派一支 LLM 生成的新小隊到後方據點。
11. **地圖**：5×5 廢棄城區（`engine/scenarios/urban.json`），我方後方據點 A3，敵方據點 E2／E4。
    敵軍會巡邏、增援、撤退合併、反攻任務地點，偶爾派小隊騷擾我方後方。

> 伺服器擁有者／管理員不受頻道權限限制，測試權限效果請用一般帳號。

GM 指令：

| 指令 | 說明 |
|---|---|
| `/ac_squad_spawn <squad> [commander] [reset]` | 暫時用：部署小隊；已存在不會重置，加 reset 才重新部署；squad 填 `new` 由 LLM 生成全新小隊 |
| `/ac_lobby` | 建立／更新報到處，同步觀戰身分組與所有指揮官頻道權限 |
| `/ac_wipe` | 測試用：確認後刪除所有 AC 頻道／分類（含舊版殘留）、觀戰身分組，並重置存檔（備份為 `.bak`） |
| `/ac_link <squad> on/off` | 切斷／恢復通訊（測試失聯與補報） |
| `/ac_tick [count]` | 手動推進 tick |
| `/ac_run on/off` | 暫停／繼續背景 tick |
| `/ac_status [squad]` | 查看戰場真相；指定小隊時顯示信念、傾向、長期記憶、經歷與內心想法（僅自己可見） |
| `/ac_tmap <cell>` | 查看某一格的 25×25 戰術地圖、我方成員（武器、座標）與敵兵位置 |
| `/ac_squad_reset` | 重置戰場（保留頻道） |

離線測試（不需 Discord）：

```
python scripts/sim_test.py      # 單小隊完整流程（需 LLM）
python scripts/card_test.py     # 比較所有角色卡對同一命令的反應（需 LLM）
python scripts/multi_test.py    # 多指揮官隔離與重複部署（不需 LLM）
python scripts/multi_play_test.py # 兩位指揮官同格作戰、同時下令、副官分開（需 LLM）
python scripts/run_llm_tests.py # 一次跑完所有需要 LLM 的實測，輸出在 data/llm_runs/（需 LLM）
python scripts/web_engine_test.py # 網頁版引擎：畫面資料、回執、自主等級、影格回放、支援（不需 LLM）
python scripts/web_test.py      # 網頁伺服器：登入、隔離、WebSocket、迷霧、GM 權限、回放（不需 LLM）
python scripts/support_batch.py # 指揮部支援批次比較（不需 LLM）
python scripts/pace_batch.py    # 撤退／固守找掩體／進場傷亡（不需 LLM）
python scripts/ff_batch.py      # 我方誤擊統計（不需 LLM）
python scripts/zone_test.py     # 連續地圖：跨格行軍、跨格射擊、敵軍進出區域、區域合併（不需 LLM）
python scripts/zone_batch.py    # 連續地圖：行軍接敵距離、撤退（不需 LLM）
python scripts/zone_bench.py    # 連續地圖效能
python scripts/mission_test.py  # 任務／補給／新兵／支線（不需 LLM）
python scripts/layout_test.py   # 指揮所訊息順序（看板／面板／短通知）（不需 LLM、不需 Discord）
python scripts/dialogue_test.py # 命令理解與回報節奏（需 LLM）
python scripts/play_test.py     # 偵察→接敵→撤回整補→新兵→閒聊 的實際流程（需 LLM）
python scripts/belief_test.py   # 信念／反思／驗證／空投／增援／戰情圖（不需 LLM）
python scripts/reflect_test.py  # 反思品質與角色卡生成（需 LLM）
python scripts/map_test.py      # 5×5 場景、尋路、場景檔（不需 LLM）
python scripts/enemy_test.py    # 敵方 AI（不需 LLM）
python scripts/run_tests.py     # 一次跑完所有不需 LLM 的測試
python scripts/tworld_test.py   # 永久戰術地圖與個體化（C1）
python scripts/engage_test.py   # 作戰層交戰改由戰術層結算（C2）與隊長戰術選單（C3）
python scripts/combat_play_test.py  # 需要 LLM：交戰中隊長選戰術、包抄與撤退
python scripts/enemy_tactics_test.py  # 敵方戰術指揮（C4）
python scripts/awareness_test.py      # 自我暴露推算（「我們被看到了嗎」）
python scripts/breach_test.py         # 投擲物配置與撿取、架槍、閃光彈、門與房間、破門
python scripts/breach_batch.py 40     # 破門批次（參數組合 vs 突擊、壓制推進；室內霰彈槍）
python scripts/reserve_test.py        # 隊員人格（白紙兵）、預備兵池、遊蕩生化人
python scripts/persona_test.py        # 性格 × 人格值影響戰術 AI、交戰後演化
python scripts/remnant_test.py        # 綠方與營救失聯友軍殘部（建築內、對峙 20 tick）
python scripts/remnant_batch.py 20    # 營救批次：沒人救 vs 不同時間抵達
python scripts/handover_test.py       # 換人帶隊、口氣跟著變、每人一份信念
python scripts/sop_test.py            # 應變準則（備援、服從度限制、簡易隊長）
python scripts/multi_engage_test.py   # 兩位指揮官：同卡並存、同格各自戰術、救助與後送、優先歸隊
python scripts/sop_batch.py 30        # 街區平手：沒有隊長 vs 簡易隊長
python scripts/reserve_play_test.py   # 需要 LLM：從預備兵池組增援小隊
python scripts/remnant_play_test.py   # 需要 LLM：殘部求救、救出後自成小隊
python scripts/handover_play_test.py  # 需要 LLM：隊長倒下後接手者口氣不同
python scripts/engage_batch.py 10     # 交戰批次統計（敵軍規模／類型 × 我方戰術）
python scripts/tactical_bench.py  # 戰術層效能與開局可見性
```

需要的 bot 權限：Manage Channels、Manage Roles、Manage Webhooks、View Channels、Send Messages、
Read Message History、Embed Links。Bot 的身分組需高於「AC 觀戰」才能發放該身分組。
