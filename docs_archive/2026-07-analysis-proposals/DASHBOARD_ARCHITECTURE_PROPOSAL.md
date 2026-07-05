# UAP 聚合系統：儀表板與監控模組軟體架構提案 (DASHBOARD_ARCHITECTURE_PROPOSAL)

本文件針對系統中各模組（`ingest`, `classify`, `curate`, `translate`, `publish` 等）的狀態分析、資料診斷與監控儀表板（Dashboard）的軟體架構設計提出評估與建議。本提案旨在為後續團隊開發提供清晰的架構依循，避免程式碼結構混亂與專案根目錄膨脹。

---

## 1. 業務背景與問題

隨着 UAP/UFO 聚合系統穩定運行，團隊需要對各個功能模組進行客製化的健康檢查與指標分析（例如：Ingest 的 RSS 來源品質、Classify 的分類準確率、Curate 的人工審核過濾比率等）。

在規劃監控代碼的存放位置時，存在以下設計方案的取捨。

---

## 2. 方案評估

### 🚫 方案零：根目錄鏡像資料夾 (不推薦)
在專案根目錄建立與核心模組對稱的分析資料夾（如 `dashboard_ingest/`, `dashboard_classify/` 等）。

* **缺點**：
  * **根目錄膨脹（Folder Bloat）**：根目錄資料夾數量翻倍，干擾對專案核心邊界的理解。
  * **違反高內聚原則（Low Cohesion）**：分析邏輯與核心業務代碼被實體隔離。當核心模組的資料結構改變時，維護人員極易遺漏修改遠端的鏡像資料夾，造成代碼同步失效。

---

### 🏆 方案一：模組自理模式 (Decentralized / Localized) — 建議初期/CLI 採用
將各模組的「數據分析與健康診斷」邏輯，直接寫在各自模組的內部（例如 `modules/<module>/src/analytics.py`），並透過模組現有的 CLI 工具進行呼叫。

#### 目錄結構示意：
```text
project-root/
└── modules/
    ├── ingest/
    │   ├── src/
    │   │   ├── cli.py
    │   │   └── analytics.py  <-- 👈 Ingest 專屬的分析/統計邏輯
    │   └── tests/
    └── classify/
        ├── src/
        │   ├── cli.py
        │   └── analytics.py  <-- 👈 Classify 專屬的分析/統計邏輯
```

#### 運作機制：
* 執行 `python -m modules.ingest.src.cli analyze` 來輸出該模組的分析報告。
* 分析產出的 Markdown 檔案統一寫入專案約定的共享路徑（如 `known_issues/` 或 `reports/`）。

#### 優缺點分析：
* **優點**：
  * **職責高度內聚**：修改 Ingest 抓取邏輯的工程師，在同一個資料夾內就能看到並修改其分析邏輯，維護成本最低。
  * **專案結構乾淨**：不需要在核心模組之外新增額外的監控資料夾。
* **缺點**：如果需要一個跨模組的網頁可視化介面（如單一 Web App 儀表板），使用者需要啟動多個模組的服務，較為零散。

---

### 🏆 方案二：中央監控模組模式 (Centralized Monitor Module) — 建議後期/Web UI 採用
在 `modules/` 下建立一個**單一且獨立**的 `dashboard` 或 `monitor` 模組，統一負責所有核心模組的數據呈現、圖表繪製與網頁伺服器（Web Server）架設。

#### 目錄結構示意：
```text
project-root/
└── modules/
    ├── ingest/
    ├── classify/
    └── dashboard/            <-- 👈 只有這一個新的獨立監控模組
        ├── config/
        ├── src/
        │   ├── main.py          <-- 啟動網頁伺服器 (如 Streamlit / Flask)
        │   ├── ingest_panel.py  <-- Ingest 頁面渲染邏輯
        │   ├── classify_panel.py<-- Classify 頁面渲染邏輯
        │   └── curate_panel.py  <-- Curate 頁面渲染邏輯
        └── tests/
```

#### 運作機制：
* `dashboard` 模組不包含核心處理業務，僅作為「唯讀消費者」讀取資料庫 `data/canonical.db`。
* 它可以視需求引入網頁渲染的依賴（如 Streamlit、Dash、React 等），並在內部切換不同的分頁呈現各模組狀況。

#### 優缺點分析：
* **優點**：
  * **技術依賴隔離**：網頁可視化、UI 套件與圖表庫的依賴全部限縮在 `dashboard/` 模組內，不會污染 `ingest` 或 `classify` 等需要輕量運作的核心後台服務。
  * **統一入口**：運維人員只需啟動一個服務，就能在一個網頁中切換查看所有模組。
* **缺點**：在開發初期（還不需要網頁圖表時）建置成本較高。

---

## 3. 落地演進建議 (Evolutionary Recommendation)

為了兼顧目前的 **MVP 敏捷開發** 與 **未來的系統可擴充性**，建議採取以下二階段演進路徑：

1. **第一階段（當前階段）**：
   採用 **「方案一：模組自理模式」**。直接在各核心模組內部編寫輕量級的分析程式（如 `analytics.py`），並透過 CLI 將報告輸出至 `known_issues/` 目錄。這可以快速滿足工程師下午評估配置的需求。
2. **第二階段（當有圖表/網頁需求時）**：
   升級至 **「方案二：中央監控模組模式」**。在 `modules/` 下新增一個 `dashboard/` 模組，並在內部導入前端框架（如 Streamlit）。此網頁 Dashboard 可以直接調用第一階段各模組寫好的 `analytics.py` 來獲取結構化數據，避免重寫查詢邏輯。
