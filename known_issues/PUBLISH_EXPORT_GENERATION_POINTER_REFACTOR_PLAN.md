# Publish Export 世代化與原子指標重構計畫

**狀態：** 提案 v5，最終文件複審已通過；第六輪外部審查的 2 項 P1＋1 項 P3 與最終複審的 1 項 P1 已採納。**Phase A 機械拆分已獲審查者批准**（驗收條件見下）；Phase B 文件設計已獲批准，實作仍待 Phase A 完成並通過獨立程式碼複審
**日期：** 2026-08-17
**來源：** 深度閱讀策略討論與 `api` 模組提案的六輪外部審查（見 `modules/api/docs/` 修訂歷史）；站主決策：api 模組停留文件階段，待本重構完成後再實作

---

## 背景

`data/publish_export/` 正從「site 建置的內部產物」演變為**多消費者的公開讀取介面**：現有 site（建置期讀取），即將加入 api（執行期讀取），未來可能有其他 agent 介面。

現行 promotion 機制為 staging → **逐檔 promote**（per-file copy＋backup＋`promoted_actions` 日誌），非原子；撤回項目清理與語言收縮掃除與 promotion 交錯進行。讀方因此無法可靠回答三個問題：

1. 這批資料寫完了沒？（世代一致性）
2. 目前哪些語言是有效配置？（目錄存在 ≠ 所有權證據，`orchestrator.py` 註解已明言）
3. 資料最後更新是什麼時候？（新鮮度）

審查中曾考慮讀方自衛方案（manifest 夾讀＋重試協議），判定為局部妥協：複雜度永遠留在每個讀方。治本做法是讓寫方一次保證、所有讀方免費享有。

**既有相關提案：** `SITE_RELEASE_POINTER_PROMOTION_PROPOSAL.md`（2026-08-02）已在 site 層提出相同的「release 目錄＋原子 pointer」模式。兩案慣例應對齊（設計原則同批評估，實作可分開進行）。

## 分階段策略

本重構與 `orchestrator.py` 的 god module 拆分屬於**同一專案、兩個階段**，不混在同一變更：

- **Phase A：機械拆分（純搬移，零行為變更）——已獲審查批准（第六輪）**。依檔內既有 Phase 註解把 `orchestrator.py`（約 880 行）切分為子模組（validation／reconciliation／emission／promotion・rollback／DB compensation 分檔）
- **Phase B：世代化行為變更——文件設計已獲批准，待 Phase A 完成並通過獨立程式碼複審**。在拆出的 emission／promotion 邊界實作完整世代與 pointer

理由：在 880 行 god module 裡直接做行為變更，審查與除錯成本都高；先拆分讓 Phase B 的 diff 只落在該落的檔案。Phase A 即使後續設計再變也不會白費。

### Phase A 驗收標準（審查者批准的約束條件）

測試基線已由審查者實測確認：`py -3 -m pytest .\modules\publish\tests -q` → **95 passed，583 subtests passed**。Phase A 完成須同時滿足：

1. 既有測試**不修改語意、不改 coverage map** 即全數通過
2. 輸出檔 bytes、DB 狀態、CLI 行為與拆分前**完全一致**（可用 rebuild 模式的匯出結果做 byte-level 比對）
3. 使用現有 FakeClock 與 promotion failure 注入測試驗證搬移後行為
4. Phase A 完成後**獨立送審**，通過才開 Phase B

## 提案（Phase B）

### 核心原則：每個世代都是完整快照

現行 incremental run 只把**新增或異動的 item** 與**受影響月份的 archive** 寫入 staging（`orchestrator.py` 的 `affected_months`／`mutated_pairs` 路徑，已驗證）。若直接把這種 staging 升格為世代，index 會列出舊項目但世代內沒有對應檔案。

因此 Phase B 必須明定：**每次產生世代時，都從 DB 的全部 active published set 重建完整世代**——全部 item、全部 archive、index、stats、meta，不沿用增量檔案寫入策略。成本可接受：約 1.3 萬個小 JSON 寫入與一次全量查詢，相對 pipeline 裡的 LLM 呼叫可忽略。

### 無變更成功 run 的 freshness 規則

現行行為（受 `test_rebuild_and_idempotency` 保護）：即使無內容變更，成功 run 仍重建 `stats.json` 並更新 `last_export_run_timestamp`（orchestrator 的 stats 重建段為無條件執行，已驗證）。若改為「無變更就不動 pointer」，freshness 訊號會錯誤地停在舊日期。

規則：

- **每次成功 run 都原子更新 `current.json`**——至少刷新 `last_successful_run_at`
- **只有內容有變更（mutated set 非空）才產生新世代**；無變更時世代目錄沿用，`generation`／`export_completed_at` 不動
- 避免無變更時重寫約 1.3 萬個 JSON，同時保住 freshness 訊號
- API 的 `data_may_be_stale` 以 `last_successful_run_at` 計算（pipeline 健康度）；`request_exceeds_window_to` 以 `window_to` 計算（內容涵蓋度）。兩個訊號分開：pipeline 健康但窗口內沒有新事件，是正當的編輯事實，不是 stale

### 網站顯示的「最後更新時間」必須改讀 pointer（第六輪審查 P1-1）

上述 freshness 規則有一個連鎖後果：無變更 run 不產生新世代，世代內的 `stats.json.last_export_run_timestamp` 會停在舊日期；而 site 的 Footer 與 stats 頁正是透過 `loadStats()` 讀這個欄位來顯示「最後更新」。不處理的話網站會永遠顯示舊時間，違反既有 idempotency 契約。

修正：**site 顯示的「最後成功執行」時間改從 `current.json.last_successful_run_at` 讀取**，世代內 `stats.json` 的時間戳僅代表該世代內容的產生時間。這意味 site 的改動略大於「純 resolver seam」：`loadStats` 仍從世代目錄讀統計數據，但 Footer／stats 頁的 run 時間戳改由 pointer 提供。實作時由 resolver 同時暴露 `generationRoot` 與 `pointer` 兩個解析結果。

### 首次零資料成功 run 的 bootstrap 規則（第六輪審查 P1-2）

空資料庫首次成功 publish 時 mutated set 為空——若依「無變更不產生世代」規則，沒有世代可讓 pointer 指向，site 會 hard-fail、API 會 503。既有 `test_stats_zero_state` 已證實零資料狀態必須成功產出 stats。

例外規則：**若 `current.json` 尚不存在，首次成功 run 一律建立完整的（可能是空的）世代與 pointer**，即使沒有任何 published item。空世代 = 各語言空 `index.json`、空 `items/`、`archives/`、合法零值 `stats.json`。「無變更不產生新世代」僅適用於 pointer 已存在之後的 run。

### 目標佈局

```text
data/publish_export/
  current.json                      # 原子指標（os.replace 切換），讀方唯一入口
  generations/
    2026-08-05T15-23-51Z/           # 不可變世代目錄：完整快照，寫入完成後永不再變
      stats.json                    # 既有根目錄 stats.json 移入世代
      meta.json                     # 可選診斷資訊；不取代 stats.json
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
  "languages": ["zh", "en", "ja"]
}
```

**讀方協議**：讀 `current.json` → 解析世代目錄路徑 → 讀取。世代目錄不可變；「世代被 retention 刪除」的殘餘 TOCTOU 以整流程重試處理（見「保留政策」）。

### 世代 ID 規則（Windows 相容）

- 格式：`YYYY-MM-DDTHH-MM-SSZ`（ISO 8601 UTC 時間戳，**冒號改為連字號**——Windows 檔名禁止 `:`）
- 轉換規則：取 export 完成時間的 UTC ISO timestamp，將 `:` 全部替換為 `-`，移除小數秒
- 唯一性：秒級精度在 pipeline 串行執行下通常足夠；若目標目錄已存在（同秒重跑），依序附加後綴 `-r2`、`-r3` 直到唯一。世代 id 與各時間戳的原始 ISO 值都寫入 `current.json`，讀方不需要自行反推

### publish 側變更

- **emission 階段改為全量**：產生新世代時從全部 active published set 重建完整世代目錄（含 `stats.json`），不再依賴 affected months 決定寫出範圍
- **promotion 階段重寫**：完整世代建構於 staging → 移入 `generations/` → `os.replace` 切換 `current.json`（同 volume 單檔原子替換，Windows 安全）；無內容變更的成功 run 僅原子更新 `current.json` 的 `last_successful_run_at`；**首次 run（pointer 不存在）一律建立完整世代**（bootstrap 規則）
- **可刪除或大幅簡化的既有機制**：逐檔 promote 迴圈、per-file backup、`promoted_actions` 日誌、撤回項目的逐檔清理（下一世代自然不含撤回項）、語言收縮的目錄掃除（`languages` 清單自然收縮）
- **必須保留**：symlink／junction 安全檢查，套用對象改為「舊世代目錄刪除」
- **保留政策**：保留最近 N=5 個世代，超出刪最舊；刪除時不跟隨 symlink／junction。磁碟成本可忽略
- **殘餘 TOCTOU 的正式策略**：N=5 不能絕對保證讀方解析後世代仍存。採用「**讀方在讀取流程任何環節（解析→讀 index→item joins→組回應）遇到世代消失時，重新解析 pointer 並完整重跑一次，仍失敗回 503**」。延遲刪除方案不採用
- **一次性遷移的所有權邊界**：只搬移 publish 擁有的 artifacts（有 `publish_language_status` 所有權證據的語言目錄＋`stats.json`）。**非 publish 擁有的頂層殘留目錄（如 `assets/`）保留在 export root 原處不動**——既有契約與測試明確保護這些目錄，遷移不得破壞。首個世代 id 依上述規則從 `stats.json` 的 `last_export_run_timestamp` 轉換

### 消費者影響面

- **site 生產路徑**：保留既有 `resolveExportRoot(env): string` 的 public contract，改為回傳由 `current.json` 指向的**世代根目錄**，供既有 loader／`loadStats`／產文流程使用。另新增明確的 context resolver（例如 `resolveExportContext(env)`），回傳 `{ generationRoot, pointer }`，僅供 Footer／stats 頁取得 `pointer.last_successful_run_at`。Phase B 必須同步更新 `export_root.d.ts`、新 resolver 的單元測試與使用 pointer 的 site caller；不得把既有字串 API 改為物件而遺漏既有 caller。既有統計數據 loader 全部沿用不變
- **site 開發與測試路徑**：`npm run dev:fixture` 與多個 Vitest 測試透過 `SITE_PUBLISH_EXPORT_DIR` 指向 `tests/fixtures/publish_export/` 的**平面 fixture**（已驗證：含 `en/ja/zh/stats.json`，無 `current.json`）。Phase B 必須把 committed fixture 一併遷移為 `current.json`＋`generations/<id>/...` 佈局——符合「fixture 與生產 handoff 同契約」的既有原則，不提供雙軌相容模式
- **api**：契約 v1.7 起以此佈局為前提；讀取流程包在一次整流程重試內；cursor 綁定世代
- **pipeline.sh**：publish CLI 呼叫參數不變（`--export-dir data/publish_export`）

### 測試

- Phase A：見上方驗收標準（不改測試、byte-level 一致、FakeClock 與失敗注入矩陣沿用）
- Phase B：promotion／交易測試改寫為 generation＋pointer 語意；失敗注入矩陣可沿用
- 新增：pointer 切換原子性、世代 id Windows 相容性與同秒碰撞後綴、完整快照重建（增量 run 也產出完整世代）、無變更成功 run 僅更新 `last_successful_run_at` 不產生新世代、**bootstrap：pointer 不存在時首次成功 run 建立完整（空）世代**、site 的 run 時間戳改讀 pointer、保留政策刪除行為、殘留目錄不誤認、遷移只搬 publish 擁有 artifacts、fixture 世代化後的 site 測試、讀方整流程重試行為

### 文件更新（實作同批）

- `modules/publish/docs/DATA_CONTRACT.md`、`EXECUTION_POLICY.md`
- `docs/DATA_LIFECYCLE.md`、`docs/STORAGE_AND_RETENTION.md`（世代保留政策）、`docs/SYSTEM_OVERVIEW.md`
- `docs/MODULE_BOUNDARIES.md` §3.6 publish 條目措辭（export emission → versioned generation emission）

## 決策點（附審查建議與採納狀態）

| 決策 | 審查建議 | 狀態 |
|:---|:---|:---|
| 世代目錄結構 | 同意 | ✅ 採納 |
| `current.json` 切換時機 | generation 完整寫好後才 `os.replace`；無變更 run 僅刷新 `last_successful_run_at` | ✅ 採納（第五輪細化） |
| 每個世代必須完整快照 | 全量重建，不沿用增量 staging | ✅ 採納（第四輪） |
| 遷移所有權邊界 | 只搬 publish 擁有的 artifacts | ✅ 採納（第四輪） |
| fixture 世代化 | committed fixture 同步遷移，不做雙軌相容 | ✅ 採納（第四輪） |
| site run 時間戳來源 | 改讀 `current.json.last_successful_run_at` | ✅ 採納（第六輪） |
| 首次零資料 bootstrap | pointer 不存在時首次成功 run 必建完整世代 | ✅ 採納（第六輪） |
| 保留 N 值 | N=5＋整流程重試一次 | ✅ 採納 |
| 與 site release pointer 對齊 | 設計原則同批對齊，實作可分開 | ✅ 採納 |
| 停機一次性遷移 | 同意，適合單使用者本機環境 | ✅ 採納 |

## 非目標

- 不改 export 檔案格式本身（`index.json`／`items/`／`archives/`／`stats.json` 的內容契約不變，僅位置移入世代）
- 不動 canonical DB schema
- 不解決 site 層的 release pointer（屬 `SITE_RELEASE_POINTER_PROPOSAL.md` 範圍）
- 不拆分 translate 的 orchestrator（同型問題，另案處理）
- 不實作 api 模組（待本重構完成後，依 `modules/api/docs/` 最新契約實作）

## 審查歷史

- **v1（2026-08-17）：** 初版提案
- **v2（2026-08-17）：** 第三輪審查 3 項 P1 採納——世代 id Windows 相容格式；殘餘 TOCTOU 重試策略；`stats.json` 納入世代。新增 Phase A/B 分階段
- **v3（2026-08-17）：** 第四輪審查 4 項 P1 採納——完整快照、遷移所有權邊界、整流程重試、fixture 世代化；Phase A 驗收標準明文化（基線 95 passed／583 subtests）
- **v4（2026-08-17）：** 第五輪審查 3 項 P1 採納——無變更 run 的 freshness 規則（`last_successful_run_at`）；cursor 綁定世代（契約側）；MODULE_PROPOSAL 文件漂移修正
- **v5（2026-08-17）：** 第六輪審查——**Phase A 獲批准**（驗收條件如上）；Phase B 2 項 P1 採納：(1) site 的「最後更新時間」改讀 `current.json.last_successful_run_at`（無變更 run 下世代內 stats 時間戳會凍結，違反 idempotency 契約）；(2) bootstrap 規則：pointer 不存在時首次成功 run 必建完整（空）世代（`test_stats_zero_state` 已驗證存在，證明零資料狀態必須可產出）。另有 1 項 P3 文件漂移於 MODULE_PROPOSAL v1.8 修正
- **v5 補充（2026-08-17）：** 最終文件複審採納 1 項 P1——保留 `resolveExportRoot(): string`，以獨立 context resolver 提供 pointer，避免既有 site caller 將物件當作路徑使用；並同步 API 契約的 v5 前置條件引用。Phase B 文件設計獲批准，但實作仍須等待 Phase A 完成並通過獨立程式碼複審。
