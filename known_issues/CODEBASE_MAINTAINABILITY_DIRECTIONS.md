# 代碼庫可維護性改進方向（記錄用）

**狀態：** 方向記錄（2026-07-25），**待工程師詳盡調查與分析**；前置的翻譯標籤洩漏重構已於 2026-07-26 全部完成並結案，啟動條件已達成
**性質：** 非緊急、非阻斷性問題；此文件只記錄方向與證據位置，不構成實作計畫
**關聯文件：**
- 已結案的重構：[`TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md`](./resolved/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md)（2026-07-26 結案，原「完成前本文件所列項目不動工」的限制已解除）
- 模組邊界現況：[`../docs/MODULE_BOUNDARIES.md`](../docs/MODULE_BOUNDARIES.md)

## 1. 背景

2026-07-25 翻譯標籤重構 Phase 3 驗收期間，對 translate／publish／analysis 三個模組做了一次突變測試（mutation testing）與重複執行檢查，藉此檢驗測試有效性。測試整體被證實有效（四項突變全數被抓到），但過程中發現數個可維護性議題，整理如下供後續調查。

## 2. 發現的問題與方向

### 2.1 無 CI  pipeline，測試品質靠人肉把關

- **證據：** `AGENTS.md` 明載「There is no repo-wide build or CI pipeline yet」。
- **影響：** 本次發現的 flaky test（見 §2.2）只要有 CI 反覆執行早就會現形；PR 目前依賴人工記得跑對測試指令。
- **方向：** 建立最小 CI（跑三個 pytest 套件 + site 的 vitest/type-check/build），並納入「重複執行 N 次抓 flake」的做法。

### 2.2 tests package 撞名，跨模組測試無法同一 invocation 執行

- **證據：** `modules/publish/tests` 與 `modules/analysis/tests` 在同一 pytest invocation 下因 tests package 同名而收集失敗，目前 workaround 是分開執行（已記於 [`TRANSLATION_LABEL_LEAKAGE_HANDOFF.md`](./resolved/TRANSLATION_LABEL_LEAKAGE_HANDOFF.md) §6）。
- **影響：** 妨礙 §2.1 的 CI 單一指令化；新人容易踩到。
- **方向：** 調整 tests 目錄的 package 結構（例如加 `__init__.py` 命名空間或改 rootdir 配置），讓全倉測試可一鍵執行。

### 2.3 orchestrator 單檔過大（god module 傾向）

- **證據：** `modules/publish/src/orchestrator.py` 約 880 行，包辦 slug 生成、payload 驗證、DB 交易、檔案發射、index/archive 聚合、staging/backup/rollback；`modules/translate/src/orchestrator.py` 約 714 行同型。兩檔註解中已自然分出 Phase A/B/C 段落。
- **影響：** 功能持續增加後導航與審查成本上升；本次標籤重構已讓檔案繼續長大。
- **方向：** 依既有 Phase 註解切分為子模組（如 validation／emission／promotion 分檔）。**時機建議在重構全部收斂後**，避免與進行中的 Phase 4/5 衝突。

### 2.4 測試不夠「多疑」：缺乏 flake 偵測與故障注入的確定性設計

- **證據：** `test_promotion_midway_failure_reversion` 的故障注入把 `os.replace` 全域下毒，連 rollback 自己的還原呼叫也被波及，加上 staging 檔案以無序 set 迭代，約每 6–10 次執行隨機失敗一次（2026-07-25 已修復：sorted 提升順序 + 固定注入點 + 全目錄樹快照斷言，見 commit `605b8db`）。
- **教訓：** 故障注入測試的注入範圍必須排除被測系統的自救路徑；依賴隨機順序的測試需要重複執行才能驗證穩定。
- **方向：** 把「突變測試抽查 + 套件連跑 N 次」納入驗收慣例或 CI；盤點其他含隨機性的測試（set/dict 迭代、時間戳同秒）。

### 2.5 文件流程偏重，需防止文件與程式漂移

- **證據：** 本次重構 Phase 1 需同步更新 21 份契約文件；現有「任何 scaffold/schema/狀態遷移必須同變更更新 module docs」規則（AGENTS.md）。
- **影響：** 文件量大本身是資產也是負擔，長期有漂移風險。
- **方向：** 調查哪些契約斷言可以機器化（例如 DDL 與 DATA_CONTRACT 的一致性檢查、export JSON 與 schema fixture 的自動比對），讓文件由測試背書而非只靠紀律。

## 3. 非範圍

- 不解決任何功能性 bug（目前無已知未解功能性 bug）。
- 不改變翻譯標籤重構的既定範圍與 Phase 順序；本文件所有項目皆排在其後。
- 不預設技術選型（CI 平台、目錄重構方式待調查後定案）。

## 4. 建議的調查順序（供參考，非定案）

1. §2.1 + §2.2 一併處理（CI 需要單一測試指令，兩者互相解鎖），投資報酬率最高。
2. §2.4 納入 CI 後自然落實。
3. §2.3 待重構收斂、程式碼穩定後再拆。
4. §2.5 為長期項，可在 CI 就位後逐步把契約斷言機器化。
