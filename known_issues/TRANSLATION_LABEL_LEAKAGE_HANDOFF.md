# 翻譯標籤洩漏重構：交接文件（2026-07-24）

**狀態：** Phase 1 全部完成並通過複審，**Phase 2 已獲准但尚未開始**
**關聯文件：**
- 核定計畫：[`TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md`](./TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md)
- 處置清單與裁決紀錄：[`TRANSLATION_LABEL_LEAKAGE_DISPOSAL_LIST.md`](./TRANSLATION_LABEL_LEAKAGE_DISPOSAL_LIST.md)
- 原始問題：[`TRANSLATION_LABEL_LEAKAGE.md`](./TRANSLATION_LABEL_LEAKAGE.md)

## 1. 今日完成的任務

### 1.1 Phase 1：契約文件更新（21 份，已通過 Review）

依計畫 §4.2 將所有契約文件改寫為目標五欄形狀，未動任何程式碼：

- **top-level（5）**：`docs/CANONICAL_ENTITY_CONTRACT.md`、`DATA_LIFECYCLE.md`、`MODULE_BOUNDARIES.md`、`MULTILINGUAL_CONTENT_STRATEGY.md`、`SYSTEM_OVERVIEW.md`
- **translate（7）**：`modules/translate/docs/` 的 DATA_CONTRACT（含五欄 DDL 與指紋規格 §2.1.1）、PROMPT_CONTRACT（translator_v2）、EXECUTION_POLICY、STATE_TRANSITIONS、TRANSLATION_POLICY、IMPLEMENTATION_PLAN、README
- **publish（4）**：`modules/publish/docs/` 的 DATA_CONTRACT、EXECUTION_POLICY、IMPLEMENTATION_PLAN、README
- **site（3）**：`modules/site/docs/` 的 DATA_HANDOFF_CONTRACT、BUILD_AND_ROUTING_POLICY、DESIGN_PROPOSAL
- **analysis（2）**：`modules/analysis/docs/` 的 DATA_DEPENDENCIES、METRICS_CATALOG

Review 修正一輪：補上 `MULTILINGUAL_CONTENT_STRATEGY.md:50` 的 bypass 設定型 stale 豁免（P1，已修）。

### 1.2 Phase 1：測試設計（已通過複審）

新增契約測試與 fixtures，全部針對尚未實作的目標 API，**預期失敗為正常狀態**：

| 模組 | 資產 | 現況 |
| --- | --- | --- |
| translate | `modules/translate/tests/test_five_field_contract.py`（39 測試） | 36 failed（目標 API 缺失）、3 passed（回歸釘） |
| publish | `modules/publish/tests/test_item_payload_contract.py` + `tests/fixtures/`（schema + 2 valid + 12 invalid） | schema 自洽 4 passed／29 subtests passed；行為測試預期失敗 |
| site | `modules/site/tests/generatePosts.contract.test.ts`（23 案例）+ `tests/fixtures/`（9 JSON） | 全部預期失敗（adapter 模組與 post_labels.json 未建） |
| analysis | `modules/analysis/tests/test_translate_workload_five_field.py`（3 測試） | 2 failed（預期）、1 passed（completion/latency 非回歸釘） |

複審前已落實六項 P1 修正：translate 逐欄 fingerprint/nullability/非字串覆蓋、publish schema label-guard 補全（18 標籤、Markdown 強調剝除、全形冒號）、site fixtures 對齊 publish 合約（machine 省略 editor）、site 必填欄位與動態 locale 測試、處置清單 0-or-3 invariant 修正。

### 1.3 環境修復（需知悉）

`.venv` 原本缺 `httpx` 與 `python-dotenv`（translate 的 `orchestrator.py`、`cli.py` 需要），導致 translate baseline 無法收集。已補裝，baseline 全綠。

### 1.4 目前測試 baseline（全綠，作為各 Phase 回歸基準）

```powershell
& .\.venv\Scripts\python.exe -m pytest modules\translate\tests\test_translate.py -q   # 12 passed
& .\.venv\Scripts\python.exe -m pytest modules\publish\tests\test_publish.py -q       # 13 passed
& .\.venv\Scripts\python.exe -m pytest modules\analysis\tests -q                       # 22 passed（新檔 2 個預期失敗另計）
Set-Location modules\site; npx vitest run tests/i18n.test.ts tests/readingTime.test.ts # 13 passed
```

## 2. 剩餘工作事項

### Phase 2：重塑 handoff 與 translate（下一個執行項）

1. `modules/translate/src/migrations/v001_initial_translate_tables.sql` 改五欄 DDL（對齊 DATA_CONTRACT §1.4；全新資料庫適用，不做 in-place migration）。
2. 實作 `approved_content_record.compute_content_fingerprint()`（黃金向量已釘：ASCII `0b0bbd33…c923`、非 ASCII `0893cb7b…070b3`；`ensure_ascii=False` 已核定）。
3. assembler 改五欄直通，移除 `splice_content_body()`；保留 upstream freshness、author metadata、approval、language resolution 規則。
4. queue、repository、bypass、failure preservation、stale detection 轉五欄；`validate_translation_response()` 換核定簽名（見處置清單 §2）。
5. `config/model_settings.yaml`、`prompt_templates.yaml` 啟用 `translator_v2`（v1/v2 形狀不得共存）。
6. 依處置清單 §3.1 改寫 `test_translate.py`（含兩處**刪除**：Markdown link 檢查、舊標籤通過斷言）。
7. 完成條件：新空庫可完成 en bypass 與 zh/ja translation，DB 與 LLM payload 均無 UI labels；`test_five_field_contract.py` 應全綠。

### Phase 3：重塑 publish export

1. `fetch_canonical_item_payload()` 改讀五欄；`validate_item_payload()` 改驗證已組裝的 export payload（Q5 核定，呼叫點需移動）。
2. bullet_1..3 → key_claim/evidence_level/objective_impact 映射只在 publish 做一次；`publish_link` 輸出 `bullets: null`。
3. index/archive 直讀 `summary_short`，刪除 `extract_summary_short()`。
4. 依處置清單 §3.2 改寫 `test_publish.py` seed；`test_item_payload_contract.py` 應轉綠。
5. publish 自有 migration（`v001_initial_publish_tables.sql`）**不動**；在乾淨工作區驗證 CLI migration 路徑可用。
6. 順手補齊：`DATA_CONTRACT.md` §6.1 的 label 敘述可與 schema 的 18 標籤清單對齊（複審留的次要項）。

### Phase 4：重塑 site adapter 與 i18n

1. 新增 `modules/site/src/config/post_labels.json`（en/zh/ja 三組定案標籤）與 `modules/site/scripts/lib/post_adapter.js`（四函式，簽名見處置清單 §2，含 Q7 的 `language_code === locale` 校驗）。
2. `generate-posts.js` 依處置清單 §3.3 改造（刪 summaryMap、content fallback、硬編碼語言）。
3. 移除硬編碼語言陣列：generate-posts.js、archives 兩條 route，以及 Q8 核定一併處理的 `[lang]/index.astro`、`[lang]/stats.astro` 的 `getStaticPaths`（`astro.config.ts` 與 stats.astro 的 union type cast **維持豁免不動**）。
4. `generatePosts.contract.test.ts` 應轉綠；`npm run type-check`、`npm run build` 通過。

### Phase 5：同步 analysis 與全量驗收

1. `translate_queries.py:142` 的 `get_translation_char_volumes()` 換五欄公式（**只改這一條**；global/share 查詢依 Q10 裁決另案，待 REPORT_CONTRACTS 納入）。
2. 依處置清單 §3.4 更新 `generate_mock_db.py`（含 `data/test_sandbox.db` 重建）與五個 test 檔的 seed；注意 `downstream_action` 與 bullets 形狀的 0-or-3 一致性。
3. 隔離空庫完整跑 ingest → site pipeline 演練，再依核准範圍執行正式全量重建（計畫 §6）。
4. 產出結構與品質稽核報告後，才能把原始問題移至 `known_issues/resolved/`。

### 全庫重建紅線（計畫 §6，執行 Phase 5 前重讀）

- 先停所有排程工作；保存舊 DB/export 唯讀快照。
- **`data/canonical_final.db` 是獨立歷史分析資料庫，絕對不得刪除、修改、搬移或納入重建。**
- 禁止寫任何把舊 `content_body` 用 regex 拆回五欄的 migration。
- 新輸出全數驗收前不切換服務；舊快照驗收期保留。

## 3. 給接手者的提示

- 處置清單 §2 有全部測試釘住的目標 API 簽名（已核定，實作勿偏離；若要改命名需同步改測試）。
- 處置清單 §4 是 Review 裁決紀錄（Q1-Q12），實作前先讀。
- 兩個複審遺留的次要判斷點：非 dict response 目前斷言 `ValueError`（Phase 2 若改 `TypeError` 需調一個測試）；publish DATA_CONTRACT §6.1 標籤措辭 Phase 3 補齊。
- 每個程式變更必須在同一變更集同步更新所屬 module 文件（計畫 §9 要求）。
- 測試執行注意：本環境 pytest 對 `unittest.subTest` 失敗只記 SUBFAILED、父測試可能仍計 passed，寫參數化案例時避免依賴 subTest 計數。
