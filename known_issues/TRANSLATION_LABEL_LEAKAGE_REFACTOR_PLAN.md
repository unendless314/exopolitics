# 翻譯標籤洩漏重構計畫

**狀態：** 待文件審查，尚未授權實作
**建立日期：** 2026-07-24
**關聯問題：** [`TRANSLATION_LABEL_LEAKAGE.md`](./TRANSLATION_LABEL_LEAKAGE.md)

## 1. 目的與決策

本計畫將翻譯文章中的呈現標籤，從 canonical 資料、LLM 輸入輸出及 publish export 中完全移除，改由 `site` 在建置時依語系加上 UI 標籤。這是既有問題文件所定案的「方案 S」：

```text
curation_output
  -> approved_content_record
  -> translation_output
  -> publish_export JSON
  -> site adapter 依 locale 加入標籤後產生 Markdown
```

本次重構的目標不是以 prompt 或文字替換掩蓋英文殘留，而是將「內容」和「呈現標籤」分離。完成後，改動標籤文案或顯示格式不需重新翻譯、不需重跑 LLM，也不會改變內容指紋。

### 1.1 已確認的事實

| 項目 | 已驗證結果 |
| --- | --- |
| 唯一注入點 | `modules/translate/src/approved_content_record.py` 的 `splice_content_body()` 硬編碼 `Key Claim`、`Evidence Level`、`Objective Impact`。 |
| 上游結構 | `curation_output` 已保存 `display_title`、`summary_short`、`bullet_1`、`bullet_2`、`bullet_3`；`publish_summary` 固定三條，`publish_link` 固定三條皆為 `NULL`。 |
| 本機正式快照 | `canonical.db` 共有 2,591 筆 `approved_content_record`，7,773 筆 `translation_output`。en/ja 各 2,591 completed，zh 為 2,588 completed 與 3 failed。 |
| 洩漏規模 | 462 筆 active `publish_summary` 中，zh 有 424 筆、ja 有 443 筆仍含三個英文標籤；en 的 462 筆為自翻譯 bypass，英文標籤在現狀下屬預期。 |
| 下游現況 | `publish` 原樣輸出 `translation_output.content`，再以第一段反推索引摘要；`site/scripts/generate-posts.js` 原樣將 item `content` 寫入 Markdown。 |
| 額外影響 | `analysis` 的 translation workload 指標、mock database 與多個測試 fixture 直接引用舊的 `content_body` 或 `content` 欄位。這不是三個指定模組的局部修改。 |

上述資料表示問題的根源是資料形狀與所有權，不是個別翻譯品質。既有文件中的 prompt 強化或 regex 後處理不得作為本次解法。

## 2. 範圍與非範圍

### 2.1 範圍

1. 將 `approved_content_record` 與 `translation_output` 改為五欄結構化內容。
2. 將 translate 的單次 API 請求、回應 schema、驗證、bypass 與持久化改為五欄結構。
3. 將 publish item export 改為 `summary_short` 加語意化 `bullets` 物件。
4. 由 site adapter 依 locale 加上標籤並產生既有 Markdown 文章。
5. 同步更新 `analysis` 對 schema 與工作量指標的讀取。
6. 更新所有受影響的契約、測試、測試資料與全庫重建程序。

### 2.2 非範圍

- 不改變 `curate` 的三條 bullet 語意、數量或產生規則。
- 不新增第二次 API 呼叫。每篇文章、每個目標語言仍以一次完整上下文請求完成翻譯。
- 不改變 slug 凍結、translation stale、strict-match、withdrawal、來源可發布資格或作者揭露規則。
- 不為尚未實作的 `edit` free-form Markdown 形狀預留 `content_body` 欄位。該相容性應在 edit 啟用時單獨設計。
- 不處理 ingest category、分類標籤或 category label。調查未發現它們進入本問題的資料鏈路。

## 3. 目標契約

### 3.1 Canonical 五欄內容形狀

`approved_content_record` 與 `translation_output` 使用相同的邏輯內容形狀：

| 欄位 | 型別與空值 | 語意 |
| --- | --- | --- |
| `display_title` | `TEXT NOT NULL`，translation 初始失敗時可為 `NULL` | 已核准或目標語言標題。 |
| `summary_short` | `TEXT NOT NULL`，translation 初始失敗時可為 `NULL` | 單段摘要，直接作為 index/archive/SEO 的摘要來源。 |
| `bullet_1` | `TEXT NULL` | curate 的 claim 槽位。 |
| `bullet_2` | `TEXT NULL` | curate 的 evidence 槽位。 |
| `bullet_3` | `TEXT NULL` | curate 的 objective implication 槽位。 |

`publish_summary` 必須有三個非空 bullet。`publish_link` 必須三個 bullet 都是 `NULL`。不得接受部分存在的 bullet 組合，避免資料語意不完整。

`approved_content_record` 保留 `source_item_id`、`parent_content_id`、`content_language_code`、`content_fingerprint`、`approved_at`、`author_metadata`、`created_at`、`updated_at`。`translation_output` 保留關聯鍵、狀態、重試、模型、prompt 版本與時間欄位。僅移除舊的 `content_body` 與 `content`。

### 3.2 指紋的唯一序列化規則

`content_fingerprint` 是 stale 偵測與 publish 同步的版本錨點，必須以單一共用 helper 實作並在 `translate/DATA_CONTRACT.md` 鎖定：

1. 欄位順序固定為 `display_title`、`summary_short`、`bullet_1`、`bullet_2`、`bullet_3`。
2. 每個欄位先將 `\r\n` 與 `\r` 正規化成 `\n`。
3. `NULL` 序列化為 JSON `null`，空字串序列化為 JSON `""`，兩者不可混淆。
4. 將下列 JSON object 以 UTF-8、固定 key 順序、無額外空白序列化後計算 SHA-256：

```json
{
  "display_title": "...",
  "summary_short": "...",
  "bullet_1": null,
  "bullet_2": null,
  "bullet_3": null
}
```

不得用 UI 標籤、語系或 site 的呈現字串參與指紋。如此標籤文案或 Markdown 樣式變更不會讓 translation stale。

### 3.3 Translate v2 合約

輸入 prompt 以固定、具語意但非呈現的槽位傳遞：

```text
Source Title
Source Summary
Source Bullet 1 (factual claim)
Source Bullet 2 (evidence level)
Source Bullet 3 (objective implication)
```

回應 JSON 必須包含：

```json
{
  "translated_title": "...",
  "translated_summary": "...",
  "translated_bullet_1": null,
  "translated_bullet_2": null,
  "translated_bullet_3": null
}
```

規則如下：

- 五個 response key 都必須存在。title 與 summary 必須為非空字串。
- 每個來源 bullet 為 `NULL` 時，對應翻譯 bullet 必須為 `NULL`；每個來源 bullet 為非空字串時，對應翻譯 bullet 必須為非空字串。
- 所有必填字串與所有非 `NULL` bullet 都必須在去除首尾空白後仍有內容。單獨的空白字串不得視為有效值。
- 整個五欄仍在同一 API call 內傳送與回收，不能分段呼叫。
- `en` bypass 直接複製五欄，不呼叫 API，並保留既有 `model_name = 'bypass'`、`prompt_version = 'bypass'` 與 stale 例外規則。
- `translator_v2` 是新的 prompt 版本。全庫重建前不得讓 v1 與 v2 內容形狀共存。

### 3.4 Translate 驗證規則

移除只適用整段 Markdown 的 code-fence、link、header 結構比對。改採：

1. title 長度限制與日文 120 字限制維持。
2. 對所有非空的 summary 與 bullets 聚合計算內容長度比例，沿用目前設定的 `content_ratio_limit` 語意。不得以單一短 bullet 的比例誤判翻譯。
3. zh 的聚合內容至少有 CJK 字元，ja 的聚合內容至少有平假名或片假名，仍允許專有名詞與縮寫保留英文。
4. source/response 的 nullability 形狀必須完全一致。
5. 對 zh、ja 的 `summary_short` 與所有非空 bullet 做遷移期品質守門：去除行首空白、可選 Markdown 強調與標籤後，若值以已知 UI label 加冒號開頭即拒絕。守門清單包含英文三標籤與既有問題文件第 4.2 節列出的已觀測 zh/ja 變體。

最後一項只用於偵測錯誤的呈現字串回流，不是正確性的主機制；不應以全域字串替換處理內容。

### 3.5 Publish export 合約

詳細 item JSON 取代 `content`：

```json
{
  "source_item_id": 3,
  "language_code": "zh",
  "slug": "example-slug",
  "display_title": "範例標題",
  "summary_short": "範例摘要。",
  "bullets": {
    "key_claim": "主張內容。",
    "evidence_level": "證據內容。",
    "objective_impact": "影響內容。"
  }
}
```

其餘既有公開欄位，例如 `canonical_url`、時間欄位、`downstream_action`、`disclosure_note`、`author_metadata` 維持不變。

語意映射只能在 publish 建立一次：

| Translate 欄位 | Export key |
| --- | --- |
| `bullet_1` | `key_claim` |
| `bullet_2` | `evidence_level` |
| `bullet_3` | `objective_impact` |

`publish_link` 的 `bullets` 一律使用 JSON `null`，不可省略、不可輸出空物件。這讓 site adapter 可用明確二元契約驗證資料。index 與 archive 直接讀取 `summary_short`，刪除 `extract_summary_short()` 與任何從內文反推摘要的 fallback。

`publish` 的 `validate_item_payload()` 是這個 invariant 的防禦邊界，必須在寫出 item JSON 前驗證：

1. `summary_short` 是去除首尾空白後仍非空的字串。
2. `downstream_action = 'publish_summary'` 時，`bullets` 恰含 `key_claim`、`evidence_level`、`objective_impact` 三鍵，且各值皆為去除首尾空白後非空的字串。
3. `downstream_action = 'publish_link'` 時，`bullets` 必須為 `null`。
4. 其他 `downstream_action` 不得通過 publish payload validation。

上游 curate 已強制 0-or-3 invariant，但 publish 必須獨立防禦，不能將錯誤資料延後到 site build 才發現。

### 3.6 Site 標籤與 Markdown 組裝

site 是唯一可保存三個 UI 標籤文字的地方。實際組裝點是 `modules/site/scripts/generate-posts.js`，不是 Astro 頁面模板。

site 新增 adapter 可直接讀取的 locale-neutral labels 資料檔，例如 `modules/site/src/config/post_labels.json`：

```json
{
  "en": {
    "key_claim": "Key Claim",
    "evidence_level": "Evidence Level",
    "objective_impact": "Objective Impact"
  },
  "zh": {
    "key_claim": "關鍵主張",
    "evidence_level": "證據等級",
    "objective_impact": "客觀影響"
  },
  "ja": {
    "key_claim": "主要主張",
    "evidence_level": "証拠レベル",
    "objective_impact": "客観的影響"
  }
}
```

選用 JSON 是因為目前 adapter 由 plain Node.js 執行，不能直接 import `src/utils/i18n.ts`。這份 JSON 是 post label 文字的唯一來源；這些標籤由 adapter 寫入 generated Markdown，Astro 頁面毋須在 `uiTranslations` 複製同一批 key。

語系集中化的邊界如下：

- `src/utils/i18n.ts` 的 `localeProfiles` 是 Astro UI 與路由程式使用的語系列表，`.astro` 路由可直接 import 它。
- `src/config/post_labels.json` 是 Node adapter 使用的 post label map；其 locale key 集合必須與 `localeProfiles` 相同，並由 site test 驗證。
- `astro.config.ts` 的 `i18n.locales` 是 Astro 框架設定，`stats.astro` 的現有 union type cast 是型別標註。本次重構不嘗試動態產生它們；只有未來新增或移除語系時，才必須同步檢閱這兩處。

adapter 必須：

1. 驗證 `summary_short` 為非空字串。
2. 驗證 `bullets` 為 `null`，或恰有三個已知 key 且值皆為非空字串。
3. 將摘要寫為第一段；若有 bullets，使用 locale label 組成：

   ```markdown
   摘要文字。

   * **關鍵主張**: ...
   * **證據等級**: ...
   * **客觀影響**: ...
   ```

4. 不再將 export 的不存在 `content` 當作可接受 fallback。
5. 由 locale profile 驅動支援語言，取代 adapter 與 archive route 中各自硬編碼的 `['en', 'ja', 'zh']`。

因 item JSON 已保證有 `summary_short`，adapter 必須刪除目前讀取 `index.json`、archive manifest 與 archive item 所建立的 `summaryMap`，以及「summaryMap -> content 首段 -> title」的 SEO fallback。frontmatter `description` 直接取用已驗證的 `item.summary_short`，避免重複 I/O 與第二套摘要語意。

## 4. 受影響路徑

### 4.1 必改程式、schema 與測試

| 區域 | 路徑 | 變更 |
| --- | --- | --- |
| shared handoff | `modules/translate/src/approved_content_record.py` | 移除 `splice_content_body()`；改為五欄直通、固定 fingerprint 序列化及結構化差異比較。 |
| schema | `modules/translate/src/migrations/v001_initial_translate_tables.sql` | 因採全新資料庫，直接將兩表改成五欄 schema。 |
| translate runtime | `modules/translate/src/orchestrator.py` | 更新 request、response schema、驗證、bypass、task payload、成功與失敗寫入。 |
| translate repository | `modules/translate/src/database.py` | 更新 SELECT、queue materialization 與 upsert 欄位。 |
| translate config | `modules/translate/config/model_settings.yaml`、`prompt_templates.yaml` | 啟用 `translator_v2`，更新 template placeholders 與輸出限制說明。 |
| translate tests | `modules/translate/tests/test_translate.py` | 改為五欄 fixtures，新增 nullability、fingerprint、bypass、v2 schema、label-guard 與失敗回滾測試。 |
| publish repository | `modules/publish/src/database.py` | 讀取 `summary_short`、三個 bullet 與必要 metadata，不再讀取 `t.content`。 |
| publish runtime | `modules/publish/src/orchestrator.py` | 改 item validation、item JSON、index/archive query；刪除 `extract_summary_short()`。 |
| publish migration | `modules/publish/src/migrations/v001_initial_publish_tables.sql` | 保留既有且已追蹤的 migration directory 與 DDL；在乾淨工作區驗證 CLI 的預設 migration 路徑可正常運作。 |
| publish tests | `modules/publish/tests/test_publish.py` | 改 schema seed 與 export assertions；新增 key mapping、`bullets = null`、摘要直通、strict-match/withdraw/rebuild 回歸。 |
| site adapter | `modules/site/scripts/generate-posts.js` | 驗證新 item shape，依 locale 組裝 Markdown，改用集中語系來源。 |
| site locale | `modules/site/src/utils/i18n.ts` 與新增 `modules/site/src/config/post_labels.json` | 保留 `localeProfiles` 作為 Astro UI/route 使用的語系列表；新增 JSON 作為 adapter 唯一 post label 來源，並以測試確保兩者 locale key 一致。 |
| site tests | `modules/site/tests/` | 新增 adapter contract tests，覆蓋 en/zh/ja、`publish_summary`、`publish_link`、不完整 bullets 與未知 locale。 |
| analysis queries | `modules/analysis/src/queries/translate_queries.py` | workload proxy 改為五欄長度總和。 |
| analysis fixtures | `modules/analysis/tests/generate_mock_db.py` 與直接建立舊 schema 的 `test_*.py` | 改 schema、seed 資料與預期工作量。 |

### 4.2 必改契約文件

| 區域 | 文件 | 內容 |
| --- | --- | --- |
| top-level | `docs/CANONICAL_ENTITY_CONTRACT.md` | 取代 spliced body 的 entity 定義，明示五欄與 site-only label ownership。 |
| top-level | `docs/DATA_LIFECYCLE.md` | 更新 translation input/output 與 publish lifecycle 的資料形狀。 |
| top-level | `docs/MODULE_BOUNDARIES.md` | translate 改為結構化內容翻譯，site 明示負責 post presentation labels。 |
| top-level | `docs/MULTILINGUAL_CONTENT_STRATEGY.md` | 補充 structured content 與 UI i18n 的邊界。 |
| top-level | `docs/SYSTEM_OVERVIEW.md` | 更新 representation layer 與 module role 的 spliced-body 描述。 |
| translate | `modules/translate/docs/DATA_CONTRACT.md` | 新 schema、DDL、fingerprint、handoff materialization、invalidations。 |
| translate | `modules/translate/docs/PROMPT_CONTRACT.md` | `translator_v2` prompt、五欄 JSON schema、欄位驗證。 |
| translate | `modules/translate/docs/EXECUTION_POLICY.md` | 聚合內容驗證、五欄成功/失敗與 bypass copy。 |
| translate | `modules/translate/docs/STATE_TRANSITIONS.md` | 五欄 NULL/成功寫入語意。 |
| translate | `modules/translate/docs/TRANSLATION_POLICY.md` | 移除 Markdown 一對一要求，改列純文字欄位與 script validation。 |
| translate | `modules/translate/docs/IMPLEMENTATION_PLAN.md`、`README.md` | 同步 handoff 與 runtime 描述。 |
| publish | `modules/publish/docs/DATA_CONTRACT.md` | 更新 upstream fields、item JSON、index/archive `summary_short` 來源。 |
| publish | `modules/publish/docs/EXECUTION_POLICY.md` | 更新 artifact validation 與 memory rules，不再提取 `content`。 |
| publish | `modules/publish/docs/IMPLEMENTATION_PLAN.md`、`README.md` | 同步 export shape。 |
| site | `modules/site/docs/DATA_HANDOFF_CONTRACT.md` | 新 item JSON、adapter label ownership、SEO 直接讀摘要。 |
| site | `modules/site/docs/BUILD_AND_ROUTING_POLICY.md`、`DESIGN_PROPOSAL.md` | 說明 generated Markdown 是由結構化 export 加標籤產生。 |
| analysis | `modules/analysis/docs/DATA_DEPENDENCIES.md` | 欄位依賴改為五欄。 |
| analysis | `modules/analysis/docs/METRICS_CATALOG.md` | 更新兩個 workload proxy 的長度公式。 |

`curate` 維持既有資料語意與 prompt，不列入實作變更；其現有 `DATA_CONTRACT.md` 與 `PROMPT_CONTRACT.md` 只作為上游不變性的驗證依據。

## 5. 實施階段與完成條件

### Phase 0: 實作前鎖定

1. 將本計畫與既有問題文件完成文件審查。
2. 確認本計畫第 3 節全部目標契約，包括 `bullets = null`、指紋序列化及 zh/ja label 文案。
3. 建立一份不可修改的 pre-rebuild 資料快照與 export manifest，僅供回溯和結果比較。
4. 明確核准全庫重建範圍：`canonical.db`、publish projection、`data/publish_export/`，以及雲端舊庫的停用方式。`data/canonical_final.db` 必須明確排除，該獨立歷史分析資料庫不得改動。

**完成條件：** 沒有未決資料形狀、所有權或資料清除範圍問題。

### Phase 1: 先更新契約與測試設計

1. 先更新第 4.2 節所有契約，將未來狀態描述為一致的五欄形狀。
2. 撰寫 translate、publish、site、analysis 的失敗與回歸測試案例，再修改 runtime。
3. 建立 JSON item schema fixture 與 site adapter fixture，作為 publish/site 的跨模組契約測試。

**完成條件：** 文件、測試案例與此計畫的 D1 至 D4 沒有衝突，且所有舊 `content_body`/`content` 引用都有處置清單。

### Phase 2: 重塑 handoff 與 translate

1. 在全新 DDL 中改造 `approved_content_record`、`translation_output`。
2. 實作固定指紋 helper，供 assembler 與測試使用。
3. 將 assembler 改為直通複製 `curation_output` 五欄，保留既有 upstream freshness、author metadata、approval 與 language resolution 規則。
4. 將 queue、repository、bypass、failure preservation 與 stale detection 轉為五欄。
5. 以 `translator_v2` 執行單次呼叫，套用 response nullability、聚合 ratio、script 與 label-guard 驗證。

**完成條件：** 新空資料庫可完成 curated en bypass 與 zh/ja translation，且資料庫及 LLM payload 均無 UI labels。

### Phase 3: 重塑 publish export

1. 更新 `fetch_canonical_item_payload()`、payload validation 及 item writer。
2. 在 publish 唯一映射 `bullet_1..3` 為語意 key。
3. index/archive 直接輸出 `summary_short`，移除反推函式與對大 `content` 欄位的依賴。
4. 保留既有 publish migration directory，確認 CLI migration 路徑可在乾淨工作區運作。

**完成條件：** item JSON 從不含 UI labels，`publish_summary` 有完整 `bullets`，`publish_link` 有 `bullets: null`，slug、strict-match、withdraw 與 rebuild 行為不變。

### Phase 4: 重塑 site adapter 與 i18n

1. 建立 adapter 可讀的共用 post labels 資料來源，並以測試驗證其 locale key 與 `localeProfiles` 一致。
2. adapter 驗證新 JSON shape，使用 locale label 生成 Markdown。
3. 以 locale profile 取代分散的硬編碼語言陣列，至少修正 `generate-posts.js` 與 archive route。
4. 驗證 generated Markdown 的摘要、三個標籤、bullet 與 frontmatter description。

**完成條件：** 三語頁面只由 site 產生正確、一致的 label。僅改共用 label 資料再重建 site，即可改變頁面標籤，沒有 translate API call。

### Phase 5: 同步分析與全量驗收

1. 將 analysis workload 計算改為：

   ```text
   LENGTH(display_title)
   + LENGTH(summary_short)
   + COALESCE(LENGTH(bullet_1), 0)
   + COALESCE(LENGTH(bullet_2), 0)
   + COALESCE(LENGTH(bullet_3), 0)
   ```

2. 更新 analysis mock database、schema validation 及所有受影響 fixtures。
3. 在隔離的空資料庫完整執行 ingest 至 site pipeline，最後再依核准範圍執行正式全量重建。
4. 重建後產出結構與品質稽核報告，才可把原問題移至 `known_issues/resolved/`。

**完成條件：** 所有驗證、資料稽核與 publish/site build 通過。

## 6. 全庫重建與回復計畫

本次不能將「修改既有 `v001`」誤當成既有資料庫 migration。已套用 migration 的資料庫不會重新執行同名檔案，且舊欄位與新 shape 不相容。因此採用以下流程：

1. 停止所有 ingest、translate、publish、site build 與排程工作。
2. 保存舊資料庫與 export 的唯讀快照，記錄 schema migration 狀態、筆數、語言狀態、slug 集合與輸出檔清單。
3. 在新路徑建立空白 canonical database，套用新 DDL，先跑 schema 與 module-local 測試。`data/canonical_final.db` 是獨立歷史分析資料庫，**不得刪除、修改、搬移或以任何方式納入重建作業**。
4. 從目前有效 ingest source configuration 重跑到 curate，materialize 結構化 handoff。
5. 執行 translate 全量重跑，再以 strict-match 發布，最後重建 site。
6. 比對 slug 穩定性策略。若資料母體已因 ingest 重構而失效，僅保留仍能對應新 source item 的 slug；不得以舊 translation 資料直接拼回新庫。
7. 於驗收期保留舊快照，不覆寫或刪除；若新 pipeline 有 blocker，將服務指向舊的已驗收 export，調查後自新空庫重新演練。

不得撰寫或執行將舊 `content_body` 以 regex 拆回五欄的 migration。這會延續舊標籤變體、無法可靠辨識內容邊界，也違反本次全庫重建的決策。

## 7. 驗證矩陣

### 7.1 單元與契約測試

| 面向 | 必測案例 |
| --- | --- |
| fingerprint | 五欄任一值、`NULL` 與空字串變更都會改變 fingerprint；換行正規化不改變 fingerprint。 |
| assembler | `publish_summary` 直通五欄；`publish_link` 三 bullets 為 `NULL`；不注入 label。 |
| translate schema | 五個 key 必備、trim 後非空 title/summary、null-in/null-out、拒絕部分 bullet 與 whitespace-only 值。 |
| translate quality | title 上限、聚合比例、zh/ja script、summary/bullet 的已知 label 前綴 guard。 |
| bypass/stale | en 五欄複製、零 API call、fingerprint mismatch 仍 stale、配置變更仍略過 bypass。 |
| failure safety | 首次失敗保存五欄 `NULL`；強制重跑失敗不覆蓋上次成功資料。 |
| publish | 完整 semantic mapping、`bullets: null`、`summary_short` 直通、依 `downstream_action` 驗證 bullets、metadata validation、strict-match、withdraw、rebuild、frozen slug。 |
| site adapter | en/zh/ja 標籤、summary-only link、缺欄失敗、locale key 一致性、移除 summaryMap、frontmatter description 直接使用摘要。 |
| analysis | 五欄工作量公式、測試 schema 與既有 completion/latency 指標不回歸。 |

### 7.2 命令驗證

實作人員需先確認實際可用命令，再至少執行：

```powershell
& .\.venv\Scripts\python.exe -m pytest modules\translate\tests -q
& .\.venv\Scripts\python.exe -m pytest modules\publish\tests -q
& .\.venv\Scripts\python.exe -m pytest modules\analysis\tests -q
Set-Location modules\site; npm test
Set-Location modules\site; npm run type-check
Set-Location modules\site; npm run build
```

正式全量重建前後，還需執行 SQLite schema 檢查與 export JSON schema 驗證。驗證不得使用 production API key 輸出到日誌。

### 7.3 資料與視覺驗收

1. canonical DB 的五欄內容、translate prompt preview、LLM response persist 與 publish item JSON 都不含三個 UI label。
2. 隨機抽查和全庫掃描 zh、ja item JSON：內容欄位不含已知 English/zh/ja label 前綴變體。
3. en、zh、ja 產生頁面顯示各自定案的三個 label；一篇 `publish_link` 文章只有摘要、沒有 bullet list。
4. 修改 `post_labels` 中一個 label 後，只跑 site build，確認文章頁立即反映且沒有 API 呼叫、canonical fingerprint、translation status 或 publish source fingerprint 變化。
5. 重新執行 publish rebuild，確認 summary、archive、withdrawal、frozen slug、coverage 與 disclosure 行為維持正確。

## 8. 風險與控制

| 風險 | 控制措施 |
| --- | --- |
| 只改 translate/publish/site，漏掉 analysis | 將 analysis query、metrics docs、mock schema 與 tests 明列為必改項，Phase 5 阻擋完成。 |
| 新舊 v1/v2 形狀共存 | 不對舊庫做 in-place reshape；使用新空庫並將 `translator_v2` 與 DDL 一起啟用。 |
| site label 資料有兩份來源 | post label 僅寫入 adapter 可讀的共用 JSON；Astro UI 路由仍以既有 `localeProfiles` 為語系列表，並以測試鎖定兩者 locale key 一致。 |
| adapter 靜默輸出不完整文章 | 對 `bullets` 的 null 或完整三鍵 shape 做 hard failure，不接受部分資料。 |
| 指紋規則不一致導致全量 stale | 以唯一 helper 與測試 fixture 鎖定 JSON 序列化，不在 runner 重算。 |
| 翻譯品質驗證變弱 | 以 shape、title、聚合比例、script presence 與 label guard 取代失去意義的 Markdown structural checks。 |
| 全量重建破壞已發布內容 | 先在隔離資料庫演練，保留舊 DB/export 快照，且在新輸出全數驗收前不切換。 |
| publish migration 在乾淨環境失效 | 既有 `modules/publish/src/migrations/v001_initial_publish_tables.sql` 已存在；在隔離空庫執行 CLI migration，驗證預設路徑與 DDL 實際可用。 |

## 9. 文件審查清單

審查通過前，需確認：

- [ ] 五欄、`NULL`、固定 fingerprint 序列化與 `bullets: null` 已被接受。
- [ ] zh 為「關鍵主張／證據等級／客觀影響」，ja 為「主要主張／証拠レベル／客観的影響」。
- [ ] 標籤只存在 site locale 資料，不能出現在 DB、LLM payload/response、export content values 或 fingerprint。
- [ ] 全庫重建已取代 in-place migration，且舊 DB/export 的保留與最終處置範圍明確。
- [ ] 重建範圍明確排除 `data/canonical_final.db`，此獨立歷史分析資料庫不得改動。
- [ ] analysis 被納入程式、文件、fixture 與驗收範圍。
- [ ] `edit` 的未來 free-form 內容相容性被明確排除，留待 edit 合約另案處理。
- [ ] 所有既有的 stale、bypass、strict-match、slug、withdrawal、disclosure 行為都有回歸測試。

審查完成並取得實作授權後，工程師應依本計畫的 Phase 1 至 Phase 5 執行，且每個程式變更必須在同一變更集同步更新所屬 module 文件。
