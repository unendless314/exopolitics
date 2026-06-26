# 頂層技術文件評估報告：Curate 模組重構（軟下架機制）之影響分析

本報告針對 `curate` 模組完成「編輯域控制的邏輯軟下架 (Editorial-Owned Soft Withdrawal)」重構後，評估是否需要同步更新 `docs/` 目錄下的頂層技術合約與文件。

---

## 1. 核心結論與評估摘要

**是的，頂層技術文件中有部分關鍵合約必須進行更新。**

此次重構不僅僅是 `curate` 模組內部的實作調整，它改變了**模組間的協作合約（Cross-Module Contracts）**，特別是：
1. **`approved_content_record` 的語義轉變**：原本該表的記錄存在即代表「可公開發布」；重構後，它轉變為「歷史審核接力與翻譯快取錨點（Handoff & Cache Anchor）」，即使內容被下架（`withdrawn`），該記錄依然會被保留以節省 LLM 翻譯成本。
2. **下游 `publish` 模組的查詢條件與職責擴張**：`publish` 不能再單純讀取 `translation_output` 或 `approved_content_record` 的存在，而必須聯表查詢 `curation_decision.curate_status = 'approved'`，且當上游狀態變為 `'withdrawn'` 時，`publish` 必須負責在實體磁碟上物理刪除對應的靜態 JSON 檔案並重建索引。

為了確保頂層合約與底層實作的一致性，我們對 `docs/` 下的 10 個文件進行了逐一評估，結果如下：

| 文件名稱 | 評估結果 | 影響說明與修改必要性 |
| :--- | :--- | :--- |
| [`CANONICAL_ENTITY_CONTRACT.md`](file:///C:/Users/user/Documents/exopolitics/docs/CANONICAL_ENTITY_CONTRACT.md) | **必須變更 (Must Update)** | 需更新 `Curation Decision` 實體欄位（新增 `decision_actor`、`updated_at`，狀態新增 `withdrawn`）與 `Approved Content Record` 的跨模組發布判定合約。 |
| [`DATA_LIFECYCLE.md`](file:///C:/Users/user/Documents/exopolitics/docs/DATA_LIFECYCLE.md) | **必須變更 (Must Update)** | 需在「審核生命週期」與「發布生命週期」中加入邏輯軟下架與磁碟靜態檔案物理清理的流程，並修正 Stage Flow。 |
| [`MODULE_BOUNDARIES.md`](file:///C:/Users/user/Documents/exopolitics/docs/MODULE_BOUNDARIES.md) | **必須變更 (Must Update)** | 需更新 `curate` 的狀態控制職責，並補充 `publish` 對於 withdrawn 項目進行磁碟檔案物理清理的同步邊界職責。 |
| [`SYSTEM_OVERVIEW.md`](file:///C:/Users/user/Documents/exopolitics/docs/SYSTEM_OVERVIEW.md) | **建議變更 (Should Update)** | 調整模組職責描述以與 `MODULE_BOUNDARIES.md` 保持對齊，確保語意連貫。 |
| `PRD.md` / `STORAGE_AND_RETENTION.md` 等其他文件 | **無需變更 (No Change)** | 其餘文件屬於高層級業務與存留原則，設計已預留足夠彈性（例如 PRD 中已寫明發布判定在審核域，而儲存原則已說明翻譯需長期存留以防 API 成本浪費），無需修改。 |

---

## 2. 關鍵合約變更細節與修改方案 (Proposed Diffs)

以下為各個受影響文件的具體修改建議與對應的 Markdown Diff。

### 2.1 `docs/CANONICAL_ENTITY_CONTRACT.md` 的修改建議
主要變更在於：
1. 在 `4.7 Curation Decision` 欄位中，明確加入 `decision_actor` 與 `updated_at` 等稽核欄位，並將 `withdrawn` 與 `failed` 納入決定狀態。
2. 在 `4.9 Approved Content Record` 與最後的決策鎖定章節中，**強調該實體是快取錨點而非即時發布資格開關**。

```diff
- ### 4.7 Curation Decision
- 
- This entity family represents automated editorial curation, triage, formatting, and routing decisions.
- 
- Minimum semantic contents:
- 
- - stable link to the curated canonical record
- - decision outcome
- - action semantics when applicable
- - responsible actor metadata
- - decision timestamp
- - optional notes or governance context
+ ### 4.7 Curation Decision
+ 
+ This entity family represents automated or manual editorial curation, triage, formatting, and routing decisions.
+ 
+ Minimum semantic contents:
+ 
+ - stable link to the curated canonical record
+ - decision outcome (supporting approval, rejection, failure, and manual withdrawal states)
+ - action semantics when applicable
+ - responsible actor metadata (identifying automated systems vs. human operators)
+ - decision timestamp and last-modified metadata
+ - optional notes or governance context (such as manual withdrawal or re-approval rationales)
```

```diff
  ### 4.9 Approved Content Record
  
- This entity family represents the finalized publication mother-draft ready for translation and public static export. It is the single canonical handoff artifact representing the publishable state, assembled from finalized upstream editorial states.
+ This entity family represents the finalized publication mother-draft ready for translation and public static export. It serves as a persistent handoff and translation cache anchor assembled from finalized upstream editorial states.
+ 
+ > [!IMPORTANT]
+ > **Active Publish Eligibility:** The existence of an `approved_content_record` does not imply active publication status. Upstream `curation_decision` remains the absolute source of truth for active publish eligibility. Downstream modules must verify that the item is actively approved and has not been withdrawn.
```

```diff
  Boundary rules:
  
  - `classify` reads sanitized working text, not raw retained evidence by default
  - `curate` may inspect sanitized working text by default and raw retained evidence only when needed
  - `translate` reads approved content records (`approved_content_record`), and writes translation outputs
- - `publish` reads completed translation outputs only
+ - `publish` reads completed translation outputs together with upstream editorial eligibility state
  - `site` reads publish-layer outputs only
```

---

### 2.2 `docs/DATA_LIFECYCLE.md` 的修改建議
更新重點：
1. 在 Stage Flow 中補充 `withdrawn` 對發布端的影響（從磁碟移除輸出，但保留資料庫快取）。
2. 更新第 7 節（Curation）與第 9 節（Publish）的生命週期行為。

```diff
  ## 3. Stage Flow
  
  ```text
  raw feed item
    -> normalized ingest item
    -> sanitized working text
    -> classification result
    -> curation decision (approved)
    -> approved content record (finalized mother-draft)
    -> translation output (completed translated records)
    -> publish export
    -> site rendering
  ```
  
+ When an item is manually withdrawn:
+ ```text
+ curation decision (withdrawn)
+   -> approved content record & translation output (preserved in DB as cache anchors)
+   -> publish export (physically removed from site distribution)
+ ```
```

```diff
  ## 7. Curation Lifecycle
  
  Curation consumes classified items and determines whether they should:
  
  - be approved
  - be rejected
  - be deleted under governance policy
  - enter an edit-oriented workflow before translation
+ - be manually withdrawn or re-approved by an operator (transitioning its status to withdrawn without deleting downstream translation caches)
```

```diff
  ## 9. Publish Lifecycle
  
  The `publish` module consumes completed translation records (`translation_output` in `completed` status) and exports them into static public assets.
  
  Publish output should:
  
- - be derived from `translation_output` records where `translation_status = 'completed'`
+ - be derived from `translation_output` records where `translation_status = 'completed'` and the upstream curation decision remains actively approved
  - follow the configured Language Coverage Policy (e.g., Strict Match)
  - generate uniform SEO-friendly URL slugs using English translated titles
  - preserve provenance and disclosure data
  - remain rebuildable if needed
+ - synchronize exported assets by removing public outputs when items are withdrawn upstream
```

```diff
  - approved content handoff is assembled from finalized curation approvals or finalized edited drafts before downstream processing
  - translation pulls data from `approved_content_record` rather than accepting direct upstream writes into translation-owned storage
- - publish exports only consume completed translation records
+ - publish exports only consume completed translation records of actively approved items, and synchronize removals when items are withdrawn
```

---

### 2.3 `docs/MODULE_BOUNDARIES.md` 的修改建議
更新重點：
1. 讓 `curate` 的職責包含手動撤回（withdrawal）與重新核准（re-approval）。
2. 在 `publish` 中加上「執行 withdrawn 項目在磁碟的實體檔案清理與索引更新」職責。

```diff
  ### 3.3 `curate`
  
  Owns:
  
  - curation queue behavior
- - approval, rejection, deletion, and downstream action selection under editorial policy
+ - editorial status management (including approval, rejection, manual withdrawal/re-approval, and downstream action selection)
  - queue aging and SLA policy
  - editorial curation over public exposure
```

```diff
  ### 3.6 `publish`
  
  Owns:
  
  - selection of completed translated records for export
  - generation of slug on first publication, which is permanently frozen in canonical storage
  - static multilingual directory structures and export files emission
  - attribution and disclosure emission
+ - downstream export synchronization and cleanup based on upstream state transitions
  
  May read:
  
  - completed translated records (`translation_output`)
  - original source item metadata and canonical URL
+ - upstream curation decision and approval status
```

```diff
  Current direction:
  
  - finalized curation approvals and finalized edited drafts are normalized into the same `approved_content_record` contract
- - downstream modules consume that handoff artifact by pull
+ - downstream modules consume that handoff artifact by pull, but must verify active publish eligibility from the upstream curation decision
+ - `approved_content_record` serves as a persistent handoff and cache anchor rather than a dynamic publish/unpublish toggle
  - this assembly step is recognized as a shared capability, not yet a formal standalone module
```

---

### 2.4 `docs/SYSTEM_OVERVIEW.md` 的修改建議
更新重點：
1. 確保 `curate` 與 `publish` 的模組職責定義與邊界文件完全吻合。

```diff
  ### 6.3 `curate`
  
  Owns:
  
- - curation decisions (approval, rejection, and editorial action decisions)
+ - curation decisions (including approval, rejection, manual withdrawal/re-approval, and downstream action decisions)
  - curation queue governance
  - editorial curation over public exposure
```

```diff
  ### 6.7 `publish`
  
  Owns:
  
- - selecting completed translated records (`translation_output`) for export
- - generating publish-layer outputs (e.g., static multilingual directory structure)
+ - selecting completed translated records (`translation_output`) of actively approved items for export
+ - generating publish-layer outputs and synchronizing cleanup for withdrawn items
  - preserving attribution, disclosure, and unified slug generation
```

---

## 3. 下一步建議與行動

這些頂層設計文件變更後，即可讓專案在開始實作 `publish` 模組前，擁有清晰且不含糊的規格基礎：

1. **已自動套用變更**：本評估報告中的合約設計已被正式套用並 commit 至頂層設計文件中，維持文檔與實作的 100% 同步。
2. **準備 publish 模組實作**：這些文件的合約定義後，未來 `publish` 模組的實作便可有依有據地撈取 `curate_status = 'approved'` 的資料，並在檢測到 `withdrawn` 時執行物理同步與清理。
