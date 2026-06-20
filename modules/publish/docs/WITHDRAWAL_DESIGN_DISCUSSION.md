# Withdrawal and Teardown Design Discussion (下架與撤回機制設計討論)

**文件版本:** v1.0  
**更新日期:** 2026-06-21  
**狀態:** 草案 / 方案討論

---

## 1. 背景與問題陳述

在典型的內容處理管道中：
`ingest (抓取) -> classify (分類) -> curate (審核) -> edit (編輯) -> translate (翻譯) -> publish (發布) -> site (呈現)`

當一篇文章已經通過審核、翻譯並發布至前端網站後，實務上常會因為以下原因需要**下架/撤回**：
1. **版權爭議**：來源網站要求刪除內容。
2. **資訊有誤**：內容後續被證實為假消息或不準確。
3. **敏感政策**：特定內容不適合繼續公開展示。

由於系統各模組職責分離，我們需要評估：當需要下架一篇文章時，如何設計其狀態流轉、資料庫紀錄的存留以及靜態檔案的清理，以達到最合理的系統架構？

---

## 2. 方案評估

目前討論出兩種可行的系統設計方案：

### 方案 A：物理級聯刪除 (Physical Cascade Delete / Hard Delete)

* **邏輯**：
  1. 管理員於後台決定下架某篇文章。
  2. `curate` 或 `edit` 模組將該文章的審核輸出（`curation_output`）及接力表紀錄（`approved_content_record`）實體刪除（Delete）。
  3. 利用 SQLite 資料庫的 `ON DELETE CASCADE` 外鍵級聯約束，自動刪除對應的翻譯輸出紀錄（`translation_output`）。
  4. `publish` 模組在下一次運行（Run）時，發現已發布的文章在資料庫中已無翻譯資料，遂從 `data/publish_export/` 目錄中刪除對應的靜態 JSON 檔案，並重新編譯索引檔（`index.json` / `feed.xml`）。
* **優點**：
  * 資料庫乾淨，不會存留未發布的冗餘翻譯內容。
  * 狀態管理單純，有 `approved_content_record` 就代表要發布，沒有就是不發布。
* **缺點**：
  * **API 成本浪費**：如果該文章後續經確認可以重新上架，系統必須重新呼叫 LLM 進行摘要與各語言翻譯，耗費二次 API Token 費用。
  * **無稽核軌跡**：一旦刪除，系統無法追溯該內容過去是否曾被抓取、審核或翻譯，缺乏營運審計日誌（Audit Log）。

---

### 方案 B：邏輯軟下架 / 發布開關 (Logical Soft Toggle / Soft Delete) — 【推薦方案】

* **邏輯**：
  1. 管理員於後台點擊下架時，系統不刪除任何資料庫紀錄，僅將該文章在發布相關表（例如 `publish_record` 或 `curation_decision`）中的發布狀態改為 `withdrawn`（撤回）或將發布開關設為 `is_active = false`。
  2. 資料庫中的母稿資料（`curation_output`）、接力紀錄（`approved_content_record`）以及已翻譯好的內容（`translation_output`）**全部保留**。
  3. `publish` 模組在執行發布時，其查詢條件僅撈取「已完成翻譯」且「發布開關為啟用（Active）」的項目。
  4. 針對狀態變為 `withdrawn` 的項目，`publish` 負責**將其對應的靜態 JSON 檔案從磁碟刪除**，並更新索引（`index.json`）。
* **優點**：
  * **保護 LLM 投資成本**：已翻譯好的內容被完整保留在資料庫中。若需重新上架，僅需將狀態切換回啟用，即可瞬間重新導出，**零 LLM API 呼叫成本**。
  * **完整的資料鏈條**：保留了內容從 ingest 到 translate 的完整生命週期數據，具備優秀的數據審計與稽核能力。
  * **職責清晰**：`curate` / `edit` 負責決定內容的生產，`publish` 負責控制最終面向大眾的曝光開關。
* **缺點**：
  * 資料庫佔用空間隨時間會比方案 A 稍微增加（但在 SQLite 中，純文字保留的體積影響極小）。
  * 查詢時需要多判斷一個狀態欄位。

---

## 3. 方案對比表

| 比較維度 | 方案 A：物理級聯刪除 (Hard Delete) | 方案 B：邏輯軟下架 (Soft Toggle) |
| :--- | :--- | :--- |
| **重新上架 Token 成本** | **高**（需重新呼叫 LLM 翻譯與摘要） | **零成本**（直接重寫靜態檔） |
| **資料庫儲存體積** | 較小（自動清理） | 稍大（保留歷史翻譯） |
| **歷史稽核軌跡** | 差（刪除後無跡可尋） | **極佳**（完整保留生命週期狀態） |
| **磁碟靜態檔案清理** | 需由 `publish` 偵測資料庫缺失並清理 | 需由 `publish` 偵測開關狀態並清理 |
| **系統彈性與營運便利性** | 較差 | **極佳**（支援暫時下架/重新審查） |

---

## 4. 收斂方案：編輯域控制的邏輯軟下架 (Editorial-Owned Soft Withdrawal)

經過深入對比與評估，我們最終收斂至**「方案 B 的變體（由編輯域擁有決策狀態，但不刪除快取）」**。

### 4.1 核心運作邏輯
1. **決策階段（Editorial Decision）**：管理員下架文章時，僅將最上游的 `curation_decision.curate_status` 更新為 `'withdrawn'`，**不刪除** `curation_output`。
2. **接力階段（Handoff Assembly）**：
   * `assemble_approved_content_records` 僅掃描 `curate_status = 'approved'` 的文章進行更新。
   * 對於狀態變為 `'withdrawn'` 的文章，接力表 `approved_content_record` 與翻譯表 `translation_output` 中的既有紀錄**完全保留**，不進行物理刪除（避免快取失效）。
   * > [!IMPORTANT]
     > **語義明確定義**：在本方案中，`approved_content_record` 被定義為**「歷史上曾通過上游審核後形成的持久化接力與快取錨點（Handoff & Cache Anchor）」**，它**不等同於**當前仍可公開的 Active Publish Set。目前內容是否可公開，必須以 `curation_decision.curate_status` 的即時狀態為唯一事實來源，而非依據 `approved_content_record` 中該列的存在與否來判斷。
3. **發布階段（Publish Exporter）**：
   * `publish` 模組在執行時，透過資料庫聯表查詢來判斷狀態。僅導出同時符合「翻譯完成 (`translation_status = 'completed'`)」且「最上游審核狀態為啟用 (`curate_status = 'approved'`)」的文章。
   * > [!NOTE]
     > **查詢路徑說明**：在此階段，發布模組需要能夠比對最上游的審核狀態與翻譯狀態。具體實作上，可透過 `translation_output JOIN approved_content_record JOIN curation_decision` 來查詢，亦可隨未來資料庫結構優化（如 `translation_output` 直接關聯 `source_item_id`）採取更直接的關聯查詢。此處不限定唯一固定的 SQL JOIN 寫法，以能正確抓取實時上游審核狀態為準。
   * 若發現某篇文章已發布在磁碟，但在資料庫中的 `curate_status` 已變為 `'withdrawn'`，則 `publish` 負責**物理刪除**該文章在 `data/publish_export/` 下的靜態 JSON 檔案，並重新編譯索引。

### 4.2 系統執行流程圖

```text
[ Curation 後台 / 營運工具 ] 
      │ 
      ▼ (管理員點擊下架)
[ 更新 curation_decision.curate_status = 'withdrawn' ]
      │ 
      ├─> (保留 approved_content_record 與 translation_output，保護翻譯快取)
      │
[ publish run ] (排程或手動執行)
      │
      ├─> 查詢條件：translation_output JOIN approved_content_record JOIN curation_decision
      │             WHERE translation_status = 'completed' AND curate_status = 'approved'
      │
      ├─> 1. 符合條件項目：生成或更新靜態 <slug>.json 檔
      ├─> 2. 磁碟已存在但不再符合條件項目（如已 withdrawn）：
      │      └─> 物理刪除：data/publish_export/<lang>/items/<slug>.json
      └─> 3. 重新生成該語言的 index.json 與 feed.xml（排除下架文章）
```

---

## 5. 補充評註：重建性、狀態持久化與建議收斂方向

在重新評估後，本節的立場調整如下：若以**重構成本較低**與**長期維護穩定性較高**為優先判準，較推薦採用「撤回屬於 editorial domain」的責任切法，但保留方案 B 的 soft withdrawal 與資料保留精神。

### 5.1 為何不建議把撤回決策完全下放到 `publish`

若將上線後的公開開關、下架與重新上架完全定義為 `publish` domain 的核心責任，則 `publish_record` 或相關發布狀態表將承載不可遺失的人類業務決策。這在「可重建」前提下有明顯風險：

1. 若未來因 schema 調整而重建 `publish` 模組資料表，曾經被人工下架的項目可能因上游仍為 `approved` 而重新出現。
2. 這會使 `publish` 從可重建的下游輸出層，變成必須永久保留核心決策真相的狀態層。
3. 此設計也與目前 `docs/MODULE_BOUNDARIES.md` 對 `publish` 的 downstream-only 定位較不一致。

因此，若專案要持續保有「可清空下游輸出，再依 canonical state 完整重建」的能力，撤回決策更適合保存在上游 editorial domain。

### 5.2 較穩定的責任切法

較推薦的收斂方式如下：

1. `curate` / `edit` 擁有 human editorial decision。
2. `withdrawn` 應是上游 canonical state，而不是 `publish` 才知道的狀態。
3. `publish` 只根據上游是否允許公開，決定是否生成或移除靜態輸出。

此切法的直接好處是：

1. 重建 `publish` 輸出時，不會讓人工撤回決策遺失。
2. `publish` 仍維持純 downstream consumer 的簡潔定位。
3. 人類決策真相集中在較上游的 canonical state，較符合長期維護與審計需要。

### 5.3 但不建議用硬刪除表達撤回

即使撤回決策回到 editorial domain，也不建議採方案 A 的 hard delete。原因如下：

1. 會破壞稽核軌跡。
2. 會讓重新上架失去既有翻譯成果，增加 LLM API 成本。
3. 會削弱系統的 rebuildability，因為部分歷史加工結果被主動銷毀。

因此，推薦的是：

1. 用狀態表達撤回。
2. 不用刪除表達撤回。
3. 不因撤回而刪除 `translation_output`。

### 5.4 對具體資料設計的建議

若要同時滿足「撤回由 editorial domain 擁有」與「保留翻譯快取」兩個目標，建議往以下方向收斂：

1. 在 `curation_decision` 中擴充 `curate_status`，支援 `withdrawn`。
2. 人工下架時，更新的是上游 editorial 狀態，而不是刪除 `approved_content_record`。
3. `approved_content_record` 不宜再新增一個單純由 `curate_status` 派生出的 `publish_allowed` 或 `is_active`，避免同一語義分散成兩份狀態來源。
4. `translation_output` 不應因 `approved_content_record` 的失效或移除而被級聯硬刪除。
   *(註：目前 [v001_initial_translate_tables.sql](file:///C:/Users/user/Documents/derived-work/modules/translate/src/migrations/v001_initial_translate_tables.sql) 對 `translation_output` 設有指向 `approved_content_record` 的 `ON DELETE CASCADE` 外鍵約束。只要我們維持不刪除 `approved_content_record` 紀錄列的設計，此級聯刪除就不會被觸發，因此現有機制是安全的。若未來調整了刪除邏輯，須同步注意此 FK 約束。)*

換句話說，若 `approved_content_record` 是下游 handoff artifact，就不應再把「撤回」主要表達為刪掉 handoff row；否則很容易把 translation cache 的生命週期一起綁進去。

### 5.5 建議收斂結論

綜合評估後，建議的方向是：

1. 採用「切法一：撤回屬於 editorial domain」。
2. 保留方案 B 的 soft withdrawal 精神。
3. 不採 hard delete。
4. 不因撤回而刪除翻譯成果。
5. 讓 `publish` 專注於依上游 canonical state 同步公開輸出，而不是擁有不可丟失的撤回決策本身。

一句話總結：

> 撤回決策應回到 `curate` / editorial domain，但實作上必須採 soft withdrawal，且不得因撤回而刪除翻譯成果。
