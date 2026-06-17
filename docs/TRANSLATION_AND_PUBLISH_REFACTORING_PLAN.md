# Translation & Publish Refactoring Design Plan

**Status:** Proposed / Under Discussion  
**Updated:** 2026-06-17  
**Author:** Antigravity  

---

## 1. 系統架構與流程設計 (System Architecture)

配合內容多語化戰略，發布流水線調整如下：

```text
[ingest] ──> [classify] ──> [curate] ──> [approved_content_record] ──> [translate] ──> [publish] ──> [site]
                                     \-> [edit] ───────────────────/
```

- **`curate` / `edit`**：產出最終可發布的統一母稿紀錄（`approved_content_record`）。當前營運可選擇以中文作為主語言母稿，但系統管線設計上保留語言彈性，不限於特定語言。
- **`translate` (翻譯模組)**：讀取上游母稿 `approved_content_record`，進行 Markdown 拼接，生成來源內容指紋，呼叫 LLM 進行外語翻譯，並寫入 `translation_output` 資料表。
- **`publish` (發布模組)**：讀取已完成翻譯的多語系資料，依據資料庫中已凍結的靜態 Slug 進行檔案輸出，並導出語系資料夾：
  - `data/publish_export/zh/items/<slug>.json`
  - `data/publish_export/en/items/<slug>.json`
  - `data/publish_export/ja/items/<slug>.json`

---

## 2. 關鍵設計優化

### 2.1 唯一鍵與上游關聯優化
將 `translation_output` 綁定之上游外鍵從原始的 `source_item_id` 改為關聯至 `approved_content_record` 的 `parent_content_id`（並保留 `source_item_id` 作為關聯查詢）。將唯一鍵改為 `UNIQUE (parent_content_id, language_code)`，用以完美區分並支援同一個發布母稿下的多語系翻譯紀錄並存，避免因人工二次編輯導致的譯文狀態漂移。

### 2.2 來源指紋與過期機制 (Fingerprinting & Invalidation)
為避免上游母稿二次修改、或模型 Prompt 升級時產生過期內容，翻譯表記錄 `source_fingerprint`、`model_name` 與 `prompt_version`。
* 當執行 `translate` 時，比對最新母稿內容計算之指紋。若指紋或配置不符，將翻譯狀態標記為 `stale` 並重新排程翻譯。

### 2.3 統一 Slug 生成與永久凍結政策
使用首次英文翻譯版本的 `display_title` 進行 Slugification。這可以產生對 SEO 友善、沒有中日文字元百分比編碼（Percent-encoding）的乾淨 URL（例如 `president-announces-new-agency`），且各語系共享此相同的 Slug。
* **網址穩定契約**：為了防止 SEO 權重丟失與讀者點擊斷鏈，URL Slug 必須於**首次發布時永久凍結**並寫入 `publish_record`（即 frozen slug registry，或與母稿等價綁定的發布主表）。後續英文翻譯內容的任何變更、修飾或重寫，均**不得重算或覆寫**已凍結的 Slug。
* **權衡說明**：此策略在「嚴格覆蓋策略（Strict Match）」下運作良好，因為所有語言均需翻譯完成才發布。但若未來引進「主語言優先策略」，英文翻譯延遲會導致主語言無法生成 URL，屆時需切換回以 `source_item_id` 或主語言拼音作為 Slug 的退路設計（詳細權衡見 [MULTILINGUAL_CONTENT_STRATEGY.md](file:///C:/Users/user/Documents/derived-work/docs/MULTILINGUAL_CONTENT_STRATEGY.md) 第 6 節）。

---

## 3. 重構實作計畫 (Refactoring Steps)

### 3.1 步驟一：更新頂層文件與契約 (docs/ 調整)
1. **新增 `docs/MULTILINGUAL_CONTENT_STRATEGY.md`**：定義多語系內容的生成、指紋失效、覆蓋策略等。
2. **修改 `docs/MODULE_BOUNDARIES.md`**：
   - 明確劃分 `site` 僅擁有介面層 UI i18n。
   - 新增 `translate` 模組擁有內容翻譯責任。
3. **修改 `modules/publish/docs/DATA_CONTRACT.md` 與 `IMPLEMENTATION_PLAN.md`**：
   - 將發布的來源資料改為 `translation_output`。
   - 修改靜態輸出為多語系路徑結構（`zh/`, `en/`, `ja/`）。

### 3.2 步驟二：實作 `translate` 模組
1. 建立 `modules/translate/` 目錄結構（`src/`, `config/`, `docs/`, `tests/`）。
2. 在 `modules/translate/src/migrations/v001_initial_translate_tables.sql` 中定義 `translation_output` 表。
3. 實作 Markdown 拼接、指紋雜湊計算、LLM 翻譯呼叫與 SQLite Repository 讀寫。
4. 實作 `translate` CLI 與 Runner 控制（包含速率 staggering 與 concurrency 限制）。

### 3.3 步驟三：更新與實作 `publish` 模組
1. 修改 `publish` 的資料載入查詢，使其自 `translation_output` 中撈取 `translation_status = 'completed'` 的行。
2. 實作多語系同時輸出邏輯（一併生成各語系對應的 `items/<slug>.json`、`index.json`、`feed.xml` 與 `stats.json`）。
