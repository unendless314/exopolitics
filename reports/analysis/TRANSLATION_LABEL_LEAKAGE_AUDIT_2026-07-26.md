# 翻譯標籤洩漏重構：結構與品質稽核報告

**稽核日期：** 2026-07-26
**稽核對象：** 全庫重建後的 `data/canonical.db`（2026-07-25 重建）與 `data/publish_export/`（同日出品）
**依據：** `known_issues/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md` Phase 5 步驟 4 與 §7.3；`known_issues/TRANSLATION_LABEL_LEAKAGE_HANDOFF.md` §5
**方法：** 一次性稽核腳本直接復用 `modules/publish/src/orchestrator.py` 的 `UI_LABELS`（19 標籤）與 `has_ui_label_prefix()`，確保稽核清單與 runtime 守門完全一致；export 掃描為全量（非抽樣）。`data/canonical_final.db` 未納入、未接觸。

## 1. 結構稽核（canonical DB）

| 檢查 | 結果 | 明細 |
| --- | --- | --- |
| `approved_content_record` 五欄 schema | 通過 | 欄位恰為 13 欄五欄形狀，無 `content_body` |
| `translation_output` 五欄 schema | 通過 | 欄位恰為 16 欄五欄形狀，無 `content` |
| 筆數 | 通過 | acr 3,015；translation_output 9,045（en/zh/ja 各 3,015，全數 completed） |
| acr 0-or-3 invariant | 通過 | 3,015 列全數符合：`publish_summary` 三條非空 bullet、`publish_link` 全 NULL，無部分組合 |
| translation bullet 無部分組合 | 通過 | 9,045 列 bullet 非 NULL 數僅為 0 或 3 |
| completed 翻譯與母稿 nullability 一致 | 通過 | 9,045 列 completed 與 parent acr 的 bullet nullability 完全鏡像 |
| failed 列五欄形狀 | 通過 | 無部分五欄的 failed 列（本次重建後無 failed 列） |
| 指紋格式 | 通過 | 3,015 個 `content_fingerprint` 全為 64 字元 SHA-256 hex |
| prompt/model 版本 | 通過 | bypass 僅 en（3,015 列 `bypass/bypass`）；非 bypass 全為 `gpt-5.4-mini/v2.0`（6,030 列），v1/v2 不共存成立 |

## 2. 品質稽核（19 標籤前綴掃描）

| 檢查 | 結果 | 明細 |
| --- | --- | --- |
| acr 五欄內容標籤掃描 | 通過 | 3,015 列 × 5 內容欄 × 3 語，19 標籤前綴（含 ASCII/全形冒號、行首空白／清單符號／強調符剝除）零命中 |
| translation_output 五欄內容標籤掃描 | 通過 | 9,045 列 × 5 內容欄 × 3 語，零命中 |
| publish export item JSON 標籤掃描 | 通過 | 9,045 件 item（en/zh/ja 各 3,015）的 `display_title`、`summary_short`、`bullets` 值零命中 |

## 3. Publish export 形狀稽核（全量 9,045 件）

| 檢查 | 結果 | 明細 |
| --- | --- | --- |
| item JSON 恰 13 鍵、無 `content` 鍵 | 通過 | 9,045 件全數符合 |
| `language_code` 與目錄語系一致 | 通過 | 9,045 件全數符合 |
| `bullets` 0-or-3 形狀 | 通過 | `publish_summary` 恰含 `key_claim`/`evidence_level`/`objective_impact` 三非空字串；`publish_link` 為 JSON `null`；無其他 `downstream_action` |
| item 內容與 DB 逐字一致 | 通過 | 9,045 件的 `display_title`、`summary_short`、`bullets` 與 `translation_output` completed 列逐字相符（語意鍵映射正確） |
| index `summary_short` 直通 | 通過 | 3 語 index.json 共 3,000 筆條目，逐一與 DB 逐字相符 |

## 4. Analysis 同步與端對端驗證

| 檢查 | 結果 | 明細 |
| --- | --- | --- |
| `get_translation_char_volumes()` 五欄公式 | 通過 | 契約測試手算值 zh=151、ja=121 精確相符；空 cohort 回 `[]` |
| 五個 analysis 端點對正式庫執行 | 通過 | `analyze-classify`／`analyze-sources`／`analyze-funnel`／`analyze-translation`／`analyze-curation` 全數成功，JSON 與 Markdown 雙格式報告均已重產出於 `reports/analysis/`（dashboard 消費 JSON） |
| workload proxy 語意 | 通過 | 正式庫 7 日窗口（2026-07-19→2026-07-26）：en=0（en 全為 bypass，`model_name != 'bypass'` 排除正確）、zh=1,055,003、ja=1,055,003；以同一窗口 SQL 手算核對完全一致 |
| mock 資料庫與 fixtures | 通過 | `generate_mock_db.py` 換五欄 DDL（對齊 translate DATA_CONTRACT §1.4）與五欄 seed；`data/test_sandbox.db` 已重建；0-or-3 與 nullability 鏡像檢查通過 |

## 5. 測試套件最終狀態

| 套件 | 結果 |
| --- | --- |
| `modules/translate/tests` | 48 passed |
| `modules/publish/tests` | 27 passed + 45 subtests passed |
| `modules/analysis/tests` | 27 passed（含 Phase 5 契約測試 3 個、複審新增 Markdown 跳脫測試 2 個） |
| `modules/site`（Phase 4 驗收） | vitest 36 passed；type-check 0 errors；build 9,340 pages |

## 6. 複審修正紀錄（2026-07-26）

本報告初版經 Code Review 後修正兩項，均不影響第 1～3 節的結構與品質稽核結論：

1. **[P1] 報告 JSON 成品為舊檔。** 初版 §4 引用的 zh/ja=133,772 來自 2026-07-24 對舊庫產出的 `TRANSLATION_PERFORMANCE_REPORT.json`：重產出時未指定 `--format json`（CLI 預設只寫 Markdown），JSON 未被覆寫，稽核引用時誤讀舊檔。已以雙格式重產出全部五份報告；新 JSON（generated_at 2026-07-26T10:08:44Z）經同一窗口 SQL 手算核對一致（zh/ja 各 1,055,003、en=0）。本檔 §4 數字已更正。
2. **[P3] Markdown 表格未跳脫標題中的 `|`。** 來源標題 `Space | The Guardian` 在 `SOURCE_QUALITY_REPORT.md` 與 `CLASSIFY_MONITOR_REPORT.md` 的表格中撐出多餘儲存格，整列指標錯位。已於 `source_service.py` 與 `classify_service.py` 的標題解析處改為跳脫輸出（`\|`），各新增一個斷言跳脫與結構 pipe 數的測試，報告重產出後該列正常。此為 analysis 模組既有顯示層問題，與本次重構的五欄契約無關；JSON 成品不受影響。

## 7. 結論

全部稽核項目通過。重建後的 canonical DB、publish export 與 analysis 管線均符合五欄目標契約：內容與呈現標籤完全分離，資料庫、export 與 LLM 鏈路無任何 UI 標籤殘留。依計畫 §5 完成條件，`known_issues/TRANSLATION_LABEL_LEAKAGE.md` 移入 `known_issues/resolved/`。
