# 舊文件（封存）

這些是 2026-10-22 統整前的文件，原樣保留，只供查歷史：當時怎麼想、每一輪批次的完整數據、修正前後的對照。
**現行設計以 `docs/` 底下的新文件為準**；數值以程式碼為準。

| 檔案 | 內容 | 統整到 |
|---|---|---|
| `design.md` | 原整體設計（Discord 時期起）、開發路線 §15、待定問題 §16 | `overview.md`、`operations.md`、`squad-llm.md`、`open-questions.md` |
| `combat.md` | 戰術層 v0.2 定稿與 §15 各輪批次結果（C1～C4、破門、性格、多指揮官） | `tactical.md`（只留現行規則與結論） |
| `simulation-architecture-proposal.md` | 多頻率模擬、單一 Battle、空間索引的提案 | `architecture.md` §重構方向 |
| `web/web-enhancement-plan.md` | 網頁強化方案（Phase 0～5、PixiJS、Unity 門檻） | `interface.md` §呈現路線 |
| `web/engine-data-gap.md` | 引擎與畫面的資料缺口、2.5 秒時間模型 | `architecture.md` §時間模型、`interface.md` |
| `web/baseline.md` | Phase 0 固定遭遇、基線數據、結構化事件格式 | `interface.md`、`architecture.md` |
| `web/character-presentation.md` | 角色呈現（戰場小人、頭像、美術取得順序） | `interface.md` §角色呈現 |
| `web/README.md` | docs/web 的索引與決策摘要 | `interface.md` |

程式碼裡的 docstring 若寫「combat.md v0.2 §N」，指的是這裡的 `combat.md`。
