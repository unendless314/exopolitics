# Known Issue: Translation Label Leakage (翻譯標籤英文殘留問題)

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
