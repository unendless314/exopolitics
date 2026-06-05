# Staging Storage Decision

**Document status:** Proposed decision  
**Date:** 2026-06-05  
**Related:** `docs/STAGING_STORAGE_PROPOSAL.md`, `docs/TECH_SPEC.md`, `docs/MODULE_BOUNDARIES.md`

---

## 1. Decision Summary

可以重構，而且在目前尚未上生產、既有資料可全部重抓的前提下，**重構成本遠低於之後補救成本**。

但不建議直接採用 `STAGING_STORAGE_PROPOSAL.md` 中的「雙資料庫 ETL」原案。

建議改採：

- **保留單一 canonical database 作為系統主庫**
- **在 canonical DB 內明確分離 raw 與 sanitized 欄位/物件語義**
- **把 sanitize 定位成 `ingest` 內部 write pipeline，而不是新的正式模組或跨庫同步層**
- **必要時再增設可清理的原文暫存層，但它應是 debug/audit retention layer，不應承擔 canonical object 建模責任**

換句話說：

- 要解決的核心問題是 **資料語義與處理階段沒有分開**
- 不一定需要用 **兩個資料庫** 才能解

---

## 2. Why Current Design Is Failing

`STAGING_STORAGE_PROPOSAL.md` 對問題判斷大致正確，尤其是以下幾點：

- feed summary 直接帶入 HTML，會嚴重浪費 token
- HTML / sidebar / ad 噪音會拉低分類品質
- 目前 `source_item.summary` 同時承擔「原始輸入」與「下游可用文本」兩種角色，語義混亂
- 若長期把高噪音原文永久塞進 SQLite 主庫，體積和 I/O 都會失控

真正的架構問題不是單純「資料庫太大」，而是：

- `modules/ingest/src/parser.py` 現在直接把 feed `summary/description` 原樣塞進 `source_item.summary`
- `modules/classify/docs/DATA_CONTRACT.md` 的 pending query 又直接讀 `source_item.summary`
- 因此 downstream 其實依賴的是「未定義品質的原始 feed 字段」，不是受控的 classify input contract

這會讓 `source_item` 的語義漂移：

- 在 `ingest` 看起來像 raw normalized record
- 在 `classify` 看起來又像 cleaned text record

這兩者不能再混在同一個欄位語義裡。

---

## 3. Why The Two-Database Proposal Is Not The Best Default

`STAGING_STORAGE_PROPOSAL.md` 的方向有價值，但原案有三個主要問題。

### 3.1 It Changes Canonical Semantics Implicitly

原案聲稱 `classify` 幾乎不用改，因為它仍然讀 `canonical.db.source_item.summary`。

這其實代表：

- `source_item.summary` 的語義會從「feed summary」偷偷變成「sanitized body text」

這是 contract change，不是實作細節。

若不明確改寫 top-level docs 與 module docs，下游會持續誤解這個欄位含義。

### 3.2 It Adds Operational Complexity Before We Need It

雙庫方案會新增：

- staging schema
- staging dedup / sync cursor / sync failure recovery
- cross-database consistency rules
- cleanup + vacuum scheduling
- debugging 時對兩份來源事實的對照成本

在系統尚早期時，這些複雜度不一定換來相應收益。

目前真正需要的是：

- 讓 downstream 永遠讀到乾淨、可控、可預測的 classify input
- 同時保留必要的原文回溯能力

這可以先在單庫內完成。

### 3.3 It Misplaces The Boundary Slightly

sanitize pipeline 的本質是：

- 將外部 feed input 轉成系統可用的 canonical ingest representation

在現有模組邊界下，這比較接近 `ingest` 的內部責任，而不是 `classify` 前的一個獨立 ETL 階段。

`classify` 應該接收已經穩定定義好的輸入，而不是替 `ingest` 承擔資料清洗補救。

---

## 4. Recommended Rewrite Direction

建議接受「可重寫」前提，但改成以下方向。

### 4.1 Keep One Canonical DB

canonical DB 仍是唯一正式主庫，避免過早引入雙庫同步問題。

### 4.2 Split Raw vs Sanitized Semantics Explicitly

`source_item` 應明確區分至少兩類文字欄位：

- `feed_summary_raw`
  - feed 原始 summary/description
- `content_text`
  - 提供 downstream 使用的清洗後純文字或 markdown-like text

可選補充欄位：

- `content_format`
  - `plain_text` / `markdown`
- `content_extraction_method`
  - `feed_summary_passthrough` / `html_stripped` / `readability` / `truncated`
- `content_truncated`
  - `0` / `1`
- `raw_content_retained`
  - `0` / `1`

重點不是欄位名字，而是：

- raw 與 sanitized 必須分開
- downstream 使用哪個欄位必須是 contract

### 4.3 Make `content_text` The Classify Input Contract

`classify` 不應再依賴模糊的 `summary`。

MVP 下游輸入應改為：

- `title`
- `content_text`
- `canonical_url`
- `published_at`

若 `content_text` 為空，才視為低上下文或 fallback case。

### 4.4 Treat Sanitization As Ingest-Owned Transformation

新的 ingest write path 應是：

```text
fetch feed
-> parse normalized item
-> sanitize / strip / truncate
-> write canonical source_item
```

也就是：

- raw input 可以保留
- 但 canonical downstream text 必須在入庫前就決定好

### 4.5 Optional Raw Retention Layer

若你仍需要「後悔藥」：

- 可以保留 `staging.db`
- 但它的定位應降為 **short-retention raw evidence store**
- 它不是 canonical source，也不是下游正式依賴資料源

這一層可晚一點再做，不必阻塞先把 canonical contract 修正。

---

## 5. Recommended Architecture Decision

### Decision

採用以下原則：

1. **重寫 ingest content storage contract**
2. **停止把 raw feed HTML 直接當成 classify input**
3. **以單庫 canonical-first 方案作為第一步重構**
4. **將 staging retention 設計為可選第二步，而不是第一步必需項**

### Rejected For Now

- 直接切成 staging/canonical 雙庫，並宣稱 classify 無需 contract 變更
- 直接覆寫既有 `summary` 而不重新定義欄位語義
- 把正文抓取/清洗先升格成新的正式共享模組

---

## 6. Concrete Reset Plan

既然目前資料與 DB 都可重建，建議直接做一次乾淨重置。

### Phase 1: Rewrite Contracts First

先更新文檔：

- `docs/TECH_SPEC.md`
- `docs/MODULE_BOUNDARIES.md`
- `modules/ingest/docs/DATA_CONTRACT.md`
- `modules/ingest/docs/STORAGE_SCHEMA.md`
- `modules/classify/docs/DATA_CONTRACT.md`

明確定義：

- raw feed fields
- sanitized downstream fields
- classify 讀取哪個欄位

### Phase 2: Replace Ingest Schema And Code

直接允許 breaking rewrite：

- 調整 `source_item` schema
- 改 `parser.py` / `orchestrator.py` / repository insert path
- 導入最小 sanitize pipeline

最小 sanitize pipeline 可先只做：

- HTML tag strip
- script/style removal
- whitespace collapse
- length cap

先不要急著把 Readability 和全文抓取一起塞進 MVP。

### Phase 3: Update Classify To Read Sanitized Field

修改 classify contract 與實作規劃：

- pending query 改讀 `content_text`
- low-context rule 改以 `title + content_text` 長度判定

### Phase 4: Rebuild Database From Scratch

因資料可丟棄：

- 不做複雜 migration compatibility
- 直接重建 DB schema 與重新抓取

這會比維持舊 schema 向前兼容更乾淨。

### Phase 5: Decide Whether Raw Retention Still Needs Separate Storage

等第一輪重構完成後，再看是否真的需要：

- `staging.db`
- retention cleanup
- vacuum schedule
- sync status tracking

只有當以下情況成立時，再升級到雙庫：

- 單庫 raw retention 仍讓主庫膨脹過快
- 你確定需要保留大量 raw payload 做長期排錯
- sanitize 與 downstream 使用已穩定，不再頻繁改 contract

---

## 7. Scope Recommendation

如果你現在要我幫你做具體變更，最合理的第一步不是立刻實作雙庫，而是：

1. 先把 top-level docs 和 module docs 改成新的 storage contract
2. 再改 `ingest` schema 與代碼，讓 canonical `source_item` 不再混用 raw/sanitized 語義
3. 最後才評估是否需要額外 `staging.db`

這樣能用最小的重寫，解掉目前最大問題，且不把系統提早推進更重的同步架構。

---

## 8. Final Recommendation

**建議重寫。**

但建議重寫的是：

- `source_item` 的文字欄位 contract
- `ingest -> classify` 的資料語義

而不是先重寫成雙資料庫同步系統。

最小正確方向是：

- **single canonical DB**
- **raw/sanitized explicit split**
- **sanitize in ingest**
- **classify reads sanitized content only**

若之後仍有容量或追溯壓力，再把 raw retention 抽成 `staging.db`。
