# Publish 測試拆分與原始碼維護性追蹤（B2 結案後）

**狀態：** 提案，待站主核准與工程師研擬細部方案
**日期：** 2026-08-22
**來源：** publish generation + hardlink reuse 重構落地後的程式碼審查與實作過程觀察
**範圍：** `modules/publish/` 的測試檔拆分與低風險可維護性改進
**非範圍：** 不改變任何 runtime 行為與輸出位元格式；不處理其他模組；各項目可獨立立案、獨立提交

## 1. 背景

generation + atomic pointer 與 hardlink reuse 兩批重構已結案（見 `resolved/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` 與 `resolved/PUBLISH_B2_IMPLEMENTATION_STEP_BREAKDOWN.md`）。過程中測試覆蓋大幅增加，同時也累積了幾個可維護性課題。以下皆**不是** defect，現行套件全綠（141 passed / 664 subtests / 1 skipped），屬後續維護改進。

## 2. 建議事項（依優先序）

### 2.1 測試檔拆分（站主已提出）

- `tests/test_generation_pointer.py` 已達約 1,460 行、18 個測試類別，單檔承載 pointer 原子性、程序鎖、快照隔離、retention、腐敗 fail-stop、hash stream、legacy 轉換、hardlink 重用與連結安全等多组關注點。
- 次要對象：`tests/test_coverage_loss.py`（約 646 行）。
- 建議比照 `TRANSLATE_TEST_FILE_SPLIT_PLAN.md` 的紀律：依現有 class 分群拆檔、helpers 維持集中在 `tests/support.py`、每個移動的 test node ID 可對應新位置、同步更新 `docs/TEST_COVERAGE_MAP.md`、拆後每檔可單獨執行、測試與 subtest 總數不減。

### 2.2 無呼叫者的 repository helper（死碼）

以 AST 掃描 + 全模組引用計數確認，以下兩個方法僅有定義、無任何呼叫者（src 與 tests 皆無）：

- `database.py` `get_publish_language_status_languages`（約 L276）
- `database.py` `get_source_item_published_at`（約 L437）

建議刪除；若為有意保留的對外查詢介面，請工程師確認後加註用途。同掃描已排除誤報：`cli.py` 的 `cmd_*`（click 動態註冊）與 `config.py` 的 `validate_*`（pydantic field_validator）皆為正常使用。

### 2.3 `orchestrator.py` 的 validation 相容轉出口

orchestrator 為相容舊呼叫端，re-export 了 `slugify`、`generate_slug`、`validate_item_payload` 等 validation 符號（檔頭 docstring 有說明）。可評估讓測試與呼叫端改為直接從 `validation.py` import，再移除轉出口，讓模組邊界更清晰。注意 FakeClock patch 點（`get_utc_now_iso8601` 的命名空間注入）是現行測試基礎設施的一部分，調整時需一併盤點。

### 2.4 Artifact 路徑文法雙寫

artifact 的路徑文法與固定順序目前由兩處各自編碼：`generation.py` 的 `_iter_planned_artifact_digests`（fingerprint 順序）與 `planned_chunks_for`（寫入端字串前綴派發）。兩者必須永遠一致，屬漂移風險點。可評估抽出單一的 artifact descriptor 表（路徑模式 → 產生器）供兩側共用。屬中等工作量，建議與 2.1 分開提交。

### 2.5 Canonical JSON 序列化雙實作

位元級序列化契約現由兩個實作維護：`serialize_json_bytes`（一次性 `json.dumps`）與 `_iter_json_array_bytes`（手工 re-indent 的串流版本，為記憶體上限而設）。等價性已由 `TestStreamingJsonArraySerialization` 鎖定，風險不高；長期可考慮共用單一底層 writer 以根除漂移可能。

### 2.6 `PublishRepository` 關注點混合（低優先）

`database.py` 約 540 行，混合 publish record 寫入、payload 查詢、archive metadata、聚合統計與 reconciliation 候選查詢。目前尚可導覽，若未來持續成長可評估依關注點拆分。不急。

## 3. 執行約束（全項目通用）

1. 各項目獨立提交，不夾帶行為變更；涉及 artifact 產生的項目（2.4、2.5）須保持輸出位元不變。
2. 完成後複跑 `py -3 -m pytest modules/publish/tests -q` 與 `cd modules/site && npm test`，數字不得低於本文件背景節的基線。
3. 任何刪除或移動須同步更新 `modules/publish/docs/` 對應文件（含 TEST_COVERAGE_MAP.md）。

## 4. 驗收

- 每個子案結案時：全套件綠燈、`git diff` 僅含該案範圍、文件同步更新。
- 2.2 死碼刪除後套件數字不變；2.1 拆分後測試/subtest 總數不減。
