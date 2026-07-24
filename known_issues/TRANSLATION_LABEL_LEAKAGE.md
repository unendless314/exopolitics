# Known Issue: Translation Label Leakage (翻譯標籤英文殘留問題)

> **狀態更新 (2026-07-24)**：處置方案已定案 —— 採用「方案 A 變體（site 端貼標籤，下稱方案 S）」，搭配全庫重建。調查驗證數據、代碼級定位、定案設計與重構範圍清單見本文第 4～7 節。第 1～3 節保留為原始分析。本文件將於實作完成並驗收後移至 `known_issues/resolved/`。

## 1. 問題描述 (Issue Description)
在系統將母稿翻譯為目標語言（繁體中文 `zh`、日文 `ja`）時，文章內結構化 Bullet 點的英文標籤（`Key Claim`、`Evidence Level`、`Objective Impact`）有極高機率（約 92%）被 LLM 照抄保留為英文，未能正確翻譯。

*   **影響範圍**：所有含有這三個 Bullet 標籤的文章（共 568 則，對應 1,136 個翻譯檔案）。
*   **目前現狀**：雖然在代碼重構中已將標籤由粗體（`**Key Claim**:`）改為純文字（`Key Claim:`），但在缺少特定 Prompt 指令下，LLM 依然會將其視為不可變動的結構化符號，導致英文標籤殘留。

---

## 2. 根本原因分析 (Root Cause Analysis)

### 2.1 內容與排版提前耦合 (Content-Presentation Coupling)
系統目前的架構是在 `translate` 模組上游的 `curate` 或 `assemble` 階段，就將 UI 排版所需要的標籤文字（例如 `* Key Claim:`）直接拼接到母稿內容（`content_body`）中，再將整段文本送給 LLM 進行翻譯。

### 2.2 LLM 對標記性文字的「直譯盲區」
對於 LLM 而言，以下類型的文字很容易被判定為「結構標記」或「程式 Key」，從而直接照抄不進行翻譯：
*   **Markdown 語法包裹**：例如 `**Key Claim**:`，模型會優先保留其原始語法。
*   **行首的鍵值對格式**：例如 `* Key Claim: [內容]`，模型容易將 `Key Claim` 視為 JSON key 或清單的導引標頭。

### 2.3 提示詞工程 (Prompt Engineering) 的侷限性
雖然可以透過在 Prompt 中加入極為強制的規則（例如「必須翻譯 Key Claim 標籤」）來解決此問題，但這種作法非常脆弱：
*   隨着支持語系的增加，Prompt 的規則會變得越來越冗長、複雜，且容易引起其他翻譯品質退化（regression）。
*   一旦未來需要更換 UI 標籤名稱，所有已翻譯的文章都必須付費重新呼叫 API 翻譯，維護成本極高。

---

## 3. 長期架構優化建議 (Recommended Long-Term Solutions)

強求 AI 進行 UI 標籤的翻譯在工程上並非最優解。建議未來改動系統架構，採用以下兩種方式之一來徹底解決此缺陷：

### 方案 A：前後端分離與前端在地化 (Frontend Localization - 推薦)
這是最符合軟體工程實踐的解法。
1.  **資料庫只儲存乾淨的內容**：
    在 `approved_content_record` 與 `translation_output` 中，**完全不要包含** `Key Claim` 等標籤文字。只保存純內容陣列或 JSON 鍵值對：
    ```json
    {
      "key_claim": "這是主張內容...",
      "evidence_level": "這是證據等級...",
      "objective_impact": "這是客觀影響..."
    }
    ```
2.  **前端 UI 渲染時再套用標籤**：
    在 `site` 渲染模組或前端頁面上，根據當前網頁語言（`en`/`zh`/`ja`）的翻譯對照表（i18n），在渲染時才動態加上對應的標籤：
    *   英文版頁面渲染：`* **Key Claim**: 內容`
    *   中文版頁面渲染：`* **關鍵主張**: 內容`
    *   日文版頁面渲染：`* **主要主張**: 內容`
*   **優點**：LLM 只需要專注翻譯純內容，100% 避免標籤殘留。未來修改 UI 標籤名稱時，只需修改前端對照表，**完全不需要重新呼叫 API 翻譯**。

### 方案 B：在組裝（Assemble）階段後置處理 (Post-processing on Assembly)
如果仍希望輸出整段 Markdown，可以在翻譯完成後，由程式在本地端進行正則表達式（Regex）或字串替換，將英文標籤替換為對應的在地化標籤。
*   **優點**：改動較小，不需要調整資料庫欄位架構。
*   **缺點**：如果翻譯內容中偶然出現了類似的英文字眼，可能會有誤判替換的風險。

---

## 4. 調查驗證結果 (Investigation Findings, 2026-07-24)

以本機備份 `data/canonical.db` 實測驗證（雲端即時資料不在本機，但使用者已確認雲端昨日更新仍有相同狀況）。

### 4.1 洩漏規模實測

*   快照概況：`approved_content_record` 共 2,591 列，其中 **462 篇**母稿含三條 bullet（即 `publish_summary` 類文章）；`translation_output` completed 列數：en 2,591 / ja 2,591 / zh 2,588（另有 zh 3 列 failed）。
*   **zh**：424/462（**91.8%**）三個英文標籤全部原樣殘留。
*   **ja**：443/462（**95.9%**）三個英文標籤全部原樣殘留。
*   與第 1 節約 92% 的估計一致（第 1 節的 568 則／1,136 檔為先前較大快照的統計，本次實測快照為 462 篇，兩者比例吻合）。

### 4.2 雙重問題：「有翻譯」的部分措辭也不一致

實測發現問題比「英文殘留」更糟：少數確實翻譯了標籤的文章，譯法彼此分裂，同一標籤在同一語言內出現多種寫法。

zh 已觀測變體（38 篇有翻譯者）：

| 原標籤 | 已觀測譯法（出現次數） |
| :--- | :--- |
| Key Claim | 主要主張 (26)、關鍵主張 (11)、核心主張 (1) |
| Evidence Level | 證據層級 (26)、證據等級 (12) |
| Objective Impact | 客觀影響 (35)、實際影響 (3) |

ja 已觀測變體（19 篇有翻譯者）：

| 原標籤 | 已觀測譯法（出現次數） |
| :--- | :--- |
| Key Claim | 主要な主張 (7)、主要主張 (7)、主張の要点 (5) |
| Evidence Level | 証拠の水準 (9)、証拠レベル (7)、証拠水準 (2)、エビデンスレベル (1) |
| Objective Impact | 客観的な影響 (9)、客観的影響 (8)、目的上の影響 (2) |

*   **含義**：即使靠 prompt 強制要求翻譯，也只能解決「不翻」這一半問題，「翻了但措辭分裂」無法靠 prompt 收斂。此數據佐證本問題必須以架構手段根除，方案 B（prompt 強制／後置 regex）否決。

### 4.3 代碼級定位

*   **唯一注入點**：`modules/translate/src/approved_content_record.py:19-33` 的 `splice_content_body()`，以寫死的字串 `* Key Claim:` / `* Evidence Level:` / `* Objective Impact:` 拼接進母稿 `content_body`。
*   **上游語意完全固定**：`modules/curate/docs/PROMPT_CONTRACT.md:153-156` 規定 `bullet_1/2/3` 恆為 claim／evidence／implication，固定順序、最多三條、恆接在摘要段落之後；`publish_link` 時三條皆為 NULL。位置與語意 100% 確定 —— 這是結構化解法可行的關鍵前提。
*   **下游全鏈路無標籤處理**：`translate` 將整包 markdown 送翻 → `publish` 整包匯出 `translation_output.content`（另以 `modules/publish/src/orchestrator.py:50` 的 `extract_summary_short()` 從首段反推摘要）→ `site` adapter 原樣寫入 markdown body 渲染。
*   **en 語言不受影響**：en 列為 bypass 自翻譯（`model_name = 'bypass'`），英文標籤在英文頁本來就是正確呈現。

---

## 5. 定案處置措施 (Decided Remediation, 2026-07-24)

### 5.1 決策摘要

*   採用**方案 A 變體 = 方案 S（site 端貼標籤）**：內容鏈路全程結構化、零 UI 字串，標籤只存在於 site 的 i18n 對照表。
*   **保留 `approved_content_record` 銜接層**（理由見 5.4）。
*   **全庫重建**，不做資料遷移或重塑（理由見 5.5）。
*   否決方案 B：只修補實例而不除根，且無法解決 4.2 的措辭分裂。

### 5.2 重要澄清：結構化 ≠ 增加 API 次數

*   **硬性成本約束**：每篇文章每語言必須**一次 API call** 完成翻譯，且全文上下文一起送（不得 summary 一次、bullets 再一次）。
*   此約束在新設計下完全滿足：`curate` 模組本身就是單次 call 產出 `display_title + summary_short + bullet_1/2/3` 五個結構化欄位的現成先例；`translate` 套用同一模式，**call 數、上下文與成本與現狀完全相同**。結構化改變的是儲存形狀與 I/O schema，不是呼叫粒度。

### 5.3 設計定案（D1–D4）

核心概念：handoff 從「拼接」改為「結構化直通」，五欄同形從 curate 一路流到 publish export，標籤直到 site build 時才貼上。

```text
curation_output ──(直通複製)──> approved_content_record ──(單次call)──> translation_output
      ──> publish_export JSON ──> site adapter 依語系貼標籤
```

*   **D1 — `approved_content_record` 結構化直通**
    *   內容欄位改為與 `curation_output` 完全同形：`display_title`、`summary_short`、`bullet_1`、`bullet_2`、`bullet_3`（`publish_link` 時三條 NULL）。
    *   **廢除 `content_body` 欄位**，刪除 `splice_content_body()`。
    *   `content_fingerprint` 序列化規則改為五欄固定順序拼接（規則須在 DATA_CONTRACT 寫死：欄位順序、NULL 處理、換行正規化）。
*   **D2 — translate 單次 call 結構化 I/O**
    *   Prompt v2 分段呈現：`Source Title` / `Source Summary` / `Source Bullet 1 (factual claim)` / `Source Bullet 2 (evidence level)` / `Source Bullet 3 (objective implication)`。槽位說明只存在於 prompt meta-text，不進入輸出。
    *   Response schema 改為 `{translated_title, translated_summary, translated_bullet_1/2/3}`，bullets nullable。
    *   Validation 調整：形狀與來源一致（null-in-null-out）；欄位值不得以英文標籤或 4.2 已知變體開頭（便宜守門）；標題長度上限保留。純文字欄位不再需要 markdown 結構驗證（code fence／header 計數）。
    *   `translation_output` 同樣改為五欄同形；`prompt_version` 升版（translator_v2）。
    *   en bypass 自翻譯改為結構化欄位複製。
*   **D3 — publish 匯出帶語意鍵的結構**
    *   item JSON 廢除 `content` 欄位，改為 `summary_short` + `bullets: {"key_claim": ..., "evidence_level": ..., "objective_impact": ...}`；`publish_link` 時 `bullets` 省略或為 null。
    *   `bullet_1/2/3 → key_claim/evidence_level/objective_impact` 的語意映射**只在 publish 這一處**發生，並寫進合約。
    *   index.json 的 `summary_short` 改為直接取用，刪除 `extract_summary_short()`。
*   **D4 — site 為唯一的標籤所在地**
    *   site adapter（build 時 JSON→markdown）依語系對照表貼標籤，例如 zh：`* **關鍵主張**: {bullets.key_claim}`；Astro 模板不需變動。
    *   對照表放 site 的 i18n locale 檔，符合 `docs/MULTILINGUAL_CONTENT_STRATEGY.md` §2.1 的 UI i18n 所有權劃分。
    *   預設措辭：**zh＝關鍵主張／證據等級／客觀影響；ja＝主要主張／証拠レベル／客観的影響**。日後調整措辭、新增語系、或改變 bullet 排版（如 definition list），都只動 site。

### 5.4 銜接層保留的理由（不冗餘的論證）

`approved_content_record` 改為同形後看似 pass-through，但其職責從來不是資料轉換，而是：

1.  **模組所有權防火牆**：下游只依賴這張穩定的對外合約，不依賴 `curate` 的內部 schema；curate 日後改欄位不直接衝擊 translate。
2.  **版本與失效中繼資料的物化點**：`content_fingerprint` 寫入時算一次、下游只比對字串；`approved_at`（編輯語義）與 `updated_at`（物化時間）在此分離；delta 預篩掛在這層。
3.  **語言解析政策落點**：`content_language_code` 的 path-based 解析規則由 assembler 負責。
4.  **「目前可發布」狀態的一等公民**：每個 source_item 恰好一列，存在即當前核准版本。
5.  **未來 edit 模組的合流點**：edit 上路後將有第二個寫入者（edit_draft 定稿 → 銜接層），以及第二種內容形狀（free-form markdown，屆時才擴充欄位，本次不預留）；已發布項目的人編修訂也經由 fingerprint 變更 → 翻譯 stale → 重譯的鏈路傳播。`author_metadata` 的 `writer_type`/`editor` 欄位已為此預留。

### 5.5 資料策略：全庫重建（不做遷移／重塑）

*   ingest 模組已完成重構（RSS sources 清單、low_context 過濾規則變更），**舊庫的 source_item 母體本身已失效**，保留下游翻譯等於保留對一個已不存在語料庫的翻譯，無意義。
*   因此不寫 reshape／migration script，也不保留雲端舊庫。
*   已知並接受的成本：curate + translate 全量 API 重跑。
*   實施順序：合約文件 → schema/migration → 各模組程式 → 測試 → 全新跑一輪 pipeline。

---

## 6. 重構範圍清單 (Refactoring Scope Checklist)

供工程師展開細部實作計畫參考用（技術文件 + 程式碼）。

### 6.1 技術文件（依 repo 規定須與程式同批變更）

*   `modules/translate/docs/DATA_CONTRACT.md` — 兩表新 schema、fingerprint 序列化規則、assembler 直通規則、migration DDL。
*   `modules/translate/docs/PROMPT_CONTRACT.md` — translator_v2 prompt template、response schema、新 validation 規則。
*   `modules/translate/docs/STATE_TRANSITIONS.md`、`EXECUTION_POLICY.md` — 檢查對 `content` 欄位與 markdown 驗證的引用並同步。
*   `modules/publish/docs/DATA_CONTRACT.md` — item/index JSON 形狀、bullets 鍵映射、`summary_short` 來源。
*   `modules/site/docs/DATA_HANDOFF_CONTRACT.md` — item 欄位變更、adapter 貼標籤職責、SEO description fallback cascade 簡化（`summary_short` 恆可直接取用）。
*   `docs/MULTILINGUAL_CONTENT_STRATEGY.md` — 補述 bullets 結構化端到端、標籤所有權歸 site（原則已符合，只需落實描述）。
*   `modules/curate/docs/*` — **不需變更**（bullet 語意來源維持不變）。

### 6.2 程式碼

*   `modules/translate/src/approved_content_record.py` — 刪除 `splice_content_body()`，改五欄直通複製，新 fingerprint 序列化。
*   `modules/translate/src/migrations/` — 新 DDL（全庫重建，可直接修訂 v001）。
*   `modules/translate/src/orchestrator.py` — queue 查詢欄位、payload 建構、prompt v2、response schema、validation（形狀一致／標籤前綴守門／移除 markdown 結構檢查）。
*   `modules/translate/src/database.py` — 結構化欄位讀寫；en bypass 改結構化複製。
*   `modules/publish/src/orchestrator.py`、`cli.py` — 讀結構化欄位、組 `bullets` 物件、payload validation 同步、刪除 `extract_summary_short()`。
*   `modules/site` adapter — 語系標籤對照表 + build 時貼標籤。

### 6.3 測試

*   `modules/translate/tests/test_translate.py` — 移除 `Key Claim` 相關 fixture／assertion，改結構化欄位；新增 nullability 形狀與標籤前綴守門測試。
*   `modules/publish/tests/test_publish.py` — 更新 schema fixture 與 `content` 欄位；新增 bullets 鍵映射測試。
*   site adapter 貼標籤測試（若 site 已有測試基礎）。

### 6.4 驗收條件

*   全新 pipeline 跑完後抽查：zh／ja 頁面標籤為對照表措辭且全站一致；en 頁呈現英文標籤不變；`publish_link` 項無 bullets；修改 site 對照表措辭後 rebuild 即生效，無需重打 API。
*   驗收通過後，本文件移至 `known_issues/resolved/`。

---

## 7. 實作注意事項 (Implementation Notes)

*   標籤前綴守門清單應包含英文三標籤及 4.2 節列出的全部已知變體（zh 七種、ja 十種寫法）。
*   fingerprint 序列化規則是失效偵測的錨點，必須在 DATA_CONTRACT 中唯一寫死，assembler 與測試共用同一規則。
*   未來 `edit` 模組建立合約時，free-form 形狀擴充（如加回 nullable `content_body`）屬於該次變更範圍，本次重構不預留。
*   若未來 bullet 數量或語意變更（屬 curate 合約層），連鎖更新範圍為：`curate` PROMPT_CONTRACT → `translate` DATA_CONTRACT／PROMPT_CONTRACT → `publish` 鍵映射 → `site` 對照表。
