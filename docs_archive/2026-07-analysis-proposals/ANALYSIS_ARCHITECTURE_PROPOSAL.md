# UAP 聚合系統：分析模組軟體架構提案 (ANALYSIS_ARCHITECTURE_PROPOSAL)

本文件針對系統進入實際運行後的數據分析、效能診斷、來源評估與優化支援需求，提出一個獨立的分析模組架構方案。此提案的核心目標，是在不污染既有核心模組責任邊界的前提下，建立一個可持續演進的跨模組分析能力。

---

## 1. 背景與問題定義

系統已連續運行一段時間，現階段的重點不再只是確認單一模組能否執行，而是基於真實營運數據，回答以下問題：

* 哪些 RSS 來源已失效、被封鎖，或長期沒有有效貢獻。
* 哪些來源雜訊過高，正在消耗大量分類成本。
* `ingest -> classify -> curate` 的轉換漏斗卡在哪一段。
* 哪些類型的內容被 `classify` 或 `curate` 阻擋，是否符合預期。
* `translate` 是否成為下游瓶頸，哪些語言的翻譯成本、延遲或失敗率正在放大營運負擔。
* 是否存在結構性問題，例如 low-context bypass、來源重複率過高、來源健康度與內容品質相互混淆等。

這類問題雖然依賴各模組產生的資料，但其本質已不再是單一模組內部功能，而是對整條 pipeline 的診斷、評估與優化支援。

---

## 2. 為何不建議將分析 MVP 直接寫入各核心模組

一種直觀方案，是將分析程式各自寫入 `modules/ingest/`、`modules/classify/`、`modules/curate/` 等模組內部，例如透過 `analytics.py` 與 CLI 指令產出報告。

此方案在非常早期、非常輕量的單模組自檢場景中可以成立，但對目前的實際需求並不理想，原因如下：

### 2.1 分析問題已明顯跨模組

目前關心的分析主題並不只屬於單一模組，例如：

* Source health 來自 `ingest`。
* Relevance rate 依賴 `classify`。
* Yield 與 reject pattern 依賴 `curate`。
* Translation coverage、failure rate、latency 與 locale cost 依賴 `translate`。
* LLM cost factor 與 pipeline bottleneck 則同時橫跨 `ingest`、`classify`、`curate`、`translate`。

這代表分析單位是整體 pipeline，而不是個別模組的內部附屬功能。

### 2.2 容易造成責任邊界膨脹

根據 `docs/MODULE_BOUNDARIES.md`，核心模組的責任應聚焦於其業務決策本身：

* `ingest` 擁有抓取、去重、sanitize 與 source health records。
* `classify` 擁有分類結果與分類訊號。
* `curate` 擁有編輯審核與發布前判斷。

若將大量分析與報表責任直接塞回各模組，會讓模組逐漸從「執行業務」膨脹為「執行業務 + 自我監控 + 跨模組評估 + 報表生成」，降低邊界清晰度。

### 2.3 指標口徑容易分散

若每個模組各自維護自己的 `analytics.py`，未來很容易出現：

* 相同漏斗指標被重複定義。
* 不同報告採用不同的計算口徑。
* SQL 查詢散落在多個模組之中。
* 維護者難以判斷何處才是正式分析定義。

對營運優化而言，最重要的不是先有多少報表，而是先有一致且可追溯的分析口徑。

### 2.4 文檔與風險成本不低

如果將分析能力視為各模組的新職責，代表不只要修改程式碼，也需要補充多份模組技術文檔，並重新解釋各模組的擴張責任。這種改動不是不能做，但其風險與邊界影響並不比新增一個唯讀模組更低。

---

## 3. 建議方案：建立獨立 `analysis` 模組

建議在 `modules/` 下新增一個單一且獨立的 `analysis` 模組，專門負責跨模組的數據分析、營運診斷、品質評估與報告輸出。

### 3.1 核心原則

此模組應被定義為：

* **唯讀消費者（Read-only Consumer）**
* **跨模組分析層（Cross-module Analysis Layer）**
* **非核心業務決策者（Non-operational Decision Owner）**

它可以讀取 canonical database 與模組設定，計算指標並產出報告；但它不應回寫核心狀態，也不應接管既有模組的業務責任。

### 3.2 建議目錄結構

```text
project-root/
└── modules/
    ├── ingest/
    ├── classify/
    ├── curate/
    └── analysis/
        ├── docs/
        ├── config/
        ├── src/
        │   ├── cli.py
        │   ├── pipeline_report.py
        │   ├── source_health_report.py
        │   ├── source_quality_report.py
        │   └── queries.py
        └── tests/
```

這裡的重點不是檔名本身，而是 `analysis` 作為單一聚合點，集中承載分析查詢、指標定義、報告輸出與診斷邏輯。

---

## 4. 與現有模組邊界的對齊方式

為避免 `analysis` 模組侵入既有系統責任，本提案建議新增以下邊界定義。

### 4.1 `analysis` owns

* cross-module metrics definitions
* pipeline funnel analysis
* source quality scoring and ranking
* translation coverage, reliability, latency, and cost analysis
* operational diagnostics and anomaly detection
* report generation for optimization review

### 4.2 `analysis` may read

* canonical database records
* source configuration
* fetch attempts and source health records
* sanitized source item metadata
* classification results
* curation decisions
* translation outputs and translation lifecycle states
* downstream publish outcomes when needed for end-to-end measurement

### 4.3 `analysis` must not own

* source fetching
* sanitization rules
* classification decisions
* curation decisions
* translation execution
* publish execution
* source enable/disable state changes
* canonical database writes that alter production pipeline state

### 4.4 關鍵邊界原則

* `analysis` 可以提出「建議」，但不能直接替代營運或核心模組做狀態變更。
* `analysis` 可以輸出「應停用來源名單」或「疑似高成本來源名單」，但真正修改 `sources.yaml` 的責任仍屬於對應模組維護流程。
* `analysis` 的主要輸出應是報告、排行、診斷結果與結構化分析資料，而不是業務狀態本身。

---

## 5. 為何 `analysis` 比 `dashboard` 更適合作為第一階段模組名稱

若直接建立 `dashboard` 模組，容易過早將重心放在 Web UI、圖表框架與展示技術上。

但現階段真正需要先穩定下來的能力是：

* 指標定義
* SQL 與資料提取邏輯
* 分析口徑統一
* 報告輸出
* 異常診斷與排名規則

這些屬於 analysis capability，而不是 dashboard presentation。

因此，第一階段建議命名為 `analysis`。若未來確定需要圖表化或 Web UI，再考慮：

* 在 `analysis` 模組內增加視覺化層，或
* 後續再建立一個純展示用的 `dashboard` 模組，專門消費 `analysis` 的結構化輸出。

此做法能避免一開始就把展示層與分析層綁死。

---

## 6. MVP 原則：先做 Must Have，再擴展 Nice to Have

分析模組可觀測的指標非常多，若第一版試圖一次涵蓋所有報表，容易導致定義過散、開發週期拉長、產出又難以立即支持決策。

因此建議採用明確的兩層範圍：

* **Must Have**：高價值、低到中等複雜度，且能直接支持近期優化決策的指標與報告。
* **Nice to Have**：有用但不急迫、或需要更細的資料契約與更多實作成本的指標，延後納入。

為控制初期工程量，建議 `analysis` MVP 只做 CLI + 查詢 + 結構化指標 + Markdown 報告，不做 Web UI，也不引入重型可視化依賴。

---

## 7. Must Have：第一階段建議能力範圍

### 6.1 Source Health Analysis

針對來源抓取健康度與連線異常進行彙整：

* 失敗來源排行
* 404 / 403 / DNS / SSL 類型分群
* 長期無成功抓取來源清單
* 需隔離為連線修復問題的來源

### 7.2 Pipeline Funnel Analysis

針對 `ingest -> classify -> curate` 的轉換漏斗進行分析：

* 各階段數量與轉換率
* low-context bypass 比率
* classify unknown / irrelevant 分布
* curate approved / rejected 分布

### 7.3 Source Quality and Cost-Yield Analysis

針對來源內容價值、過濾成本與最終產出進行評估：

* relevance rate
* unique contribution rate
* LLM cost factor
* overall yield
* 權威來源保護與例外標記
* 四象限分類與優先處置建議

### 7.4 Translation Coverage, Reliability, and Cost Analysis

針對 `translate` 模組納入最具營運價值、且相對容易定義的基礎指標：

* 各語言翻譯成功率
* 各語言失敗率與重試率
* 各語言平均翻譯延遲
* stale rate
* 各語言成本占比或代理成本指標
* approved content 到 translation completed 的完成率

此處的目標不是在 MVP 階段就精準完成所有翻譯品質評分，而是先回答三個營運核心問題：

* 哪些語言最花錢
* 哪些語言最不穩
* `translate` 是否正在拖慢下游發布節奏

---

## 8. Nice to Have：後續可擴展能力

以下能力具備實際價值，但不建議放入 MVP 首波範圍：

### 8.1 Advanced Translation Quality Proxies

* 語言別輸出長度異常檢測
* 重複重跑率分析
* 特定語言或特定內容類型的品質代理分數
* stale 後再翻譯的成本放大量化

### 8.2 End-to-End Publish Readiness Analysis

* approved content 到最終 publish 完成的全鏈路完成率
* 各語言前台覆蓋完整度
* 各階段 backlog aging
* 哪些內容長期卡在 translate 或 publish

### 8.3 Segmented Cost Analysis

* 依來源、主題類別、語言、日期區間切分的成本分析
* 特定來源進入多語翻譯後的實際 ROI
* 上游內容變更導致的 retranslation cost amplification

### 8.4 Automated Alerting and Visualization

* 閾值告警
* 排程化報表
* Web dashboard
* 趨勢圖與比較圖

---

## 9. 與核心模組的協作方式

本提案不否定各核心模組未來保有少量「模組內自診斷工具」的可能性，但建議將其定位為例外，而不是主架構方向。

較合理的分工方式是：

* 核心模組負責產生與維護自己的 canonical records。
* `analysis` 統一讀取這些 records，形成跨模組分析口徑。
* 若某個模組確實需要本地自檢工具，應限制在該模組 own 的狹義診斷場景，例如 fetch retry debug 或單一 prompt outcome 檢查，而不應承擔全系統優化分析主責。

這樣可以兼顧局部除錯效率與整體架構整潔。

---

## 10. 演進建議

建議採以下演進順序：

1. **第一階段（現在）**  
   建立獨立 `modules/analysis/` 模組，僅提供 CLI、SQL 查詢、結構化指標與 Markdown 報告輸出，並以 Must Have 指標為範圍上限。

2. **第二階段（指標穩定後）**  
   將報告輸出擴展為機器可讀格式（如 JSON），並逐步納入 Nice to Have 指標，供後續自動化或視覺化消費。

3. **第三階段（確定有長期操作需求時）**  
   再決定是否在 `analysis` 之上增加 Web UI，或另建純展示型 `dashboard` 模組。

此路徑的好處是，先把最重要的分析口徑與資料契約固定，再決定要不要投入更多 UI 工程。

---

## 11. 結論

對於已上線並開始依賴真實數據進行優化的系統而言，「分析能力」不應被視為各核心模組的零散附屬功能，而應被視為一個獨立、唯讀、跨模組的能力層。

因此，本提案建議：

* **不以「模組內嵌 analytics.py」作為主要架構方向。**
* **在 `modules/` 下建立單一獨立的 `analysis` 模組。**
* **第一階段聚焦 Must Have 指標、CLI、SQL、指標定義與報告輸出，不急於導入 dashboard UI。**

此方案雖然在初期比模組內嵌做法多出一些 scaffold 成本，但能更好地維持核心模組邊界、集中分析口徑，並為後續的優化、監控與可視化演進打下較穩固的結構基礎。
