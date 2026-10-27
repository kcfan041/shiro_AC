# shiro_AC — Area Commander

戰爭指揮遊戲：你是地區指揮官，透過通訊對由 LLM 扮演的機器人小隊下令。
以網頁遊玩。舊的 Discord 版已移除，最後的版本封存在標籤 `discord-final`。設計文件見 [docs/README.md](docs/README.md)。

## 安裝

```
pip install -r requirements.txt
```

戰術圖的新畫面（`web/tac/`）另外需要 Node 20.19 以上，建置：`python scripts/build_web.py`。沒有建置時戰術圖會顯示怎麼建置（log 也會提示）；開著伺服器再建置，重新整理頁面就好。

## 設定

1. 建立 `.env`：
   ```
   LLM_BASE_URL=http://localhost:20128/v1
   LLM_API_KEY=你的key
   LLM_MODEL=gpt
   ```
2. 將 `setting example` 複製為 `setting`，設定在 `setting/web.json`（`port`、`gm` GM 暱稱清單、`starter_cards`、`max_commanders`、`tick_seconds`、`tac_steps`…，完整鍵見 [docs/interface.md](docs/interface.md#2-現在的畫面)）。
3. 小隊角色卡放在 `setting/squads/<id>.json`。

## 執行

```
python server.py   # http://localhost:8080
```

- 註冊暱稱＋密碼 → 報到處「就任指揮官」→ 指揮室：常駐戰術圖（跟隨選中小隊）、迷你作戰圖與作戰圖彈窗、小隊卡片（隊員、自主等級、無線電、快捷鍵、回執、請求補給、請求後送）、即時戰況、副官、任務、交戰回放、說明、GM。
- 存檔：`data/web_world.json`、帳號 `data/accounts.json`、回放 `data/replays/`。
- 示範（不需 LLM）：`python scripts/web_demo.py 8090`，印出甲／乙／boss 的登入連結。
- 截圖（開發用）：`pwsh scripts/web_shot.ps1 -Hash "#tac=C3"`。

玩法概要見 [docs/overview.md](docs/overview.md#4-玩家怎麼玩網頁版)。

## 測試

```
python scripts/run_tests.py       # 所有離線測試（不需 LLM）
python scripts/run_llm_tests.py   # 需要 LLM 的實測，輸出在 data/llm_runs/
python scripts/encounter.py       # 固定遭遇與基線
python scripts/ab_batch.py --root=<舊版 worktree>   # 行為改動 A/B 對照
```

單一測試直接跑腳本（例如 `python scripts/zone_test.py`）。批次與效能工具列表見 [docs/architecture.md](docs/architecture.md#8-測試與工具)。

