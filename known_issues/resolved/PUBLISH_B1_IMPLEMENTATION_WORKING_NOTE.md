# Phase B1 實作工作筆記（context 恢復用）

**建立：** 2026-08-18（Kimi session，給 compact 後的自己索引用；Phase B1 完成後由站主決定刪除或歸檔）
**主計畫：** [Phase B2 plan](PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md)（B1、B2 皆已結案，同目錄歸檔）
**交接文件：** `C:\Users\user\Documents\kimi\workspace\exopolitics-api-handoff.md`、`C:\Users\user\Documents\kimi\workspace\exopolitics-deep-reader-strategy.md`


> ## B1 closure index
> 
> **Closed:** Final review LGTM on 2026-08-19. Final publish validation: **117 passed / 585 subtests**.
> 
> **Follow-up outcome:** the [Phase B2 hardlink-reuse plan](PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md) landed and closed on 2026-08-22 (post-implementation review LGTM); it is archived in this same directory.
> 
> **Durable operational facts:** generations are immutable complete snapshots selected by atomic `current.json`; unchanged `run` executions refresh pointer freshness without changing generation; `rebuild` always creates a generation; the publish lock file persists after release to avoid inode-reuse races; generation snapshots use `BEGIN IMMEDIATE`; retention protects the live generation, orders `-rN` suffixes numerically, and never refills retired same-second ID gaps; readers retry the full pointer-to-generation flow once on retention TOCTOU.

---

## 0. 狀態快照（2026-08-19 第九次更新：審查員正式給予 LGTM，重構結案）

- **LGTM 結案（2026-08-19）**：審查員確認 Round 5 修正無誤，所有 P1 完全解決（`-r7` Allocator 配置、單測精準保護、對齊發行策略 v2.4 / 覆蓋圖 v1.6，Publish 117 passed / 585 subtests，無語法與空白警告）。Phase B1 重構已全數審查完畢，正式 LGTM 結案！
- **Round 5（id 回收 P1）已採納並完成**：`allocate_generation_id` 改為掃描 `generations/` 取最高存活 `-rN` 後綴＋1（base 視同 N=1），不再回填 retention 刪出的空位——回收 id 會讓新建世代排序成最舊而被之後的 sweep 誤刪（reviewer 四步重現）。migration 共用同一 allocator 一併受益。**我在 round 4 判斷「病態不修」是錯的**：零資料/小型部署建版極快，同秒連建生產邊界可能發生；已在計畫 round 5 紀錄認領此误判。新測試 `test_same_second_id_allocation_never_refills_retired_gaps`。
- **驗證**：publish **117 passed／585 subtests** 全綠；site 無改動。文件：EXECUTION_POLICY v2.4、TEST_COVERAGE_MAP v1.6；計畫與工作筆記均已更新。
- **最終狀態**：✅ Phase B1 重構獲得 LGTM 結案。工作筆記保留供歸檔參考。

## 0.R4 歷史快照（2026-08-19 第七次更新：review round 4 完成）

- **Round 4（retention 排序 P1）已採納並完成**：`generation_store.py` 新增 `_generation_sort_key`（時間戳部分字典序＋`-rN` 後綴數值序），修掉 `-r10` 排在 `-r2` 前導致誤刪較新世代。新測試 `TestRetention::test_retention_orders_same_second_suffixes_numerically`（直接構造 11 個同秒世代目錄單測 sweep；不走連跑建版——**連帶發現：retention 刪舊 id 後 allocate 會回收 id，凍結時鐘下同秒 >5 次建版 id 單調性不成立，屬測試情境病態邊界，不修僅記錄於計畫 round 4**）。
- **驗證**：publish **116 passed／585 subtests** 全綠；site 無改動。文件：EXECUTION_POLICY v2.3、TEST_COVERAGE_MAP v1.5；計畫文末追加 round 4 紀錄。
- **待站主決定**：是否送最終複驗；工作筆記刪除或歸檔。

## 0.R3 歷史快照（2026-08-19 第六次更新：review round 3 完成）

- **Round 3（最後 1 項 P1）已採納並完成**：generation 快照由 deferred `BEGIN` 改為 **`BEGIN IMMEDIATE`**（`orchestrator.py:212` 附近）。原因：deferred 只持 SHARED，與並發 writer 的 RESERVED 相容 → writer 可開始寫入，而 publish 的 metadata commit 升級會撞 RESERVED 衝突失敗（reviewer 用獨立 SQLite 重現確認）。BEGIN IMMEDIATE 在 phase 開頭保留 writer slot，writer 在自己的 BEGIN IMMEDIATE 即被拒；讀者不受影響。測試改為斷言第二連線 `BEGIN IMMEDIATE` 本身失敗（`begin refused: ... locked`）。文件同步：EXECUTION_POLICY v2.2、TEST_COVERAGE_MAP v1.4、DATA_LIFECYCLE §9.1；計畫文末追加 round 3 紀錄。
- **驗證**：publish **115 passed／585 subtests** 全綠（round 3 無數量變化）；site 未動（上一輪 154 passed＋astro check 淨仍有效）。
- **待站主決定**：是否再送 reviewer 複驗；工作筆記刪除或歸檔。
- Round 2 五項 P1 採納細節與 spec 去 phase 化範圍見下方第五次日更新與主計畫「複審修正紀錄（review round 2）」。

## 0.R2 歷史快照（2026-08-18 第五次更新：review round 2 五項 P1 全部採納並完成）

- **Review round 2 完成**：站主轉來的 5 項 P1 全部採納實作，兩套件重跑全綠（publish **115/585**、site **154**、astro check 0/0/0、`git diff --check` 無警告）。
  1. `process_lock.py` release 不再刪 lock 檔（inode 重用 race）；測試改名 `..._lock_file_persists_after_run`＋斷言可重取。**curate 前例仍有 unlink，屬既有碼未動，已在計畫紀錄標註可另案。**
  2. `orchestrator.py` generation phase 改為一個明式持有的 SQLite 快照交易（`conn.execute("BEGIN")` 於 reconciliation 後；archive metadata 寫入併入、pointer 切換前 commit；例外 rollback no-op）；rollback journal 下並發上游寫入者得 SQLITE_BUSY 而非靜默交錯；rebuild `published_count` 移入快照內。新測試 `TestGenerationPhaseSnapshot::test_concurrent_upstream_write_during_build_is_excluded`（第二連線 timeout=0.1 寫入被拒＋run 成功＋probe 表不存在）。
  3. 時間戳日曆合法性：publish `is_valid_iso_timestamp` 加 strptime；site 加 `Date` round-trip（`toISOString() === value.slice(0,-1)+'.000Z'`）。generation id 維持僅格式驗證（排序/路徑 token，不顯示）。
  4. pointer `languages` 兩端改為非空 list。
  5. `load_current_generation_hashes` 拒絕空表（合法世代至少有 stats.json＋各語言聚合檔；空表＝損毀 fail-stop）。
- **文件同批**：publish DATA_CONTRACT v3.1／EXECUTION_POLICY v2.1／TEST_COVERAGE_MAP v1.3（§14 更新＋改名對照）；site DATA_HANDOFF_CONTRACT 與 BUILD_AND_ROUTING_POLICY §4 hard-fail 清單補新規則；STORAGE_AND_RETENTION 更正 lock 檔留存描述。5 份文件標頭 trailing whitespace 已清。
- **spec 去 phase 化（站主指示，reviewer 背書）**：頂層四份＋publish/site 模組文件正文移除 Phase B1／pre-B1 用語；歷史保留於各文件 Revision History 與 known_issues；`modules/api/docs/` 仍 proposal 依建議保留。
- 主計畫文末已追加「Phase B1 複審修正紀錄（2026-08-18，review round 2）」。**待站主決定是否再送 reviewer 複驗。**
- 先前四轮狀態（Step 1–5 完成、真實資料演練 CRLF→bootstrap 結論、部署提示）維持有效，見主計畫實作紀錄。

- 文件審查完成：**計畫 v7 與現行程式碼全部關鍵主張核對屬實，無阻礙事項，方案可行**。站主外部複審（2026-08-18）三點補充已採納為 §1 第 8–10 條並已實作。
- **Step 1–4 全部完成**：publish 核心＋測試（`114 passed / 582 subtests`）；site（vitest `153 passed`／astro check 0/0/0／generate-posts 對世代化 fixture 成功）；文件同批 14 份（publish 5＋site 3＋頂層 4＋api 2，由兩個 coder 子代理先後完成並經我抽查 diff 品質）；主計畫文末已追加「Phase B1 實作紀錄（2026-08-18）」（含 11 項偏差/加強清單，§1 十條＋下方第 11 條）。
- **實作中發現並修正一個計畫落點偏差**（偏差清單第 11 條）：`verify_flat_tree` 原本只剔 stats 時間戳比 dict，平面 stats 的 `last_export_run_timestamp` 無效時會在 `migrate_flat_tree` raise（fail-stop）；筆記 §3 與計畫皆為「無效時間戳＝驗證失敗→bootstrap」。已改為 `verify_flat_tree` 回 False，並由 `test_flat_stats_with_invalid_run_timestamp_falls_back_to_bootstrap_build` 鎖定。
- **Step 5 完成**：兩套件重跑全綠；真實資料遷移演練（temp 複本）結果——首次 run 因真實平面樹為 CRLF 換行（舊 runner Windows 文字模式寫出）而 byte-exact 驗證不符 → **依設計落 bootstrap**（語義內容／stats／檔案集合皆相等，僅換行不同）；無變更 run 不建版；rebuild 建新版＋`published_count=12,726`；site resolver 解析演練根正確（原「lock 檔有清除」驗證點自 review round 2 起改為 lock 檔留存）。結論與部署提示（舊平面殘留 inert、可手動刪）已補記到主計畫實作紀錄第 3 點。`.b1_rehearsal.sh` 與 temp 目錄已刪除。
- **Phase B1 全部完成，待站主安排 code review。**
- publish 測試綠基線（Phase A 後、B1 動工前實測）：95 passed / 583 subtests。
- 環境：Windows + Git Bash；`data/` 已 gitignore（本地真實快照 `data/publish_export/` 平面佈局 zh/en/ja 各 4242 篇 + `data/canonical.db` 可供遷移演練，絕不動本體）。

### 0.1 已完成落地清單（不要重做）

**publish src：**
- 新增 `modules/publish/src/process_lock.py`（對齊 curate ProcessLock：msvcrt LK_NBLCK／fcntl LOCK_EX|LOCK_NB；**review round 2 起 release 只解鎖＋關閉，不刪檔**——inode 重用 race）。
- 新增 `modules/publish/src/generation.py`：`FINGERPRINT_ALGORITHM="sha256-exportstate-v1"`、`serialize_json_bytes`（indent=2、ensure_ascii=False、無尾行換行）、`archive_file_name`、`GenerationPlan`（index_entries／archive_months ASC／archive_hashes／archive_stamps／manifest_entries DESC／stats）、`build_generation_plan`（含 stamping 五優先序）、`compute_content_fingerprint`（header＋每 artifact `rel_path\0sha256hex\0`，stats 剔 `last_export_run_timestamp`）、`iter_planned_artifact_bytes`（固定順序；manifest 一律寫、stats 含時間戳）。**不綁 clock**（run_ts 參數傳入）。
- 新增 `modules/publish/src/generation_store.py`：`GENERATION_ID_RE=^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$`、`validate_pointer`（欄位＋id 格式＋世代目錄存在，全 fail-stop）、`read_pointer`（缺→None／毀→raise）、`write_pointer_atomic`（`.current.json.tmp`＋os.replace＋PermissionError 5 次重試×0.1s）、`allocate_generation_id`（`-rN` 後綴）、`write_generation_to_staging`（staging 建完整世代含 meta.json＋每語言 items/、archives/ 空目錄 → `os.rename` 入 generations/）、`discard_staging`、`load_current_generation_hashes`（缺/毀 meta.json→raise）、`sweep_retired_generations`（keep=5＋protected=live；junction/含 junction 樹警告跳過；OSError 警告跳過）、`_is_symlink_or_reparse_point`。logger 名 `publish.generation_store`。
- 新增 `modules/publish/src/migration.py`：`flat_layout_present`（root stats.json）、`verify_flat_tree`（逐 artifact byte-exact＋stats 剔時間戳比 dict＋平面 *.json 集合相等）、`migrate_flat_tree`（id 由平面 stats 時間戳轉換＋`-rN`；搬設定語言目錄＋stats.json；meta.json 用實際搬移 bytes 雜湊；回傳 pointer dict 由呼叫方原子切換）。
- 改 `modules/publish/src/database.py`：`insert_publish_record`／`upsert_publish_language_status`／`upsert_archive_metadata` 加 `now: Optional[str]=None`（預設回退 `get_utc_now_iso8601()`）；新增 `fetch_published_payload_batch(lang, limit, offset)`（欄位＝fetch_canonical_item_payload＋pr.slug＋pls.published_at，`ORDER BY pr.slug ASC`）。
- 重寫 `modules/publish/src/orchestrator.py`（776→約 290 行）：ProcessLock（`<db_path 同目錄>/publish_runner.lock`）全程持有→lock 後取一次 `run_ts`→語言警告／reconciliation diff／publish＋withdraw 短交易（**無 compensation**，全部用 run_ts）→語言收縮 metadata 無條件刪除→`read_pointer`（毀→raise）→live meta hashes／fallback_root→`build_generation_plan`→判定（rebuild 必建版；pointer 缺→有平面樹走遷移驗證（不符→warn＋bootstrap 建版）否則 bootstrap；fingerprint 不符→建版）→建版序：allocate id→staging 建世代→短交易 metadata 同步（計畫 stamp upsert＋inactive 刪除）→`write_pointer_atomic`→retention；無變更 run 僅原子刷新 `last_successful_run_at`；finally＝`discard_staging`＋`conn.close()`；facade re-export 與 logger 名 `publish.orchestrator` 不變；summary 鍵不變。`cli.py` 未動。

**publish tests（既有檔改寫，路徑全走 `support.live_root`/`read_pointer`）：**
- `support.py`：加 `read_pointer`／`live_root`；五個 read_* helper 改經 live_root 解析。
- `test_publish.py`：4 個失敗語意測試改名改寫為收斂語意——`test_first_time_publish_build_failure_converges`、`test_update_build_failure_converges`、`test_failed_rebuild_leaves_live_generation_untouched`、`test_pointer_switch_failure_converges`；其餘路徑改 live_root。
- `test_idempotency.py`：無變更 rerun＝世代 id 不變、**stats.json 時間戳凍結**、全 bytes 不變、pointer `last_successful_run_at` 前進、DB 全凍結。
- `test_aggregate_contracts.py`：heal 測試改名 `test_missing_metadata_heals_by_restamping_archive_once`（斷言改為「heal run 恰建一個新世代＋archive bytes 不變＋其後無變更 run 不再建版」）；`test_archive_metadata_converges_after_pointer_switch_failure`（原 rolls_back）；新增 `test_zero_state_bootstrap_layout`（空世代每語言空 index＋空 manifest＋items/ archives/ 目錄存在）。
- `test_coverage_loss.py`：`assert_fully_withdrawn` 斷言 manifest 為 `[]`（不再缺檔）；`assert_removed_language_artifacts_gone` 改斷言 live 世代無該語言目錄＋pointer languages 排除；junction 兩測試移到新 class `TestRetentionLinkSafety`（`test_retention_skips_junction_generation`／`test_retention_skips_generation_containing_junction`，直接單測 `sweep_retired_generations`，斷言 publish.generation_store 警告＋link target 不動＋另一 retiree 正常刪）；`test_leftover_language_directory_at_export_root_is_inert`（原 lone_index）；`test_shrink_converges_after_build_failure`（原 shrink rolls_back）；`test_shrink_allows_missing_removed_language_directory` 改刪 live_root 下的 ja。
- 路徑-only 修正：`test_batching.py`（helper＋June-gone＋docstring）、`test_author_metadata.py`（失敗 run 無 pointer 斷言＋live_root）、`test_item_payload_contract.py`（label 掃描走 live_root）、`test_cjk_slug_fallback.py`（4 處 replace_all）、`test_cli_failures.py`（1 處）。

**失敗注入新 seam（測試已用，勿改）：** patch `modules.publish.src.generation_store.write_generation_to_staging`（建版失敗）與 `...generation_store.write_pointer_atomic`（pointer 切換失敗）。

## 1. 文件審查發現（實作時要處理的小缺口；皆已在下方設計內化解，實作紀錄要寫給審查者）

1. **計畫 v7「文件更新」節漏列 site 模組文件**：`modules/site/docs/DATA_HANDOFF_CONTRACT.md`、`BUILD_AND_ROUTING_POLICY.md`、`README.md` 仍以平面佈局描述 handoff 契約／hard-fail 清單／fixture；依 AGENTS.md 同批更新規則補上。
2. **Retention 未明文保護 live 世代**：病態情境（≥5 次 run 在移入 generations/ 後、pointer 切換前失敗）會讓 live 世代掉出最新 5 個之外。實作加「永不刪 `current.json` 指向的世代」保護。
3. **site resolver 測試可攜性**：`data/` 不入 git，現行 `resolveExportRoot({})` 測試靠「default 不檢查存在性」做純路徑斷言；pointer 化後必須讀 `current.json`。解法：拆出內部 seam `resolveExportBase(env)`（保留原 override/default 路徑邏輯），pointer 行為全部用 temp-dir override 測。
4. **`published_count` 語義**（計畫未明文，保持現行可觀察行為）：`run` = reconciliation 異動對數；`rebuild` = 全量 published 列數；`withdrawn_count` = len(items_to_withdraw)（兩模式同）。`test_rebuild_and_idempotency` 鎖定 rebuild published_count==2。
5. **lock 位置**：對齊 curate/translate 的 ProcessLock 前例但由 `--db-path` 推導：`<db_path 同目錄>/publish_runner.lock`（預設即 `data/publish_runner.lock`；測試 temp DB 自然隔離）。
6. **遷移 ownership 細化**：計畫文字「只搬有 publish_language_status 證據的語言目錄」在邊界案例（設定語言從未發佈→無 pls 行→平面目錄只有空 index.json）會產生殘缺世代。實作改為「搬移＝通過逐 artifact 驗證的**設定語言**目錄＋stats.json」；非設定語言目錄（含殘留 ja/、assets/）一律留原處。
7. **corrupt `current.json`**：計畫未定義 → 實作採 fail-stop（缺 pointer 才走遷移/bootstrap；JSON 損毀或缺欄位視為需人工介入，raise）。
8. **（外部複審 2026-08-18 採納）零資料世代的 archive manifest 與空目錄**：site `loadArchiveManifest()` 對缺檔 hard-fail → B1 完整世代對**每個設定語言**固定寫 `archives/index.json`（空內容為 `[]`），並固定建立每語言 `items/`、`archives/` 目錄（無 artifact 也要建，符合 bootstrap 佈局）；publish/site 契約與測試同步。固定 artifact 順序中 manifest 不再「僅非空」。連鎖影響：舊平面零資料樹（無 manifest）遷移驗證必不符 → 走 bootstrap 建版，屬預期行為。
9. **（外部複審採納）pointer 完整驗證與 generation 路徑約束**：`read_pointer`／site `resolveExportContext()` 皆須完整驗證——JSON 可解析、必要欄位齊（generation／export_completed_at／last_successful_run_at／languages／content_fingerprint）、generation id 符合 Windows-safe 嚴格格式 `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$` 才准組路徑、指向的世代目錄存在且為目錄；任一不符 fail-stop（raise），不把任意字串傳給 path join。
10. **（外部複審採納）單一 `run_ts` 全程傳遞**：取得 lock 後只取一次 `run_ts`，涵蓋 reconciliation 新寫入的 `published_at`／`withdrawn_at`／`publish_record.updated_at`、各 INSERT 的 `created_at`、archive stamping、stats `last_export_run_timestamp`、generation id、pointer 兩個時間戳。作法：orchestrator 內所有 `get_utc_now_iso8601()` 呼叫改為 `run_ts`；repo 方法（`insert_publish_record`、`upsert_publish_language_status`、`upsert_archive_metadata`）加 `now: Optional[str] = None` 參數，預設回退 `database.get_utc_now_iso8601()`——FakeClock 兩個 patch 點（orchestrator／database 命名空間）皆維持有效。

## 2. 現行程式碼關鍵事實（已驗證，B1 要對齊/刪除的點）

- `modules/publish/src/orchestrator.py`（776 行）：run 脊樑＋逐檔 promote＋`.backup` rollback＋`rollback_db_state`（DB compensation）＋增量 emission（`affected_months`/`mutated_pairs`）＋語言收縮掃除＋`_is_symlink_or_reparse_point`。facade re-export 必須保留：`orchestrate_run`、`ValidationError`、`slugify`、`generate_slug`、`validate_item_payload`、`get_disclosure_note`。
- 既有 artifact 位元組格式：`json.dump(obj, f, indent=2, ensure_ascii=False)`，**無尾行換行** → 序列化器必須是 `json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")` 才能 byte-identical（遷移比對與 Phase A 驗收都依賴）。
- index/archive entry 鍵序：`slug, display_title, summary_short, canonical_url, source_published_at, approved_at, published_at`；stats 鍵序：`total_active_published_items_by_language, total_withdrawn_items_by_language, latest_index_count_by_language, archive_month_count_by_language, oldest_archive_month_by_language, last_export_run_timestamp`（最後）。manifest entry：`archive_month, file_name, item_count, updated_at`，月份 DESC。空 manifest 不寫 `archives/index.json`；零資料 run 只產生 `<lang>/index.json`("[]")＋stats.json（舊碼連空目錄都不 promote）。
- `published_at` 在 upsert 重發時會刷新為該 run clock（內容變更⇒published_at 變⇒bytes 變⇒fingerprint 變，收斂性成立）。
- FakeClock（`tests/support.py`）只 patch `modules.publish.src.orchestrator.get_utc_now_iso8601` 與 `modules.publish.src.database.get_utc_now_iso8601`。**新模組不得本地綁定 clock**：`run_ts` 由 orchestrator 取一次往下傳；repo 方法內部用 `database.get_utc_now_iso8601()` 維持。
- 既有失敗注入測試 patch `json.dump`／`os.replace`——B1 改用新 seam（見 §4 測試改寫）。
- `pipeline.sh` publish 呼叫參數不變（`run --db-path data/canonical.db --export-dir data/publish_export`）。
- site 消費面（explore 已完成）：`src/utils/export_root.js`（`resolveExportRoot(env): string`＋`workspaceRoot`＋`EXPORT_ROOT_ENV_VAR`）、`export_root.d.ts`、`src/utils/paths.ts`（module-load 時解析 `publishExportDir`）、`src/utils/exportData.ts` 的 `loadStats(exportRoot, requiredLocales)`；`components/Footer.astro` 與 `pages/[lang]/stats.astro` 顯示 `stats.last_export_run_timestamp`（要改讀 pointer）；`scripts/generate-posts.js` 用 `resolveExportRoot()` 讀 `<root>/<lang>/items/*.json`（resolver 回傳世代根即零改動）；`scripts/dev-fixture.js` 設 `SITE_PUBLISH_EXPORT_DIR` 指向 `tests/fixtures/publish_export/`（平面 fixture，要世代化）；Vitest：`tests/exportRoot.test.ts`、`tests/exportData.test.ts` 硬編碼平面佈局；`tests/generator.contract.test.ts` 自建 temp export root（＝世代內容平面形狀，**不需改**）。site 測試：`cd modules/site && npm test`。

## 3. B1 目標佈局與核心設計（已定，照此實作）

```text
data/publish_export/
  current.json
  generations/<generation-id>/{stats.json, meta.json, <lang>/{index.json, items/, archives/}}
```

- generation id：`YYYY-MM-DDTHH-MM-SSZ`（run logical clock 的 ISO，`:`→`-`），同秒碰撞加 `-r2`/`-r3`。
- `current.json`：`{generation, export_completed_at, last_successful_run_at, languages, content_fingerprint}`。無變更 run 僅原子刷新 `last_successful_run_at`。
- `meta.json`：`{generation, created_at, content_fingerprint, aggregate_file_hashes: {<rel>: "sha256:<hex>"}}`——B1 只收聚合檔（stats.json、各 lang index.json、archives/index.json、archive_*.json）；item 檔雜湊留給 B2。
- `content_fingerprint`：`sha256-exportstate-v1:<hex>`；輸入＝header（algorithm|coverage_policy|latest_limit|archive_granularity|逗號串接語言）＋固定順序每 artifact `rel_path\0sha256hex(artifact_bytes)\0`（digest 組合代替原文餵入，記憶體有界；為 v1 自定版本化演算法的內部定義，外部僅視為不透明字串）；**stats.json 以剔除 `last_export_run_timestamp` 的 dict 序列化入 hash**（排除一切 run wall-clock；`published_at` 是 DB 值要入 hash；manifest `updated_at` 以計畫態入 hash）。
- 固定 artifact 順序：每語言（config 順序）`index.json` → `archives/index.json`（**一律寫入，空為 `[]`**，審查補充 8）→ archive 檔（月份 ASC）→ `items/<slug>.json`（slug ASC）；最後 stats.json。每語言 `items/`、`archives/` 目錄一律建立（含空世代）。
- **archive `updated_at` stamping 優先序**：rebuild→run_ts；metadata 列缺失→run_ts（heal 語意）；現行世代 `meta.json` 雜湊相符→沿用 DB 值；缺雜湊→與 fallback root byte-compare（相符沿用／不符 run_ts）；否則 run_ts。fallback root＝pointer 世代根；無 pointer 且有平面樹時＝export root；皆無→全 run_ts。
- **雙 pass**：pass 1 串流全部 artifact 算 fingerprint（不落地、記憶體有界，批次大小 `config.execution_policy.batch_size`）；需建版時 pass 2 重新串流寫檔＋記聚合檔雜湊。item payload 批次查詢要在 `database.py` 新增 `fetch_published_payload_batch(lang, limit, offset)`（欄位同 `fetch_canonical_item_payload`＋`pr.slug`、`pls.published_at AS published_at`，`ORDER BY pr.slug ASC`）。
- **建版順序**：配置 generation id → staging 建完整世代（含 meta.json）→ `os.rename` 移入 `generations/` → 短交易套用 `publish_archive_metadata`（全部計畫 stamp upsert＋inactive 月份刪除）→ `os.replace` 切換 `current.json`（最後一步；tmp 檔＋PermissionError 有限重試後 fail-stop）→ retention（warn-only）。
- **語言收縮 metadata 刪除**留在 reconciliation 階段（無條件、無 compensation）；config 變更⇒fingerprint header 變⇒必建版，收斂不依賴刪除時機。
- **失敗模型**：fail-stop；live pointer 不動；DB 可超前；**刪除** `rollback_db_state`、`db_compensations`、per-file backup/rollback、`promoted_actions`、撤回逐檔清理、語言掃除。retention 只在 pointer 切換成功後執行；刪除遇佔用/唯讀→警告跳過；generation 目錄是 symlink/junction→警告跳過（沿用 `_is_symlink_or_reparse_point`，搬到 generation_store.py）。
- **Retention**：`generations/` 下按 id 排序保留最新 5 個＋**永遠保留 pointer 指向的世代**（審查缺口 2 的加強）。
- **遷移**（pointer 缺失＋`export_dir/stats.json` 存在）：以 reconciliation 後 DB plan 逐一比對平面 artifact bytes（stats 剔除 `last_export_run_timestamp` 後比 dict；其餘 byte-exact）；另要求平面側 `*.json` 集合（設定語言目錄＋stats.json）與 plan artifact 集合相等。全數相符→搬移設定語言目錄＋stats.json 入 `generations/<id>`（id 由平面 stats 的 `last_export_run_timestamp` 轉換，無效則視為驗證失敗），寫 meta.json（實際 bytes 雜湊）＋pointer（fingerprint＝plan 值；`export_completed_at`＝平面 stats 時間戳；`last_successful_run_at`＝run_ts）。任何不符/缺失→從 DB plan 建首個完整世代（bootstrap 建版路徑）。pointer 缺失且無平面樹→直接 bootstrap（空世代合法）。
- **ProcessLock**：`process_lock.py` 複製 curate 實作（msvcrt/fcntl、非阻塞），整個 run 全程持有；release 不刪檔（review round 2，inode race）。
- **corrupt pointer**：raise（fail-stop），不靜默當缺失。

## 4. 檔案更動清單與執行順序

### Step 1 — publish 核心（新增 4 檔、改 2 檔）
1. 新增 `modules/publish/src/process_lock.py`（ProcessLock，對齊 curate）。
2. 新增 `modules/publish/src/generation.py`：`FINGERPRINT_ALGORITHM`、`serialize_json_bytes`、`GenerationPlan`、`build_generation_plan(repo, config, current_hashes, fallback_root, run_ts, rebuild)`、`compute_content_fingerprint`、`iter_planned_artifact_bytes(plan, repo, config, normalized)`（fingerprint/寫檔/遷移驗證三用）、meta.json model。**不綁 clock**（run_ts 參數传入）。
3. 新增 `modules/publish/src/generation_store.py`：`read_pointer`（缺→None；JSON 毀損／欄位缺失／generation id 不符嚴格格式／指向世代目錄不存在→raise，審查補充 9）、`write_pointer_atomic`（tmp＋os.replace＋PermissionError 有限重試後 fail-stop）、`allocate_generation_id`（同秒 `-rN`）、`write_generation_to_staging`＋move（固定建每語言 items/、archives/；聚合檔雜湊入 meta.json）、`load_current_generation_hashes`（live 世代缺/毀 meta.json→raise）、`sweep_retired_generations(keep=5, protect=live)`、`_is_symlink_or_reparse_point`（自 orchestrator 搬入）、guarded rmtree（先掃 reparse point 再刪）、`discard_staging`（finally 用）。
4. 新增 `modules/publish/src/migration.py`：`flat_layout_present`、`verify_flat_tree`、`migrate_flat_tree`。
5. 改 `modules/publish/src/database.py`：新增 `fetch_published_payload_batch`；`insert_publish_record`／`upsert_publish_language_status`／`upsert_archive_metadata` 加 `now: Optional[str] = None`（審查補充 10；其餘不動）。
6. 重寫 `modules/publish/src/orchestrator.py`：保留語言警告、reconciliation diff、publish/withdraw DB 短交易（**移除所有 compensation 記錄**）、收縮語言 metadata 刪除；之後接 pointer/migration/bootstrap/狀態比對/建版/retention 脊樑；刪除 `rollback_db_state` 與整段 promotion/backup；facade re-export 與 `get_utc_now_iso8601` import 保留；logger 名 `publish.orchestrator` 不變；summary 鍵不變。
7. `cli.py` **不改**。

### Step 2 — publish 測試
8. `tests/support.py`：加 `live_root(export_dir)`（讀 current.json→generations/<id>）、`read_pointer`；`read_item/read_index/read_archive/read_manifest/read_stats` 改為經 live_root 解析（多數測試因此零改動）。FakeClock 不動。
9. 逐檔改寫（直接路徑 `self.export_dir / "zh" / ...` 全改走 `support.live_root(...)`）：
   - `test_publish.py`：路徑更新；`test_first_time_file_write_compensation`／`test_update_file_write_compensation`／`test_promotion_midway_failure_reversion` **改寫為收斂語意**（失敗後 DB 超前、無還原、live 世代不變、下次成功 run 收斂）；注入點改新 seam（patch `generation_store` 的寫檔/os.replace）；`test_rebuild_file_write_failure_divergence_prevention` 改為斷言舊世代仍 live。
   - `test_idempotency.py`：無變更 rerun → 世代 id 不變、**stats.json 不重寫（時間戳凍結）**、pointer `last_successful_run_at` 前進、DB 全凍結、全部 bytes 不變。
   - `test_aggregate_contracts.py`：manifest 生命週期語意保留（改/不變/heal/rebuild 全刷新）路徑改 live_root；heal 測試的 mtime 斷言改到新世代檔；`test_archive_metadata_rolls_back_on_promotion_failure` 改寫為收斂語意（metadata 已套用＋pointer 切換失敗→下次 run 重建收斂）。
   - `test_coverage_loss.py`：撤回/收縮/擴張語意保留（新路徑）；`assert_removed_language_artifacts_gone` 改斷言 live 世代無該語言；**junction 兩測試改為 retention 不跟隨 symlink/junction**；`test_shrink_rolls_back_on_promotion_failure` 改收斂語意；`lone_index`/`missing_directory` 兩測試改為「export root 殘留目錄惰性不影響世代」；`assets/` 保留兩測試不動（仍在 export root）。
   - `test_batching.py`、`test_index_archive_sorting.py`、`test_item_payload_contract.py`、`test_author_metadata.py`、`test_cjk_slug_fallback.py`：僅路徑改 live_root。`test_label_prefixes.py`、`test_config_validation.py`、`test_migrations.py`、`test_handoff_contract.py`、`test_cli_failures.py` 預期零或近零改動。
10. 新增 `tests/test_generation_pointer.py`：bootstrap（空/有資料）、無變更只刷 pointer、狀態比對觸發（DB 提交後建版失敗→下次收斂）、fingerprint 收斂（archive updated_at 前進後無變更 run 不誤建版）、rebuild 強制建版＋rebuild 後無變更 run 不建版、世代 id 格式/同秒後綴、pointer 原子性（sharing violation 舊指標有效＋fail-stop＋重試成功）、單寫者 lock、retention N=5＋live 保護＋junction 跳過＋刪除失敗僅警告、遷移（全相符搬移／DB 超前→bootstrap／非擁有目錄保留／缺 artifact→bootstrap）、meta.json 雜湊 stamping、corrupt pointer fail-stop。

### Step 3 — site
11. `src/utils/export_root.js`：拆 `resolveExportBase(env)`（原邏輯）；新增 `resolveExportContext(env)`（讀 base/current.json→驗證→`{generationRoot, pointer}`，缺/毀/世代目錄不存在→拋清晰錯誤）；`resolveExportRoot(env)` 改回傳 `resolveExportContext(env).generationRoot`。同步 `export_root.d.ts`。
12. `src/utils/paths.ts`：`const ctx = resolveExportContext(); export const publishExportDir = ctx.generationRoot; export const publishExportPointer = ctx.pointer;`
13. `components/Footer.astro` 與 `pages/[lang]/stats.astro`：「最後更新」改讀 `publishExportPointer.last_successful_run_at`；`loadStats(publishExportDir, ...)` 沿用。
14. fixture 世代化：`tests/fixtures/publish_export/` → `current.json`（generation `2026-07-22T03-00-00Z`，由 fixture stats 的 `last_export_run_timestamp` 轉換；fingerprint 用格式正確的固定佔位 hex）＋`generations/<id>/`（原平面內容搬入＋新寫 meta.json，聚合檔真實 sha256 用腳本算）。
15. `tests/exportRoot.test.ts` 改寫（base seam＋pointer 行為 temp-dir 矩陣＋fixture 佈局斷言改世代）；`tests/exportData.test.ts` 的 committed-fixture 段改指向 `generations/<id>`；`generator.contract.test.ts` 預期不動。

### Step 4 — 文件同批
16. `modules/publish/docs/`：DATA_CONTRACT.md（§2.3 措辭改「記錄最後一次內容變更的寫入」、§6 路徑改世代佈局、新增 current.json/meta.json/content_fingerprint 契約）、EXECUTION_POLICY.md（§4 失敗模型新語意、§6.1 改狀態比對觸發＋全量建版、lock/retention/bootstrap/遷移）、README.md、STATE_TRANSITIONS.md、TEST_COVERAGE_MAP.md（對齊新測試名）。
17. 頂層：`docs/DATA_LIFECYCLE.md`、`docs/STORAGE_AND_RETENTION.md`（世代保留 N=5）、`docs/SYSTEM_OVERVIEW.md`、`docs/MODULE_BOUNDARIES.md` §3.6（export emission → versioned generation emission）。
18. site docs（計畫漏列，補）：`DATA_HANDOFF_CONTRACT.md`、`BUILD_AND_ROUTING_POLICY.md`、`README.md`。
19. `modules/api/docs/API_CONTRACT.md`＋`MODULE_PROPOSAL.md`：前置條件引用 v5→v7、fingerprint 驅動「僅內容變更才換 generation」語意（計畫列為同批非阻擋項）。
20. 主計畫文末追加「Phase B1 實作紀錄」（含本筆記 §1 的偏差/加強清單，供獨立複審）。

### Step 5 — 驗證
21. `py -3 -m pytest modules/publish/tests -q` 全綠。
22. `cd modules/site && npm test` 全綠（必要時 `npm run type-check`）。
23. 真實資料演練（sandbox 複本，不動 `data/` 本體）：複製 `data/canonical.db`＋`data/publish_export` 到 temp → 首次 run 走遷移（比對世代內容 vs 平面 bytes，應僅 stats 時間戳語意差異）→ 二次 run 無變更不建版（世代 id 不變、pointer 刷新）→ rebuild 建新版 → retention 生效。

## 5. 狀態宣告（2026-08-18 最終更新）

**Phase B1 已全部完成並通過全量驗證，無待辦事項。** 本筆記剩餘價值僅供 code review 對照設計定案（§0.1 落地清單、§1 偏差/加強十一條、§3 設計細節）——但給審查者的正式紀錄以主計畫文末「Phase B1 實作紀錄（2026-08-18）」為準（含 11 項偏差清單與真實資料演練的 CRLF 發現）。review 完成後由站主決定本筆記刪除或歸檔。若未來 session 在 review 後需要修改程式碼，先讀主計畫實作紀錄與 `modules/publish/docs/`（v3.0/v2.0 起）確認現行契約，再動手。
