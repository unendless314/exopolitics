# 翻譯標籤洩漏重構：舊欄位處置清單與測試設計紀錄

**狀態：** Phase 1 測試設計已通過 Review 修正；§3.1（translate）已於 Phase 2 執行完畢並通過複審（2026-07-25），§3.2（publish）已於 Phase 3 執行完畢並通過複審（2026-07-25），§3.3（site）已於 Phase 4 執行完畢並通過複審（2026-07-26），§3.4 待 Phase 5 執行
**建立日期：** 2026-07-24
**關聯計畫：** [`TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md`](./TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md)（本檔為其 Phase 1 完成條件要求的處置清單）

本檔彙整：(1) Phase 1 新增的契約測試與 fixture；(2) 測試定義的目標 API（已經 Review 核定）；(3) 所有舊 `content_body`/`content` 引用的處置清單（Phase 2-5 才執行，現在不動）；(4) Review 裁決紀錄（原開放問題）。

## 1. Phase 1 新增測試資產

| 模組 | 新檔案 | 規模 | 現況 |
| --- | --- | --- | --- |
| translate | `modules/translate/tests/test_five_field_contract.py` | 33 測試 | 30 failed（目標 API 缺失）、3 passed（現行邏輯回歸釘） |
| publish | `modules/publish/tests/test_item_payload_contract.py` + `tests/fixtures/`（item_payload.schema.json + 2 valid + 10 invalid JSON） | 14 測試方法 | schema 自洽測試全過；目標行為測試如預期失敗 |
| site | `modules/site/tests/generatePosts.contract.test.ts` + `tests/fixtures/`（4 valid + 5 invalid JSON） | 17 案例 | 17/17 failed（adapter 模組與 post_labels.json 尚不存在） |
| analysis | `modules/analysis/tests/test_translate_workload_five_field.py` | 3 測試 | 2 failed（char_volumes 公式與空 DB：舊查詢讀 `acr.content_body`，五欄 schema 下 OperationalError，預期失敗）、1 passed（completion/latency 非回歸釘） |

Baseline（測試設計前已驗證全綠）：translate 12 passed、publish 13 passed、analysis 22 passed、site vitest 13 passed。新增測試不影響既有套件；各模組全套件執行時失敗僅來自新契約測試的預期失敗。

**環境備註：** 測試設計前發現 `.venv` 缺 `httpx` 與 `python-dotenv`（translate 的 `orchestrator.py`、`cli.py` 需要），導致 translate baseline 無法收集；已補裝後 baseline 全綠。`jsonschema 4.26.0` 已存在，publish schema fixture 直接使用。

## 2. 測試定義的目標 API（已經 Review 核定）

文件鎖定行為但未定名者，以下為測試釘住的目標介面。Phase 2-5 實作若採不同命名，需同步改測試。

### translate
- `approved_content_record.compute_content_fingerprint(display_title, summary_short, bullet_1=None, bullet_2=None, bullet_3=None) -> str`：固定 key 順序、`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`、UTF-8、SHA-256。黃金向量：ASCII `0b0bbd33…c923`、非 ASCII `0893cb7b…070b3`。
- `orchestrator.validate_translation_response(data, target_language_code=, source_summary=, source_bullet_1=, source_bullet_2=, source_bullet_3=, max_title_len=, content_ratio_limit=)`：保留現名、改五欄簽名，失敗 raise `ValueError`。
- `translate_task`、`TranslationRepository.upsert_translation_output`、`get_pending_translation_tasks`、`assemble_approved_content_records`：名稱不變，payload 改五欄。

### publish
- `orchestrator.validate_item_payload(payload) -> None`：簽名不變，但輸入語意釘為「組裝完成的 export 形 payload」（含 `bullets` 語意鍵、無 `content`），違規拋 `ValidationError`；呼叫點需從 canonical row 移到 export payload。
- `database.PublishRepository.fetch_canonical_item_payload(...)`：回傳列改含 `summary_short`、`bullet_1..3`，不再有 `content`。
- `orchestrator` 不再存在 `extract_summary_short`（測試以 `hasattr` 為 False 鎖定）。

### site
- 新模組 `modules/site/scripts/lib/post_adapter.js`（plain Node ESM、零相依）：
  - `loadPostLabels(labelsPath?)`：預設讀 `src/config/post_labels.json`；缺失/無效/任一 locale 非恰三鍵非空即拋錯。
  - `validateItem(item)`：既有檢查（slug、display_title、source_published_at）＋ summary_short 非空、bullets 為 null 或恰三已知鍵非空。
  - `assembleMarkdown(item, locale, labels)`：內部先 validateItem；未知 locale 拋錯；驗證 `item.language_code === locale`，不一致拋錯（Q7 裁決）；frontmatter description 直取 `item.summary_short` 原文不截斷；body = 摘要段 + 固定鍵序 `* **Label**: value` 列表。
  - `getAdapterLanguages(labels)`：`Object.keys(labels)`，取代硬編碼語言陣列。

### analysis
- `translate_queries.get_translation_char_volumes(...)`（既有）：僅換五欄公式，其餘語意（per-row、含 failed、排除 bypass、GROUP BY language）不變。

Review 裁決：原提案的三條新查詢（global Recorded/Intended workload、§4.3.4 character share）缺乏 report consumer，另案處理——待納入 `REPORT_CONTRACTS.md`、report schema 與 dashboard scope 後再設計實作（見 §4 Q9-Q12）。

## 3. 舊欄位處置清單

### 3.1 translate（Phase 2 執行）：`modules/translate/tests/test_translate.py`

| file:line | 內容 | 處置 |
| --- | --- | --- |
| :21 | `import compute_fingerprint` | 改 import `compute_content_fingerprint` |
| :115 | fixture template 用 `{content_body}` | 替換為 v2 五欄 placeholder |
| :207-222 | assembler 拼接 body 與英文標籤斷言 | 改寫五欄直通（新檔已覆蓋，可簡化） |
| :253-312 | `translated_content`、code fence/bracket/header 案例 | 刪除 Markdown 結構案例；title/ratio 部分改寫五欄 |
| :327, 429, 659, 900 | INSERT 用 `content_body` | 改五欄 INSERT |
| :339, 365, 393, 548, 671, 715 | upsert dict 用 `"content"` | 改五欄 dict |
| :440, 483, 521, 692, 728, 929, 950 | task dict 用 `"content_body"` | 改五欄 task |
| :462, 500, 539, 569, 744, 964 | 斷言 `row["content"]` | 改斷言五欄 |
| :472, 510, 914 | mock 回應 `translated_content` | 改 v2 五 key 回應 |
| :575-597 | Markdown link 檢查兩測試 | **刪除**（結構檢查已廢） |
| :787-843 | `translated_content` script 檢查 | 改寫五欄或刪除（新檔已覆蓋） |
| :845-886 | 斷言帶標籤翻譯「通過」 | **刪除**（與 label-guard 直接矛盾） |
| :599-645, 750-785 | delta prescreen、CLI | 無舊欄位引用，保留 |

另：`modules/translate/src/migrations/v001_initial_translate_tables.sql` 改五欄 DDL；`approved_content_record.py` 移除 `splice_content_body()`；`orchestrator.py`、`database.py` 改五欄。新契約測試目前以內嵌五欄 DDL 建表，Phase 2 更新 migration 後可換回 `run_migrations`。

### 3.2 publish（Phase 3 執行）：`modules/publish/tests/test_publish.py`

| file:line | 內容 | 處置 |
| --- | --- | --- |
| :22 | `extract_summary_short` import | 刪除（函式屆時移除） |
| :51, :68 | mock DDL 的 `content_body` / `content` 欄 | 改五欄 DDL |
| :149-151, :161-163, :167-169 | seed 寫入 `content_body` / `content` | 改五欄 INSERT |
| :568, :679-680, :691, :717 | 註解/變數名提及 content | 措辭隨 seed 更新 |
| :219, :262, :329, :370, :422 | strict-match / withdraw / rebuild / archive / editor 規則 | 案例保留，僅換五欄 seed |

Runtime 處置：`database.py` 的 `t.content` 查詢、`orchestrator.py` 的 `extract_summary_short()` 與 validate 呼叫點、item/index/archive writer 改新形狀。`modules/publish/src/migrations/v001_initial_publish_tables.sql` **不動**（publish 自有表不存 content）。

### 3.3 site（Phase 4 執行）

`modules/site/scripts/generate-posts.js`：

| file:line | 內容 | 處置 |
| --- | --- | --- |
| :25 | `const languages = ['en', 'ja', 'zh'];` | 改 `getAdapterLanguages(loadPostLabels())` |
| :41-80 | summaryMap 建立（讀 index.json、archives manifest、archive items） | 整段刪除 |
| :102-110 | 既有 slug/display_title/source_published_at 驗證 | 併入 `validateItem` |
| :115 | 解構 `content` | 刪除，body 改由 `assembleMarkdown` 產生 |
| :122-137 | description fallback 鏈（summaryMap → content 首段 → title） | 刪除，直取 `item.summary_short` |
| :150 | `content || ''` 寫入 body | 改組裝後 body |
| :173-201 | `getFirstParagraph`/`stripMarkdown`/`truncateDescription` | 刪除（僅 fallback 鏈使用） |

硬編碼語言陣列（計畫點名前兩處，後兩處見裁決 Q8）：

| file:line | 處置 |
| --- | --- |
| `src/pages/[lang]/archives/[month].astro:21` | 改由 `Object.keys(localeProfiles)` 驅動 |
| `src/pages/[lang]/archives/index.astro:10-12` | 改 map `localeProfiles` |
| `src/pages/[lang]/index.astro:14-16` | 同型硬編碼，Phase 4 一併移除（Q8 裁決確認） |
| `src/pages/[lang]/stats.astro:10-12` | 同型硬編碼，Phase 4 一併移除（Q8 裁決確認） |

新增：`src/config/post_labels.json`（en/zh/ja 三組定案標籤）、`scripts/lib/post_adapter.js`。

### 3.4 analysis（Phase 5 執行）

`modules/analysis/src/`：

| file:line | 內容 | 處置 |
| --- | --- | --- |
| `queries/translate_queries.py:142` | `LENGTH(acr.display_title) + LENGTH(acr.content_body)` | 換五欄長度和（原擬同檔新增的 3 條 global/share 查詢經 Review 裁決另案，見 §4 Q10） |
| 其餘 src 檔 | 經 grep 確認無舊欄位引用 | 不需處置 |

`modules/analysis/tests/`：

| file:line | 內容 | 處置 |
| --- | --- | --- |
| `generate_mock_db.py:188, :205` | `content_body` / `content` DDL | 換五欄 DDL |
| `generate_mock_db.py:425-450, :453-490` | acr / translation seed | 換五欄 seed（publish_link 三 bullet 全 NULL、publish_summary 三條非空）；`data/test_sandbox.db` 一併重建 |
| `test_translate_service.py:40-56, :84, :92` | content INSERT、proxy 期望值 34 | 換五欄 seed；`"Body content"` 改作 `summary_short` 且 bullets NULL 時，該列 `downstream_action` 須同步設為 `publish_link`（0-or-3 invariant：bullets 全 NULL 僅 publish_link 合法）；此前提下 34 不變、斷言保留 |
| `test_schema_validation.py:81-96` | acr/translation INSERT | 換五欄；report JSON schema 不變 |
| `test_publish_service.py:30-40` | acr/translation INSERT | 換五欄 |
| `test_source_classifier.py:162-167` | acr INSERT | 換五欄 |
| `test_funnel_calculator.py:58-70` | acr/translation INSERT | 換五欄 |
| `conftest.py` | 無舊欄位引用 | 不需處置 |

## 4. Review 裁決紀錄

Phase 1 測試設計已通過 Review，原開放問題裁決如下。

- **Q1（translate）**：**採納。** 指紋序列化採 `ensure_ascii=False`（非 ASCII 原文直入 UTF-8）。文件只寫「UTF-8 序列化」，`json.dumps` 預設 `ensure_ascii=True` 會產生不同指紋；測試已以黃金向量釘住前者。
- **Q2（translate）**：**維持測試已釘。** label-guard 的「冒號」為 ASCII `:` 與全形 `：` 皆拒絕（兩者都在觀測洩漏中出現過）。
- **Q3（translate）**：**維持測試已釘。** script 檢查範圍為「summary + 非空 bullets 聚合，**不含 title**」（CJK 只出現在 title 判失敗）。
- **Q4（translate）**：**維持測試已釘。** label-guard 只適用 zh/ja；en 目標以 "Key Claim:" 開頭不拒絕（en 正常走 bypass），確認為有意為之。
- **Q5（publish）**：**採納。** `validate_item_payload` 輸入釘為 export 形 payload（`bullets` 語意鍵），而非 canonical row（`bullet_1..3`）；呼叫點需隨之從 canonical row 移到 export payload。錯誤訊息要求含欄位名 token（文件未定訊息文字，屬測試自訂）。
- **Q6（publish）**：**維持測試已釘。** 「不含 label」統一採「label＋冒號前綴」語意（schema `not` pattern 與 e2e 一致），不禁止正文合法提及標籤字樣。schema 對時間欄位限定 `YYYY-MM-DDTHH:MM:SSZ`，比 runtime 嚴，屬 fixture 嚴格化決策。
- **Q7（site）**：**採納。** adapter 模組路徑 `scripts/lib/post_adapter.js` 與四函式簽名照測試提案；`assembleMarkdown(item, locale, labels)` 加驗 `item.language_code === locale`，不一致拋錯。生成檔尾端換行政策不另行釘定（測試採 `trimEnd()` 比較）。
- **Q8（site）**：**採納。** `[lang]/index.astro:14-16` 與 `[lang]/stats.astro:10-12` 的硬編碼三語 `getStaticPaths` 於 Phase 4 一併移除（已反映於 §3.3）。
- **Q9（analysis）**：**裁決採「全部 approved cohort ×（語言數 − 1）」**（total queue workload 讀法，原測試期望值 408）；另一讀法「與 Recorded 同母體」會恆等於 Recorded×2，不採。惟該查詢隨 Q10 另案，暫無實作。此母體模糊為舊版既有，非本次引入。
- **Q10（analysis）**：**另案。** 三條目標查詢（global Recorded/Intended、§4.3.4 share）目前無 report contract 歸屬——`REPORT_CONTRACTS.md` 只定義 per-language proxy。本次 analysis 僅改 `get_translation_char_volumes()` 五欄公式；global/share 查詢待納入 `REPORT_CONTRACTS.md`、report schema 與 dashboard scope 後再設計實作。
- **Q11（analysis）**：**隨 Q10 另案。** §4.3.4 share 公式照字面不排除 bypass（正式庫 en 全為 bypass，en share 會暴增）；是否加 `model_name != 'bypass'` 於另案一併裁定。相關測試已刪除，現不釘此行為。
- **Q12（analysis）**：**隨 Q10 另案。** per-language（per-row）與 global Recorded（per-article）語意不同，同篇 zh+ja 雙列在前者計兩次、後者計一次；文件點明此差異於另案實作 global 查詢時一併處理。
