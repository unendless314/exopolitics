# Proposal: Translation Module & Decoupled Multilingual Flow

**Status:** Proposed / Under Discussion  
**Updated:** 2026-06-17  
**Author:** Antigravity & User  

---

## 1. 背景與動機

本系統的靜態網頁（`site` 模組）預計在初期支援**中、英、日**三種語系。為了提供絕佳的讀者體驗與控制營運成本，多語系翻譯必須在後端批次處理完成，再導出為靜態 JSON，避免讓讀者瀏覽器直接呼叫 LLM 進行翻譯。

然而，現有的審核模組（`curate`）之 [Prompt 模板](file:///C:/Users/user/Documents/exopolitics/modules/curate/config/prompt_templates.yaml) 已經極為複雜。如果強行在 `curate` 的 Prompt 中加入多語系翻譯要求，會導致以下問題：
1. **AI 指令稀釋與格式錯誤**：過多限制會降低 AI 遵循字數與邏輯規範的成功率。
2. **Token 浪費**：我們會花費額外的 API 費用去翻譯那些「最終被拒絕」或「需要重寫」的文章。
3. **結構耦合（Schema Coupling）**：若翻譯與發布直接使用 `curate` 的多欄位結構（`summary_short`、`bullet_1` ~ `bullet_3`），一旦未來審核格式調整，整個翻譯和發布模組都需要跟著重構。

---

## 2. 核心提案：新增獨立的 `translate` 模組

我們建議在 `curate` 與 `publish` 之間，引入一個輕量級的 `translate` 模組，並進行**內文合併解耦**。

### 2.1 處理流程 (Pipeline)
```text
[curate] ──> (拼接為 Markdown) ──> [translate] ──> [publish] ──> [site]
```

1. **`curate` 模組**：依舊產出結構化的中文資料（`summary_short`, `bullet_1`, `bullet_2`, `bullet_3`），這有助於引導 AI 進行客觀思考。
2. **拼接階段**：將中文摘要與重點拼接成單一 Markdown 字串：
   ```markdown
   {summary_short}
   
   * **核心宣稱**：{bullet_1}
   * **證據層次**：{bullet_2}
   * **客觀影響**：{bullet_3}
   ```
3. **`translate` 模組**：讀取審核通過（`approved`）的內容，調用一個專一的翻譯 Prompt，將標題與拼接好的 Markdown 內文翻譯為英文與日文。
4. **`publish` 模組**：讀取翻譯好的多語系資料，導出為語系資料夾（如 `zh/`, `en/`, `ja/`）。

---

## 3. 資料庫設計與解耦合約

`translate` 模組將擁有 `translation_output` 資料表。此表與 `publish` 模組直接對接，實現與審核內部欄位的徹底解耦。

### 3.1 `translation_output` 表結構

| 欄位名稱 | SQLite 類型 | 空值限制 | 說明 |
| :--- | :--- | :--- | :--- |
| `translation_output_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | 流水號主鍵 |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | 外鍵，關聯至 `source_item` |
| `language_code` | `TEXT` | `NOT NULL` | 語系代碼（如 `'zh'`, `'en'`, `'ja'`） |
| `display_title` | `TEXT` | `NOT NULL` | 該語系去誇張化的文章標題 |
| `content` | `TEXT` | `NOT NULL` | 該語系的 Markdown 內文（包含摘要與列表） |
| `translated_at` | `TEXT` | `NOT NULL` | 翻譯完成的 UTC 時間戳記 |
| `created_at` | `TEXT` | `NOT NULL` | 記錄建立時間 |

### 3.2 統一語系發布合約
當一篇文章審核通過：
1. 將原始中文版內容拼接後，作為 `language_code = 'zh'` 直接寫入 `translation_output`。
2. 調用 LLM 翻譯，將英文與日文版分別寫入 `translation_output`（`language_code = 'en'` 與 `'ja'`）。
3. `publish` 模組只需讀取 `translation_output`，並依據 `language_code` 將檔案分組寫入對應的靜態資料夾（例如 `data/publish_export/en/items/<slug>.json`），**完全不需要知道內部翻譯的細節**。

---

## 4. 方案優勢

1. **品質更佳**：翻譯 Prompt 極為單純（"請將此 Markdown 格式的文章翻譯為英文..."），LLM 可以參考上下文，名詞翻譯更精準，格式不易出錯。
2. **完全解耦**：如果未來 `curate` 的 Prompt 結構改變（例如改為 2 段文字），只需調整拼接函數，`translate` 與 `publish` 模組完全不用修改程式碼。
3. **節省成本**：僅對審核通過的項目進行翻譯，且一次性完成翻譯並快取，避免重複呼叫 API。
4. **擴充性高**：未來若想新增法文、西班牙文等語系，只需在翻譯模組的語系名單中加入，靜態發布端與審核端完全不受影響。
