# Content Lifecycle：上層內容生命週期

**文件版本：** v0.2 草稿  
**更新日期：** 2026-05-27  
**狀態：** 待審核

---

## 1. 文檔定位

本文件描述的是 **跨模塊的內容生命週期**。  
它定義內容如何在 `ingest -> classify -> review -> edit -> publish -> site` 之間流動。

其中 `edit` 不應被理解為固定排在 `review` 前或後的線性階段。  
較準確的定位是：由 `review` 決定是否進入 edit 分支，edit 完成後再回到 `review` 收口。

這不是模塊內部設計文件，因此不展開各模塊的程式實作。

---

## 2. 生命週期原則

- 內容先保存，再決定是否展示
- LLM 只做初篩，不做最終裁決
- 人工審核是正式流程的一部分
- 發布層只承載已核准內容
- 公開網站永遠落後於 canonical database 一個處理階段
- 來源條目與 edit 內容屬於不同內容類型
- 對外內容必須可追溯來源、AI 參與程度與人工責任
- `classified` 可短期停留，但必須受審核時限治理，不可無限期積壓
- 當人工審核量不足時，可由 agent 先做 queue triage；最終責任仍需可追溯到人類決策

---

## 3. 條目流向

```text
source feed
  -> ingest
  -> canonical database
  -> classify
  -> review
  -> edit branch (when needed)
  -> review
  -> publish
  -> site
```

補充：

- 來源層的 `category_id` 只表示初始主題歸檔
- 來源層的 `fetch_group` 只負責並行抓取切片
- 來源層的 `schedule_class` 只負責抓取頻率
- 條目層最終是否屬於 `core / adjacent / irrelevant`，由後續分類與審核流程決定
- 部分條目在 `classify`、`review` 或 `edit` 階段，可能因 feed 資訊不足而需要額外的 page-level retrieval；這屬於按需 enrichment，而非固定主流程階段

### 3.1 聚合流

```text
source feed
  -> source_item
  -> classify
  -> review
  -> publish
  -> site
```

### 3.2 編輯流

```text
source_item(s)
  -> review
  -> edit_candidate
  -> edit_draft
  -> review
  -> publish
  -> site
```

補充：

- `review` 先決定某條內容是否值得進入 edit 流
- `edit_draft` 可由 LLM 起稿，也可由人工直接建立
- `edit` 的內容契約與邏輯規則歸屬 `edit`；早期可由 `review` 承接執行入口
- edit 完成後仍需回到 `review` 做最終人工確認
- edit 流的輸出不是來源全文的鏡像，而是站內自有內容單位
- 在需求尚未穩定前，edit flow 可先作為 `review` 的延伸，而非獨立可執行模塊

---

## 4. 主題分類

### 4.1 `core`

- 直接與 UAP / UFO / disclosure / sightings / investigations 相關
- 預期是首頁與主列表的核心來源

### 4.2 `adjacent`

- 不完全是主題核心
- 但可能與防務、航太、SETI、雷達、政策、影像分析或社會討論脈絡有關
- 需要更多人工判斷

### 4.3 `irrelevant`

- 目前判定與站點主題無顯著關聯
- 不對外展示
- 仍可短期保留供人工複核，或經人工確認後刪除

---

## 5. 狀態模型

- `ingested`
- `classified`
- `draft`
- `approved`
- `published`
- `rejected`
- `deleted`
- `edit_candidate`（可選）
- `edit_draft`

---

## 6. 模塊與狀態責任

### 6.1 `ingest`

輸入：

- RSS source config

輸出：

- 原始條目
- `ingested`
- 來源抓取元資料更新

### 6.2 `classify`

輸入：

- `ingested` 條目

輸出：

- `topic_class`
- `classification_reason`
- `classification_confidence`
- `edit_candidate`（可選）
- `classified` 或 `draft`

補充：

- 進入 `classified` 的條目應被視為待處理隊列，而非長期封存狀態

### 6.3 `review`

輸入：

- `draft` / `rejected` / `approved` 條目

輸出：

- `approved`
- `rejected`
- `deleted`
- `edit_candidate`
- edit 責任確認

補充：

- `review` 是 edit 分支的入口與收口
- `review` 可決定條目直接進入聚合發布，或轉入 edit flow
- `review` 應擁有 `classified` 隊列的 SLA 與逾時處理策略
- 若由 agent 執行 triage，應回寫可審計欄位（actor、reason、confidence、timestamp）
- `review` 在必要時也可使用共享的 external content retrieval 能力輔助判斷，但不應把該能力視為 review 私有邏輯

### 6.4 `publish`

輸入：

- `approved`

輸出：

- 發布層資料
- `published`

### 6.5 `site`

輸入：

- 已發布資料

輸出：

- 公開靜態頁面

---

## 7. 發布策略

前台可以至少分兩層：

- `Core Stream`
- `Adjacent Signals`

這樣可以保住主題辨識，也不犧牲邊緣內容的價值。

若未來引入站內改寫或整理稿，前台至少還應區分：

- `Aggregated Item`
- `Edit Piece`

---

## 8. 刪除策略

- `rejected` 不等於立刻刪除
- `deleted` 必須由人工最終確認
- 物理刪除不是分類流程的一部分，而是治理決策的一部分
- `deleted` 的執行應滿足最小 retention window 與 audit log 要求；具體參數在 `review/docs/` 定義

## 9. 追溯與揭露策略

- 每個公開內容單位都應能追溯到對應來源條目
- 每個公開內容單位都應標記 AI 參與程度
- 每個公開內容單位都應標記是否完成人工審核
- edit 內容應保留最終責任主體
- 對外展示的揭露資訊應由 `publish` 輸出，而不是由 `site` 臨時推斷

## 10. 與模塊文檔的關係

未來各模塊自己的 `docs/` 應基於本文件展開：

- `ingest/docs/`：抓取與 schema 細節
- `classify/docs/`：LLM prompt、批次策略、回寫契約
- `review/docs/`：審核規則與操作方式
- `edit/docs/`：站內 edit 草稿、引用與責任模型
- `publish/docs/`：輸出格式與 rebuild 規則
- `site/docs/`：頁面與內容展示規則

其中 `review/docs/` 與 `edit/docs/` 應共同描述兩者的交界：

- 何時標記 `edit_candidate`
- `edit_draft` 如何回到 `review`
- 哪些批准責任不能由 `edit` 直接跳過
