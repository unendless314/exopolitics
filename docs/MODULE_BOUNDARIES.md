# Module Boundaries：模塊邊界與所有權

**文件版本：** v0.1 草稿  
**更新日期：** 2026-05-27  
**狀態：** 待審核

---

## 1. 目的

本文件用來避免以下問題：

- 一個模塊順手做了另一個模塊的事
- 狀態變更散落在多處
- 配置與資料所有權不清
- 後續遷移時無法分離模塊

---

## 2. 模塊清單

### 2.1 `ingest`

擁有：

- RSS sources config
- categories config
- fetch group config
- schedule class config
- fetch policy
- source health
- raw article ingestion

不擁有：

- LLM prompt
- review queue policy
- publish output format
- site rendering

### 2.2 `classify`

擁有：

- model config
- prompt templates
- batch policy
- classification result contract
- edit candidate tagging

不擁有：

- RSS source ownership
- manual review decision
- front-end output
- final edit authorship

### 2.3 `review`

擁有：

- review queue logic
- approval / rejection rules
- deletion policy execution
- edit responsibility confirmation
- AI participation disclosure confirmation

不擁有：

- feed fetching
- prompt design
- site page generation

### 2.4 `edit`

擁有：

- edit draft contract
- source linking for edit content
- summary / rewrite / context-note workflow
- edit metadata

不擁有：

- RSS source ownership
- final public output structure
- site rendering

補充：

- `edit` 是架構上的能力，不代表早期一定要拆成獨立可執行模塊
- 若 edit 需求仍屬低頻，應先由 `review` 承接，避免過早抽象

### 2.5 `publish`

擁有：

- export rules
- publish-layer file structure
- selection of approved records for output
- disclosure label emission
- source attribution emission

不擁有：

- raw data collection
- classification logic
- manual review judgment

### 2.6 `site`

擁有：

- page routes
- content presentation
- i18n
- SEO metadata

不擁有：

- canonical database writes
- review state transitions
- feed configuration

---

## 3. 配置所有權

### 3.1 目前原則

配置先屬於模塊，而不是根目錄。

例子：

- `modules/ingest/config/`
  - `sources.yaml`
  - `categories.yaml`
  - `schedule_classes.yaml`
  - `fetch_groups.yaml`
- `modules/classify/config/`
  - prompt
  - threshold
  - model settings

### 3.2 何時才建立根目錄 `config/`

只有在以下情況同時成立時才建立：

- 至少兩個模塊穩定共用同一設定
- 這份設定不是某模塊的內部策略
- 抽出後能降低維護成本

---

## 4. 狀態變更所有權

建議遵守以下規則：

- `ingest` 只能建立新條目與更新抓取元資料
- `ingest` 可更新來源層的排程欄位，但不應以 `category_id` 直接決定抓取節奏
- `classify` 只能更新分類相關欄位與初始分類狀態
- `classify` 可標記改寫候選，但不應直接產出可發布終稿
- `review` 只能更新人工審核相關狀態與 edit 責任確認
- `edit` 只能更新站內 edit 草稿、引用關係與責任欄位
- `publish` 只能更新輸出、發布記錄與對外揭露資料
- `site` 不應回寫 canonical database

---

## 5. 實作順序建議

1. `ingest`
2. `classify`
3. `review`
4. `edit`（僅在需求穩定後）
5. `publish`
6. `site`

這個順序可讓每一步都建立在前一步可驗證的輸出上，也避免在早期為低頻 edit 需求過度抽象。
