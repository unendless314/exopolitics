# Translate 測試檔拆分維護計畫（test_five_field_contract / test_state）

**狀態：** 已定案，排入下週維護計畫（2026-08-01 暫緩，待兩個 P1 修正完成後執行）
**日期：** 2026-08-01
**來源：** TRANSLATE_TEST_MAINTAINABILITY_PLAN 交付後的 Code Review（request changes）審查意見
**範圍：** `modules/translate/tests/test_five_field_contract.py` 與 `modules/translate/tests/test_state.py` 的純測試拆分
**非範圍：** 不改變任何 runtime 行為、不增刪測試覆蓋規則、不處理其他模組的測試檔

## 1. 背景

translate 測試可維護性方案（Phase 0–4）交付後，Code Review 對兩個大型測試檔提出維護性關切。審查結論明確指出這**不是 release-blocking defect**，而是後續維護改進：

- `test_five_field_contract.py`（1,297 行、44 個 contract tests）並非單一冗長程序，而是按 fingerprint、handoff shape、response validation、quality validation、bypass/stale、failure safety 分群的精確測試集合。審查確認其體積**不是**未移除的 legacy 重複情境造成（Phase 4 已移除 9 個舊 scenario tests 並收斂 `test_translate.py` 為 3 個 journey tests）。
- `test_state.py`（約 876 行、17 個測試）是 Phase 3 新建檔案，體積來自高價值的 public state-machine 覆蓋（queue eligibility、retry locking、force rerun、dry-run/preview、batch selection、ordering、summary），非繼承的無效舊碼。

審查建議的執行順序：**先完成兩個 P1 修正（permanent 4xx 邏輯鎖定、非正整數 batch-size 拒絕），再安排本拆分**。

## 2. 建議拆分方向

### test_state.py（依行為拆分，審查建議）

- `test_queue_state.py`：bulk queue eligibility matrix、retry_count 遞增與邏輯鎖定、stale detection 行為
- `test_force_rerun.py`：locked failed force、fresh completed force（成功原子覆寫／失敗完整保留）、stale force 走正常 retry
- `test_run_modes.py`：dry-run（呼叫 API 但不寫入、不標 stale）、preview（不呼叫不寫入）
- `test_batch_selection.py`：來源文章計數、同篇語言不拆分、已完成語言不重做、deterministic ordering、summary 計數

### test_five_field_contract.py（依現有 class 分群拆分）

- fingerprint（`TestContentFingerprint`）
- handoff shape（`TestHandoffAssemblerFiveField`、`TestUpstreamBulletShapeEnforcement`）
- response schema validation（`TestResponseSchemaValidation`）
- quality validation（`TestTranslationQualityValidation`）
- bypass / stale / failure safety（`TestBypassAndStale`、`TestFailureSafety`）

## 3. 執行約束

1. 純測試重構，**不得**夾帶任何 runtime 變更（比照 TRANSLATE_TEST_MAINTAINABILITY_PLAN §5 紀律）。
2. 共用的 temporary DB、seed、response factory、mock config 等 helper 維持集中在 `tests/support.py`，不得因拆檔而複製。
3. 拆分為獨立提交；移動的每個 test node ID 應可對應到新檔位置，並同步更新 `modules/translate/tests/README.md` 的 coverage mapping 與 per-file 測試數。
4. 拆分後每個新檔須可單獨執行（`python -m pytest modules/translate/tests/test_<name>.py -q`），全套件連跑 5 次無 flaky。
5. 隔離契約不變：無真實 HTTP、無真實等待、不讀 workspace `data/canonical.db`、不讀 `.env`。

## 4. 驗收

- `python -m pytest modules/translate/tests -q` 測試總數與 subtests 數不減少（拆分當下基線：142 passed、1 skipped、43 subtests；若 P1 修正先落地則以修正後基線為準）。
- `python -m modules.translate.src.cli validate` 通過。
- `git diff` 僅含 tests 目錄與 `tests/README.md` 變更。
