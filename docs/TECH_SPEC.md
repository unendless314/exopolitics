# TECH SPEC：模塊化總體技術設計

**文件版本：** v0.2 草稿  
**更新日期：** 2026-05-27  
**對應文件：** `PRD.md`  
**狀態：** 待審核

---

## 1. 文檔定位

本文件描述的是 **整個系統的上層技術結構**，不是任何單一模塊的實作細節。

因此這份文件關注的是：

- 模塊邊界
- 資料流
- 狀態流
- 儲存分層
- 目錄規劃

不在這份文件展開的內容：

- 單一模塊的 CLI 參數
- 單一模塊的函式設計
- prompt 細節
- 前端元件細節

這些內容應在各模塊子目錄下的 `docs/` 中維護。

---

## 2. 核心設計原則

- 資料先保存，再決定是否展示
- 模塊之間以明確資料契約溝通，不以隱含 side effect 耦合
- 發布層不是主資料源，資料庫才是 canonical source
- 前台不直接依賴主資料庫
- 配置預設由各模塊自行擁有，只有真正共享時才上提為全域配置
- 原始來源條目與 edit 內容必須分開建模
- 發布內容必須保留來源追溯、AI 參與標記與人工責任欄位
- `classified` 狀態可作為暫存隊列，但必須有可治理的時限與清理策略
- 人工審核可被 agent queue triage 輔助，但不可失去可追溯的最終責任鏈

---

## 3. 系統分層

```text
RSS / Feed Sources
  -> ingest
  -> canonical database
  -> classify
  -> review
  -> edit (when needed)
  -> publish
  -> site
```

### 3.1 `ingest`

責任：

- 讀取 RSS 來源配置
- 抓取 feed
- 按 `fetch_group` 做並行分片
- 按 `schedule_class` 控制抓取節奏
- 去重
- 保存原始條目
- 更新來源健康狀態與快取 header

不負責：

- LLM 呼叫
- 發布決策
- 前台輸出

### 3.2 `classify`

責任：

- 讀取待分類條目
- 呼叫 LLM
- 回寫 `topic_class`、理由、信心與初始審核狀態
- 視需要標記 `edit_candidate`

不負責：

- RSS 抓取
- 手動審核
- 前台 build

補充：

- `classify` 產生的 `classified` 條目不可無上限滯留；逾時條目應交由 `review` 模塊的 queue policy 處理
- 當 feed 提供的 title / summary 不足以支持判斷時，`classify` 可在後續階段按需觸發 page-level retrieval 或 content enrichment 能力；此能力不屬於 `ingest` 的 MVP 責任

### 3.3 `review`

責任：

- 人工檢視草稿
- 做狀態流轉
- 決定是否批准發布
- 提供適合人工與 agent 使用的 review entry points

不負責：

- 抓 RSS
- 呼叫 LLM
- 輸出靜態站檔案

介面方向：

- MVP 先以 CLI-first 實作
- 核心應先穩定 review queue、filtering、state transition 與批次操作 contract
- 若後續人工審核量增加，可在同一組後端能力之上補 thin web UI
- 不應把 UI 當作 `review` 核心能力的唯一載體
- queue policy 應定義 `classified` 的審核時限（SLA）與逾時處理路徑
- 逾時條目可由 agent 先做 triage，但 agent 決策須寫回可審計紀錄（actor、reason、confidence、timestamp）

### 3.4 `edit`

責任：

- 基於一個或多個 `source_item` 建立站內 edit 草稿
- 支援摘要、引述整理、改寫或脈絡補充
- 保留來源追溯關係
- 將 edit 內容交由人工審核與責任確認

不負責：

- RSS 抓取
- 直接跳過人工審核發布
- 取代 `publish` 的輸出責任

補充：

- `edit` 是架構上的正式能力，不等於一開始就要做成獨立可執行模塊
- 在早期階段，少量 edit flow 可先由 `review` 承接
- 只有在 edit 工作流穩定後，才建議拆出獨立 `edit` 模塊

### 3.5 `publish`

責任：

- 讀取已核准內容
- 匯出發布層資料
- 控制哪些內容進入前台 build
- 將 disclosure label 與 source attribution 一併輸出

不負責：

- 改寫原始抓取資料
- 重新判斷內容主題

### 3.6 `site`

責任：

- 讀取發布層資料
- 生成公開靜態網站
- 處理 i18n、SEO、頁面結構

不負責：

- 直接讀寫 canonical database
- 執行審核

---

## 4. 儲存分層

### 4.0 Canonical Content Objects

canonical database 至少應能區分三種內容物件：

- `source_item`
  - 由 RSS / feed 抓取而來的原始條目
- `edit_draft`
  - 基於一個或多個 `source_item` 生成的站內 edit 草稿
- `published_piece`
  - 最終對外發布的內容單位，可是聚合條目，也可是 edit 內容

這三者不能混成同一筆語義不清的資料。

### 4.1 Canonical Storage

主資料庫保存：

- 正規化抓取欄位與來源識別資訊
- 來源狀態
- LLM 判定結果
- 人工審核狀態
- 發布記錄
- 來源追溯關係
- AI 參與程度
- 人工責任與 edit 資訊

推薦：

- MVP 以 SQLite 起步
- 保留未來升級到 Postgres 的可能

### 4.2 Publish Storage

發布層保存：

- 已批准或已發布內容
- 供 `site` 直接讀取的資料格式
- 對外顯示用的來源列表與 disclosure label

MVP 建議格式：

- Markdown
- frontmatter 承載必要 metadata

原則：

- 可以重建
- 不作為唯一歷史來源
- 優先保持人類可讀與可維護
- 應保留最小發布版本契約（例如 `publish_version`、`exported_at`、`source_snapshot`）以支援增量重建與回溯
- 若未來需要供多個 machine consumers 使用，或 metadata 結構已不適合維護在 Markdown 中，可增補 JSON 派生輸出

### 4.3 Provenance And Disclosure Contract

對於可能公開展示的內容，canonical storage 至少應保留以下欄位：

- `content_origin_type`
  - `aggregated` / `edit`
- `ai_assistance_level`
  - `human_only` / `ai_assisted` / `ai_generated`
- `human_reviewed`
  - 是否已有人類完成審核
- `edit_owner`
  - 最終負責的自然人或法人
- `source_item_ids`
  - 內容引用或依據的來源條目列表
- `public_disclosure_label`
  - 前台對讀者顯示的揭露文字
- `rights_notes`
  - 內部版權、引用與改寫備註

Roadmap note：

- 若未來需要細分摘要、改寫、多來源綜述等差異，應新增獨立欄位表達，而不是擴張 `content_origin_type`
- MVP 階段可先只保留 `content_origin_type = aggregated / edit`
- 未來若 edit 工作流穩定，可再引入獨立的 derivation 或 method 維度
- 例如以額外欄位表達 `summary`、`rewrite`、`synthesis`、`commentary`
- 這些細分暫不屬於 MVP 必備 contract

---

## 5. 狀態流

```text
ingested
  -> classified
  -> draft
  -> approved
  -> published

draft
  -> rejected
  -> approved

rejected
  -> approved
  -> deleted
```

說明：

- `ingest` 只負責產生 `ingested`
- `classify` 負責把來源條目推到 `classified` / `draft`
- `review` 負責 `draft`、`approved`、`rejected`、`deleted`
- `publish` 負責輸出 `approved` / `published`
- `classified` 作為待審核暫存狀態時，應受 queue SLA 約束，不應無限期停留

### 5.1 Aggregation Flow

```text
source_item
  -> ingested
  -> classified
  -> draft
  -> approved
  -> published
```

### 5.2 Edit Flow

```text
source_item(s)
  -> review
  -> edit_candidate
  -> edit_draft
  -> human review
  -> approved
  -> published_piece
```

補充：

- `review` 先決定內容是否值得進入 edit 分支
- `edit_draft` 可以由 LLM 起稿，也可以由人工直接撰寫
- 沒有人工責任確認的 edit 內容，不應進入公開發布層
- `published_piece` 對外必須能區分為聚合條目或站內 edit 內容
- 早期若 edit 數量很少，可先由 `review` 流程承接，而不急於獨立模塊化

---

## 6. 目錄規劃

```text
project-root/
├── docs/
│   ├── PRD.md
│   ├── TECH_SPEC.md
│   ├── CONTENT_LIFECYCLE.md
│   └── MODULE_BOUNDARIES.md
└── modules/
    ├── ingest/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── classify/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── review/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── edit/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── publish/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    └── site/
        ├── config/
        ├── docs/
        └── ...
```

目前不規劃根目錄 `config/`。  
原因是現階段尚未出現穩定的 truly global config。

---

## 7. 配置策略

### 7.1 模塊私有配置

以下配置應先屬於各模塊：

- `modules/ingest/config/`
  - RSS sources
  - categories
  - schedule classes
  - fetch rules
- `modules/classify/config/`
  - prompts
  - model settings
  - threshold
- `modules/review/config/`
  - queue policy
  - reviewer defaults
- `modules/edit/config/`
  - edit policy
  - attribution defaults
- `modules/publish/config/`
  - export rules
  - output policy
- `modules/site/config/`
  - locales
  - metadata

### 7.2 全域配置的升級條件

只有當某設定同時符合以下條件，才考慮提升到根目錄：

- 被兩個以上模塊穩定依賴
- 語義完全一致
- 抽出後能降低重複而非增加耦合

### 7.3 `ingest` source config contract

`ingest` 應擁有來源配置的定義與驗證責任。

在系統層級，來源配置至少應把以下三種維度分開建模：

- 內容語義分類
- 抓取執行分片
- 抓取頻率層級

這些維度不可互相偷代，避免內容語義、執行策略與排程策略耦合。

具體欄位命名、YAML 結構、範例與 validation 規則，應維護在 `modules/ingest/docs/`。

---

## 8. 實施順序

### Stage 1: `ingest`

先建立：

- 來源配置
- 抓取器
- DB schema 基礎
- 入庫與去重

### Stage 2: `classify`

再建立：

- LLM prompt 與模型設定
- 分類結果回寫
- 批次處理
- 改寫候選標記

### Stage 3: `review`

再建立：

- 草稿檢視
- 狀態轉換
- 拒絕與刪除規則
- edit 內容人工責任確認

### Stage 4: `edit`

僅在站內編修需求穩定後建立：

- edit draft 建立流程
- 多來源引用關係
- 摘要 / 改寫 / 脈絡補充工作流
- edit metadata

在此之前，少量 edit flow 可先作為 `review` 的延伸流程處理。

### Stage 5: `publish`

再建立：

- 已批准內容匯出
- 輸出格式
- 發布範圍控制
- 揭露標籤與來源資訊輸出

### Stage 6: `site`

最後建立：

- Astro 頁面
- 路由
- i18n
- SEO 與部署

### Future Capability: external content retrieval

作為後續共享能力考慮：

- page-level content retrieval
- content enrichment for low-context feed items
- 給 `classify`、`review`、`edit` 按需觸發

原則：

- 不屬於 `ingest` MVP
- 不應預設成所有條目的固定主流程
- 只有在被多個模塊穩定依賴後，才考慮升級成獨立模塊或共享服務

---

## 9. 技術選型

| 項目 | 建議 |
|------|------|
| Ingest / Classify / Review / Edit / Publish | Python 3.11+ |
| Feed fetching | `aiohttp` + `feedparser` |
| Canonical DB | SQLite 起步，預留 Postgres 遷移 |
| Site | Astro |
| Deployment | 靜態輸出 + Nginx |
| Scheduling | cron 或 systemd timer |

---

## 10. 需要後續在模塊文檔落實的內容

- `ingest` 的 schema 與 fetch 邏輯
- `classify` 的 prompt contract 與回寫策略
- `review` 的操作流程與審核工具
- `edit` 的草稿契約、引用模型與責任欄位
- `publish` 的輸出格式與 rebuild 規則
- `site` 的頁面 IA 與 SEO 規則
- external content retrieval 的觸發條件、快取策略與 ownership
