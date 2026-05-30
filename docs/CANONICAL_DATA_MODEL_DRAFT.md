# Canonical Data Model Draft

**文件版本：** v0.2 草稿  
**更新日期：** 2026-05-30  
**狀態：** 待審核

---

## 1. 文檔定位

本文件描述的是系統層級的 **canonical data model 草案**。  
目的不是凍結最終資料庫 schema，而是先定義跨模塊共用的 logical entities、欄位責任、關聯方向與審計邊界。

本文件應介於：

- 上層流程文件（`TECH_SPEC.md`、`CONTENT_LIFECYCLE.md`）
- 各模塊內部資料契約與實作 schema

之間，作為後續模塊設計的共同參考。

---

## 2. 目標

- 定義 canonical storage 中的核心內容物件
- 定義模塊之間共享的主要關聯
- 定義哪些欄位由哪個模塊擁有
- 定義跨模塊必備的 provenance / audit 能力
- 避免後續模塊各自擴張時產生語義重疊或欄位漂移

本文件暫不處理：

- SQLite / Postgres 的實體 DDL
- index 與 migration 細節
- ORM model 設計
- CLI 或 API 介面

---

## 3. 核心設計原則

- canonical database 是系統的主資料源
- `source_item`、`edit_draft`、`published_piece` 是不同內容物件，不可混成單一語義模糊的記錄
- 模塊應只擁有自己負責的欄位與狀態變更
- 狀態流轉應可追溯到 actor、reason、timestamp
- 對外發布內容必須保留來源追溯、AI 參與標記與人工責任欄位
- 早期階段可先使用 logical contract，再逐步收斂為實體 schema

---

## 4. 核心 Logical Entities

本階段建議至少識別以下 logical entities：

1. `source_definition`
2. `source_state`
3. `fetch_run`
4. `fetch_attempt`
5. `source_item`
6. `classification_result`
7. `review_decision`
8. `edit_draft`
9. `published_piece`
10. `content_source_link`
11. `audit_event`

這些名稱在實作階段可以調整，但語義邊界應保持清楚。

---

## 5. Entity Sketches

### 5.1 `source_definition`

表示一個已配置的外部來源。

可能欄位：

- `source_id`
- `title`
- `xml_url`
- `html_url`
- `category_id`
- `fetch_group`
- `schedule_class`
- `enabled`
- `notes`

所有權：

- `ingest`

說明：

- 此物件對應模塊配置，但在 canonical model 中仍需有明確語義位置
- 早期可由 config 驅動，不一定需要先落為資料表

### 5.2 `source_state`

表示來源的執行中狀態與健康資訊。

可能欄位：

- `source_id`
- `last_fetch_at`
- `last_success_at`
- `last_http_status`
- `etag`
- `last_modified`
- `consecutive_failures`
- `last_error_class`
- `last_error_at`
- `health_status`
- `quarantine_until`

所有權：

- `ingest`

MVP status examples:

- `healthy`
- `degraded`
- `quarantined`

### 5.3 `fetch_run`

表示一次 ingest 執行批次。

可能欄位：

- `fetch_run_id`
- `started_at`
- `ended_at`
- `run_scope`
- `trigger_type`
- `run_status`
- `error_summary`

所有權：

- `ingest`

MVP status examples:

- `success`
- `partial_failure`
- `failed`

### 5.4 `fetch_attempt`

表示單一 source 在某次 run 中的抓取結果。

可能欄位：

- `fetch_attempt_id`
- `fetch_run_id`
- `source_id`
- `attempt_started_at`
- `attempt_ended_at`
- `http_status`
- `error_class`
- `error_detail`
- `new_item_count`
- `outcome`

所有權：

- `ingest`

MVP outcome examples:

- `success`
- `failed`

MVP does not require a separate `partial_success` attempt outcome.

### 5.5 `source_item`

表示從外部 feed 抓取並正規化後的原始條目。

可能欄位：

- `source_item_id`
- `source_id`
- `source_item_guid`
- `canonical_url`
- `title`
- `summary`
- `published_at`
- `fetched_at`
- `ingest_dedup_key`
- `ingest_status`

所有權：

- `ingest`

說明：

- `source_item` 不應混入 `classify`、`review`、`publish` 的欄位
- 後續模塊可透過關聯表或附屬記錄延伸，而不是直接破壞其語義邊界
- ingest MVP 不要求保存 raw payload snapshot；若後續需要，可用獨立結構擴充而非回寫既有語義

### 5.6 `classification_result`

表示對 `source_item` 的機器分類結果。

可能欄位：

- `classification_result_id`
- `source_item_id`
- `topic_class`
- `classification_reason`
- `classification_confidence`
- `edit_candidate`
- `model_name`
- `prompt_version`
- `classified_at`
- `classification_status`

所有權：

- `classify`

說明：

- 若未來需要保留多次重跑結果，可考慮一筆 `source_item` 對多筆 `classification_result`
- MVP 也可先只保留 latest result，但應明確定義覆寫與歷史策略

### 5.7 `review_decision`

表示人工或 agent 協助的審核決策記錄。

可能欄位：

- `review_decision_id`
- `target_type`
- `target_id`
- `decision`
- `reason`
- `confidence`（若來自 agent triage）
- `actor_type`
- `actor_id`
- `reviewed_at`
- `queue_state_before`
- `queue_state_after`

所有權：

- `review`

說明：

- `target_type` 可能指向 `source_item` 或 `edit_draft`
- `review` 應擁有狀態流轉與決策審計責任

### 5.8 `edit_draft`

表示基於一個或多個來源條目形成的站內編修草稿。

可能欄位：

- `edit_draft_id`
- `title`
- `body`
- `draft_status`
- `ai_assistance_level`
- `human_reviewed`
- `edit_owner`
- `rights_notes`
- `created_at`
- `updated_at`

所有權：

- `edit`

補充：

- 在早期階段，`review` 可承接 edit flow 入口，但 `edit_draft` 的語義邊界仍應獨立

### 5.9 `published_piece`

表示最終對外發布的內容單位。

可能欄位：

- `published_piece_id`
- `content_origin_type`
- `title`
- `body_ref` 或等價發布內容引用
- `publish_status`
- `published_at`
- `ai_assistance_level`
- `human_reviewed`
- `edit_owner`
- `public_disclosure_label`
- `source_snapshot`
- `publish_version`

所有權：

- `publish`

### 5.10 `content_source_link`

表示內容與來源條目之間的追溯關聯。

可能欄位：

- `link_id`
- `target_type`
- `target_id`
- `source_item_id`
- `link_role`
- `created_at`

所有權：

- `edit` / `publish` 共同依契約使用

說明：

- 這層關聯不應只存在於 Markdown frontmatter 或臨時導出檔
- canonical storage 應保留可查詢的來源追溯能力

### 5.11 `audit_event`

表示跨模塊的重要審計事件。

可能欄位：

- `audit_event_id`
- `entity_type`
- `entity_id`
- `event_type`
- `actor_type`
- `actor_id`
- `event_payload`
- `created_at`

所有權：

- 共享 contract，按模塊寫入

說明：

- 若後續決定用各模塊自己的 event log，也應保證可統一查詢與追溯

---

## 6. Cross-Entity Relationships

建議至少明確以下關聯：

- 一個 `source_definition` 對應一個 `source_state`
- 一個 `fetch_run` 對多個 `fetch_attempt`
- 一個 `source_definition` 對多個 `fetch_attempt`
- 一個 `source_definition` 對多個 `source_item`
- 一個 `source_item` 對零到多個 `classification_result`
- 一個 `source_item` 對零到多個 `review_decision`
- 一個 `edit_draft` 對一到多個 `content_source_link`
- 一個 `published_piece` 對一到多個 `content_source_link`
- 一個 `edit_draft` 對零到多個 `review_decision`
- 一個 `published_piece` 可由一個 `source_item` 或一個 `edit_draft` 派生

---

## 7. Ownership Rules

### 7.1 `ingest`

可建立或更新：

- `source_state`
- `fetch_run`
- `fetch_attempt`
- `source_item`

不得寫入：

- `classification_result`
- `review_decision`
- `edit_draft`
- `published_piece`

### 7.2 `classify`

可建立或更新：

- `classification_result`

不得直接寫入：

- source config
- `source_state`
- `published_piece`

### 7.3 `review`

可建立或更新：

- `review_decision`
- queue / review-related status fields defined by contract

不得直接承擔：

- raw ingest persistence
- publish export writing

### 7.4 `edit`

可建立或更新：

- `edit_draft`
- edit-side `content_source_link`

### 7.5 `publish`

可建立或更新：

- `published_piece`
- publish-side `content_source_link`

### 7.6 `site`

- 不應回寫 canonical database

---

## 8. Status Model Direction

本文件不凍結最終欄位實作，但建議至少區分以下狀態語義：

- ingest intake status
- classification status
- review queue status
- edit draft status
- publish status

建議避免：

- 把所有流程狀態硬塞進同一欄位
- 讓不同模塊覆寫彼此的狀態語義

狀態模型的具體欄位與轉移細節，應在對應模塊文件中展開。

---

## 9. Provenance And Audit Minimum

以下能力建議為跨模塊最低要求：

- 每個公開內容單位可追溯到對應 `source_item`
- AI 參與程度可被明確查詢
- 審核與批准決策可追溯到 actor
- 關鍵狀態轉移具備 timestamp 與 reason
- 抓取決策與來源條目具備回查能力（MVP 以正規化欄位與 run/attempt 記錄為主）

---

## 10. Deferred Decisions

以下項目暫不在本草案凍結：

- 各 logical entity 是否一對一映射為資料表
- `classification_result` 是否保留 full history
- `review_decision` 是否兼作 queue event log
- `published_piece` 是否直接存 body 或只存導出引用
- ingest 是否需要引入 raw payload snapshot 能力（非 MVP 必要）
- `audit_event` 採共用表或模塊分表
- 各 entity 的 index 與唯一鍵細節

---

## 11. Suggested Next Documents

建議後續由各模塊文件展開：

- `modules/classify/docs/`：分類結果 contract 與 history policy
- `modules/review/docs/`：review queue、decision record、SLA 與 audit 欄位
- `modules/edit/docs/`：`edit_draft` 與來源連結模型
- `modules/publish/docs/`：`published_piece`、export contract 與 source snapshot

當 `classify` 開始實作前，應至少先把 `classification_result` 與 `source_item` 的關係定清楚。
