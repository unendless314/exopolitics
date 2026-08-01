# Publish 模組測試可維護性補強計畫

**狀態：** 已實施（2026-08-01，Phase 0–4 全部交付；詳見文末「實作紀錄」）  
**日期：** 2026-07-30  
**修訂：** 2026-07-31，納入文件審查與已定案的 metadata 型別、archive manifest persistence、測試 helper、語言集合轉換、CJK slug fallback 與 handoff test 分層  
**範圍：** `modules/publish/` 的 Python 測試、測試支援結構、fixture 與可直接驗證的 publish 契約  
**非範圍：** 本方案不改變 publish eligibility、slug、翻譯內容、撤回語意、資料狀態機、模組邊界或 CI 平台選型。除非本文件明確列出，測試補強不得夾帶 production behavior 變更。

## 1. 背景與目標

`publish` 是由 canonical database 產出公開 JSON artifact 的下游邊界，負責 strict-match eligibility、slug 凍結、語言別 item export、index/archive/stats 聚合、撤回同步，以及檔案與資料庫狀態的補償回復。這些責任使測試除了驗證正常輸出，還必須保護資料契約、可重複執行性與失敗時不產生公開輸出或 publish-layer state 漂移的要求。

2026-07-30 的盤點確認，現有測試已能保護多個高風險路徑，但有歷史敘述未同步、測試 harness 重複，以及若干文件已鎖定但未被直接斷言的邊界行為。本計畫的目標是：

1. 保留已有效保護的 publish / rollback / payload 契約，不因測試整理而降低覆蓋。
2. 移除測試中已過期的「預期失敗」與 Phase 3 前提，使測試文件描述現行行為。
3. 收斂重複的 temporary SQLite schema 與 seed 邏輯，降低上游 handoff schema 漂移風險。
4. 補上 idempotency、排序、coverage loss、設定驗證、metadata 失敗路徑、aggregate artifact 與分批處理的直接 regression tests。
5. 將每個 production behavior 修正限制在有先行或同批 regression test 的小變更中。

## 2. 已確認基線

- 模組測試指令：

  ```powershell
  python -m pytest modules/publish/tests -q
  ```

- 盤點時結果：27 項 tests、45 項 subtests 全數通過。
- 同一套件已連續執行 10 次，全數通過，未發現目前已知 flaky test。
- 主要測試檔：
  - `modules/publish/tests/test_publish.py`
  - `modules/publish/tests/test_item_payload_contract.py`
- 主要實作與契約：
  - `modules/publish/src/orchestrator.py`
  - `modules/publish/src/database.py`
  - `modules/publish/src/config.py`
  - `modules/publish/docs/DATA_CONTRACT.md`
  - `modules/publish/docs/EXECUTION_POLICY.md`
  - `modules/publish/docs/STATE_TRANSITIONS.md`

既有測試已覆蓋：

- slug collision、ASCII fallback 與 frozen slug；
- strict-match 對 missing、failed、stale、fingerprint-mismatched translation 的阻擋；
- upstream withdrawal、re-publication、full rebuild 與 archive cleanup；
- item payload 的 `summary_short`、semantic `bullets`、`publish_link` null bullets、author metadata 與 presentation-label leakage；
- 第一次輸出、更新輸出及 promotion 中途失敗時的 database/file-system compensation；
- target-language warning 的 command 範圍；
- 部分 CLI command success surface，以及 index latest-limit。

本基線只代表 `publish` 可單獨測試，並不取代後續跨模組 collection 修復或 CI gate。

## 3. 已知測試維護風險與缺口

### 3.1 新版 payload 測試仍保留已過期的 Phase 3 描述

`test_item_payload_contract.py` 的 module docstring 與多個 class docstring 仍聲稱 target runtime「預期失敗」、實作「仍使用 legacy content 欄位」，或將舊測試列為「Phase 3 disposal list」。實際上 translation label leakage refactor 已結案，structured payload runtime 已存在，該測試也已通過。

這是文件與程式漂移，容易讓後續維護者誤判測試預期、錯誤地保留 legacy transition code，或把真正失敗視為可接受。

應處理：

1. 將敘述改為描述現行已鎖定的 runtime contract。
2. 移除已完成 phase 的未來式與「expected to fail」用語。
3. 不因註解更新而改變 assertion 或刪除既有 coverage。

### 3.2 兩套 temporary SQLite upstream schema 與資料工廠重複

`test_publish.py` 的 `create_mock_upstream_tables()` / `seed_data()`，以及 `test_item_payload_contract.py` 的 `create_five_column_upstream_tables()` / `seed_item()`，都手動宣告 `source_item`、`approved_content_record`、`translation_output` 與 `curation_decision` 的最小 SQLite schema，並建立近似資料。

兩套 upstream table DDL 目前逐字相同，且都採五個 structured content 欄位；現有差異在 seed helper 的預設 timestamps、可覆寫參數及 `publish_link` 支援。風險是未來 schema 或資料契約變動時兩份手動 DDL 可能漂移，而不是現有 DDL 已經不一致。只要 translate、curate 或 ingest 的 handoff schema 變動，其中一套 mock schema 可能繼續讓 publish 測試通過，卻不再代表真實交接。

應處理：

1. 建立 publish tests 專用的 shared helper，集中 temporary DB、upstream schema、publish migration、config 與基本 seed。
2. 讓 seed 明確接受每種語言的 `display_title`、summary/bullets、translation status、fingerprint，以及可包含任意 configured language 的 translation rows；同時讓 caller 指定 curate status、downstream action 與 author metadata，不隱藏測試前提。
3. 將特殊測試資料維持在各自 test case，不把所有情境塞入高度抽象的 builder。
4. 不把其他模組的內部 test helper 當作 dependency，也不建立 runtime coupling。
5. 將新的 shared schema 與目前 active upstream docs/migrations 對照，另以最小 handoff test 驗證真正需要的欄位與 FK 行為。

### 3.3 舊整合測試與新版 structured-payload 測試有重疊

兩個測試檔分別涵蓋 strict-match、frozen slug、author metadata、rebuild 和撤回的相關面向。重疊本身不是 bug，且 `test_publish.py` 仍含有重要的 compensation、CLI 與 promotion failure coverage，不能直接刪除。

風險在於新案例持續加到兩套相似 seed 與相似 end-to-end workflow，未來會讓同一契約有不同前提或不同 assertion。

應處理：

1. 先建立 coverage map，列出每個現存 test 保護的規則與唯一 assertion。
2. 將 structured export 的正常與失敗 payload cases 以新版 five-column fixture 為主。
3. 保留 old suite 中尚未被取代的 CLI、failure compensation、promotion rollback 與 operational behavior。
4. 只有在新測試完整保護同一規則，且保留測試沒有獨有 assertion 時才合併；刪除前應於 PR 說明替代 node ID。

### 3.4 Idempotency 僅驗證大致結果，未鎖定不可變觀察值

`EXECUTION_POLICY.md` 要求 unchanged rerun 安全，並規定 unchanged item 可避免重寫。現有 `test_rebuild_and_idempotency` 實際上先撤回另一筆 item，再驗證其 cleanup；它沒有直接對同一份不變 upstream state 執行兩次 incremental run，檢查 publish timestamps、publish record `updated_at`、language status timestamps、輸出 bytes 和 summary 是否完全不變。

應新增 direct test：

1. 對一個已完整 publish 的 item 連續執行兩次不變的 incremental run。
2. 第二次的 `published_count` 與 `withdrawn_count` 都應為零。
3. 凍結並比對 `publish_record.updated_at`、`publish_language_status.published_at`、`source_fingerprint`，以及 item/index/archive 預期欄位。
4. 直接比對不含 `stats.json` 的 unchanged artifact bytes。`stats.last_export_run_timestamp` 已確認由每次成功 run 寫入，符合其「last export execution」的資料契約；idempotency test 應把它當作 volatile ISO-8601 field，而不能要求完整 `stats.json` bytes 不變。若需斷言兩次值確實不同，測試必須注入或 mock clock，避免同秒 timestamp 造成 flake。

此項不應將每次 run 的 stats timestamp 變動誤判為 item publish state 或歷史 archive 被改寫。

### 3.5 index 與 archive 的排序規則沒有直接測試

`DATA_CONTRACT.md` 要求 index 和 monthly archive 都依 `source_published_at DESC, slug ASC` 排序，並明確禁止以 `published_at` 作為排序或 sharding 依據。現有 archive 測試確認個別 item 存在，但沒有製造同 timestamp 的 deterministic tie，也沒有確認 index 與 archive 同時符合完整排序規則。

應新增：

1. 至少三筆資料，其中兩筆有相同 `source_published_at` 但 slug 不同，另一筆日期較新或較舊。
2. 分別驗證每種 configured language 的 index 和對應 month archive 排序。
3. 變更 publish 時間而不變更 source time，確認 source time 仍是唯一排序與 archive month 依據。
4. 將 `latest_limit` 設成會截斷結果的值，驗證截斷發生於正確排序之後。

### 3.6 strict-match coverage loss 的完整撤回面未被直接鎖定

文件要求已發布 item 若任一 configured language 缺失、failed、stale 或 fingerprint 不匹配，所有公開語言 artifact 均撤回。目前測試驗證這些條件阻止首次 publication，但沒有直接測試「已發布後失去其中一種 required language」的全套同步結果。

應新增：

1. 先以所有 required languages publish。
2. 將其中一種語言改為 failed、移除 row，或改成 stale fingerprint，每個原因至少覆蓋一種。
3. 斷言所有已發布語言的 item files 都被移除，`publish_language_status` 均為 withdrawn，並保留原 `published_at`。
4. 斷言 index、affected monthly archive、archive manifest 與 stats 均排除該 item。
5. 還原完整 coverage 後，斷言同 frozen slug 可重新公開，且 `withdrawn_at` 符合 state transition 的保留語意。
6. 以原本已完整公開的多語言 item，將 `target_languages` 從 `zh/en/ja` 縮減為 `zh/en`，斷言只撤回 `ja` artifact/status，既有 `zh/en` artifact 與 publish timestamps 維持不變。
7. 將 required language set 再擴增一種語言，分別驗證：新語言缺 completed current-fingerprint translation 時，strict-match 會撤回整個 item；新語言已完整翻譯時，新增該語言 artifact 且既有語言維持 published。

此 config-driven 行為是現行 strict-match reconciliation 的結果，應在 `DATA_CONTRACT.md` 或 `EXECUTION_POLICY.md` 明確記錄 language-set 變更會在下一次 run 同步公開 artifacts，避免把高影響的 config edit 視為純顯示設定。

### 3.7 config validation 與 CLI failure surface 覆蓋不足

`config.py` 對 `target_languages`、`coverage_policy`、`batch_size`、`latest_limit` 和 `archive_granularity` 有明確 validation。現有 CLI 測試主要驗證成功指令與正常 summary，尚未直接保護不合法設定、遺漏 key、非 mapping YAML、database/table 缺失、validate command 對缺少 configured completed language 的 blocking failure，以及 run/rebuild 只應 warning 的差異。

應新增：

1. Pydantic/config loader direct unit tests，涵蓋空 language mapping、未知 coverage policy、零或負 batch/index limits、非 month granularity、missing required sections、非 mapping YAML。
2. CLI `validate` 對不存在 database、缺 `translation_output` table、configured target language 無 completed translation 的 nonzero exit code 與錯誤訊息。
3. CLI `run` / `rebuild` 在缺少 configured language 時僅 warning，strict-match item 不會被公開，且 command 不將它錯誤視為 structural config failure。
4. 所有 CLI case 必須使用 temporary config、temporary DB 和 temporary export dir，不讀寫 workspace canonical DB 或 `.env`。

### 3.8 author metadata 的 invalid input matrix 不完整

既有測試已鎖定 human/hybrid 缺 editor 的拒絕，也測試 machine disclosure note；但 `DATA_CONTRACT.md` 與 `EXECUTION_POLICY.md` 另要求 author metadata 不得為 NULL、必須為 JSON object、必含 `source_module` / `writer_type`，且 writer type 受 allowlist 限制。

應以 table-driven 或 subtest matrix 補齊：

- `NULL`；
- malformed JSON；
- JSON string、array、number 等非 object；
- 缺 `source_module`；
- 缺 `writer_type`；
- 未知 `writer_type`；
- `source_module` 的 `null`、number、空字串與只含 whitespace；
- `human` 與 `hybrid` 的 editor 為 `null`、number、空字串或只含 whitespace；
- `AI` / `machine` 的正確 disclosure note。

每個失敗 case 都應確認 item file 不會出現，且新 item 不留下 published state。若 case 在已有 item 的 update 流程中觸發，另確認既有公開 artifact 與 DB state 由 compensation 回復。

`source_module` 已定案為必填且 trim-non-empty string。`human` / `hybrid` 的 `editor` 採相同型別與空白規則，必須是 trim-non-empty string，不得將 number 或其他 JSON 型別以 `str()` coercion 後接受。現有 Markdown 文件與 runtime 對 `source_module` 僅要求 key 存在，而 editor runtime 會 coercion；fixture JSON schema 雖對兩者要求 string、但 `source_module` 仍允許全空白。這些面向必須在同一小批修正並以 regression test 鎖定。

### 3.9 aggregate artifact 契約只覆蓋少數欄位

現有測試確認 index 的 item 排除、空 archive 刪除，以及少數 stats 計數，但未完整驗證 `archives/index.json` 的 `file_name`、`item_count`、`updated_at`，以及 `stats.json` 的 per-language active/withdrawn/latest-index/archive-month/oldest-month 和 run timestamp 欄位。

`archives/index.json.updated_at` 已定案採用 `DATA_CONTRACT.md` 的定義，即該 monthly archive file 最近一次成功寫入或更新的 UTC ISO-8601 時間。現行 implementation 從 active published rows 計算 `MAX(published_at)`，撤回一筆 item 並重寫 archive 時仍可能產生舊的 publication timestamp，無法代表這次檔案寫入；查詢中的 withdrawn `CASE` 分支也因只選擇 `publish_status = 'published'` 的 rows 而不可達。

此修正不得以檔案系統 mtime 作為對外契約來源，因其精度與跨平台行為不穩定。implementation 應在 archive promotion 成功後，以 publish-owned state 或等價的可重建 metadata 記錄該 archive 的 logical write timestamp，並讓 manifest 從該來源讀取。archive 因撤回變空而刪除時，對應 timestamp metadata 也必須刪除；日後同月 archive 重新建立時，以新的 logical write timestamp 建立新 metadata。若需要新增 persistence，migration、rollback/compensation 與 full rebuild 對所有 archive 的 timestamp 語意必須和 regression test 同一小批交付。

應新增：

1. 多語言、多月份、包含已撤回 item 的 fixture。
2. 驗證 archive manifest 的月份排序、filename format、exact item counts 和所有非空 month 的 existence；以 injected/mock clock 斷言首次建立、withdrawal-driven rewrite、full rebuild、empty-archive deletion/recreation 與 unchanged incremental run 的 precise `updated_at` 行為。
3. 驗證 stats 每個 configured language 均有 key，零值與 null oldest month 的呈現一致。
4. 驗證 `latest_index_count_by_language` 是 `min(active_count, latest_limit)`，不從磁碟掃描推導。
5. 避免將實際 UTC 字串硬編碼為固定值，應驗證 ISO-8601 format、或注入 clock 後驗證準確值。

### 3.10 分批與增量 archive 範圍沒有真正跨 batch 驗證

目前 `test_archive_index_batching_limit` 使用五筆資料，但 batch size 大於資料量，沒有跨頁或跨 batch，也未確認 incremental run 不重寫非受影響的 archive。這無法驗證 `EXECUTION_POLICY.md` 的 bounded processing 和增量 archive policy。

應新增：

1. 以 `batch_size=1` 或 2、資料量超過 batch size 的 fixture，驗證 index 和 archive 不遺漏、不重複、維持排序。
2. 準備兩個月份，僅更新或撤回其中一月的一筆 item。
3. 記錄另一個歷史 archive 的 bytes 或 mtime，並驗證 incremental run 後保持不變。
4. 在不 mock SQL 結果的前提下驗證 observable output；不應以實作內部 `offset` 次數等脆弱細節取代契約。

記憶體上限本身不宜由 unit test 以 process memory 數字斷言。此處的測試應保護跨 batch 完整性與未觸及 archive 的不變性，實際 profiling 留給獨立效能驗收。

### 3.11 label-prefix 資料在 implementation、schema 與 test helper 間重複

presentation labels 同時存在於 `orchestrator.py`、payload JSON schema 及 test helper。固定 invalid fixture 已能保護部分 zh/ja/English 前綴，但三份清單日後可能不同步。

此項不要求在本測試補強中立刻建立新的 runtime shared module。工程師應先決定 schema 與 runtime validation 的 canonical ownership，再以測試鎖定兩者接受/拒絕結果一致。至少應：

1. 保留固定 valid/invalid fixtures，避免測試直接從 production label tuple 生成而失去獨立檢查價值。
2. 對每個已文件化 label variant 建立或集中為 parameterized validation cases。
3. 驗證 Markdown emphasis、leading whitespace、ASCII colon 與 fullwidth colon 的處理一致。

### 3.12 CJK 標題的 slug fallback 只受 unit-level 部分保護

現有測試只斷言 `slugify("中文") == ""`，但沒有透過 `orchestrate_run()` 驗證空 slug 會在 `generate_slug()` fallback 成 `"item"`、碰撞時依序成為 `"item-2"`，以及這些 slug 在後續 translation title 變更後仍被凍結。這是 route identity 的可觀察契約，應以 end-to-end case 保護，而非只保護 helper 的中間結果。

應新增：

1. 將第一筆 item 的 English slug source 設為全 CJK title，驗證各語言 artifact 共用 `"item"` slug。
2. 再 publish 第二筆全 CJK title，驗證 deterministic collision suffix `"item-2"`。
3. 修改 translation title 與 fingerprint 後重新 publish，驗證兩筆既有 fallback slug 仍保持 frozen。

## 4. 分階段執行方案

### Phase 0：基線、coverage map 與歷史敘述清理

目的：在不改變 runtime behavior 的前提下，讓測試現況可審查且文件描述正確。

工作：

1. 執行並記錄 §2 的 baseline command。
2. 建立測試 coverage map，對應 `IMPLEMENTATION_PLAN.md` 十個 test focus、payload contract 和失敗補償測試。
3. 更新 `test_item_payload_contract.py` 的 historical comments，移除已過期的 expected-failure 與未來 Phase 3 描述。
4. 標記 `test_publish.py` 中每個仍有獨有 protection 的 test，特別是 CLI、first/update file failure、rebuild failure 及 promotion rollback。
5. 不刪除任何 test，不改 production code，不改測試語意。

驗收：

```powershell
python -m pytest modules/publish/tests -q
1..5 | ForEach-Object { python -m pytest modules/publish/tests -q }
```

預期：測試數量和 assertion 語意不減少，重複執行沒有 flake。

### Phase 1：shared test support 與現有 coverage 收斂

目的：消除 duplicate temporary schema 和 seed 的長期漂移風險，保留所有現有保護。

工作：

1. 新增 publish-test-local helper，例如 `modules/publish/tests/support/` 下的 temporary database、seed 和 artifact read helpers。
2. 將兩份 upstream schema 定義收斂為一份 five-column structured-content schema。
3. 將設定產生器集中，允許測試覆寫 target languages、batch size 和 latest limit。
4. 將兩個 test files 遷移到 shared helper，保留各 test 的 domain-specific input 與 assertion。
5. 依 Phase 0 coverage map 合併確實重複、且沒有獨有 assertion 的 cases。每個移除的 node ID 必須有替代 node ID。
6. 將 shared helper 定位為 publish-owned minimal schema 的 unit/integration test support，不替代 Phase 4 以真實上游 migration 驗證 handoff 的 contract test。
7. 不從 production modules import test-only helpers，也不讓 test helper 變成 runtime package dependency。

驗收：

```powershell
python -m pytest modules/publish/tests -q
python -m pytest modules/publish/tests --collect-only -q
1..10 | ForEach-Object { python -m pytest modules/publish/tests -q }
```

預期：所有測試使用 five-column schema；collection 總數不因重構減少，除非替代關係已明確記錄並經審查。

### Phase 2：核心 state、idempotency、排序與 aggregate contracts

目的：直接保護公開輸出最容易因重構而漂移的 observable contracts。

工作：

1. 加入 §3.4 unchanged rerun 的 database/output idempotency test。
2. 加入 §3.5 index/archive sorting and latest-limit tests。
3. 加入 §3.6 published-to-incomplete-coverage withdrawal 和 recovery test。
4. 加入 §3.6 的 configured-language shrink/expand matrix，並同步補足 language-set 變更的 module contract 說明。
5. 加入 §3.9 manifest/stats 的多語言、多月份、withdrawn-item matrix，並以 injected/mock clock 鎖定 archive manifest `updated_at` 的建立、重寫、empty-archive deletion/recreation、full rebuild 與 unchanged-run 行為。
6. 讓 tests 明確區分 `source_published_at`、`approved_at`、`published_at` 的各自責任。
7. 加入 §3.12 的 CJK slug fallback、collision 與 frozen-slug end-to-end tests。
8. 對 unchanged rerun 的 `stats.last_export_run_timestamp` 驗證其 ISO-8601 format，並在注入或 mock clock 時驗證每次 run 的準確值，而不要求完整 `stats.json` bytes 不變。
9. archive manifest timestamp 修正屬 production behavior change，必須以先行或同批 regression test 實作；若採新增 publish-owned persistence，同一小批亦須覆蓋 migration、promotion failure rollback、empty-archive metadata cleanup/recreation 與 full rebuild。

驗收：

- 每條 state transition 都驗證 DB、item artifact 和 aggregate artifact。
- 所有排序以 source timestamp 和 slug tiebreaker 斷言。
- zero-count language 與 empty archive 的輸出規則有直接 test。
- 不使用真實 sleep、clock race 或檔案列舉順序作為 assertion。

### Phase 3：validation、CLI failure surface 與 payload failure matrix

目的：將不能公開的設定與資料在 boundary 前可靠阻擋，並防止 CLI 行為漂移。

工作：

1. 實作 §3.7 config loader 和 CLI invalid-path tests。
2. 實作 §3.8 author metadata validation matrix。
3. 補齊 `display_title`、`summary_short`、route component、`downstream_action` 和 bullets contract 的 runtime rejection paths，優先利用既有 JSON fixtures；同一小批將 `source_module` 及 human/hybrid `editor` 改為 required、string 且 trim-non-empty，移除 editor 的 JSON 型別 coercion，並同步更新 `DATA_CONTRACT.md`、`EXECUTION_POLICY.md`、payload schema 與 runtime validation。
4. 將 §3.11 label-prefix 變體 parameterize，並維持 schema fixtures 與 runtime validation 的獨立檢查。
5. 對 first publish 和 existing publish update 分別驗證 validation failure 後的 DB/file outcome。
6. 不為測試而放寬任何 payload validation；若發現文件與實作不一致，先記為明確決策，再在同一小批更新文件、程式與 regression test。

驗收：

- 每一 invalid case 有明確錯誤 surface，且不寫出新的 public artifact。
- CLI test 不觸及 workspace data 或 secret。
- `validate` 的 blocking behavior 與 `run` / `rebuild` 的 warning-and-skip behavior 有明確區隔。

### Phase 4：batch、incremental-scope 與 migration/handoff contract

目的：補強大量資料與跨模組資料表演進下的可維護性，但不將 profiling 偽裝成 unit tests。

工作：

1. 實作 §3.10 超過 batch size 的 index/archive correctness test。
2. 驗證 incremental run 只改動 affected month，未受影響 archive 保持原 bytes。
3. 為 `run_migrations()` 的 re-run idempotency、失敗 migration rollback、`split_sql_statements()` 註解與多 statement cases、transaction rollback 建立 direct tests。
4. 使用 active upstream migration/schema contract 建立 temporary DB，驗證 publish 實際讀取所需欄位與 FK cascade，不直接依賴其他模組的 test internals。
5. 僅測試 publish 的 documented read dependencies，不在此 phase 擴大成全 pipeline integration suite。

驗收：

- batch size 小於資料筆數時，output 完整、無重複、排序正確。
- 非受影響 archive 不被重寫。
- migration failure 不留下 migration marker 或 partial DDL。
- temporary handoff DB 能通過 publish migration 和最低限度 publish path。

## 5. 建議的提交與驗證方式

每一 phase 都應可獨立審查與合併。測試 support 的機械整理不應混入 state machine、SQL 或 public JSON 行為修改。若補強測試揭露 production bug，應先提交可重現的 regression test，接著在同一小批提交最小修正與其所需文件更新。

各 phase 最少執行：

```powershell
python -m pytest modules/publish/tests -q
```

涉及檔案 promotion、compensation、sorting、batch 或多筆資料操作時，額外執行：

```powershell
1..10 | ForEach-Object { python -m pytest modules/publish/tests -q }
```

涉及 publish / site artifact handoff 時，另依 site 模組既有 test command 執行其 contract tests。此項不應使用跨全部 Python modules 的單一 pytest invocation，因 repository 仍有已知 `tests` package collection collision，須待後續跨模組收集工作處理。

在提交前，工程師應檢查：

```powershell
git diff -- modules/publish/tests modules/publish/src modules/publish/docs known_issues/resolved/PUBLISH_TEST_MAINTAINABILITY_PLAN.md
```

任何 production code、schema 或 module doc 變更都必須能對應本計畫的明確測試或正式審查決策。

## 6. 完成定義

本計畫完成時，應滿足：

1. 測試中的 historical commentary 已描述現行 structured payload runtime，沒有仍聲稱已完成功能「預期失敗」的內容。
2. temporary upstream schema、seed/config 與 artifact reader 已收斂為 publish tests 的最小 shared support，且沒有降低既有 compensation/CLI coverage。
3. unchanged rerun、sorting、strict-match coverage loss、republish、manifest、stats、empty archive 與 multi-language aggregates 都有直接 deterministic tests。
4. config language-set shrink/expand 對 strict-match eligibility、per-language withdrawal 和 public aggregate artifacts 的影響已有直接 tests 與 module contract 說明。
5. invalid config、CLI command mode 差異、author metadata 型別/空白規則、payload validation 和 label-prefix leakage 都有清楚的 failure tests，且不產生新的公開 artifact 或 publish state divergence。
6. batch size 小於資料量及增量 archive scope 都受 observable output tests 保護。
7. publish-local minimal shared schema 僅用於 unit/integration test support；另有至少一個 temporary handoff contract test 依資料流順序套用真實 upstream migrations，驗證 publish 的 documented read dependencies，不使用其他模組的 private test helpers。
8. publish tests 在重複執行抽查中穩定通過，沒有新增 flaky failure。

## 7. 已定案的契約與技術結構

### 7.1 Archive manifest `updated_at`

採「該 monthly archive file 最近一次成功寫入或更新的 UTC ISO-8601 時間」。此值不是 item 的 `published_at`，也不能從 active rows 的 aggregate 推導。

實作與測試規則：

1. 首次建立 archive、撤回導致 archive rewrite、內容修正導致 archive rewrite，以及 full rebuild 寫出 archive 時，該 archive 的 `updated_at` 使用當次成功 write/promotion 的 logical clock。
2. 未觸及該 archive 的 incremental run 必須保留其既有 `updated_at`，即使 `archives/index.json` 或 `stats.json` 會在該 run 重建。
3. archive 因撤回變空而被刪除時，manifest entry 與對應 publish-owned timestamp metadata 一併刪除；日後同月 archive 重新建立時，使用新的 logical write timestamp。
4. tests 注入或 mock clock，避免檔案 mtime 精度與同秒時間造成不穩定 assertion。

### 7.2 `author_metadata.source_module` 與 `editor`

`source_module` 採「必填、JSON string、trim-non-empty」。human/hybrid `editor` 採相同規則；AI/machine writer type 不要求 `editor`。缺 key、非 string、空字串或只含 whitespace 都必須在 publish payload boundary 被拒絕。

實作與測試規則：

1. 同一小批同步更新 `DATA_CONTRACT.md`、`EXECUTION_POLICY.md`、item payload JSON schema 和 `validate_item_payload()`，並移除 editor 的 `str()` coercion。
2. JSON schema 使用能拒絕全空白的 pattern，而非僅 `minLength: 1`。
3. direct validator fixture matrix 分別覆蓋 `source_module` 與 human/hybrid `editor` 的缺 key、`null`、number、empty string 與 whitespace-only string。
4. end-to-end cases 驗證 first publish 不遺留 published state；existing published item 的 invalid update 會保留原 DB 和 public artifact。

### 7.3 Shared test support 位置

採用顯式匯入的 `modules/publish/tests/support.py`，不使用 `conftest.py` 隱式 fixture。此 module 只提供 publish tests 的 temporary DB、minimal schema、config factory、seed 和 artifact reader；seed 必須支援任意 configured language 的 language-specific `display_title`、structured content 與 translation state。各 test case 仍須明確傳入重要狀態與內容，避免通用 fixture 隱藏前提。

### 7.4 舊整合測試的收斂準則

Phase 0 coverage map 完成前不刪除任何既有 test。後續僅在新 test 完整取代同一條規則，且原 test 沒有 CLI、rollback、promotion failure 或其他獨有 assertion 時才合併。每個移除的 node ID 必須在 PR 說明中列出其替代 node ID。

### 7.5 真實 migration handoff test

採兩層結構：

1. `tests/support.py` 的 minimal schema 隔離 publish unit/integration tests，並明確標示其不是 canonical upstream contract。
2. 獨立 handoff contract test 依資料流順序，在 temporary DB 套用 active `ingest`、`curate`、`translate` 與 `publish` migrations，驗證 publish documented read dependencies、必要 FK 與最小 publish path。

該 handoff test 不得引用其他模組的 private test helpers，也不應擴大為全 pipeline integration suite。

## 8. 實作紀錄（2026-08-01）

Phase 0–4 已全部交付。測試規模由 27 tests / 45 subtests 增至 86 tests / 545 subtests，連續 10 次完整執行全數通過；site 模組 contract tests（36 tests）亦全數通過。

實作期間的主要技術決策：

1. **Archive manifest `updated_at`（§3.9、§7.1）**：新增 `v002_archive_metadata.sql`，建立 publish-owned `publish_archive_metadata` 表記錄每個 monthly archive 的 logical write timestamp；manifest 改從該表讀取。v001 既有 DB 缺少 metadata row 的 active month 會於下次 incremental run 重寫該 archive 一次，使新 row 記錄的仍是「確實寫入該檔案」的 run（self-heal by rewrite，見 §8.1）。rollback/compensation、empty-archive metadata cleanup/recreation 與 full rebuild 語意均由同批 regression tests 鎖定（`test_aggregate_contracts.py`，含 promotion failure rollback case）。
2. **Clock 注入**：採 `tests/support.py` 的 `FakeClock`，同時 patch `orchestrator` 與 `database` 兩個 namespace 的 `get_utc_now_iso8601`，不改變 production 函式簽名；不使用真實 sleep 或 wall-time assertion。
3. **Coverage map（Phase 0）**：落於 `modules/publish/docs/TEST_COVERAGE_MAP.md`，已列入 `docs/README.md` 索引。本輪未刪除任何既有 test node。
4. **`source_module` / `editor`（§3.8、§7.2）**：同一小批完成 runtime（移除 `str()` coercion、改為 trim-non-empty string 檢查）、payload JSON schema（`source_module` 改 `pattern: \S`）、`DATA_CONTRACT.md` 與 `EXECUTION_POLICY.md` 文件更新與 direct/e2e regression matrix（`test_author_metadata.py`）。
5. **新測試揭露的 production bug**：language-set shrink 時，被撤回語言若已不在 `target_languages`，affected-month 計算會對 `affected_months_by_lang[lang]` 產生 `KeyError`。已在 `orchestrator.py` 略過未配置語言（其 item artifact 撤回、aggregate 不再受管），並由 `test_coverage_loss.py::test_shrink_withdraws_only_removed_language` 鎖定。
6. **語言集合變更契約（§3.6）**：已於 `EXECUTION_POLICY.md` 新增 §6.2，明確記錄 shrink/expand 會在下一次 run 同步公開 artifacts。
7. **Shared test support（§7.3）**：`tests/support.py` 收斂 temporary schema、config factory、`seed_item`、artifact readers 與 `FakeClock`；`seed_item` 的 `INSERT OR REPLACE` 會 cascade 重置 publish state，已在 docstring 標明，state-transition 情境一律改用 targeted SQL。
8. **Handoff contract（§7.5）**：`test_handoff_contract.py` 依資料流順序套用 active ingest/curate/translate/publish migrations，驗證 documented read dependencies、FK cascade 與最小 publish path，未引用其他模組 private test helpers。

### 8.1 Code Review 後續修正（2026-08-01）

Code Review 提出兩項 P1，均確認為真實問題並已修復，測試規模增至 90 tests / 577 subtests：

1. **language-set shrink 未移除被刪語言的聚合 artifacts**（`orchestrator.py`）：原先只撤回 removed language 的 item JSON，`index.json`、monthly archives 與 archives manifest 仍留在 export tree 對外可見（rebuild 模式甚至連 item JSON 也殘留），且 `publish_archive_metadata` row 未刪。現改為：reconciliation 階段刪除未配置語言的 metadata rows（走既有 archive_meta compensation），promotion 階段在 run/rebuild 兩模式統一移除未配置語言目錄下的 publish-owned artifacts（含無 DB rows 的 orphan 目錄）。由 `test_coverage_loss.py` 的 shrink incremental/rebuild、orphan-dir 與 shrink rollback 四個 case 鎖定；`EXECUTION_POLICY.md` §6.2 同步改寫為明確移除全部公開 artifacts。
2. **self-heal 對未重寫的 archive 補蓋當下時間**（`orchestrator.py`）：v001 既有 DB 在 unchanged run 會以當次 logical clock 補記 missing metadata row，但該 run 並未寫入對應 archive，違反 `DATA_CONTRACT.md` §2.3「row 只在同 run 寫入檔案時建立/更新」。現改為 self-heal by rewrite：active month 缺 metadata row 時，於該 run 將其 archive 重寫一次（內容相同），metadata 記錄該次真實寫入；manifest 階段的補記分支改為 defensive `RuntimeError`。由 `test_aggregate_contracts.py::test_missing_metadata_heals_by_rewriting_archive_once` 以 mtime backdate 鎖定「重寫一次、內容不變、之後不再重寫」；`DATA_CONTRACT.md` §2.3/§6.4 與 `EXECUTION_POLICY.md` §6.1 同步更新。

另補：`TEST_COVERAGE_MAP.md` 更新至 v1.1，補齊 Phase 2b–4 新增的全部測試檔與本輪修正的 node。

### 8.2 Code Review 第三輪修正（2026-08-01）

Code Review 提出一項 P1，確認為真實問題並已修復，測試規模增至 92 tests / 579 subtests：

1. **stale-language sweep 誤刪非 publish 目錄**（`orchestrator.py`）：§8.1 新增的未配置語言清理把 export_dir 底下**所有**非隱藏目錄都當 removed language，會刪除與 publish 無關的 `assets/index.json` 等檔案，與程式註解「non-publish files are left alone」矛盾。現改為所有權證據門檻：目錄須滿足其一才清理 — (a) 該語言仍有 `publish_language_status` rows（withdrawn 狀態會留存，為 durable evidence），或 (b) 目錄帶有 publish 語言目錄結構（`items/` 或 `archives/` 子目錄，涵蓋 DB reset 後的 orphan 目錄）；兩者皆無的目錄不屬於 publish，一律保留。由 `test_coverage_loss.py` 三個 case 鎖定：unrelated 目錄保留、orphan 結構目錄清理、lone `index.json` 經 publish state 清理；`EXECUTION_POLICY.md` §6.2 與 `TEST_COVERAGE_MAP.md` 同步更新。

### 8.3 Code Review 第四輪修正（2026-08-01）

Code Review 提出兩項 P1，均確認為真實問題並已修復，測試規模為 93 tests / 582 subtests：

1. **generic 子目錄名稱不構成所有權證據**（`orchestrator.py`）：§8.2 的結構啟發式（`items/` 或 `archives/` 子目錄）會誤刪 `assets/items/customer.json` 這類無關目錄。現移除檔案系統啟發式，所有權證據僅剩 publish state（`publish_language_status` rows）；DB reset 後的殘留目錄改由契約處理 — export tree 是 derived artifact store，canonical DB reset 後應先清空再跑（`EXECUTION_POLICY.md` §6.2）。原 `test_orphan_language_directory_without_db_rows_removed` 由 `test_orphan_directory_without_publish_state_is_preserved` 取代，反向鎖定新契約。
2. **cleanup 會跟隨 junction/symlink 刪到 export tree 外部**（`orchestrator.py`）：removed-language 目錄若是指向外部的 junction，`os.replace` 會把 **target** 的檔案搬進 `.backup`（成功後即刪除）。現新增 `_is_symlink_or_reparse_point()`（`islink` 加上 Windows `st_reparse_tag`，因 junction 不是 symlink），destructive 路徑一律跳過並記 warning，涵蓋 sweep 與 incremental withdrawn-item cleanup 兩條路徑；DB 端 reconciliation 不受影響。由 `test_removed_language_junction_is_not_followed` 以真實 `mklink /J` 鎖定（非 Windows 平台 fallback `os.symlink`，無法建立連結時 skip）。

### 8.4 Code Review 第五輪修正（2026-08-01，審查人員直接修改）

前兩輪修正仍有兩個殘缺，審查人員取得授權後直接修改，經本人逐行核對後接受，測試規模為 95 tests / 583 subtests：

1. **Missing removed-language 目錄導致 shrink 中止**（`orchestrator.py`）：§8.3 的 `_is_symlink_or_reparse_point()` 對不存在的路徑執行 `os.lstat` 會拋 `FileNotFoundError` — operator 先手動刪掉已淘汰的語言目錄再跑 shrink 時，reconciliation 會在 guard 處整體失敗。修改為 `FileNotFoundError` 時回傳 `False`（不存在的路徑不是 link，後續 `.exists()` 檢查自然略過）。由 `test_shrink_allows_missing_removed_language_directory` 鎖定。
2. **巢狀 items/archives link 未被 guard 涵蓋**（`orchestrator.py`）：§8.3 只檢查頂層語言目錄；`ja` 為一般目錄但 `ja/items` 本身是 junction 時仍會穿透刪到外部。withdrawn-item cleanup 與 sweep 現皆檢查巢狀子目錄；sweep 對 link 子目錄跳過並記 warning，其餘可安全清理的 artifacts 照常移除。由 `test_removed_language_nested_junction_is_not_followed` 鎖定。

`EXECUTION_POLICY.md` §6.2 與 `TEST_COVERAGE_MAP.md` 由審查人員同步更新，內容與程式行為一致；本輪驗證（95 tests / 583 subtests、10 次連跑無 flake、site 36 tests、analysis 1 test）由本人獨立重跑確認。
