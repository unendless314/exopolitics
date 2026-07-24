# Ingestion Low-Context Filter Investigation Report (Ingest 模組低上下文過濾機制調查報告)

**文件狀態：** 已定案——方案五「最小撤閘」獲選（2026-07-23），實作計畫見 [INGEST_LOW_CONTEXT_REFACTORING_PLAN.md](INGEST_LOW_CONTEXT_REFACTORING_PLAN.md)  
**更新日期：** 2026-07-23（方案五定案；方案四未獲選；§3.9 依 §4.8 關閉；backlog 改採開發庫重建）  
**分析資料庫：** [canonical_final.db](file:///C:/Users/user/Documents/exopolitics/data/canonical_final.db)（舊庫，用於內容型態與門檻覆蓋率評估）  

---

## 1. 背景與核心問題

在 UAP/UFO 資訊聚合系統的實際運行中，發現大量來自 **Google News RSS** 以及 **Reddit 連結轉貼文章 (Link-only posts)** 的項目，在 `ingest` 階段被判定為 `low_context`（低上下文），並直接分流至 `UNKNOWN` 或予以排除。

在新版（V2）的管線設計中，這項問題變得更加關鍵：
1. [sanitizer.py](file:///C:/Users/user/Documents/exopolitics/modules/ingest/src/sanitizer.py) 會對 Feed Entry 的 Body（內文描述）進行規則式清理與長度檢測。
2. 若清理後的內文被標記為 `low_context` 或 `failed`，該項目**將不會進入 `classify` 的 LLM 待處理佇列**。
3. 這導致了像 Google News 這種 RSS Body 僅含連結與發行商名稱的優質即時新聞（快訊），完全無法通過分類與策展，最終無法呈現在前台。

---

## 2. 數據抽樣與發現

我們針對 [canonical_final.db](file:///C:/Users/user/Documents/exopolitics/data/canonical_final.db) 中的 `source_item_text` 進行了數據統計分析，共有 **2,658 筆** 被判定為 `is_low_context = 1` 的項目，分析如下：

### A. 低上下文原因分佈 (Low-Context Reasons)

> **口徑注意：** 本節為舊庫 V1 語義（`is_low_context` / `low_context_reason`）的歷史資料。現行 V2 合約（`SANITIZATION_STRATEGY.md` §8.3、`sanitizer.py`）中，`missing_body` 歸屬 **`failed`** 而非 `low_context`，兩者不可混用。

* **`mostly_links` (1,776 筆)：** 佔比最大。因為 Google News 的內文通常只是一個包裹著標題的 `<a>` 標籤，Reddit 連結貼文的 Body 也僅含 `[link]` 與 `[comments]` 超連結。清理時，超連結字元比例 > 70% 而被阻擋。
* **`too_short` (856 筆)：** 清理後的內文字元長度小於 `MIN_TEXT_LENGTH = 100`。
* **`title_heavy` (20 筆) / `title_only` (2 筆)：** 內文幾乎完全是標題的複製品。
* **`post_cleanup_empty` (2 筆)：** 清理後完全無內文。
* **`missing_body` (2 筆)：** 舊庫 V1 語義下歸入 low_context 的歷史資料；**在現行 V2 合約中此 reason 屬於 `failed`**，不屬於 low_context。

### B. 被判定為低上下文項目的「標題長度」分佈
雖然這些項目的 Body 極短，但它們的**標題本身攜帶了非常豐富的上下文**。這 2,658 筆項目的標題字元長度統計如下：
* 標題長度 `>= 30` 字元：**95.3%** (2,533 筆)
* 標題長度 `>= 40` 字元：**93.3%** (2,479 筆)
* 標題長度 `>= 50` 字元：**89.7%** (2,383 筆)

### C. 具代表性之被阻擋項目範例
以下為目前被攔截為 `low_context` 但顯然極具情報價值的標題實例：
* `[too_short] Washington archbishop removes priest over UFO comments`
* `[too_short] UFO videos show ‘we’re not the apex predator anymore’: Ross Coulthart`
* `[too_short] Disclosure Foundation releases NSA UFO documents`
* `[mostly_links] Department of War Publishes Second Release of Unidentified Anomalous Phenomena Files on WAR.GOV/UFO - U.S. Department of War (.gov)`
* `[too_short] LUNA says UFO task force getting access to classified documents. Is an exec order coming?!`

### D. 補充驗證（2026-07-21 下午追加）

* **資訊覆蓋檢查：** 在 2,658 筆低上下文項目中，僅 19 個標題可與非低上下文項目做精確標題匹配，顯示以相同標題由其他來源補入的情況很少。
  * **比對方法與限制（可重現）：** 對低上下文項目取 `LOWER(TRIM(title))` 與非低上下文項目標題做**精確比對**；範圍為舊庫全時間窗（2026-06-11 ~ 2026-07-13）、**跨來源**、未使用 dedup marker、僅比對標題未比對 URL。精確比對會低估模糊重複（同一事件經不同媒體報導時標題略有差異），因此 19 是觀察到的精確匹配數，而非實際事件覆蓋率的完整估計。此查詢不能推導「近乎全量流失」；評估不同標題的同事件覆蓋需另行建立模糊比對或事件層級去重方法。
* **自然實驗（短內容下游表現）：** 現行管線中內文 100–199 字元、通過 sanitizer 的項目，classify 將 72% 判為 `irrelevant`（過濾有效）；進入 curate 的 653 筆中 **84% 核准**（416 筆 `publish_link` + 130 筆 `publish_summary`），僅 16% 被拒。這表明具有真實內文的短內容可呈現，且下游兩道 LLM 關卡能有效處理該族群。
  * **對照限制：** 此族群具有 100–199 字元的**真實內文**，與放行後的 title-only / mostly_links 項目（實質內文趨近於零）**並非等價對照組**；上述比率僅供定性參考，不得作為驗收門檻（見 §3.6）。
* **長度門檻調整無效：** `mostly_links` 判定基於連結文字佔比 > 0.7，與長度無關；調整 `MIN_TEXT_LENGTH` 最多只影響 `too_short` 桶（856 筆），無法處理最大宗的 `mostly_links`（1,776 筆，66.8%）。

---

## 3. 方案四——撤除 ingest 閘門，status 降格為觀測訊號（未獲選）

> **2026-07-23 狀態更新：** 本方案未獲選；最終採用 §4 方案五。本節保留為歷史評估紀錄。

### 3.1 決策理由

`low_context` 量測的是「RSS 提供的可用內文品質」，**不是「事件是否值得分類」**。把它當成 classify 的硬閘門，是將品質訊號誤用為語義決策；Google News 案例（標題-only 但可分類性極高、93.3% 標題 ≥40 字元）證明了這個代理指標的失效。因此：

- ingest 不再判斷「什麼值得分類」——語義過濾完全交給 classify（LLM），編輯閘門完全交給 curate（LLM）。
- `text_processing_status` / `text_processing_reason` 保留為 ingest 擁有的**觀測輸出**，功能轉為：
  1. **curate 排版提示**（low_context → 僅允許 `publish_link`，見 3.4）
  2. **來源健康監控維度**（見 3.4）
  3. **事後量測維度**（放行項目的分類分佈與核准率，見 3.6）

### 3.2 設計內容與實作定位

**佇列規則：** classify 佇列接收所有「具有可分類訊號」的項目——僅排除「根本沒有東西可分類」者，即 `text_processing_status = 'failed'`（V2 語義，含 `missing_body`、`sanitizer_exception`）以及 `reason = 'post_cleanup_empty'`。其餘 low_context 項目（`mostly_links`、`too_short`、`title_heavy`、`title_only`、`template_heavy`）一律進入分類。

**實作定位（code 改動最小，不需要 schema migration）：**

- `modules/classify/src/database.py`（pending 查詢）：佇列條件由 `status = 'completed'` 改為「排除 `failed` 與 `post_cleanup_empty`」，並選出 `text_processing_status` / `text_processing_reason` 供 prompt 使用。謂詞必須以 SQL 的 `NULL` 語義安全地保留 reason 為 `NULL` 的 `completed` 項目。
- `modules/curate/src/orchestrator.py`：新增驗證——low_context 項目僅允許 `publish_link`（見 3.4，程式強制）
- `modules/curate/src/database.py`：pending 查詢帶出 `text_processing_status` / `text_processing_reason`（既已 JOIN `source_item_text`）
- `modules/classify/config/prompt_templates.yaml` 與 `modules/classify/src/orchestrator.py`：將 `text_processing_status` / `reason` 作為**明確中繼資料**傳入 prompt（例如「此項目被 ingest 標記為 low_context / mostly_links，內文可能僅為連結包裝，請依標題與可用文字判斷主題」），取代泛化指示。現行 prompt formatter 僅傳入 title 與 sanitized text，故兩處都必須調整。此為品質中繼資料的傳遞，不讓 ingest 重新取得語義裁量權；其對 `unknown` 輸出率的影響列入 §3.6 觀測。注意：`MODULE_BOUNDARIES.md` §3.2 classify 的「May read」清單需同步加入此二欄位（見 §3.3）。
- 測試：classify 必須覆蓋 reason 為 `NULL` 的 completed 項目仍會入列，以及 `failed` / `post_cleanup_empty` 不會入列；curate 必須覆蓋 low_context 項目的 `publish_summary` 會被驗證層拒絕。
- ingest 端**零改動**；sanitizer 維持現行判定與 reason 輸出，**不得抹除 reason**（觀測依據）

**成本估算（新增呼叫，非總量）：** 舊庫 32 天口徑，放行後**新增約 83 次 classify 呼叫/日**，佇列總量約**由 240 增至 323 次/日（增幅約 34%）**。
補充參考（暫定估計）：2026-07-21 對 live `canonical.db` 的一次性快照查詢顯示 low_context 佔比 18.3%（886 / 4,842，V2 schema）。該數字**未經獨立驗證、不在本文可重現的舊庫證據內、且 source ID 已重整**，僅作暫定參考——若成立則實際增幅約 20–25%。**正式成本預測以舊庫口徑（+34%）為上限，實際值以上線後觀測（§3.6）為準。**

### 3.3 合約修訂點（前置條件，五處）

「low_context 不得進入 classify」目前寫死在以下頂層合約，必須**先修訂合約、code 才有依據跟進**。修訂方向：「classify 讀取所有具可分類訊號的項目；`text_processing_status` 為觀測與排版訊號，不作為佇列閘門」。

- `docs/SYSTEM_OVERVIEW.md`（§6.2 classify pending item selection）
- `docs/MODULE_BOUNDARIES.md`（§3.2 classify 唯讀 completed 項目、不得為 low_context/failed 建佔位列；**「May read」清單需加入 `text_processing_status` / `text_processing_reason`**，供 prompt 中繼資料與佇列謂詞使用）
- `docs/DATA_LIFECYCLE.md`（生命週期圖與終止規則，三處）
- `docs/CANONICAL_ENTITY_CONTRACT.md`（Classification Result 實體僅保留給 completed 項目，四處）
- `modules/ingest/docs/SANITIZATION_STRATEGY.md`（§8.4 Scope Boundary）

### 3.4 工程師共識附加限制（硬性要求）

1. **curate 護欄必須程式強制，不只是 prompt。** `text_processing_status` / `reason` 必須帶進 curate 查詢，並由 `orchestrator.py` 的驗證邏輯拒絕 low_context 項目的 `publish_summary`——否則標籤的安全價值落空（LLM 可能從一則標題編造三點摘要）。
2. **來源健康監控採 source-specific baseline。** Google News 的 `mostly_links` 高比例是**預期格式**，不得視為故障；真正的告警條件是**同一來源的比例或 reason 分佈突然改變**（例如某來源 low_context 率從 10% 跳升至 90%，代表 feed 格式異動）。

### 3.5 佇列優先級指引

**不要**將 low_context 項目一律排在 completed 之後。本產品為快訊站，最具時效性的 Google News 項目往往正是 low_context；延後處理會削弱方案四的主要價值。若未來需要降載（如接入大量新聞關鍵字 feed），採用：

- 按容量限流（capacity-based rate limiting）
- 每 source 配額（per-source quota）
- 或對 low_context 項目使用較便宜的分類模型

而非讓 low_context 天生低優先序。

### 3.6 上線後觀測（重建後 1–2 週）

實作上線後觀測以下指標，驗證決策並作為後續調整依據：

- 放行項目的 classify 分佈（`core` / `adjacent` / `irrelevant` / `unknown` 比例；特別注意 `unknown` 是否過高 → 檢討 §3.2 的 prompt 中繼資料設計是否誘發偏差）
- 放行項目的 curate 核准率。**基準說明：** §2.D 自然實驗的 84% 核准率來自具有真實內文的 100–199 字元族群，非 title-only 項目的等價對照，**僅作寬鬆參考，不作為成功門檻**。
- **`publish_link` 合規率預期為 100%：** 新規則強制所有 low_context 核准項目走 `publish_link`，此比率是**護欄合規檢查**（偏離 100% 即護欄失效），不是品質指標。
- classify 每日實際呼叫量與 token 成本（對照 §3.2 估算上限）
- 各來源 low_context 比例與 reason 分佈的 baseline 建立（供 3.4-2 監控告警使用）

### 3.7 不阻塞本案的呈現層注意事項（另行評估）

- Google News 項目的 link card 會帶 `news.google.com/rss/articles/...` 跳轉鏈（瀏覽器可正常跳轉至原始發行商，功能可用但不透明）。
- Reddit 連結貼文的 `canonical_url` 指向 Reddit 討論串；外站原文 URL 其實在 `raw_payload` 的 `[link]` href 中，未來可在 ingest 提取（獨立輕量改動）。

### 3.8 備註

canonical_final.db 的 source ID 為清理前配置（如 ID 81 在舊庫為 Google News，現行 `sources.yaml` 中 ID 81 為 Scientific American；現行 Google News 快訊群為 ID 60–65）。舊庫數據可用於門檻與內容型態評估，不可用於推論清理後各 source 佔比。

### 3.9 Open Question：low_context 是否應影響 curate 動向？

尚待決定：`low_context` 是 ingest 對可用內文品質的觀測訊號，是否應限制 item 在 curate 階段可採取的動作，或僅作為提供 curate 判斷的中繼資料？

目前系統中，item 通過 classify 後仍必須經 curate 決策，沒有任何路徑可直接發佈 `publish_summary`。因此「low_context 項目僅允許 `publish_link`」或「禁止 `publish_summary`」是否為必要的程式護欄，應與 curate 模組的既有編輯責任邊界一併重新評估；在此問題定案前，不應將該限制視為方案四不可變更的前提。

> **2026-07-23 關閉：** 依 §4.8，方案五獲選即等同對本問題做出答覆——`low_context` 不限制 item 在 curate 階段可採取的動作，也不作為中繼資料傳入 curate；curate 依現行政策對所有進件一視同仁。未來是否啟動 curate-owned 的薄文本路由規則，由 §4.6 觀測結果另行提案。

---

## 4. 方案五——最小撤閘：僅修佇列謂詞，標籤完全留在分析域（已採用）

**提案日期：** 2026-07-22 晚間  
**定案日期：** 2026-07-23（獲選為採用方向；實作依 [INGEST_LOW_CONTEXT_REFACTORING_PLAN.md](INGEST_LOW_CONTEXT_REFACTORING_PLAN.md) 執行）
**提出背景：** 2026-07-22 對 live `canonical.db` 的抽查（見 4.7）確認 low_context 閘門如預期失效；同日晚間針對方案四做邊界合規性審閱，認定 §3.4-1 的程式護欄抵觸 `MODULE_BOUNDARIES.md` §3.3（downstream action selection 為 curate 專有所有權），且 prompt 中繼資料等附加設計所防禦的失敗模式均未經觀測。方案五將方案四收斂至最小核心：撤閘門，僅此而已。

### 4.1 決策理由

方案四的正確核心只有一件事：**撤除佇列閘門**。其餘附加設計（classify prompt 中繼資料、curate 程式護欄、publish_link 合規率監控）防禦的都是尚未觀測到的失敗模式，且各自帶有成本：

- **curate 程式護欄抵觸模組邊界。** `MODULE_BOUNDARIES.md` §3.3 明定 curate 擁有 downstream action selection，其 May read 清單亦未含 `text_processing_status` / `reason`。讓 ingest 的觀測標籤對 curate 的路由決策行使程式否決權，等同將「代理指標搶占 LLM 判斷」的錯誤從 classify 門口移到 curate 門口——正是本案要消除的設計錯誤。
- **classify prompt 中繼資料資訊量趨近零。** `mostly_links` 項目送達 classify 時，LLM 的輸入已是「完整標題 + 重複標題的內文」，模型可自行識別連結包裝；提示語僅复述其可見之事，卻需改動 `prompt_templates.yaml` 與 `orchestrator.py`，並承擔 §3.6 自身顧慮的「誘發 unknown 偏差」風險。
- **單一變數實驗。** 只改佇列謂詞，上線後觀測到的任何現象（unknown 率、核准率、摘要品質）均可乾淨歸因；多處同時變動將使觀測難以解讀。
- **可逆性。** 被方案五推遲的每一項功能，都能在取得 baseline 觀測數據後以小改動補上；先寫再拆的成本更高。

### 4.2 設計內容與實作定位

**唯一核心 pipeline 改動：** `modules/classify/src/database.py` 的 pending 查詢 WHERE 條件，由 `text_processing_status = 'completed'` 改為排除式謂詞：

```sql
text_processing_status != 'failed'
AND (text_processing_reason IS NULL OR text_processing_reason != 'post_cleanup_empty')
```

- `text_processing_status` 為 NOT NULL 且 enum 僅三值，`!= 'failed'` 語義安全。
- `text_processing_reason` 可為 NULL，謂詞必須以 NULL-safe 寫法保留 reason 為 NULL 的 completed 項目（現行庫 4,845 筆 completed 全為 NULL reason）。
- 不改 SELECT 欄位：不帶出 status / reason（因為不傳 prompt），改動僅限 WHERE。

> **2026-07-23 補充（實作範圍）：** 核准後的實作範圍除此核心 pipeline 改動外，另包含 analysis 與 dashboard 的唯讀配套更新（聚合查詢口徑、報表欄位改名 `low_context_bypass_*` → `low_context_observation_*`、schema version 遞增、report loader 與 view 調整）。缺少這些配套時，放行的 low-context 分類列會被誤報為 `ORPHANED_CLASSIFICATION` 異常，並從 classified、curate、approval、translation、publication 等下游漏斗指標中遺漏。完整清單以實作計畫 §4.2 為準。

**明確不做（相對方案四的刪除項）：**

- classify `prompt_templates.yaml` / `orchestrator.py`：**零改動**，不傳中繼資料
- curate `orchestrator.py` / `database.py` / `CURATION_POLICY.md` / `PROMPT_CONTRACT.md`：**零改動**——不加護欄、不帶欄位、不加指引。low_context 項目抵達 curate 後由現行政策全權裁量（`CURATION_POLICY.md` §2.5 Text Extraction Quality 與 §3 路由規則已涵蓋薄文本的處理依據）
- ingest / sanitizer：**零改動**（同方案四；`text_processing_status` / `reason` 照寫，不得抹除——觀測依據）
- schema：**零改動**（2026-07-22 確認保留兩欄位）

**測試（classify）：** 覆蓋以下四項——reason 為 NULL 的 completed 項目入列；`failed` 不入列；`post_cleanup_empty` 不入列；low_context 其餘 reason（`mostly_links` / `too_short` / `title_heavy` / `title_only` / `template_heavy`）入列。

**成本估算：** 與方案四完全相同（佇列口徑一致）。舊庫 32 天口徑 +83 次/日（240 → 323，+34% 為上限）；live 庫 2026-07-22 驗證值 low_context 佔比 19.9%（1,203 / 6,049），若成立則實際增幅約 20–25%。

**上線時 backlog 處理（已定案）：** 2026-07-23 定案採用**開發庫重建**：目標環境為開發環境，`data/canonical.db` 屬可拋棄開發產物，重構完成後直接刪除重建，不做歷史項目回填、cutoff 或遷移（原 (a) 自然消化 / (b) cutoff / (c) 部分回填選項因此不適用）；`data/canonical_final.db` 為獨立歷史分析庫，不受影響。重建與煙霧驗證程序見實作計畫 §6。

### 4.3 合約修訂點（同方案四的五處，但修訂幅度更小）

- `docs/SYSTEM_OVERVIEW.md`（§6.2 classify pending item selection）：改為排除式謂詞描述
- `docs/MODULE_BOUNDARIES.md` §3.2：「唯讀 completed 項目、不得為 low_context/failed 建佔位列」改為「讀取所有具可分類訊號項目，僅排除 `failed` 與 `post_cleanup_empty`」；May read 清單補列 `text_processing_status` / `text_processing_reason`，註明**僅供佇列謂詞使用**（不用於 prompt）
- `docs/DATA_LIFECYCLE.md`（生命週期圖與終止規則）：low_context 項目不再終止於 classify 前
- `docs/CANONICAL_ENTITY_CONTRACT.md`：Classification Result 實體不再僅保留給 completed 項目
- `modules/ingest/docs/SANITIZATION_STRATEGY.md` §8.4：移除「low_context 排除於 classify 佇列」條文，保留「ingest 不做語義判斷」邊界

**不需要修訂**（方案四原本要動的）：curate 全系列文件、`MODULE_BOUNDARIES.md` §3.3 curate May read（不帶欄位給 curate）。

### 4.4 與方案四對照（供工程師評估）

| 維度 | 方案四 | 方案五 |
|---|---|---|
| 撤除佇列閘門 | 同 | 同（兩案唯一共有的改動） |
| code 改動範圍 | classify（查詢 + prompt + orchestrator）+ curate（查詢 + 驗證） | classify 查詢 WHERE 一處 |
| curate 邊界 | §3.4-1 程式護欄抵觸 §3.3 所有權（§3.9 自認 open question） | 完全符合現行邊界 |
| 防 LLM 編造摘要 | 預防性程式護欄 | 不預防；依賴 curate 現行政策 + 上線後抽查（4.5） |
| classify unknown 率 | prompt 中繼資料本身可能誘發偏差（§3.6 自列觀測項） | prompt 不變，無新增變數；unknown 率照實觀測 |
| 合約修訂幅度 | 五處 + curate May read + CURATION_POLICY | 五處，幅度較小 |
| 實驗可歸因性 | 多變數同時變動 | 單一變數 |
| 後續擴充 | 已內建（但對應未驗證的需求） | 延後至觀測數據支持時以小改動補上 |

### 4.5 已接受風險與緩解

- **風險一：curate 對 title-only 項目編造三點摘要。** 方案四以程式護欄預防；方案五不預防。緩解：(a) `CURATION_POLICY.md` §3.2 要求 bullet 必須為文中萃取的 claim / evidence / implication，無文可萃時的合規行為本應是 publish_link 或 reject；(b) §2.5 已將文本提取品質列為 curate 裁量準則；(c) 上線後人工抽查（4.6）。**若抽查發現捏造普遍存在，屆時的正確作法是由 curate 自己的 policy 文件訂立規則、由 orchestrator 替 curate 執行，所有權留在 curate**——而非讓 ingest 標籤越界否決。
- **風險二：classify 對 title-only 項目的 unknown 率偏高，API 成本回收率低。** 已接受（2026-07-22 共識：classify 全量成本既已認列，unknown 率僅為觀測指標而非事故）。若觀測值過高，屆時再評估 prompt 中繼資料，且有 baseline 可對照。
- **風險三：Google News link card 跳轉鏈不透明**（同 §3.7，呈現層問題，不阻塞本案）。

### 4.6 上線後觀測（純分析域，不寫 pipeline code）

放行後 1–2 週，以臨時 SQL 或 analysis 模組量測：

- 放行 low_context 族群的 classify 分佈（core / adjacent / irrelevant / unknown；unknown 過高 → 再評估 prompt 中繼資料）
- 放行族群的 curate 核准率與 downstream_action 分佈（publish_link / publish_summary 比例）
- **publish_summary 項目的人工抽查**：摘要是否捏造（捏造普遍 → 啟動 4.5 風險一的 curate-owned 規則程序）
- classify 每日實際呼叫量與 token 成本（對照 4.2 估算）
- 各來源 low_context 率與 reason 分佈 baseline（供未來來源健康監控；分析域用途，不需 pipeline 改動）

### 4.7 附錄：2026-07-22 live `canonical.db` 抽查結果（方案五的事實基礎）

2026-07-12 ~ 07-22 累積 6,049 筆：

- `completed` 4,845（80.1%）/ `low_context` 1,203（19.9%）/ `failed` 1；1,203 筆 low_context **全數無** `classification_result`（閘門 100% 生效）
- low_context reason 分佈：`mostly_links` 781（64.9%）、`too_short` 402（33.4%）——與舊庫同構，調整長度門檻依舊無效
- 被擋項目標題 ≥30 / ≥40 / ≥50 字元比例：95.7% / 92.9% / 89.1%（舊庫為 95.3 / 93.3 / 89.7）
- 隨機抽樣 30 筆，含大量高價值被擋快訊（五角大廈文件解密、吹哨者保護法案、UAP 聽證會等）
- 自然實驗在 live 庫重現：completed 且內文 100–199 字元族群（n=1,258），classify 判 66.4% `irrelevant`；進入 curate 的 421 筆核准率 83.6%（352 筆）——下游兩道 LLM 關卡能有效處理短內容族群
- 歸因注意：live 庫 source_id 80–83 之項目 `canonical_url` 全指向 `news.google.com`（累積期間該等 ID 指向 Google News 快訊 feed，`sources.yaml` 後續重新映射）；每來源歸因須以抓取時點的設定為準

### 4.8 §3.9 Open Question 之處理

方案五等同對 §3.9 做出答覆：**`low_context` 不限制 item 在 curate 階段可採取的動作，也不作為中繼資料傳入 curate**；curate 依現行政策對所有進件一視同仁。若方案四獲選，§3.9 維持 open；若方案五獲選，此問題關閉，未來是否啟動 curate-owned 的薄文本路由規則，由 4.6 觀測結果另行提案。
