# Known Issue: Cross-Source Duplicates and Dead Weight Ingestion (跨來源重複收錄與無效訂閱源問題)

## 1. 問題描述 (Issue Description)
目前系統在運行過程中，面臨兩個主要的管線效益與資料品質問題：
1. **跨來源重複內容收錄 (Cross-Source Duplicates)**：同一個新聞或文章，若出現在多個不同的 RSS 訂閱源中，系統會重複收錄、重複進行 LLM 分類、重複翻譯，最終在前端網站產生大量重複的頁面。
   * *實例數據*：同一個主題的文章 `Presidential Unsealing and Reporting System for UAP Encounters (PURSUE) - U.S. D` 被重複收錄並處理了 **19 次**；部分 Google News RSS 的同一個 `canonical_url` 重複收錄了 3 次。這造成了顯著的 LLM API 費用浪費與網站內容膨脹（約有 5% 的網頁屬於重複內容）。
2. **無效高流量來源 (Dead Weight)**：部分訂閱源（如廣泛關鍵字的 Google News alerts）產生了極大的收錄量，但實際經 LLM 過濾後的關聯率與最終核准率極低，形成運算負擔。
   * *實例數據*：
     * `Google News: UAP & Non-Human` (ID: 81) — 收錄 627 篇，Relevance Rate: **3.5%**，Overall Yield: **3.3%**
     * `CBS News (Latest/Space)` (ID: 75) — 收錄 400 篇，Relevance Rate: **2.2%**，Overall Yield: **2.2%**
     * `Google News: Technosignatures` (ID: 83) — 收錄 299 篇，Relevance Rate: **1.3%**，Overall Yield: **1.3%**

---

## 2. 根本原因分析 (Root Cause Analysis)

### 2.1 跨來源去重機制的 Key 設計過窄
目前 `ingest` 模組的 `ingest_dedup_marker` 在產生去重唯一鍵時，將 `source_id` 綁定在唯一的 Key 中（例如 `guid:3:[unique_string]`）。
* 這樣做導致：**同一個網址或標題如果出現在來源 A 和來源 B 中，會因為 `source_id` 不同而產生兩個不同的 Dedup Key**，使得跨來源的去重機制失效。

### 2.2 訂閱源篩選精準度不足
Google News RSS alerts 採用了較為寬鬆的關鍵字比對。這些來源會拉取大量僅包含部分關鍵字但實際上與 UAP/UFO 無涉的太空、常規軍事或科學新聞，將過濾的重擔完全壓在下游的 LLM `classify` 模組，大幅增加了 API 呼叫的成本。

---

## 3. 長期架構優化建議 (Recommended Long-Term Solutions)

### 方案 A：引入跨來源全域去重 (Global Dedup in Ingest)
在 `ingest` 模組的去重邏輯中，引入不依賴於 `source_id` 的全域去重機制：
1. **URL 全域去重**：針對非 `None` 的 `canonical_url` 進行全域雜湊比對。若該 URL 已存在於 `source_item` 中（不論是由哪個來源抓取），則直接忽略不寫入資料庫。
2. **標題相似度去重**：針對標題進行基礎的正則化與雜湊比對，防止同一個新聞在不同網站轉貼時因 URL 參數不同而繞過去重。

### 方案 B：動態訂閱源稽核與停用 (Active Source Auditing)
利用 `analysis` 模組的指標來指導營運決策：
1. **收斂 Google News 關鍵字**：在 `sources.yaml` 中，重新設計 Google News RSS 的查詢語法，加上更嚴格的布林運算子（例如 `UAP AND (Pentagon OR Congress OR Whistleblower)` 替代寬鬆的 `UAP`）。
2. **自動停用建議 (Auto-Disable Suggestions)**：在 `analysis` 模組的 `SOURCE_QUALITY_REPORT` 中，列出 Yield 低於 5% 的來源，並保留 `[AUTHORITY]` 僅作人工覆核提示標籤。CLI 可輸出警告提示，由管理員依指標與來源戰略價值綜合判斷後，再決定是否在 `sources.yaml` 中將其設為 `enabled: false`。
