# Multilingual Content Strategy

**Document version:** v1.0  
**Updated:** 2026-06-17  
**Status:** Under Discussion / Planning Draft

---

## 1. 背景與動機 (Background & Motivation)

為了提供高品質的多語系讀者體驗並兼顧 LLM 營運成本，系統需要支援中、英、日三種語言的文章內容呈現。多語系生成不應是前端網頁（`site` 模組）的即時行為，而應在後端管線中完成處理，最終以靜態多語系 JSON 檔案形式導出。

本文件確立「內容多語系（Content Multilingualism）」與「介面多語系（UI i18n）」的分離，並引導未來可能成立的 `translate` 獨立處理模組之合約設計。

---

## 2. 核心架構原則 (Core Architecture Principles)

### 2.1 內容多語化 vs. 介面多語化
1. **內容多語系 (Content Multilingualism)**：指的是文章的標題、摘要、核心要點（Markdown）等動態內容的多語版本。這屬於**內容生產管線**的範疇，必須持久化於資料庫並在發布前導出。
2. **介面多語系 (UI i18n)**：指的是網站介面的靜態字串（如「首頁」、「閱讀更多」、「發布時間」）與路由、切換機制。這由下游的 `site` 模組在前端處理，不屬於資料庫的內容範疇。

### 2.2 發布流順序 (Pipeline Sequence)

內容多語翻譯應接在**「最終可發布母稿」**產出之後。處理流程演化如下：

```text
[ingest] ──> [classify] ──> [curate] ──> [approved_content_record] ──> [translate] ──> [publish] ──> [site]
                                     \-> [edit] ───────────────────/
```

* **上游合約**：`translate` 模組不直接依賴 `curate` 或 `edit` 的內部狀態，而是讀取統一抽象化的「已核准發布母稿」紀錄（`approved_content_record`）。不論是自動審核通過直接發布，抑或是經過人工編輯定稿發布，皆會統一生成此母稿紀錄。
* **下游合約**：`publish` 模組只消費 `translation_output` 中的多語系成品，不直接依賴 `approved_content_record` 的內部欄位結構。

### 2.3 Current Operating Assumption
The pipeline is designed to remain language-agnostic at the contract level. The current operating assumption may still use Chinese as the primary draft language, but that is an editorial policy choice rather than a fixed architectural requirement.

---

## 3. 翻譯狀態與指紋管理 (Fingerprinting & Invalidation)

為了解決內容更新、二次編修、或翻譯品質改版（Prompt/Model 變更）時的失效判定問題，`translate` 模組必須引入**指紋管理機制**。

### 3.1 來源指紋 (Source Fingerprint)
當翻譯模組讀取上游母稿時，將依據其關鍵內容欄位計算 SHA-256 雜湊值（即 `source_fingerprint`）。
* **計算公式**：
  $$\text{source\_fingerprint} = \text{SHA-256}(\text{display\_title} + \text{content\_body} + \text{source\_attribution\_note})$$
* **功用**：每次翻譯執行時，比對上游母稿的最新指紋與 `translation_output` 中已快取的指紋。若指紋不一致，代表上游母稿被重跑、改寫或更動，舊的翻譯即宣告失效。

### 3.2 翻譯失效與品質指引變更判定
除了母稿內容變更外，以下因素也會觸發失效判定：
1. **模型升級或 Prompt 更新**：當配置的 `model_name` 或 `prompt_version` 與已快取的翻譯紀錄不符時，該筆翻譯將被標記為 `stale`（過期）。
2. **手動重跑 (Operator Force)**：維運人員可以手動將特定項目的翻譯狀態改為 `pending` 以強制重新翻譯。

### 3.3 翻譯品質狀態矩陣 (Translation Quality Status)

| 狀態 (Status) | 說明 | 下游 `publish` 行為 |
| :--- | :--- | :--- |
| **`pending`** | 翻譯任務已建立，等待 LLM 處理中。 | 拒絕發布該項目，或僅發布已完成語系（依據發布策略）。 |
| **`completed`** | 該語系翻譯已成功完成且指紋一致。 | 允許發布該語系內容。 |
| **`failed`** | 翻譯過程中出錯（如 API 逾時、Refusal、格式錯誤）。 | 不發布，並於下次執行時重試。 |
| **`stale`** | 來源母稿指紋變更，或翻譯配置（Prompt/Model）變更。 | 觸發重新翻譯，在此之前可降級發布舊版或暫時隱藏。 |

---

## 4. 資料庫合約草案 (`translation_output`)

為維護資料庫完整性，應將 `parent_content_id` 與 `language_code` 設為複合唯一鍵 `UNIQUE (parent_content_id, language_code)`，用以區分同一個母稿下的多語系翻譯。

| 欄位名稱 | SQLite 類型 | 空值限制 | 說明 |
| :--- | :--- | :--- | :--- |
| `translation_output_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | 流水號主鍵 |
| `parent_content_id` | `INTEGER` | `NOT NULL` | 外鍵，關聯至 `approved_content_record` (上游發布母稿) |
| `source_item_id` | `INTEGER` | `NOT NULL` | 外鍵，關聯至 `source_item` (保留對帳與來源追蹤用) |
| `language_code` | `TEXT` | `NOT NULL` | 語系代碼（`'zh'`, `'en'`, `'ja'` 等） |
| `display_title` | `TEXT` | `NOT NULL` | 該語系的標題 |
| `content` | `TEXT` | `NOT NULL` | 該語系的 Markdown 內文（摘要與列表的拼接） |
| `source_attribution_note`| `TEXT` | `NULL` | 該語系的來源備註 |
| `source_fingerprint` | `TEXT` | `NOT NULL` | 計算自上游母稿內容的 SHA-256 指紋 (偵測內部編輯更新) |
| `translation_status` | `TEXT` | `NOT NULL` | 品質與生命週期狀態（`'pending'`, `'completed'`, `'failed'`, `'stale'`） |
| `model_name` | `TEXT` | `NOT NULL` | 所使用的翻譯 LLM 名稱 |
| `prompt_version` | `TEXT` | `NOT NULL` | 所使用的翻譯 Prompt 版本 |
| `translated_at` | `TEXT` | `NULL` | 翻譯完成的 UTC 時間戳記（僅在 `completed` 狀態下有值） |
| `created_at` | `TEXT` | `NOT NULL` | 記錄建立時間 |
| `updated_at` | `TEXT` | `NOT NULL` | 記錄更新時間 |

---

## 5. 發布策略與語言覆蓋 (Language Coverage Policy)

`publish` 模組在讀取 `translation_output` 時，必須遵守語言覆蓋策略，以避免前端網站出現內容破碎的現象：

1. **嚴格覆蓋策略 (Strict Match)**：只有當一個項目在所有配置語系（如中、英、日）的 `translation_status` 皆為 `completed` 時，才允許將此項目輸出至 static 資料夾。這能保證讀者在任何語系介面下點擊都能讀到對應的文章。
2. **主語言優先策略 (Primary-Only Fallback)**：只要主語言（如 `zh`）完成，即可發布；其餘語系若為 `pending`/`stale`，則在該語系頁面下顯示「翻譯中...」或自動 Fallback 回主語言。
3. **獨立發布策略 (Independent Publish)**：各語系獨立，只輸出已 `completed` 的語系檔案。

**實作決策與演進政策**：
* **MVP 預設契約**：強制採用**「嚴格覆蓋策略」**，保證發布品質的一致性。
* **代碼設計彈性 (Config-driven)**：發布策略的判定邏輯應於 `publish` 模組內實作為可配置的規則 (Config-driven)，各語系仍保持獨立的資料庫資料列 (Row)。如此一來，未來調整為 Fallback 或獨立發布時，僅需調整 `publish` 模組的查詢條件與配置，不需重構資料表 schema。

---

## 6. Slug 生成策略與權衡 (Slug Generation & ID Trade-offs)

在多語系架構下，URL Slug 的唯一身分識別（Identity）生成有以下三種權衡方案：

1. **方案 A：由英文翻譯標題生成（目前推薦）**
   - **作法**：等待英文翻譯完成後，使用英文 `display_title` 進行 slugify。
   - **優點**：URL 乾淨美觀（無中文百分比編碼 ％-encoding），極利於國際 SEO 與人工辨識。
   - **缺點**：將 URL 身分綁定在外語翻譯成功上。如果英文翻譯失敗或延遲，即便中文母稿和日文版都已準備就緒，仍會因無法生成 slug 而導致整篇文章無法發布。
   - **適用情境**：在**「嚴格覆蓋策略（Strict Match）」**下，因要求所有語言均完成才發布，此缺點不會造成額外阻礙。

2. **方案 B：由主語言標題或來源標題生成**
   - **作法**：直接對主語言（例如中文）標題進行 slugify，或將中文轉為拼音。
   - **優點**：不依賴任何翻譯，母稿一核准即有唯一 Slug。
   - **缺點**：網址會出現繁複的百分比編碼（Percent-encoding），對讀者與 SEO 體驗不佳；使用拼音則可能不精確。

3. **方案 C：使用穩定之來源項目 ID 或 UUID**
   - **作法**：使用 `source_item_id`（如 `items/123.json`）作為 Slug。
   - **優點**：絕對穩定、完全無語言依賴。
   - **缺點**：完全喪失 URL 的 SEO 語意價值。

**MVP 與中長期策略 (Slug 永久凍結規則)**：
為了極大化 SEO 價值與避免讀者遇到 404 斷鏈，系統強制執行 **「首次發布即永久凍結（First-Publish Freeze）」** 契約：
1. 當文章首次成功發布時，系統依據當時的英文標題生成 Slug，並將其寫入資料庫中的 `publish_record`（或等價的 frozen slug registry）永久保存。
2. 後續不論英文標題因重新翻譯、Prompt 調整或人工修訂如何變更，`publish` 模組都必須使用資料庫中已凍結的 Slug，**不得重新生成**。
3. 若確有更正網址之必要，必須視為特殊運維操作，並由操作者手動設定 301 重導向，系統本身不提供自動更新 Slug 的功能。

在 MVP 中，我們搭配 **「嚴格覆蓋策略」**，因此 **方案 A（首次英文標題生成後凍結）** 為最優解。未來若升級為「主語言優先策略」，系統將自動 Fallback 至以 `source_item_id` 或主語言拼音作為永久 Slug 的方案。
