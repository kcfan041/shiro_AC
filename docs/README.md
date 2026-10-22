# 文件索引

| 文件 | 內容 |
|---|---|
| [overview.md](overview.md) | 核心概念、世界觀、兩層模擬、玩法、目前做到哪。**第一次看先讀這份** |
| [operations.md](operations.md) | 作戰層：地圖、任務、整補與補給、指揮部支援、兵源、友軍、敵方 AI、殘部、多指揮官 |
| [tactical.md](tactical.md) | 戰術層：幾何、視線與發現、射擊、壓制、傷勢、士氣、武器、投擲物、破門、戰術 AI、性格 |
| [squad-llm.md](squad-llm.md) | 小隊長與 LLM：下令、服從、自主等級、回報、反思與信念、應變準則、白紙兵、副官 |
| [interface.md](interface.md) | 網頁介面現況、結構化事件與迷霧過濾、呈現路線、角色呈現、Discord 版 |
| [architecture.md](architecture.md) | 程式架構、一拍的流程、時間模型、效能、已知問題、模擬重構方向、測試工具 |
| [open-questions.md](open-questions.md) | 待定問題（延後的設計題） |
| [TODO.md](TODO.md) | 唯一的待辦清單 |
| [archive/](archive/) | 2026-10-22 統整前的舊文件，只供查歷史 |

## 規則

- 寫「現在是怎樣」，不寫「當時怎麼想」。歷史看 git 與 archive。
- 數值以程式碼為準；改了程式的數值，同一個 commit 裡更新對應文件。
- 可執行的待辦只放 TODO.md；還沒決定的設計題只放 open-questions.md；決定了就寫進對應文件並從那裡刪掉。
