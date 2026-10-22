# shiro_AC — Area Commander

戰爭指揮遊戲：你是地區指揮官，透過通訊對由 LLM 扮演的機器人小隊下令。
網頁版是目前的主要版本；Discord 版凍結維護。設計文件見 [docs/README.md](docs/README.md)。

## 安裝

```
pip install -r requirements.txt
```

## 設定

1. 建立 `.env`：
   ```
   LLM_BASE_URL=http://localhost:20128/v1
   LLM_API_KEY=你的key
   LLM_MODEL=gpt
   # 只有 Discord 版需要：
   TOKEN=你的bot token
   application_id=你的application id
   ```
2. 將 `setting example` 複製為 `setting`。
   - 網頁版：`setting/web.json`（`port`、`gm` GM 暱稱清單、`starter_cards`、`max_commanders`、`tick_seconds`、`tac_steps`…，完整鍵見 [docs/interface.md](docs/interface.md#2-現在的畫面)）。
     注意：範例檔目前寫 `tick_seconds: 10`，網頁版預設是 5 秒＋`tac_steps: 2`。
   - Discord 版：`setting/setting.json` 的 `owner`（GM 的 Discord ID）、`setting/ac_game.json`。
3. 小隊角色卡放在 `setting/squads/<id>.json`。

## 網頁版

```
python server.py   # http://localhost:8080
```

- 註冊暱稱＋密碼 → 報到處「就任指揮官」→ 指揮室：常駐戰術圖（跟隨選中小隊）、迷你作戰圖與作戰圖彈窗、小隊卡片（隊員、自主等級、無線電、快捷鍵、回執、請求補給、請求後送）、即時戰況、副官、任務、交戰回放、說明、GM。
- 存檔：`data/web_world.json`、帳號 `data/accounts.json`、回放 `data/replays/`。和 Discord 版分開，**兩者不要同時開同一份存檔**。
- 示範（不需 LLM）：`python scripts/web_demo.py 8090`，印出甲／乙／boss 的登入連結。
- 截圖（開發用）：`pwsh scripts/web_shot.ps1 -Hash "#tac=C3"`。

玩法概要見 [docs/overview.md](docs/overview.md#4-玩家怎麼玩網頁版)。

## 測試

```
python scripts/run_tests.py       # 所有離線測試（不需 LLM、Discord）
python scripts/run_llm_tests.py   # 需要 LLM 的實測，輸出在 data/llm_runs/
python scripts/encounter.py       # 固定遭遇與基線
python scripts/ab_batch.py --root=<舊版 worktree>   # 行為改動 A/B 對照
```

單一測試直接跑腳本（例如 `python scripts/zone_test.py`）。批次與效能工具列表見 [docs/architecture.md](docs/architecture.md#8-測試與工具)。

## Discord 版（凍結）

```
python bot.py
```

1. GM 執行 `/ac_lobby` 建立 `#報到處`。玩家按「🎖 就任指揮官」：建立自己的分類（`#指揮所` + `#<呼號>-小隊`），領一支起始小隊。
   `setting/ac_game.json`：`open_enlist`（false＝只有 GM 能按）、`starter_cards`、`max_commanders`（預設 4）。
2. 指揮官在自己的小隊頻道直接打字下令（其他人的訊息不會被當成命令）。
3. `#指揮所`（指揮官唯讀）依序是：📜 任務看板 → 副官面板（建議按鈕、更新、📦 請求補給、🔗 情報共享）→ 任務短通知（60 秒後自動刪除）。順序亂掉時 bot 會自動重排。
4. 任何人可在 `#報到處` 按「觀戰」取得唯讀觀戰身分。

| GM 指令 | 說明 |
|---|---|
| `/ac_squad_spawn <squad> [commander] [reset]` | 部署小隊；加 reset 才重新部署；squad 填 `new` 由 LLM 生成 |
| `/ac_lobby` | 建立／更新報到處，同步觀戰身分組與頻道權限 |
| `/ac_wipe` | 刪除所有 AC 頻道、觀戰身分組，重置存檔（備份為 `.bak`） |
| `/ac_link <squad> on/off` | 切斷／恢復通訊 |
| `/ac_tick [count]` | 手動推進 tick |
| `/ac_run on/off` | 暫停／繼續背景 tick |
| `/ac_status [squad]` | 戰場真相；指定小隊時顯示信念、傾向、記憶、經歷、內心想法 |
| `/ac_tmap <cell>` | 某一格的 25×25 戰術地圖、成員與敵兵位置 |
| `/ac_squad_reset` | 重置戰場（保留頻道） |

Bot 權限：Manage Channels、Manage Roles、Manage Webhooks、View Channels、Send Messages、Read Message History、Embed Links。Bot 的身分組要高於「AC 觀戰」。
伺服器擁有者／管理員不受頻道權限限制，測試權限請用一般帳號。
