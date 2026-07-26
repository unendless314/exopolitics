# 翻譯標籤洩漏重構：交接文件（2026-07-24）

**狀態：** Phase 1、Phase 2、Phase 3 已完成並通過複審（Phase 3 於 2026-07-25 完成）；Phase 4（重塑 site adapter 與 i18n）已完成並通過複審（2026-07-26）；下一個執行項為 Phase 5（同步 analysis 與全量驗收）
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

## 2. Phase 2 完成紀錄（2026-07-25，已通過複審）

依處置清單 §3.1 與計畫 Phase 2 完成 handoff 與 translate 重塑，全部測試轉綠。

### 2.1 變更內容

| 路徑 | 變更 |
| --- | --- |
| `modules/translate/src/migrations/v001_initial_translate_tables.sql` | 五欄 DDL，與 DATA_CONTRACT §1.4 逐字對齊（全新資料庫適用）。 |
| `modules/translate/src/approved_content_record.py` | 移除 `splice_content_body()` 與舊 `compute_fingerprint()`；新增 `compute_content_fingerprint()`（黃金向量驗證通過）；assembler 改五欄直通，保留 delta 預篩、author metadata、approval、`content_language_code = 'en'` 規則。 |
| `modules/translate/src/database.py` | `upsert_translation_output`、`get_pending_translation_tasks` 改五欄；stale 偵測邏輯不變。 |
| `modules/translate/src/orchestrator.py` | `validate_translation_response()` 換核定五欄簽名（非 dict 維持 `ValueError`，未改 `TypeError`）；移除 Markdown 結構檢查（code fence／link／header）；新增聚合 ratio、聚合 script（不含 title）、zh/ja label-guard（19 個去重標籤、ASCII/全形冒號、剝除行首空白／清單符號／強調符）；request payload 改五欄槽位、NULL bullet 渲染為 `null`；structured-output schema 改 v2；bypass／成功／失敗寫入與 single-task mode 全改五欄。 |
| `modules/translate/config/prompt_templates.yaml` | 移除 `translator_v1`，僅註冊 `translator_v2`（v1/v2 不共存），內文對齊 PROMPT_CONTRACT §4。 |
| `modules/translate/config/model_settings.yaml` | `active_prompt_template: translator_v2`；`content_ratio_limit` 註解改聚合語意（值 5.0 不變）。 |
| `modules/translate/tests/test_five_field_contract.py` | 建表 helper 依處置清單授權換回 `run_migrations()`（內嵌 DDL 已刪除），契約測試現直接鎖定正式 migration 檔。 |
| `modules/translate/tests/test_translate.py` | 依處置清單 §3.1 全量改寫：五欄 INSERT/upsert/task/mock 回應；**刪除** Markdown link 兩測試與舊標籤通過斷言（`test_plain_text_labels_validation`）；保留 delta prescreen、CLI、stale/forced 區分、bypass 政策案例。 |

### 2.2 驗證結果

- `modules/translate/tests`：**48 passed**（test_translate.py 9 + test_five_field_contract.py 39，契約測試全綠）。
- publish：`test_publish.py` 13 passed 維持；Phase 3 契約測試如預期失敗（22 failed，Phase 3 處理）。
- analysis：22+1 passed 維持；Phase 5 契約測試 2 個如預期失敗（Phase 5 處理）。
- 冒煙演練（mock API、全新空庫 + 正式 migration）：assemble 2 筆 → 全 queue 6 任務（2 篇 × en/zh/ja）全成功；en 為 bypass 零 API call（實際 API call 僅 zh/ja 共 4 次）；`publish_link` 三 bullet 全 NULL、`publish_summary` 三 bullet 非空；DB 內容欄位與 LLM user prompt 均無三個英文 UI 標籤；single-task `--parent-content-id --force` 路徑亦驗證。
- 指紋黃金向量：ASCII `0b0bbd33…c923`、非 ASCII `0893cb7b…070b3`，與測試釘定值一致。

### 2.3 給複審者的提示

- label-guard 實作為單一 regex（`orchestrator.py` 的 `_LABEL_GUARD_PATTERN`）：行首空白 → 可選清單符號（`*`／`-`／`+`）→ 可選強調符（`**`／`__`／`*`／`_`）→ 19 標籤（依長度降冪 alternation）→ 可選強調符 → ASCII `:` 或全形 `：`。僅適用 zh/ja（Q4 裁決）。
- 聚合 ratio／聚合 script 的分母皆為「summary + 非空 bullets」，不含 title（Q3 裁決）；source 聚合為 0 時不做 ratio 檢查（沿用舊語意）。
- prompt 中 NULL bullet 渲染為 JSON 字面值 `null`，配合 system instruction 的 null-in/null-out 指示。
- `translator_v2` 的 `version` 欄位定為 `v2.0`（沿用 v1.0 命名慣例）；stale 偵測比對的就是此值，全新資料庫無舊列不受影響。
- 非 dict response 維持 `ValueError`，未觸動複審遺留的次要判斷點。

## 3. Phase 3 完成紀錄（2026-07-25，已通過複審）

依處置清單 §3.2 與計畫 Phase 3 重塑 publish export，全部測試轉綠。

### 3.1 變更內容

| 路徑 | 變更 |
| --- | --- |
| `modules/publish/src/database.py` | `fetch_canonical_item_payload()` 改 SELECT 五欄（`summary_short, bullet_1..3` 取代 `t.content`）；`get_reconciliation_candidates` docstring 措辭同步。publish 自有 migration 未動。 |
| `modules/publish/src/orchestrator.py` | 刪除 `extract_summary_short()`；新增 `UI_LABELS`（19 標籤，與 schema fixture 同清單）、`has_ui_label_prefix()`、`BULLET_KEY_MAP`、`assemble_item_payload()`（bullet_1..3 → key_claim/evidence_level/objective_impact 映射只在此做一次、解析 author_metadata、`publish_link` 輸出 `bullets: None`）；`validate_item_payload()` 改驗證已組裝的 export payload（summary_short 非空+label guard、downstream_action 白名單、bullets 形狀 0-or-3、author_metadata 規則沿用舊訊息）；`get_disclosure_note()` 改吃 parsed dict；Phase A 於 DB 變動前先 assemble+validate；Phase B assemble（帶真 `published_at`）+validate 後直接寫出 13 鍵 item JSON；index/archive 兩條 SQL 改直讀 `t.summary_short`。 |
| `modules/publish/tests/test_publish.py` | 依處置清單 §3.2 改寫：刪 `extract_summary_short` import；mock 上游 DDL 改五欄；`seed_data` 的 acr 硬編碼 publish_summary → 三 bullet 非空、translation completed → 五欄、非 completed → 內容欄 NULL；content 措辭變數改名 `orig_item_json`。 |
| `modules/publish/docs/DATA_CONTRACT.md` | §6.1 label 措辭補齊為 19 標籤完整清單（EN 3 + zh 7 + ja 9），與 schema fixture 對齊（複審留的次要項，結案）。 |

### 3.2 驗證結果

- `modules/publish/tests`：**27 passed + 45 subtests passed**（test_publish.py 13 維持 + test_item_payload_contract.py 全綠；Phase 2 時的 22 個預期失敗全部轉綠）。
- translate：**48 passed** 維持不變。
- analysis：23 passed + 2 個 Phase 5 預期失敗，維持不變。
- 乾淨工作區驗證：暫存目錄空庫跑 `publish migrate` 成功，建出 `publish_record`／`publish_language_status`／`schema_migrations`。
- CLI 冒煙（五欄 mock DB，zh/en/ja completed + publish_summary）：`run` 與 `rebuild` 各發布 3 件成功；item JSON 恰好 13 鍵、無 `content` 鍵；`bullets` 三語意鍵映射正確；item 與 index 的 `summary_short` 逐字來自 `translation_output.summary_short`；輸出全文無 UI 標籤前綴。

### 3.3 給複審者的提示

- `validate_item_payload()` 的輸入語意已從 canonical row 改為組裝完成的 export payload（Q5 核定）：`author_metadata` 是 dict、`bullets` 是語意鍵物件或 None；錯誤訊息保留既有字串（含 hybrid editor 那條）以免打破既有斷言。
- label-guard regex 與 schema fixture 的 `not` pattern 相同：`^[\s*_-]*(LABELS)[\s*_]*[:：]`，19 標籤清單三處（orchestrator、schema fixture、契約測試）一致。
- `assemble_item_payload()` 在 Phase A 以 `published_at=None` 驗證（時間戳不屬於四條驗證規則），Phase B 以真實 `published_at` 重組後寫檔；兩階段都過同一 validator。
- `get_disclosure_note()` 簽名由 JSON 字串改為 parsed dict（內部函式，無外部引用）；metadata 非 dict 時回預設 AI-generated 文案，由 validator 補刀拒絕。
- site 仍讀舊 `content` 鍵，在 Phase 4 adapter 落地前無法消費新格式 export——site 對外發布須待 Phase 4／5 完成（現況見 §3.4）。

### 3.4 實庫重建與 publish 實跑紀錄（2026-07-25）

使用者於 Phase 3 複審通過後，提前執行了正式全庫重建（原排定於 Phase 5 演練）：

- 舊 `canonical.db` 已移除（雲端伺服器可隨時重新下載舊版，等效滿足快照紅線）；以真實 pipeline（ingest → classify → curate → translate）建立全新五欄資料庫。`data/canonical_final.db` 未動。
- 新庫實測：3,015 筆 approved_content_record、9,045 筆 translation_output；0-or-3 invariant 完全成立（`publish_link` 7,830 筆三 bullet 全 NULL、`publish_summary` 1,215 筆全非空、partial 為 0）；五欄內容掃 19 個 UI 標籤前綴零命中。
- publish 實跑：`migrate` + `run` + `rebuild` 皆成功，各發布 9,045 件；`status` 顯示 Active 9,045／Withdrawn 0／Frozen Slugs 3,015／Blocked 0。
- export 驗證（run 與 rebuild 後各驗一次）：item JSON 恰好 13 鍵、無 `content` 鍵；bullets 0-or-3 形狀正確；3,000 筆 index 條目逐一與 DB 比對 `summary_short` 逐字相符；全部輸出字串零 UI 標籤。
- `data/publish_export/` 現為新格式真實資料，可直接供 Phase 4 的 site adapter 開發除錯。

## 4. Phase 4 完成紀錄（2026-07-26，已通過複審）

依處置清單 §3.3 與計畫 Phase 4 重塑 site adapter 與 i18n，全部測試轉綠。

### 4.1 變更內容

| 路徑 | 變更 |
| --- | --- |
| `modules/site/src/config/post_labels.json` | 新增：en/zh/ja 三組定案標籤；adapter 唯一 post label 來源，locale key 集與 `localeProfiles` 一致（測試鎖定）。 |
| `modules/site/scripts/lib/post_adapter.js` | 新增：plain Node ESM、零相依，四函式 `loadPostLabels`／`validateItem`／`assembleMarkdown`／`getAdapterLanguages`，簽名與 arity 同處置清單 §2（含 Q7 的 `language_code === locale` 校驗；labels 檔缺失／無效／任一 locale 非恰三鍵非空即拋錯）。 |
| `modules/site/scripts/generate-posts.js` | 依處置清單 §3.3 改造：刪 summaryMap（index.json／archives manifest／archive items 三段讀取）、description fallback 鏈、`content` 解構與 `getFirstParagraph`／`stripMarkdown`／`truncateDescription`；語言集改 `getAdapterLanguages(loadPostLabels())`；逐 item `assembleMarkdown()`，失敗即 `process.exit(1)`；translation map 行為不變。 |
| `modules/site/src/pages/[lang]/index.astro`、`stats.astro`、`archives/index.astro`、`archives/[month].astro` | `getStaticPaths` 硬編碼三語改由 `Object.keys(localeProfiles)` 驅動（Q8）；`astro.config.ts` 的 `i18n.locales` 與 stats.astro union type cast 依豁免不動。 |
| `modules/site/docs/DATA_HANDOFF_CONTRACT.md` | 複審 P3 修正：§2.2 標籤讀檔責任文句改為 `post_adapter.js` 的 `loadPostLabels()`，移除 generate-posts.js「直接」讀檔的過時描述（唯一非阻擋項，已結案）。 |

### 4.2 驗證結果

- `npx vitest run`：**36 passed**（generatePosts.contract.test.ts 23 全綠＋baseline i18n/readingTime 13 維持）。
- `node scripts/generate-posts.js`（真實 export）：en/zh/ja 各 3,015 件全數通過 adapter 驗證，零失敗；translation map 正常寫出。
- `npm run type-check`：0 errors／0 warnings。
- `npm run build`：**9,340 pages built**，無錯誤。
- 建置抽驗：zh `publish_summary` 頁恰含「關鍵主張／證據等級／客觀影響」各一次；en 頁含三個英文標籤；ja `publish_link` 頁零標籤、僅摘要段。

### 4.3 給複審者的提示

- `assembleMarkdown()` 內部先 `validateItem()`；generate-posts.js 僅呼叫 `assembleMarkdown()` 一處，驗證不重複，錯誤訊息帶 slug／檔名脈絡。
- frontmatter 六鍵與舊版逐字一致（`title`／`publishDate`／`description`／`canonicalUrl`／`disclosureNote`／`authorMetadata`），`description` 直取 `summary_short` 原文不截斷。
- 標籤 JSON key 順序（en/zh/ja）僅影響 adapter 處理順序；頁面語言顯示順序由 `localeProfiles`（zh/en/ja）決定，不受影響。
- `BaseHead.astro`／`LanguageSelector.astro` 的 `["zh","en","ja"]` 為非 post 頁的 fallback 預設值，不在處置清單 §3.3 點名範圍，未動。
- type-check 期間 glob-loader 對兩個 slug 報 duplicate id warning（同 id 同路徑自我覆寫；generated/en 為 3,015 檔、與 item 數一致），判定為既有現象，不影響建置結果。

## 5. 剩餘工作事項

### Phase 5：同步 analysis 與全量驗收（下一個執行項）

1. `translate_queries.py:142` 的 `get_translation_char_volumes()` 換五欄公式（**只改這一條**；global/share 查詢依 Q10 裁決另案，待 REPORT_CONTRACTS 納入）。
2. 依處置清單 §3.4 更新 `generate_mock_db.py`（含 `data/test_sandbox.db` 重建）與五個 test 檔的 seed；注意 `downstream_action` 與 bullets 形狀的 0-or-3 一致性。
3. ~~隔離空庫完整跑 ingest → site pipeline 演練，再依核准範圍執行正式全量重建（計畫 §6）。~~ **正式重建已由使用者提前執行完畢（2026-07-25，見 §3.4）**；site 段的端到端驗證併入 Phase 4 完成後進行。
4. 對新庫補跑結構與品質稽核報告（重建先於稽核，此項不得省略；如需新舊對照可從雲端取回舊庫），通過後才能把原始問題移至 `known_issues/resolved/`。

### 全庫重建紅線（計畫 §6，執行 Phase 5 前重讀）

- 先停所有排程工作；保存舊 DB/export 唯讀快照。
- **`data/canonical_final.db` 是獨立歷史分析資料庫，絕對不得刪除、修改、搬移或納入重建。**
- 禁止寫任何把舊 `content_body` 用 regex 拆回五欄的 migration。
- 新輸出全數驗收前不切換服務；舊快照驗收期保留。

## 6. 給接手者的提示

- 處置清單 §2 有全部測試釘住的目標 API 簽名（已核定，實作勿偏離；若要改命名需同步改測試）。
- 處置清單 §4 是 Review 裁決紀錄（Q1-Q12），實作前先讀。
- 複審遺留的次要判斷點：非 dict response 已於 Phase 2 維持 `ValueError`（結案）；publish DATA_CONTRACT §6.1 標籤措辭已於 Phase 3 補齊為 19 標籤清單（結案）。
- 每個程式變更必須在同一變更集同步更新所屬 module 文件（計畫 §9 要求）。
- 測試執行注意：本環境 pytest 對 `unittest.subTest` 失敗只記 SUBFAILED、父測試可能仍計 passed，寫參數化案例時避免依賴 subTest 計數。
- publish 與 analysis 測試套件須分開執行（同一 pytest invocation 會因 tests package 同名衝突而收集失敗）。
