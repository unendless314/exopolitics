# Proposal: Index Archiving & Pagination Reform

**Document Version:** v1.2 (Accepted & Reconciled)  
**Date:** 2026-06-25  
**Status:** Integrated (Superseded by DATA_CONTRACT.md and EXECUTION_POLICY.md updates)  
**Target Module:** `publish`

> [!NOTE]
> This proposal has been accepted and fully integrated into the official DATA_CONTRACT.md and EXECUTION_POLICY.md files. The references below have been reconciled to match the final contract specifications.

---

## 1. Background (背景與動機)

在目前的 `publish` 設計中，系統採用基於數量限制的索引分頁機制（透過 `index_pagination_threshold` 限制單一 `index.json` 筆數，如 50,000 筆）。

然而，對於一個**高頻率更新的快訊門戶網站**（每日可能新增數百筆資料，長期累積達十萬筆以上），此設計存在以下效能與維護上的挑戰：

1. **級聯寫入瓶頸（Cascading Write / 骨牌效應）**：
   若採用固定筆數分頁（如每 1,000 筆一頁），當新增第 1,001 篇文章時，所有分頁的邊界都會發生偏移（第一頁最舊的文章被擠入第二頁開頭，第二頁最舊的文章被擠入第三頁，依此類推）。這會導致每次發布執行時，系統都必須重新重寫幾乎所有的分頁 JSON 檔案。
2. **瀏覽器載入與快取效率低下**：
   由於分頁檔案的內容隨著新文章的不斷湧入而頻繁變動，瀏覽器端無法對歷史分頁進行強效能的靜態快取（Static Cache / CDN Cache），導致重複造訪的讀者耗費不必要的頻寬與等待時間。

因此，本案提案將發布層的索引策略改為 **「最新快訊 (Latest N) + 歷史月份歸檔 (Monthly Archive) 雙軌制」**。

---

## 2. Proposed Architecture (雙軌制架構設計)

此機制將索引輸出拆分為兩種性質不同的檔案類型：

```text
發布資料庫 (canonical.db)
     │
     ├─► [發布最新 N 筆] ──► 寫入最新索引 (index.json)  [動態更新 / 快速載入]
     │
     └─► [發布歷史封存] ──► 寫入月份歸檔 (archive_YYYY_MM.json) [安定追加 / 長效快取]
```

### 2.1 最新快訊索引 (`index.json`)
* **路徑**：`data/publish_export/<language_code>/index.json`
* **內容**：不論時間，永遠僅包含該語言下最新的 $N$ 筆文章（例如：預設 $N = 1000$）。
* **行為**：每次 `publish` 執行同步時，皆會重新計算並覆寫此檔案。
* **用途**：提供網站首頁、最新快訊列表、搜尋預載等高頻存取頁面使用，檔案大小固定且輕量。

### 2.2 歷史月份歸檔 (`archive_YYYY_MM.json`)
* **路徑**：`data/publish_export/<language_code>/archives/archive_YYYY_MM.json`
* **內容**：包含該月份（依據文章的上游原始發布時間 `source_item.published_at` 歸類，此排序鍵為 `source_published_at`）所發布的所有文章。
* **行為**：
  * **當月歸檔**：在當月期間，該月份檔案會隨著每次同步增量更新。
  * **歷史歸檔**：一旦跨入下一個月份，前一個月的歸檔檔案即**安定追加（Append-Stable）**。為滿足合規撤回或更正需求，發布 Runner 在遇到下架撤回或修正時，仍有權修正並重寫該歷史月份檔案，其餘常態增量運行中不進行任何讀寫。
* **用途**：提供網站的「歷史存檔列表」、「月份篩選器」等冷數據存取頁面使用。

### 2.3 Projection & Write Execution Model (投影與寫入執行模型)

為確保系統的簡潔與強健性，避免複雜的「資料回填或搬移」邏輯，系統遵循以下實作原則：

1. **唯一真理源投影 (Database-driven Projection)**：
   所有發布的靜態檔案（不論是 `index.json` 還是 `archive_YYYY_MM.json`）均為資料庫中最新狀態的直接「投影」。系統**不進行**「當文章從最新列表溢出時，才搬移至月份歸檔」的資料搬移邏輯，而是直接從資料庫撈取並分流寫入。這保證了發布狀態的絕對一致，並支援隨時清空檔案後無損重建（Rebuild）。
2. **活動月份增量寫入 (Active-only Incremental Updates)**：
   為避免日常發布時重寫所有歷史歸檔，Runner 採用增量寫入策略：
   * **最新索引 (`index.json`)**：每次發布同步時皆重新生成（固定最新 $N$ 筆）。
   * **月份歸檔**：每次發布同步時，僅重新計算並覆寫「**本次運行中包含新增、修改或下架快訊的月份**」之歸檔檔案（多數情況下僅為當前月份）。未受影響的歷史月份歸檔檔案保持原樣，不進行任何磁碟 I/O。

---

## 3. Directory Layout (發布目錄結構變更)

以中文 (`zh`) 與英文 (`en`) 語系為例，實施雙軌制歸檔後的目錄結構如下：

```text
data/
└── publish_export/
    ├── stats.json                   # 全站跨語系統計數據
    │
    ├── en/
    │   ├── index.json               # 英文版最新消息索引（僅含最新 N 筆）
    │   ├── archives/                # 📂 英文歷史月份歸檔目錄
     │   │   ├── archive_2026_05.json # 2026 年 5 月歸檔 (安定追加，必要時可因撤回而修正)
     │   │   └── archive_2026_06.json # 2026 年 6 月歸檔 (增量寫入中)
    │   └── items/                   # 📂 英文文章詳情 JSON
    │       ├── slug-a.json
    │       └── slug-b.json
    │
    └── zh/
        ├── index.json               # 中文版最新消息索引（僅含最新 N 筆）
        ├── archives/                # 📂 中文歷史月份歸檔目錄
         │   ├── archive_2026_05.json # 2026 年 5 月歸檔 (安定追加，必要時可因撤回而修正)
        │   └── archive_2026_06.json # 2026 年 6 月歸檔 (增量寫入中)
        └── items/                   # 📂 中文文章詳情 JSON
            ├── slug-a.json
            └── slug-b.json
```

---

## 4. Technical Advantages (架構優勢分析)

| 評估維度 | 原設計 (數量分頁) | 新提案 (年月雙軌制) | 提案優勢說明 |
| :--- | :--- | :--- | :--- |
| **I/O 寫入負載** | 高（每次新增文章，所有分頁均需重寫） | **極低**（僅重寫 `index.json` 與當月歸檔檔） | 避免了磁碟寫入的骨牌效應，對高頻快訊寫入非常友善。 |
 | **快取效率 (CDN)** | 低（分頁內容會隨時間往後擠壓而失效） | **極高**（過往月份檔案在常態增量下維持穩定） | 歷史月份歸檔可設置長期瀏覽器與 CDN 快取，降低伺服器流量。 |
| **前端載入速度** | 中（分頁門檻過大時檔案過重） | **極快**（首頁索引檔案大小永久維持在上限 $N$ 內） | 首頁載入時間具備可預測性，不會隨文章總量增加而退化。 |
| **系統重建成本** | 中 | **低**（重建歷史月份時可並行寫入，無需計算跨頁邊界） | 當執行 `rebuild` 時，各月份檔案可獨立生成，提升資料回復效率。 |

---

## 5. Configuration & Contract Impact (配置與合約異動)

本提案屬於**純粹的發布投影層優化**，上游數據庫結構（`publish_record`, `publish_language_status`）**無需進行任何 Schema 修改**。

### 5.1 配置檔 `publish_settings.yaml` 調整
我們將原有的 `index_pagination_threshold` 替換為 `index_policy` 配置項：

```yaml
# 發布索引歸檔策略配置
index_policy:
  # 最新快訊索引 (index.json) 的最大筆數限制
  latest_limit: 1000
  
  # 歷史封存歸檔的切分維度 (目前僅支援 'month')
  archive_granularity: "month"
```

### 5.2 執行政策與撤回處理 (Withdrawal)
* **下架/撤回同步**：若上游撤回了某篇已發布的文章：
  1. 系統將該文章從 `index.json` 中移除。
  2. 系統定位該文章 `source_published_at`（`source_item.published_at`）所屬的月份歸檔檔（例如：`archive_2026_05.json`），將該文章從中移除並重寫該月份檔案。
  * *註：雖然歷史月份一般處於安定追加狀態，但遇到「下架/撤回」事件時，發布 Runner 仍擁有依據資料庫最新狀態修正該月份歸檔的寫入權限，以確保公開內容的合規性。若該月份檔案被清空，則檔案應被刪除且從 manifest 移除。*
