# Translation & Publish Refactoring Design Plan

**Status:** Proposed / Under Discussion  
**Updated:** 2026-06-17  
**Author:** Antigravity  

---

## 1. 系統架構與流程設計 (System Architecture)

配合內容多語化戰略，發布流水線調整如下：

```text
                  ┌───> [curation_output] ───┐
[ingest] ──> [classify] ──> [curate]         ├──> [translate] ──> [publish] ──> [site]
                               └──> [edit] ──┘
```

- **`curate` / `edit`**：產出最終可發布的母稿。當前營運可選擇以中文作為主語言母稿，但系統管線設計上保留語言彈性，不限於特定語言；內容可存於 `curation_output` 或未來的編輯草稿表中。
- **`translate` (翻譯模組)**：讀取上游母稿，進行 Markdown 拼接，生成來源內容指紋，呼叫 LLM 進行外語翻譯，並寫入 `translation_output` 資料表。
- **`publish` (發布模組)**：讀取已完成翻譯的多語系資料，統一生成靜態 Slug，並導出語系資料夾：
  - `data/publish_export/zh/items/<slug>.json`
  - `data/publish_export/en/items/<slug>.json`
  - `data/publish_export/ja/items/<slug>.json`

---

## 2. 關鍵設計優化

### 2.1 唯一鍵 Bug 修正
將 `translation_output` 的 `source_item_id` 單一 Unique 限制移除，改為 `UNIQUE (source_item_id, language_code)`，以覆蓋多語系翻譯記錄並存。

### 2.2 來源指紋與過期機制 (Fingerprinting & Invalidation)
為避免上游母稿二次修改、或模型 Prompt 升級時產生過期內容，翻譯表記錄 `source_fingerprint`、`model_name` 與 `prompt_version`。
* 當執行 `translate` 時，比對最新母稿內容計算之指紋。若指紋或配置不符，將翻譯狀態標記為 `stale` 並重新排程翻譯。

### 2.3 統一 Slug 生成與權衡
使用英文翻譯版本的 `display_title` 進行 Slugification。這可以產生對 SEO 友善、沒有中日文字元百分比編碼（Percent-encoding）的乾淨 URL（例如 `president-announces-new-agency`），且各語系共享此相同的 Slug。
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
