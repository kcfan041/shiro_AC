# CLAUDE.md

shiro_AC（Area Commander）：戰爭指揮遊戲，玩家是地區指揮官，透過通訊指揮由 LLM 扮演的機器人小隊。
安裝、設定、玩法見 [README.md](README.md)；設計見 [docs/README.md](docs/README.md)（索引，先讀 overview.md）。

- **網頁版**（`server.py`，`web_v2` 分支）是目前的開發重點。
- **Discord 版**（`bot.py`）凍結維護，沒被要求就不要改。

## 目錄

- `engine/`：作戰層（5×5 大格、tick、任務、預備兵力、友軍、SOP、活動區域 `zone.py`）
- `engine/tactical/`：戰術層（25×25 即時模擬、AI、破門、地圖生成）
- `web/`：aiohttp 伺服器、帳號、前端 `static/`
- `lib/llm.py`：LLM 呼叫
- `scripts/`：測試 `*_test.py`、批次統計 `*_batch.py`、效能量測 `*_bench.py`、`lag_probe.py`
- `setting/`、`data/`、`log/`、`.env`：本機設定、存檔與紀錄，不進 git（範例在 `setting example/`）

## 執行與測試

環境：conda `kuro`。

- 跑全部離線測試：`python scripts/run_tests.py`（不需要 LLM 和 Discord）
- 測試是一般腳本，不是 pytest；單獨跑一個：`python scripts/zone_test.py`
- 新增 `*_test.py` 時，要加進 `scripts/run_tests.py` 的 `OFFLINE` 清單
- 真實 LLM 測試：`python scripts/run_llm_tests.py`（需要 `.env`）
- 網頁示範（不需 LLM）：`python scripts/web_demo.py 8090`
- 截圖：`pwsh scripts/web_shot.ps1 -Hash "#tac=C3"`
- 行為改動做 A/B 對照：`python scripts/ab_batch.py --root=<舊版 worktree>`

## 工作慣例

- 文件、註解、commit 訊息都用繁體中文。
- commit 訊息用一段話寫清楚：改了什麼、為什麼、加了哪個測試案例。
- [docs/TODO.md](docs/TODO.md) 是唯一的待辦清單。做完一項，就在**同一個 commit** 裡把它移到「已完成」，並附 commit 編號。
- 延後的設計題放在 [docs/open-questions.md](docs/open-questions.md)，不要在 TODO.md 重複。
- 文件寫「現在是怎樣」，數值以程式碼為準；改了數值或規則，同一個 commit 裡更新對應的 docs（作戰層 `operations.md`、戰術層 `tactical.md`、隊長 `squad-llm.md`、介面 `interface.md`、架構 `architecture.md`）。
- `docs/archive/` 是舊文件，只供查歷史，不要更新也不要當依據。
- 行為改動要附對應的測試案例（例如 `zone_test`、`allies_test`）。

## 容易踩的坑

- 作戰層和戰術層要同步：改了其中一邊的數值（整補、空投、HP、編制），要確認另一邊也一致（sync_in／寫回）。
- 編制人數是「沒陣亡的都算」（倒地、被拖運、後送中的也算），不是「還能作戰的人數」。
- LLM 呼叫不能在 tick 鎖內同步等待，要在背景執行。
- 效能：一拍超過 1 秒會寫 log。大地圖搜尋要限制在本步走得到的範圍。
- 確定性：結果不能依賴 `set`／`dict` 的 hash 順序（`PYTHONHASHSEED` 不同結果就不同），需要挑一個時要排序或給明確的平手規則。
- 網頁版和 Discord 版的存檔分開，不要同時開同一份存檔。

## 文件

- [docs/README.md](docs/README.md)：索引
- [docs/overview.md](docs/overview.md)：核心概念、目前做到哪
- [docs/operations.md](docs/operations.md)：作戰層
- [docs/tactical.md](docs/tactical.md)：戰術層與戰鬥公式
- [docs/squad-llm.md](docs/squad-llm.md)：小隊長、服從、反思、自主等級
- [docs/interface.md](docs/interface.md)：網頁介面與呈現路線
- [docs/architecture.md](docs/architecture.md)：程式架構、時間模型、效能、已知問題、重構方向
- [docs/open-questions.md](docs/open-questions.md)：待定問題
- [docs/TODO.md](docs/TODO.md)：待辦清單
