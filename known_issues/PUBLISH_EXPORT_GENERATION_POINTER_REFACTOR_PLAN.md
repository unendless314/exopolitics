# Publish Export 世代化與原子指標重構計畫

**狀態：** 提案 v7，已通過最終審查。第七輪複審（v6 審查）的 4 項 P1 已採納：(1) `rebuild` 強制建版例外——fingerprint 閘門僅適用於 `run`；(2) fingerprint 改以 **deterministic generation plan 的計畫態**計算（本次將寫入的 archive `updated_at` 先納入 plan 再算 hash），消除「舊值算 fingerprint → 寫檔後 DB 與 pointer 不一致 → 下一 run 誤建版」的迴圈；(3) 遷移前驗證平面 artifacts 與 DB plan 一致，否則以 DB plan 建立首個完整世代；(4) cursor 在任何 generation 切換後皆過期，包含 fingerprint 不變的強制 rebuild。**Phase A「倖存程式碼拆分」已完成並通過獨立程式碼複審（2026-08-17）**（驗收結果見文末「Phase A 實作紀錄」）；**Phase B1 gate 解除，可開始實作**；Phase B2 待 B1 完成後獨立送審
**日期：** 2026-08-17
**來源：** 深度閱讀策略討論與 `api` 模組提案的七輪外部審查（見 `modules/api/docs/` 修訂歷史）；站主決策：api 模組停留文件階段，待本重構完成後再實作

---

## 背景

`data/publish_export/` 正從「site 建置的內部產物」演變為**多消費者的公開讀取介面**：現有 site（建置期讀取），即將加入 api（執行期讀取），未來可能有其他 agent 介面。

現行 promotion 機制為 staging → **逐檔 promote**（per-file copy＋backup＋`promoted_actions` 日誌），非原子；撤回項目清理與語言收縮掃除與 promotion 交錯進行。讀方因此無法可靠回答三個問題：

1. 這批資料寫完了沒？（世代一致性）
2. 目前哪些語言是有效配置？（目錄存在 ≠ 所有權證據，`orchestrator.py` 註解已明言）
3. 資料最後更新是什麼時候？（新鮮度）

審查中曾考慮讀方自衛方案（manifest 夾讀＋重試協議），判定為局部妥協：複雜度永遠留在每個讀方。治本做法是讓寫方一次保證、所有讀方免費享有。

**既有相關提案：** `SITE_RELEASE_POINTER_PROMOTION_PROPOSAL.md`（2026-08-02）已在 site 層提出相同的「release 目錄＋原子 pointer」模式。兩案慣例應對齊（設計原則同批評估，實作可分開進行）。

**規模背景（第七輪新增）：** 正式環境為雲端 Linux 伺服器，內容量已為本倉本地快照的數倍（2026-08-15 時點約 4,960 篇 × 3 語 ≈ 1.5 萬個 item 檔）且持續成長。2026-08-15 的 site build OOM 事故（`resolved/SITE_BUILD_HEAP_OOM_INVESTIGATION_2026_08_15.md`）經查為 **site 層全量 SSG 的頁數線性成長**問題，與 publish 寫入策略無直接因果；但本重構因此確立一條設計原則：**publish 側的每次 run 成本應與變更量成正比，而非與總館藏成正比**。此原則由「狀態比對觸發」（B1）與「hardlink 重用」（B2）共同落實。

## 分階段策略

本重構分三個階段，不混在同一變更：

- **Phase A：倖存程式碼拆分（純搬移，零行為變更）——維持已批准，範圍依第七輪審查縮小**。只抽出在 Phase B 終態確定存活的部份：`validation.py`（payload 驗證、slug 生成、UI label 檢查、payload 組裝）、純 reconciliation diff 邏輯、aggregate SQL／query helpers。**不**獨立搬移 promotion/rollback、DB compensation、增量 emission——後三者大多會在 Phase B1 刪除或重寫，先搬移只會產生一個難審查、短命的中間層。`orchestrator.py`（現 **1,077 行**）保留其餘 run 脊樑並作為 facade
- **Phase B1：世代＋pointer 主重構（行為變更）——待 Phase A 完成並通過獨立程式碼複審**。完整世代快照、原子 pointer、`content_fingerprint` 狀態比對觸發、單寫者 lock、失敗模型變更（於此語意下刪除 DB compensation 與 per-file filesystem rollback）
- **Phase B2：hardlink 重用最佳化（獨立批次）——B1 完成後獨立送審**。未變更檔案以 hardlink 重用，使有變更 run 的實體 I/O 與磁碟增長也與變更量成正比。不作為 B1 的前置；可獨立審查、獨立回退

理由：在 1,077 行 god module 裡直接做行為變更，審查與除錯成本都高；先拆分倖存程式碼讓 B1 的 diff 只落在該落的檔案。Phase A 即使後續設計再變也不會白費（抽出的全是終態需要的部份）。

### Phase A 驗收標準（審查者批准的約束條件，第七輪補強）

測試基線已由審查者實測確認：`py -3 -m pytest .\modules\publish\tests -q` → **95 passed，583 subtests passed**。Phase A 完成須同時滿足：

1. 既有測試**不修改語意、不改 coverage map** 即全數通過；既有測試**不得改寫為從新模組 import**
2. 輸出檔 bytes、DB 狀態、CLI 行為與拆分前**完全一致**（可用 rebuild 模式的匯出結果做 byte-level 比對）
3. 使用現有 FakeClock 與 promotion failure 注入測試驗證搬移後行為
4. Phase A 完成後**獨立送審**，通過才開 Phase B1
5. **facade 相容（第七輪新增）：** `orchestrator.py` 必須保留 re-export，至少包含 `orchestrate_run`、`ValidationError`、`slugify`、`generate_slug`、`validate_item_payload`、`get_disclosure_note`——既有測試以此命名空間引用這些符號
6. **FakeClock 相容（第七輪新增）：** 測試的 `FakeClock.patch()` 只 patch `modules.publish.src.orchestrator.get_utc_now_iso8601` 與 `modules.publish.src.database.get_utc_now_iso8601` 兩個命名空間。新抽出的模組**不得** `from ... import get_utc_now_iso8601` 後本地綁定使用；應以 `database.get_utc_now_iso8601()` 模組屬性呼叫，或由 orchestrator 在執行期注入 clock

## 提案（Phase B1）

### 核心原則：每個世代都是完整快照，且由狀態比對觸發

現行 incremental run 只把**新增或異動的 item** 與**受影響月份的 archive** 寫入 staging（`orchestrator.py` 的 `affected_months`／`mutated_pairs` 路徑，已驗證）。若直接把這種 staging 升格為世代，index 會列出舊項目但世代內沒有對應檔案。

因此 B1 明定：**建立世代時，都從 DB 的全部 active published set 重建完整世代**——全部 item、全部 archive、index、stats、meta，不沿用增量檔案寫入策略。

**觸發方式（第七輪修訂）：** 是否建立新世代，不由「本次 run 的 mutation event」（`mutated_pairs` 是否非空）決定，而由**狀態比對**決定——reconciliation 與驗證完成、DB 達到穩定快照後，先建立本次的 deterministic generation plan（見下節），對計畫態的完整 export-state 計算 `content_fingerprint`，與 `current.json` 中記錄的值比較：

- 不一致（含 pointer 不存在）→ 建立完整新世代並原子切換 pointer
- 一致 → 不建世代，僅原子刷新 `last_successful_run_at`

狀態比對是收斂保證的關鍵：若 run 在 DB 提交後、世代建構或 pointer 切換前失敗，DB 側的 fingerprint 已變而 pointer 未變，下一次 run 的比對自然判定需要建版。若沿用 mutation event 觸發，此情況下 reconciliation 會判定 `mutated_pairs` 為空（DB 對 DB 無差異），舊世代將永久卡在 live pointer 上。這也是刪除 DB compensation 的前提（見「失敗模型」）。

成本說明：每次 run（含無變更）需一次全量 export-state 讀取以計算 fingerprint——以批次串流方式取得、不落地、記憶體有界（遵守 EXECUTION_POLICY §9），目前規模下為秒級；完整世代的實體寫入只發生在內容真正變更的 run。

### content_fingerprint 範圍與版本化（第七輪審查 P1）

fingerprint 必須回答「若現在重建，輸出會不會不同」，**不是**只對 DB 的 `(id, fingerprint, timestamp)` tuple 做 hash——那會漏掉下列會影響輸出的來源：

- 每個 active published `(item, language)` 的完整 export-state：frozen `slug`、`published_at`，以及所有會進入任一 artifact 的 payload 欄位（`display_title`、`summary_short`、bullets、`canonical_url`、`source_published_at`、`approved_at`、`downstream_action`、`author_metadata`、disclosure 衍生物）
- 語言集合與 publish 設定（`target_languages`、coverage policy、`latest_limit`）
- index／archive 的排序與 membership
- stats 的 withdrawn count、archive manifest 的 `updated_at` 等所有出現在聚合檔中的值（以計畫態為準，見下節）
- export schema／serializer 版本（序列化規則或欄位集合變更時 fingerprint 必須改變）

**明確排除：** `last_successful_run_at` 與一切 run wall-clock 欄位（含 stats 的 `last_export_run_timestamp`）——否則每次成功 run 都會改變 fingerprint，狀態比對形同虛設。

實作定義：對「預定輸出的 deterministic artifact bytes」以固定順序串流計算 **versioned SHA-256**（批次取得 payload → 固定序列化 → 增量雜湊），自動涵蓋上述全部來源。fingerprint 演算法須版本化（格式如 `sha256-exportstate-v1:<hex>`）並記錄於 `current.json`，使未來演算法升級可明確觸發一次重建。

### deterministic generation plan 與 fingerprint 計算時點（第七輪複審 P1-2）

archive `updated_at` 這類「本次 run 才產生」的值，若以建版前的舊 metadata 計算 fingerprint，寫檔後 DB 狀態與 pointer 記錄即不一致，下一個無變更 run 會誤判需要再建一版。因此 fingerprint 必須對**計畫態**（planned final state）計算，全流程為單一 deterministic plan：

1. reconciliation 提交 DB 變更（publish／withdraw），取得穩定快照
2. 從 DB 建立計畫態 export-state model（全部 item payload、index、各月 archive 內容、stats、manifest 雛形）
3. 決定每個活躍月份的 archive stamping：內容與現行世代相同 → 沿用既有 `updated_at`；不同或新增 → 使用**本次 run 的 logical clock**。月份內容是否變更，以現行世代 `meta.json` 記錄的內容雜湊判定（避免每 run 重讀所有 archive 實體檔；遷移或缺雜湊時退回 byte-compare）
4. 以此計畫態（含本次將寫入的 `updated_at`）計算 `content_fingerprint`，與 `current.json` 比較
5. 建版時依序執行：世代目錄建構完成 → 依同一 plan 以短交易套用 `publish_archive_metadata` 更新 → `os.replace` 切換 pointer（最後一步）。pointer 記錄的 fingerprint 必然等於建版後的 DB 狀態，下一個無變更 run 不會誤觸發；若失敗發生在 metadata 提交後、pointer 切換前，下次 run 的 plan 會再度比對出差異並收斂（僅多建一版，內容正確）

`meta.json` 的雜湊表在 B1 涵蓋聚合檔（archive stamping 判定所需），B2 擴及全部 item 檔供 hardlink 判定。

### rebuild 的強制建版例外（第七輪複審 P1-1）

fingerprint 閘門只適用於 `run`。`rebuild` 的契約是強制實體重寫與修復，因此：**rebuild 一律建立新世代**，不問 fingerprint 是否改變；所有活躍月份的 archive metadata 依現行契約以 rebuild run 的 logical clock 刷新；pointer 寫入的 fingerprint 以「全部刷新後的計畫態」計算，使 rebuild 後的第一個無變更 `run` 不會誤判需要再建版。零資料下的 rebuild 等同 bootstrap（建立完整空世代）。

### 無變更成功 run 的 freshness 規則

現行行為（受 `test_rebuild_and_idempotency` 保護）：即使無內容變更，成功 run 仍重建 `stats.json` 並更新 `last_export_run_timestamp`（orchestrator 的 stats 重建段為無條件執行，已驗證）。若改為「無變更就不動 pointer」，freshness 訊號會錯誤地停在舊日期。

規則：

- **每次成功 run 都原子更新 `current.json`**——至少刷新 `last_successful_run_at`
- **僅 `content_fingerprint` 改變才產生新世代**；無變更時世代目錄沿用，`generation`／`export_completed_at` 不動
- 避免無變更時重寫全部 JSON，同時保住 freshness 訊號；API cursor 只在內容指紋改變時才過期（見「消費者影響面」）
- API 的 `data_may_be_stale` 以 `last_successful_run_at` 計算（pipeline 健康度）；`request_exceeds_window_to` 以 `window_to` 計算（內容涵蓋度）。兩個訊號分開：pipeline 健康但窗口內沒有新事件，是正當的編輯事實，不是 stale

### 失敗模型（第七輪審查：與刪除 DB compensation 綁定）

B1 採用明確的新失敗語意：**讀方零副作用；DB 允許在失敗後短暫超前；下一次成功 run 依狀態比對收斂**。

- run 在任何階段失敗 → fail-stop；live pointer 不動，讀方永遠看到完整的舊世代
- 失敗若發生在 DB 提交之後，DB 可短暫超前於 live 世代；**不做補償還原**，由下一次成功 run 的 fingerprint 比對收斂（世代內容永遠由當下 DB 全量重建，收斂是必然的）
- 在此語意下才刪除：**DB compensation**（`rollback_db_state` 與 `db_compensations` 記錄）與 **per-file filesystem rollback**。這是失敗語意的明確變更，既有「promotion 中途失敗後 DB 還原」的測試斷言必須改寫為收斂語意，並在 EXECUTION_POLICY §4 明文化
- **Windows pointer 失敗策略：** `current.json` 的 `os.replace` 遇 sharing violation 時，舊 pointer 必須保持有效，run fail-stop，可有限次重試
- **retention 失敗策略：** retention 必須在 pointer 成功切換後才執行；刪除舊世代遇被讀方（site 建置／api）開啟的檔案時，記錄警告、跳過、下次 run 再試，**不得使已成功的 run 失敗**

### 網站顯示的「最後更新時間」必須改讀 pointer（第六輪審查 P1-1）

上述 freshness 規則有一個連鎖後果：無變更 run 不產生新世代，世代內的 `stats.json.last_export_run_timestamp` 會停在舊日期；而 site 的 Footer 與 stats 頁正是透過 `loadStats()` 讀這個欄位來顯示「最後更新」。不處理的話網站會永遠顯示舊時間，違反既有 idempotency 契約。

修正：**site 顯示的「最後成功執行」時間改從 `current.json.last_successful_run_at` 讀取**，世代內 `stats.json` 的時間戳僅代表該世代內容的產生時間。這意味 site 的改動略大於「純 resolver seam」：`loadStats` 仍從世代目錄讀統計數據，但 Footer／stats 頁的 run 時間戳改由 pointer 提供。實作時由 resolver 同時暴露 `generationRoot` 與 `pointer` 兩個解析結果。

### 首次零資料成功 run 的 bootstrap 規則（第六輪審查 P1-2）

空資料庫首次成功 publish 時沒有任何 published item——若沒有世代可讓 pointer 指向，site 會 hard-fail、API 會 503。既有 `test_stats_zero_state` 已證實零資料狀態必須成功產出 stats。

規則：**若 `current.json` 尚不存在，首次成功 run 一律建立完整的（可能是空的）世代與 pointer**，即使沒有任何 published item。空世代 = 各語言空 `index.json`、空 `items/`、`archives/`、合法零值 `stats.json`。在狀態比對觸發下此規則自然成立（pointer 不存在視為 fingerprint 不一致），仍明列以固定語意。

### 目標佈局

```text
data/publish_export/
  current.json                      # 原子指標（os.replace 切換），讀方唯一入口
  generations/
    2026-08-05T15-23-51Z/           # 不可變世代目錄：完整快照，寫入完成後永不再變
      stats.json                    # 既有根目錄 stats.json 移入世代
      meta.json                     # 診斷與內容雜湊表：B1 涵蓋聚合檔（archive stamping 判定），B2 擴及全部 item 檔（hardlink 判定）；不取代 stats.json
      zh/  index.json  items/  archives/    # 完整語言目錄
      en/  ...
      ja/  ...
```

`current.json` 內容：

```json
{
  "generation": "2026-08-17T12-30-45Z",
  "export_completed_at": "2026-08-17T12:30:45Z",
  "last_successful_run_at": "2026-08-17T18:05:11Z",
  "languages": ["zh", "en", "ja"],
  "content_fingerprint": "sha256-exportstate-v1:…"
}
```

**讀方協議**：讀 `current.json` → 解析世代目錄路徑 → 讀取。世代目錄不可變；「世代被 retention 刪除」的殘餘 TOCTOU 以整流程重試處理（見「保留政策」）。

### 世代 ID 規則（Windows 相容）

- 格式：`YYYY-MM-DDTHH-MM-SSZ`（ISO 8601 UTC 時間戳，**冒號改為連字號**——Windows 檔名禁止 `:`）
- 轉換規則：取 export 完成時間的 UTC ISO timestamp，將 `:` 全部替換為 `-`，移除小數秒
- 唯一性：秒級精度在 pipeline 串行執行下通常足夠；若目標目錄已存在（同秒重跑），依序附加後綴 `-r2`、`-r3` 直到唯一。世代 id 與各時間戳的原始 ISO 值都寫入 `current.json`，讀方不需要自行反推

### publish 側變更（Phase B1）

- **單寫者 lock（第七輪新增）：** 整個 run（staging、DB 狀態、pointer 切換）由 process lock 全程保護，杜絕兩次 publish run 並行操作（curate／translate 已有同型 `ProcessLock` 前例可對齊）
- **emission 階段改為全量**：建立新世代時從全部 active published set 重建完整世代目錄（含 `stats.json`），不再依賴 affected months 決定寫出範圍
- **promotion 階段重寫**：完整世代建構於 staging → 移入 `generations/` → 依 plan 套用 archive metadata 更新 → `os.replace` 切換 `current.json`（同 volume 單檔原子替換，Windows 安全）；無內容變更的成功 run 僅原子更新 `current.json` 的 `last_successful_run_at`；`rebuild` 一律強制建版（見「rebuild 的強制建版例外」）；首次 run（pointer 不存在）一律建立完整世代（bootstrap 規則）
- **可刪除的既有機制**：逐檔 promote 迴圈、per-file backup、`promoted_actions` 日誌、撤回項目的逐檔清理（下一世代自然不含撤回項）、語言收縮的目錄掃除（`languages` 清單自然收縮），以及——在「失敗模型」節的新語意下——**DB compensation 與 per-file filesystem rollback**
- **必須保留**：symlink／junction 安全檢查，套用對象改為「舊世代目錄刪除」
- **保留政策**：保留最近 N=5 個世代，超出刪最舊；刪除時不跟隨 symlink／junction。磁碟成本可忽略（B2 後進一步與變更量成正比）
- **殘餘 TOCTOU 的正式策略**：N=5 不能絕對保證讀方解析後世代仍存。採用「**讀方在讀取流程任何環節（解析→讀 index→item joins→組回應）遇到世代消失時，重新解析 pointer 並完整重跑一次，仍失敗回 503**」。延遲刪除方案不採用
- **archive `updated_at` 語意保留（第七輪修訂；計算時點見「deterministic generation plan」）：** 月份 archive 內容與現行世代相同者沿用既有 `publish_archive_metadata.updated_at`（內容變更才前進），不同或新增者以本次 run 的 logical clock 更新——stamping 決定與 fingerprint 計算在同一 plan 內完成，不採「每個世代刷新所有月份時間戳」。`rebuild` 依強制建版例外全量刷新。`DATA_CONTRACT.md` §2.3「記錄真實檔案寫入」的措辭同步調整為「記錄最後一次內容變更的寫入」
- **一次性遷移的所有權邊界**：只搬移 publish 擁有的 artifacts（有 `publish_language_status` 所有權證據的語言目錄＋`stats.json`）。**非 publish 擁有的頂層殘留目錄（如 `assets/`）保留在 export root 原處不動**——既有契約與測試明確保護這些目錄，遷移不得破壞。首個世代 id 依上述規則從 `stats.json` 的 `last_export_run_timestamp` 轉換。遷移不得只以當前 DB 算出的 fingerprint 直接信任平面 artifacts：必須逐一以 DB plan 驗證其 bytes（排除既有 run wall-clock 欄位）後，才可建立對應的 `meta.json` 與 `current.json`。任何 artifact 缺失或不一致，均必須以 DB plan 建立首個完整 bootstrap 世代，並依同一 plan 套用 archive metadata；只有全數驗證相符時，遷移後第一次 run 才可不立即產生新世代

### 消費者影響面

- **site 生產路徑**：保留既有 `resolveExportRoot(env): string` 的 public contract，改為回傳由 `current.json` 指向的**世代根目錄**，供既有 loader／`loadStats`／產文流程使用。另新增明確的 context resolver（例如 `resolveExportContext(env)`），回傳 `{ generationRoot, pointer }`，僅供 Footer／stats 頁取得 `pointer.last_successful_run_at`。Phase B1 必須同步更新 `export_root.d.ts`、新 resolver 的單元測試與使用 pointer 的 site caller；不得把既有字串 API 改為物件而遺漏既有 caller。既有統計數據 loader 全部沿用不變
- **site 開發與測試路徑**：`npm run dev:fixture` 與多個 Vitest 測試透過 `SITE_PUBLISH_EXPORT_DIR` 指向 `tests/fixtures/publish_export/` 的**平面 fixture**（已驗證：含 `en/ja/zh/stats.json`，無 `current.json`）。Phase B1 必須把 committed fixture 一併遷移為 `current.json`＋`generations/<id>/...` 佈局——符合「fixture 與生產 handoff 同契約」的既有原則，不提供雙軌相容模式
- **api**：契約 v1.7 起以此佈局為前提；讀取流程包在一次整流程重試內；cursor 綁定世代，並在**任何 generation 切換**（內容 fingerprint 改變或強制 rebuild）後回 `cursor_expired`。一般無內容變更的成功 run 不換世代，分頁不會被無變更 run 打斷
- **pipeline.sh**：publish CLI 呼叫參數不變（`--export-dir data/publish_export`）

## 提案（Phase B2：hardlink 重用最佳化，獨立批次）

B1 的「無變更不建版」已消除無變更 run 的檔案成本；B2 進一步讓**有變更**的 run 也只實體寫入變更的檔案，使 publish 的 I/O 與磁碟增長完全與變更量成正比（rsync `--link-dest`／Time Machine 的同型設計）。

設計規則（第七輪審查定稿）：

- 建立新世代時逐檔比對內容雜湊（雜湊表存於世代 `meta.json`，B1 已為聚合檔建立，B2 擴及全部 item 檔）：內容與前一世代相同的一般檔案以 `os.link()` 建立 hardlink，不同或新增的檔案才實體寫入
- 僅對**同一 filesystem** 的一般檔案連結；來源必須位於可信的前一世代目錄內，且不得為 symlink／Windows junction
- 連結失敗（跨 volume、NTFS 限制、網路磁碟等）必須安全 fallback 為複製
- **不可變性鐵律**：世代內任何檔案建立後永不得原地覆寫、truncate、chmod 或 replace，否則會污染共享 inode；建造器只建立新檔
- retention 刪除舊世代僅為 unlink；仍被新世代引用的檔案不會消失
- 備份／rsync 未必保留 hardlink 關係，實際磁碟節省需依 VPS 備份策略驗證，**不得預設必然成立**
- 世代不可變性與讀方協議不受影響：hardlink 是實體儲存最佳化，讀方看到的仍是邏輯上完整獨立的世代
- `rebuild` 指令維持「強制全量實體重寫」的逃生艙位（序列化邏輯改版、雜湊演算法升級時使用）

測試（獨立於 B1 送審）：Linux 與 NTFS 的連結正確性、跨 volume fallback、不可變性（連結檔不被原地修改）、retention 的 link-count 行為、連結失敗注入。

## 測試

- **Phase A**：見上方驗收標準（不改測試、byte-level 一致、FakeClock 與失敗注入矩陣沿用、facade 與 clock patch 相容）
- **Phase B1**：promotion／交易測試改寫為 generation＋pointer 語意；失敗注入矩陣可沿用。新增或改寫：
  - 狀態比對觸發：DB 已提交但世代建構／pointer 切換失敗時 live 世代不變，下一次成功 run 依 fingerprint 比對收斂建版
  - fingerprint 收斂：archive 內容變更的 run 建版後，下一個無變更 run 不得因 `updated_at` 前進而誤觸發建版（plan 態一致性，第七輪複審 P1-2）
  - rebuild 強制建版：無內容變更的 `rebuild` 仍建立新世代並刷新全部 archive `updated_at`；rebuild 後下一個無變更 `run` 不再建版（第七輪複審 P1-1）
  - 無變更成功 run 不產生新世代、僅刷新 `last_successful_run_at`；**bootstrap：pointer 不存在時首次成功 run 建立完整（空）世代**
  - pointer 切換原子性、世代 id Windows 相容性與同秒碰撞後綴、完整快照重建（增量 run 也產出完整世代）
  - 單寫者 lock；pointer 切換失敗（sharing violation）時舊指標保持有效；retention 於切換成功後執行、刪除被佔用檔案僅警告不使 run 失敗（lock／PermissionError 注入）
  - 保留政策刪除行為、殘留目錄不誤認、遷移只搬 publish 擁有 artifacts 且寫入正確 `content_fingerprint`；若 DB 已超前平面 tree，遷移驗證失敗後必須從 DB plan 建立首個完整世代
  - archive `updated_at` 新語意（內容變更才前進；rebuild 全數刷新）；site 的 run 時間戳改讀 pointer
  - DB 超前語意：失敗後 DB 可超前、無 compensation 還原，收斂由下次成功 run 完成（取代原 DB 還原斷言）
  - fixture 世代化後的 site 測試、讀方整流程重試行為
- **Phase B2**：hardlink 測試矩陣（見 Phase B2 節），獨立送審

## 文件更新（實作同批）

- `modules/publish/docs/DATA_CONTRACT.md`（§2.3 `updated_at` 措辭、`content_fingerprint` 契約）、`EXECUTION_POLICY.md`（§4 失敗模型變更；§6.1 的增量 granularity 規定改為狀態比對觸發下的全量建版）
- `docs/DATA_LIFECYCLE.md`、`docs/STORAGE_AND_RETENTION.md`（世代保留政策）、`docs/SYSTEM_OVERVIEW.md`
- `docs/MODULE_BOUNDARIES.md` §3.6 publish 條目措辭（export emission → versioned generation emission）
- `modules/api/docs/API_CONTRACT.md`、`MODULE_PROPOSAL.md`：前置條件引用從 v5 更新為本計畫最新版，記錄 fingerprint 驅動的「僅內容變更才換 generation」語意（第七輪複審列入實作同批，非阻擋項）

## 決策點（附審查建議與採納狀態）

| 決策 | 審查建議 | 狀態 |
|:---|:---|:---|
| 世代目錄結構 | 同意 | ✅ 採納 |
| `current.json` 切換時機 | generation 完整寫好後才 `os.replace`；無變更 run 僅刷新 `last_successful_run_at` | ✅ 採納（第五輪細化；第七輪將「無變更」的判定基礎從 mutation event 改為狀態比對） |
| 每個世代必須完整快照 | 全量重建，不沿用增量 staging | ✅ 採納（第四輪） |
| 建版觸發：狀態比對取代 mutation event | 以完整 export-state `content_fingerprint` 與 pointer 比對；修復「DB 超前＋事件遺失」的永久卡住漏洞；一般 `run` 的 cursor 僅內容變更時過期，強制 rebuild 的 generation 切換仍使 cursor 過期 | ✅ 採納（第七輪；第七輪複審補 rebuild 例外） |
| fingerprint 範圍與版本化 | 完整 export-state model／deterministic artifact bytes；排除 wall-clock 欄位；演算法版本化並記錄於 `current.json` | ✅ 採納（第七輪） |
| fingerprint 以 deterministic generation plan 的計畫態計算 | 本次將寫入的 archive `updated_at` 先納入 plan 再算 hash，DB metadata／檔案／pointer 皆依同一 plan；杜絕多餘建版迴圈 | ✅ 採納（第七輪複審 P1-2） |
| rebuild 強制建版例外 | fingerprint 閘門僅適用於 `run`；rebuild 一律建版、刷新全部 archive metadata，並以刷新後計畫態寫入 fingerprint | ✅ 採納（第七輪複審 P1-1） |
| 失敗模型：DB 可短暫超前、下次成功 run 收斂 | 與刪除 DB compensation 綁定採納；既有 DB 還原測試改寫為收斂語意；EXECUTION_POLICY §4 明文化 | ✅ 採納（第七輪） |
| Phase B 拆分為 B1／B2 | hardlink 重用為獨立最佳化批次，不作為 pointer 重構的前置；獨立審查、獨立回退 | ✅ 採納（第七輪） |
| archive `updated_at` 維持現行語意 | 內容變更才前進（不採每世代全量刷新）；rebuild 維持全量刷新 | ✅ 採納（第七輪） |
| 單寫者 lock | 全程保護 staging／DB／pointer | ✅ 採納（第七輪） |
| 遷移所有權邊界 | 只搬 publish 擁有的 artifacts；遷移時逐 artifact 驗證 DB plan，只有全數相符才沿用平面 artifacts；否則建立完整 bootstrap 世代 | ✅ 採納（第四輪，第七輪補 fingerprint；第七輪複審補驗證） |
| fixture 世代化 | committed fixture 同步遷移，不做雙軌相容 | ✅ 採納（第四輪） |
| site run 時間戳來源 | 改讀 `current.json.last_successful_run_at` | ✅ 採納（第六輪） |
| 首次零資料 bootstrap | pointer 不存在時首次成功 run 必建完整世代 | ✅ 採納（第六輪；第七輪起為狀態比對的自然結果，仍明列） |
| 保留 N 值 | N=5＋整流程重試一次 | ✅ 採納 |
| 與 site release pointer 對齊 | 設計原則同批對齊，實作可分開 | ✅ 採納 |
| 停機一次性遷移 | 同意，適合單使用者本機環境 | ✅ 採納 |

## 非目標

- 不改 export 檔案格式本身（`index.json`／`items/`／`archives/`／`stats.json` 的內容契約不變，僅位置移入世代）
- 不動 canonical DB schema
- 不解決 site 層的 release pointer（屬 `SITE_RELEASE_POINTER_PROPOSAL.md` 範圍）
- 不處理 site build 的 heap 擴展問題（site 層全量 SSG 的頁數線性成長，見 `known_issues/resolved/SITE_BUILD_HEAP_OOM_INVESTIGATION_2026_08_15.md` §13；B2 的 per-file 雜湊表可為其「archive freeze／增量建置」方向鋪路，但非本重構範圍）
- 不拆分 translate 的 orchestrator（同型問題，另案處理）
- 不實作 api 模組（待本重構完成後，依 `modules/api/docs/` 最新契約實作）

## 審查歷史

- **v1（2026-08-17）：** 初版提案
- **v2（2026-08-17）：** 第三輪審查 3 項 P1 採納——世代 id Windows 相容格式；殘餘 TOCTOU 重試策略；`stats.json` 納入世代。新增 Phase A/B 分階段
- **v3（2026-08-17）：** 第四輪審查 4 項 P1 採納——完整快照、遷移所有權邊界、整流程重試、fixture 世代化；Phase A 驗收標準明文化（基線 95 passed／583 subtests）
- **v4（2026-08-17）：** 第五輪審查 3 項 P1 採納——無變更 run 的 freshness 規則（`last_successful_run_at`）；cursor 綁定世代（契約側）；MODULE_PROPOSAL 文件漂移修正
- **v5（2026-08-17）：** 第六輪審查——**Phase A 獲批准**（驗收條件如上）；Phase B 2 項 P1 採納：(1) site 的「最後更新時間」改讀 `current.json.last_successful_run_at`（無變更 run 下世代內 stats 時間戳會凍結，違反 idempotency 契約）；(2) bootstrap 規則：pointer 不存在時首次成功 run 必建完整（空）世代（`test_stats_zero_state` 已驗證存在，證明零資料狀態必須可產出）。另有 1 項 P3 文件漂移於 MODULE_PROPOSAL v1.8 修正
- **v5 補充（2026-08-17）：** 最終文件複審採納 1 項 P1——保留 `resolveExportRoot(): string`，以獨立 context resolver 提供 pointer，避免既有 site caller 將物件當作路徑使用；並同步 API 契約的 v5 前置條件引用。Phase B 文件設計獲批准，但實作仍須等待 Phase A 完成並通過獨立程式碼複審
- **v6（2026-08-17）：** 第七輪審查採納——(1) Phase A 縮為「倖存程式碼拆分」（只抽出 validation、純 reconciliation diff、aggregate query helpers；不搬移 promotion/rollback、DB compensation、增量 emission），驗收補強 facade re-export 清單與 FakeClock patch 相容兩條；(2) 建版觸發從 mutation event 改為完整 export-state `content_fingerprint` 狀態比對，修復「DB 已提交但建版失敗後，mutation event 遺失導致舊世代永久卡住」的漏洞，並使 API cursor 僅在內容變更時過期；(3) fingerprint 範圍與版本化明文化（完整 export-state／deterministic artifact bytes，排除 wall-clock）；(4) 失敗模型改為「讀方零副作用、DB 可短暫超前、下次成功 run 收斂」，與刪除 DB compensation 綁定；(5) Windows pointer sharing violation 與 retention 失敗策略補強；(6) archive `updated_at` 維持現行語意（內容變更才前進）；(7) Phase B 拆分為 B1（主重構）與 B2（hardlink 重用最佳化，獨立批次）；(8) 新增單寫者 lock。文件漂移修正：`orchestrator.py` 行數 880 → 1,077（與測試基線 95 passed／583 subtests 同經實測確認）
- **v7（2026-08-17）：** 第七輪複審（v6 審查）4 項 P1 採納——(1) **rebuild 強制建版例外**：原規則「僅 fingerprint 改變才建版」與「rebuild 強制實體重寫並刷新全部 archive timestamp」互相衝突；修正為 fingerprint 閘門僅適用於 `run`，rebuild 一律建版，並以全部刷新後的計畫態計算寫入 fingerprint，使 rebuild 後的無變更 `run` 不誤判再建版。(2) **fingerprint 計算時點**：archive `updated_at` 為本次 run 產物，以建版前舊值計算 fingerprint 會使 pointer 記錄與建版後 DB 狀態不一致、下一無變更 run 誤觸發建版；修正為建立單一 deterministic generation plan（先決定各月份 stamping，再以計畫態算 hash，metadata 更新、檔案寫出、pointer 寫入皆依同一 plan；metadata 更新移至世代建構完成後、pointer 切換前）。(3) **遷移驗證**：舊 runner 可在 DB 提交後、平面 promotion 前中止，遷移不得以 DB fingerprint 靜默認可該平面 tree；逐 artifact 驗證失敗時，改以 DB plan 建立首個完整世代。(4) **cursor 與強制 rebuild**：cursor 綁定 generation，強制 rebuild 即使內容 fingerprint 未變仍切換 generation，故仍須回 `cursor_expired`。非阻擋項併入文件清單：`API_CONTRACT.md`／`MODULE_PROPOSAL.md` 的 v5 前提引用於實作同批更新

## Phase A 實作紀錄（2026-08-17）

**狀態：** ✅ 已通過獨立程式碼複審（2026-08-17）。審查確認：拆分範圍符合批准內容（未提前抽取 B1 將重寫的 promotion／rollback／compensation／emission）；facade re-export 與 FakeClock 相容性保持；`conn.close()` 位於 `finally`，覆蓋成功與失敗路徑，屬合理且必要的資源生命週期修正；文件紀錄與程式碼一致；實測 95 passed／583 subtests passed。**Phase B1 gate 解除。**

**拆分結果（純搬移）：**

- 新增 `modules/publish/src/validation.py`（207 行）：`ValidationError`、`slugify`、`generate_slug`、`UI_LABELS`／`_UI_LABEL_PREFIX_RE`、`BULLET_KEY_MAP`、`has_ui_label_prefix`、`validate_item_payload`、`get_disclosure_note`、`assemble_item_payload`。
- 新增 `modules/publish/src/reconciliation.py`（91 行）：`compute_reconciliation_diff` 純函式（reconciliation candidates × active publish statuses × configured languages → publish-or-update／withdraw 清單與 `candidates_by_item`），無 clock、DB、檔案存取。
- `modules/publish/src/database.py` +139 行（純新增，無刪改）：index／archive 批次查詢、active archive months、item `published_at` 查詢、manifest 月份聚合、stats 狀態計數與月份統計，共 8 個唯讀 aggregate query helpers 移入 `PublishRepository`（SQL 文字逐字搬移；stats 狀態計數改為參數化 `publish_status`，語意相同）。
- `modules/publish/src/orchestrator.py`：1,077 → 776 行。保留 run 脊樑、promotion／rollback、DB compensation（`rollback_db_state`）、增量 emission 與 symlink 檢查；facade re-export 完整保留（`orchestrate_run`、`ValidationError`、`slugify`、`generate_slug`、`validate_item_payload`、`get_disclosure_note` 等），logger 名稱 `publish.orchestrator` 不變。

**驗收結果：**

1. `py -3 -m pytest modules/publish/tests -q` → **95 passed，583 subtests passed**；測試零修改，未改寫為從新模組 import，coverage map 不變。
2. 以 `data/canonical.db` 複本＋凍結時鐘（patch 點與 FakeClock 相同）跑 rebuild：拆分前後 export 樹 13,228 個檔案 **byte-identical**，`publish_record`／`publish_language_status`／`publish_archive_metadata` 三表 DB 狀態一致；CLI 行為不變（`cli.py` 未修改）。
3. FakeClock 與 promotion failure 注入測試全數沿用通過；新模組均不綁定 `get_utc_now_iso8601`（無任何 clock 使用），FakeClock 兩個 patch 點不受影響。

**唯一刻意偏差（提請複審確認）：** `orchestrate_run` 的 `finally` 區塊新增 `conn.close()`。Python 3.12 的 `sqlite3.Connection` 與其內部 statement cache 存在循環參照，連線檔案控制代碼原本依賴 cyclic GC 的觸發時機釋放；拆分改變了配置（allocation）計數，使 Windows 測試在 teardown 刪除暫存 DB 時確定性觸發 `PermissionError`（55 項失敗，可穩定重現；舊程式碼僅因 GC 時序巧合而未觸發，最小重現證實任何連線皆有此循環）。顯式關閉讓釋放時機確定；上述三項驗收均為加入此修正後的結果，對 export bytes、DB 狀態、CLI 行為無可觀察影響。
