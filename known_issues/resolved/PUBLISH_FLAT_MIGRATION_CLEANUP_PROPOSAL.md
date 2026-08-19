# Proposal: Removal of Legacy Flat-Export Migration Layer in Publish Module

**Status:** Done（2026-08-19 清理完成並通過 Code Review；實際驗收 113 passed / 583 subtests）
**Author:** Pair-programming AI Agent & Exopolitics Core Engineering  
**Date:** 2026-08-19  
**Target Module:** `modules/publish`  
**Related Docs:** `modules/publish/docs/EXECUTION_POLICY.md`, `known_issues/resolved/PUBLISH_B1_IMPLEMENTATION_WORKING_NOTE.md`

---

## 1. 背景與現況 (Background & Context)

在 2026-08-18 完成的 **Phase B1 發行指引與世代重構 (Publish Export Generation Pointer Refactor)** 中， Publish 模組引入了 `modules/publish/src/migration.py` 作為臨時的「一次性線上平滑遷移機制」（One-time Inline Migration）。

當發行根目錄 `data/publish_export/` 缺乏 `current.json` 指標檔，但存在舊版平面目錄的 `stats.json` 時，`migration.py` 會自動校驗平面檔內容並將其轉移入 `generations/<generation-id>/` 世代目錄中。

在雲端伺服器（Cloud VPS）經過數輪真實生產發行演練後，結果確認：
1. **雲端伺服器已成功完成 Phase B1 世代轉型**，`current.json` 與世代目錄運作正常。
2. **後續發行完全命中 `pointer is not None` 分支**，`migration.py` 的平滑過渡程式碼在生產環境中已成為**永遠不會被執行的 dead code**。

---

## 2. 提案原因與必要性 (Motivation & Rationale)

保留過渡期遷移程式碼（`migration.py`）雖然在重構初期具有相容性效益，但長期留在主程式碼庫中會帶來以下副作用：

1. **可維護性降低 (Increased Cognitive Overhead)**：
   發行核心導引邏輯 [orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/publish/src/orchestrator.py) 夾雜了對舊平面目錄的格式校驗 (`verify_flat_tree`)、搬移 (`migrate_flat_tree`) 與後退判斷，使發行主流程變得複雜且不容易一眼掌握。
2. **累積技術債 (Technical Debt)**：
   當整個系統（包含 Cloud VPS、Staging、全體開發者環境）皆已遷移至世代結構後，維護永遠不會發生的過渡分支會增加程式碼重構與測試追蹤的負擔。
3. **安全自癒邏輯已有成熟替代方案 (Clean Bootstrap)**：
   當完全移除舊平面遷移機制後，若在全新無指標環境中執行 Publish，`orchestrator.py` 仍可直接透過強健的 `bootstrap` 機制從資料庫 Snapshot 直接建立第一個完整的世代，功能完全不受影響且行為更加明確。

因此，提案劃分出一個輕量的腳本拆除與重構清理任務 (De-scaffolding Cleanup)，徹底移除舊平面遷移邏輯。

---

## 3. 預計變更範圍 (Scope of Changes)

程式實作變更僅限 `modules/publish/`；為保持現行跨模組契約一致，同批也會更新
`docs/DATA_LIFECYCLE.md` 與 `modules/api/docs/API_CONTRACT.md`。本提案不變更 API
實作、資料庫 Schema 或 Site 前端行為。

### 3.1 程式碼變更 (Code Changes)

1. **刪除檔案**：
   * 移除 `modules/publish/src/migration.py`
2. **精簡 [modules/publish/src/orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/publish/src/orchestrator.py)**：
   * 移除 `import migration` 引用。
   * 移除 `migration.flat_layout_present()`、`migration.verify_flat_tree()` 與 `migration.migrate_flat_tree()` 的呼叫與判斷。
   * **目標指標判定流程**：以 §7.3 第 2 點的完整 pseudocode 為準；必須保留
     `rebuild` 的短路、`pointer is None` 時的 bootstrap 建版，以及無變更 run
     原子刷新 `last_successful_run_at` 的分支。
   * 移除 `fallback_root = export_dir` 相關的歷史退場邏輯（`fallback_root` 僅在 `pointer` 存在時指向 `live_root`）。

### 3.2 測試套件調整 (Test Suite Adjustments)

1. **[modules/publish/tests/test_generation_pointer.py](file:///C:/Users/user/Documents/exopolitics/modules/publish/tests/test_generation_pointer.py)**：
   * 自 `TestFlatLayoutMigration` 刪除 4 個遷移專屬測試，並保留及改寫
     1 個測試，鎖定「無 pointer 且有平面殘留時直接 bootstrap，殘留檔惰性保留」
     的新契約；精確範圍見 §7.3 第 3 點。
   * 僅更新 `TestCorruptStateFailStop` 的 migration 相關 docstring／comment
     措辭，不調整不存在的 `pointer is None` 斷言。
2. **驗證全量測試**：
   * 確保 `pytest modules/publish/tests` 執行通過，預期為
     **`113 passed / 583 subtests`**（見 §7.4）。

### 3.3 文件同步 (Documentation Updates)

同步更新下列文件，保持現行契約與實作 100% 一致：
* **`EXECUTION_POLICY.md`**：移除 Section 6.4 關於平面目錄遷移的說明，將 `pointer is None` 的行為明確定義為純粹的 Bootstrap 建立首世代。
* **`DATA_CONTRACT.md`**：移除 archive updated_at stamping 中對 pre-pointer flat tree 的 fallback 措辭。
* **`TEST_COVERAGE_MAP.md`**：移除已被清理的測試案例說明。
* **`docs/DATA_LIFECYCLE.md`**：移除 §9.2 的一次性平面佈局遷移說明。
* **`modules/api/docs/API_CONTRACT.md`**：將 `mid-first-migration` 改為
  bootstrap 尚未建立的狀態，並以現行 publish 契約取代 `pre-refactor orchestration`
  的引用。

---

## 4. 時程安排與執行步驟 (Schedule & Phasing)

本項清理工作屬於結構單純、風險可控的輕量重構，預計可於 **1 個工作天內** 完成並交付 Code Review。

| 階段 (Phase) | 預計時間 | 工作內容 (Tasks) | 交付物 / 驗收標準 (Deliverables) |
| :--- | :--- | :--- | :--- |
| **Phase 1: 程式碼清理** | 0.5 天 | 刪除 `migration.py`，簡化 `orchestrator.py` 分支 logic | `orchestrator.py` 程式碼行數減少，Logic 簡潔 |
| **Phase 2: 測試重構與驗證** | 0.25 天 | 刪除 4 個 migration 測試、改寫 1 個平面殘留 bootstrap 測試，重跑全套測試 | `pytest modules/publish/tests` **113 passed / 583 subtests** |
| **Phase 3: 文件更新與審查** | 0.25 天 | 同步更新 publish、頂層與 API 契約文件及變更紀錄 | 文件與代碼保持一致，開立 PR 供工程師 Review |

---

## 5. 風險評估與因應對策 (Risk Assessment & Mitigation)

| 風險項目 | 風險等級 | 因應與防護對策 (Mitigation) |
| :--- | :--- | :--- |
| **若全新部署環境完全沒有發行資料** | 低 | 系統讀不到 `current.json` 時會自動進入 `bootstrap` 模式，直接從資料庫建立包含完整結構的世代目錄，功能完備且安全。 |
| **雲端伺服器在維護期間 `current.json` 被意外手動刪除** | 低 | 即使 `current.json` 遺失，系統不會誤將舊檔案毀損，而是會進行 `bootstrap` 重新產生新世代，並寫入新的 `current.json` 指標，系統自動收斂。 |
| **測試覆蓋率受影響** | 極低 | 刪除 4 個已被確定廢棄的舊平面遷移測試，並改寫保留 1 個平面殘留 bootstrap 測試；發行世代的核心邏輯（Lock、Snapshot、Pointer Atomicity、Retention、Hardlink 等）測試覆蓋率仍保持 100%。 |

---

## 6. 結論與工程建議 (Recommendation)

建議在 Phase B2（Hardlink 記憶體與硬碟重用優化）正式展開前，先執行本提案之「舊平面遷移層清理」，可使 Publish 模組在進入 B2 重構時具備更乾淨、更單純的世代導引核心程式碼結構。


---

## 7. 二次審查補充 (Second-Pass Coverage Audit, 2026-08-19)

**審查方式**：對照實際程式碼逐點核對提案覆蓋面（`migration.py` 呼叫圖、`orchestrator.py`、測試套件、模組與頂層文件全文 grep），並實測目前測試基線。原提案方向與主要範圍正確（`migration.py` 確實僅有 `orchestrator.py:227` 與 `242-244` 兩個呼叫點），以下為發現的遺漏與需修正之處。

### 7.1 文件面遺漏（§3.3 未列出）

1. **`docs/DATA_LIFECYCLE.md` §9.2「One-Time Migration From the Flat Layout」（頂層契約文件，必須處理）**：整節描述即將刪除的遷移行為，提案 §3.3 完全未列出。依 AGENTS.md「頂層文件聚焦現行跨模組契約」原則，此節應整節移除（§9.1 世代發行與 §10 維持不動）。
2. **`EXECUTION_POLICY.md` 的引用點不止 §6.4**：
   - §4（generation phase 步驟 9）與 §5（快照交易說明）均含 "migration verification" 字樣，需同步刪除。
   - §6.1 build trigger 條列含 "or when a flat-tree migration verification failed (see Section 6.4)"，需改為僅餘 fingerprint 不符／無 pointer（bootstrap）／rebuild 三種觸發。
   - §6.4 **不可整節刪除**：Bootstrap 子項必須保留，僅移除 "One-time migration" 子項，並將節標題收斂為純 Bootstrap。
3. **`TEST_COVERAGE_MAP.md` §14 具體位置**：intro 段（"the one-time flat-layout migration"）、`TestFlatLayoutMigration` 五行表格列、以及 `TestCorruptStateFailStop::test_corrupt_pointer_variants_fail_stop` 列的 "only a *missing* pointer triggers migration/bootstrap" 措辭（改為 bootstrap-only）。

### 7.2 程式碼註解／Docstring 清掃清單（§3.1 未列出）

刪除分支後，以下註解仍引用即將不存在的機制，需同批更新（僅改文字，不改行為）：

- `modules/publish/src/generation.py`
  - 模組 docstring："the one-time flat-layout migration verification"。
  - `serialize_json_bytes()` docstring："Migration byte-comparison and Phase A acceptance both depend on this exact shape" —— 注意 byte-identical 序列化仍是現行契約（位元組穩定性測試依賴），僅移除過時的動機描述，**不得改動序列化行為本身**。
  - `_decide_archive_stamp()` docstring priority 4："(live generation root, or the flat export tree when no pointer exists)" —— 清理後 `fallback_root` 僅在 pointer 存在時等於 live generation root。
  - `iter_planned_artifact_bytes()` docstring："Shared by the generation build and the flat-layout migration verification"。
- `modules/publish/src/generation_store.py`：`read_pointer()` docstring 的 "fresh export root or pending one-time migration"；以及 `load_current_generation_hashes()` 內 comment（:305）的 "built or migrated generation"——清理後不再有 migrated generation。（教訓：grep 需涵蓋 migrated 等詞形變化，只搜 migration 會漏掉此處。）
- `modules/publish/src/orchestrator.py`：generation phase 註解中的 "plan build, fingerprint pass, migration verification and the write pass"。
- `modules/publish/tests/test_generation_pointer.py`：檔案 docstring 的 "the one-time flat-layout migration matrix"；`TestCorruptStateFailStop` class docstring，以及 `test_missing_or_corrupt_live_meta_json_fails_stop` 內 "built or migrated generation" 的註解措辭。

### 7.3 對原提案描述的三點修正

1. **§3.2「調整 TestCorruptStateFailStop 中關於 pointer is None 的測試斷言」**：經核對，該類別的測試**並無**針對 `pointer is None` 的斷言需要調整；僅 docstring／comment 措辭需更新（見 7.2）。原描述會讓執行者尋找不存在的斷言。
2. **§3.1 的簡化 pseudocode 不完整**：目標邏輯應保留 `rebuild` 短路與無變更 run 的 pointer 刷新分支，`migrated` flag 一併移除（原 `elif not migrated:` 收斂為 `else:`）。完整目標結構：
   ```python
   pointer = generation_store.read_pointer(export_dir)
   current_hashes: Dict[str, str] = {}
   fallback_root = None
   if pointer is not None:
       live_root = generation_store.generation_root_for(export_dir, pointer["generation"])
       current_hashes = generation_store.load_current_generation_hashes(live_root)
       fallback_root = live_root

   plan, content_fingerprint = generation.build_generation_plan(
       repo, config, current_hashes, fallback_root, run_ts, rebuild
   )

   build_needed = rebuild
   if not build_needed:
       if pointer is None:
           # Bootstrap: the first successful run always builds a complete
           # (possibly empty) generation.
           build_needed = True
       elif pointer["content_fingerprint"] != content_fingerprint:
           build_needed = True

   if build_needed:
       ...  # 建版序（staging → metadata sync → pointer switch → retention）不變
   else:
       # 無變更 run：僅原子刷新 last_successful_run_at
       ...
   ```
3. **測試調整的精確範圍**（依審查意見修訂，見 §8）：`TestFlatLayoutMigration` 的 5 個測試方法中，4 個刪除（成功搬移、unowned 目錄保留、缺 artifact 落 bootstrap、無效時間戳落 bootstrap），但**保留並改寫 1 個**以鎖定清理後的新契約——以 `test_db_ahead_of_flat_tree_falls_back_to_bootstrap_build` 為基底，改寫為「無 pointer 且 export root 有平面殘留（`stats.json`＋舊語言目錄）→ 直接 bootstrap 建版、殘留檔原封不動、不再出現 'does not match the DB plan' 警告」（建議改名如 `test_flat_residue_without_pointer_is_ignored_and_bootstraps`）。理由：現有 `test_coverage_loss.py::test_leftover_language_directory_at_export_root_is_inert` 僅覆蓋「已有 pointer」的殘留情境（先建版再放 stray 目錄），§7.5 承諾的「無 pointer＋平面殘留 → bootstrap」行為在全刪後將無測試鎖定。兩個 helper（`deflate_live_generation_to_flat_layout`、`seed_two_items`）保留供改寫後的測試使用；其餘 4 個測試刪除後檢查檔案頂部 imports 是否有 unused。

### 7.4 量化驗收基線

- 清理前基線（2026-08-19 本地實測）：**`117 passed / 585 subtests`**。
- 預期清理後：**`113 passed / 583 subtests`**（依審查意見修訂：刪除 4 個遷移測試＋改寫保留 1 個平面殘留 bootstrap 測試；被刪的 `test_matching_flat_tree_is_migrated_into_first_generation` 含 2 次 subTest 迭代，故 subtests 585 → 583）。
- 全數 pass 且數字吻合即為無 regression 的強訊號；若數字不符，表示刪除範圍有誤，應暫停並回查。

### 7.5 風險與營運補充

| 風險項目 | 風險等級 | 因應與防護對策 (Mitigation) |
| :--- | :--- | :--- |
| 未來從 pre-B1 備份還原 `data/publish_export/`（平面樹） | 低 | 清理後系統不再識別平面樹：直接 bootstrap 從 DB 建版，平面殘留檔惰性留在 export root（site 僅經 `current.json` 讀取，不受影響），可手動刪除。此行為差異建議在 EXECUTION_POLICY §6.4 以一句話明示。 |
| 本地 `data/publish_export/` 留有 pre-B1 平面殘留檔 | 低 | 已核實（2026-08-19）：本地已收斂至世代佈局（`current.json` + `generations/` 運作中），但根目錄仍殘留舊平面 `stats.json` 與 `zh/`、`en/`、`ja/`——當時遷移驗證因舊檔為 CRLF 換行、byte-exact 比對不符而依設計落 bootstrap，不信任的平面樹被留在原地。殘留檔對 publish 與 site 均惰性，可手動刪除。值得注意：本機其實從未真正走過 migration 路徑（直接 bootstrap），因此本清理對本機行為零影響。 |

### 7.6 已核實「不需更動」的範圍

- `known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md`（B2 主計畫）：全文無 flat／migration／平面引用，無需更動。
- `known_issues/resolved/PUBLISH_B1_IMPLEMENTATION_WORKING_NOTE.md`：歷史紀錄，依慣例保留原貌。
- `modules/site`：site 從不支援平面佈局（無 pointer 即 hard-fail），`BUILD_AND_ROUTING_POLICY.md` 的 "no silent fallback to a stale or flat layout" 措辭在清理後依然成立，無需更動。
- `modules/publish/src/cli.py`、`tests/support.py`、`docs/IMPLEMENTATION_ROADMAP.md` 等處的 "migration(s)" 均指 DB schema migrations，與本提案無關。
- `test_coverage_loss.py::test_leftover_language_directory_at_export_root_is_inert`：清理後平面殘留即屬「export root 惰性殘留」的一種，此測試持續有效，應保留。
- `modules/api/docs/API_CONTRACT.md`：**依審查意見改列為同批必改**（原「可選」判斷過於寬鬆——該檔描述的狀態在清理後不可能存在），細節見 §8 第 2 條。

### 7.7 結案慣例補充

清理完成、測試全綠並經審查後：將本提案 Status 改為 Done 並移入 `known_issues/resolved/`（對齊 repo 既有慣例）。被刪除的 migration 程式碼保留於 git 歷史，未來如需查閱可自舊 commit 取得，無需在程式碼庫留存。


---

## 8. 審查意見採納紀錄 (Review Round 1, 2026-08-19)

外部審查結論：清理方向與 §7.3 的目標控制流程正確，無阻擋事項。審查提出 3 項建議，經逐條對照程式碼核實（引用位置均確認存在），**全部採納**，並已併入上文對應章節：

1. **[P3] `generation_store.py:305` comment 漏清** —— 屬實，採納。該處寫的是 "built or **migrated** generation"，不含 "migration" 字串，首輪以 `flat|migration` grep 時因詞形未涵蓋而遺漏。已併入 §7.2 清掃清單。
2. **[P3] `API_CONTRACT.md` 更新不應只列可選** —— 採納，改列同批必改（原判斷過寬：清理的原則是文件與實作 100% 一致，而該檔描述的狀態在清理後不可能存在）。兩處具體修正：
   - `:165` "export has never completed or is mid-first-migration" → 改為「export has never completed / bootstrap 尚未建立（`current.json` 缺失或無效）」。
   - `:155` "publish's pre-refactor orchestration states that directory names are not ownership evidence" → 改為引用現行 `modules/publish/docs/EXECUTION_POLICY.md` §6.2 的對應契約表述（directory names are not ownership evidence）。
   - 範圍限制：該檔其餘 Phase B1 字樣（如 §7 開頭的落地紀錄）依 B1 時的 de-phasing 決議刻意保留，不在本次更動範圍。
3. **保留一個「無 pointer＋平面殘留 → bootstrap」整合測試** —— 採納。已核實 `test_coverage_loss.py::test_leftover_language_directory_at_export_root_is_inert` 僅覆蓋「已有 pointer」的殘留情境，全刪 `TestFlatLayoutMigration` 後 §7.5 承諾的行為將無測試鎖定。§7.3（改寫範圍與建議測試名）與 §7.4（預期數量修正為 **113 passed / 583 subtests**）已相應修訂。

審查已獨立驗證且與本提案 §7 一致的事項：測試基線 117 passed / 585 subtests；`migration.py` 的唯一執行期呼叫者為 `orchestrator.py`；§7 列出的 orchestrator.py、generation.py、測試、publish 文件與 `docs/DATA_LIFECYCLE.md` 清理點均存在且方向正確。
