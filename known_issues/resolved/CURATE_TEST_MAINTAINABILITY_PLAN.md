# Curate 模組測試可維護性改善方案

**狀態：** 已定案，待工程師排程與實作  
**日期：** 2026-07-30  
**修訂：** 2026-07-30，依獨立審查補入 `DATA_CONTRACT.md` 的 upstream FK 文件漂移、withdrawn null-action 漏洞、validation-failure retry 決策、第三份手動 upstream fixture、一般 4xx 修正提前至 Phase 1，以及 CLI `.env`/lock-path 測試護欄；同日定案 DDL 與 repository 的雙層 null-action 防線、fallback unknown-key 寬鬆行為、validation-failure retry，以及 subprocess `ProcessLock` regression test  
**範圍：** `modules/curate/` 的 Python 測試、測試支援結構，以及可直接驗證的 curate 資料與執行契約  
**非範圍：** 不在本方案中改變 curation 政策、prompt 的業務語意、LLM provider 選型、重試上限、模組邊界或上游 ingest/classify 的正式 schema；本方案僅校正 curate 文件對既有 upstream handoff 的描述；不導入 CI，也不處理跨模組 pytest package 收集衝突

## 1. 背景與目標

目前 `curate` 模組有 15 項 pytest 測試，單獨執行可全數通過；`python -m modules.curate.src.cli validate` 亦可通過。既有測試已保護主要的 publish-summary 成功流程、transient failure 狀態、locked item 排除、部分 re-curation cleanup、forced re-run rollback，以及 withdraw/reapprove CLI 流程。

盤點發現，現行測試不能完整保護文件所定義的 LLM response contract、上游資料庫 handoff、完整狀態轉換、migration/transaction helper，以及 CLI 與執行政策的操作行為。其中有一項已確認的 validator 漏洞：缺少頂層 `editor_brief` 或 `curation_output` 的 response 可被錯誤接受。由於 active `mini-proxy` 設定採 `json_object` fallback，這個 validator 是模型輸出進入 canonical DB 前的最後一道本地 schema gate。

目標：

1. 優先修正並回歸測試 response validator 的頂層欄位缺失漏洞。
2. 修正一般 HTTP 4xx 被重試到上限的 production bug，避免 API key 或設定錯誤放大 request 與 rate-limit 成本。
3. 將手動複製且已與 active ingest schema 漂移的上游 DB fixture，替換或補強為 migration-contract handoff test，並同步修正 curate 文件中的同一漂移。
4. 依 `STATE_TRANSITIONS.md` 補齊可觀察的成功、失敗、重跑與 cleanup 契約。
5. 為 request payload、其餘 retry/rate-limit/process-lock 行為、preview/dry-run、migration、transaction 與 CLI 增加 deterministic tests。
6. 保留既有測試，只有新測試明確且完整取代其保護規則時才考慮合併或移除。

## 2. 已確認基線

- 模組測試：`python -m pytest modules/curate/tests -q`
- 目前結果：15 項通過。
- Active config 驗證：`python -m modules.curate.src.cli validate` 零 error。
- Active config 使用 `mini-proxy`，`supports_structured_output: false`，因此現行 production path 使用 `json_object` fallback，而非 API provider 的 strict JSON schema enforcement。
- Active config 的 execution policy 為 `batch_size: 200`、`max_concurrent_requests: 20`、`rate_limit_per_minute: 1200`。既有測試以 MagicMock 使用較小的 `3 / 60` 值，後續測試必須從受測 config object 推導預期，不能把任一組值硬編碼為通用契約。
- 手動 upstream schema 存在三處：`test_database.py`、`test_orchestrator.py` 的 `create_mock_upstream_tables()`，以及 `test_cli.py` 的 inline `source_item` DDL；前兩者與 active ingest migration 不完全一致。

這些基線不是長期 CI gate 的替代品，但可用於判斷各 phase 是否引入回歸。

## 3. 已知測試缺口與維護風險

### 3.1 Response validator 漏接頂層必填欄位

`PROMPT_CONTRACT.md` 的 JSON schema 要求 `curation_decision`、`editor_brief` 與 `curation_output` 都必須存在。不過 `validate_curation_response()` 只顯式檢查 `curation_decision`，並以 `.get()` 取得另外兩欄。因此，以下不完整 payload 會被當作有效的 `reject_discard`：

```python
{
    "curation_decision": {
        "curate_status": "rejected",
        "downstream_action": "reject_discard",
        "decision_reason": "duplicate",
    }
}
```

現有 `test_validation_matrix_logic` 只測試兩個欄位明確為 `null` 的正常 `reject_discard` 情境，未測試欄位缺失。這是應優先修正的 production behavior 問題，修正與 regression test 必須位於同一小批提交。

須直接驗證：

- 缺少任一頂層欄位會被拒絕，包括 `reject_discard` 的兩個 nullable output 欄位。
- 頂層欄位存在但型別錯誤會被拒絕。
- 四種 downstream action 的有效 null/object 組合仍可通過。
- `publish_link` 的三個 bullet 必須為 `null`，`publish_summary` 的三個 bullet 必須為非空字串。
- fallback `json_object` 路徑暫時維持接受未知 top-level 與 nested keys，不新增拒絕 assertion；此為已決定的相容性行為，並應在 `PROMPT_CONTRACT.md` 記錄，不得假定 API-side strict schema 會代為攔截。

### 3.2 手動上游 schema fixture 已漂移

`test_database.py` 的 mock `source_item` 允許 `ingest_status = 'draft'`，並讓 `source_item_text` 對 `source_item` 使用 `ON DELETE CASCADE`。active ingest v001 migration 則只允許 `ingested`，且對該關係使用 `ON DELETE RESTRICT`。

此外，`DATA_CONTRACT.md` §5.2 同樣把 `source_item_text.source_item_id` 寫為 `ON DELETE CASCADE`，與 active ingest migration 的 `ON DELETE RESTRICT` 矛盾，顯示 fixture 很可能是依照已漂移的文件建立。

因此，`test_cascade_delete` 目前驗證的是測試自行建立、但 active canonical schema 不會允許的資料刪除路徑。三份各自複製的 schema 又使此類漂移容易再次發生。

後續應：

- 建立使用 active ingest migration、classify migration 與 curate migration 的 temporary SQLite handoff test。
- 在同一 handoff 變更中修正 `DATA_CONTRACT.md` §5.2 的 FK delete action，使文件、fixture 與 active ingest migration 對齊。
- 保留最小 isolated unit fixture 時，明確標示其只服務 repository unit test，不能被視為 canonical schema contract。
- 不匯入其他模組的 test helper，避免建立測試期 runtime coupling。

### 3.3 State transition 與 rollback assertions 不完整

現有 `test_state_transitions_data_cleanups` 覆蓋 `publish_summary → edit_rewrite → reject_discard`，但未覆蓋：

- `publish_link ↔ publish_summary` 的 bullet 與 output 更新。
- `edit_rewrite → publish_link/publish_summary` 的 output 重建。
- normal queue 中的 failed item 後續成功時 retry count 歸零，並建立正確的 brief/output。`test_orchestrate_with_source_item_id_and_force` 已覆蓋 locked item 的 forced success，不能取代此正常 queue 路徑。
- completed 或 withdrawn item forced re-run 失敗時，decision、editor brief 與 output 都保持不變。

特別是現有 `test_forced_rerun_failure_rollback` 已建立 `editor_brief`，但 rollback assertions 只檢查 curation decision 與 output title，沒有驗證 brief 完整未變。文件要求強制重跑失敗時既有成功資料「完全不變」，因此應使用三張 curate 表的可比較 snapshot，或至少逐欄比對所有既有資料。

### 3.4 LLM request 與執行政策沒有直接測試

`_build_messages()`、`_build_request_payload()`、`_parse_response_content()`、`fetch_llm_curation()` 與 `ProcessLock` 目前沒有直接測試。這使下列已實作且有文件契約的行為沒有自動化保護：

- user prompt interpolation 是否正確帶入 title、sanitized text、topic class 與 government flag。
- `supports_structured_output` 為 false 時使用 `json_object`，為 true 時輸出正確的 strict JSON schema、required list 與 `additionalProperties: false`。
- model、temperature、top_p、token limit 與 timeout 是否從 config object 帶入。
- 429、5xx、timeout、network error、invalid JSON 與 empty choices 的 retry 次數是否符合設定上限。
- 一般 4xx 與 model refusal 是否不重試，並有可讀取的失敗資訊。
- schema validation failure 視為 retryable transient failure。active temperature 為 0.7，重試會重新取樣，型別或欄位錯誤可能在下一次成功；此規則應加入 `EXECUTION_POLICY.md`，並以 retry 上限 test 保護。系統性 prompt 或 schema 錯誤仍會耗盡上限後將 item lock，這是可接受的既定代價。
- backoff、jitter、rate-limit spacing 與 semaphore concurrency 是否以可控制的 sleep/clock/client 驗證，不使用真實等待。
- runner process lock 在第二個 runner 取得同一路徑 lock 時會失敗，且釋放後可重新取得。

現有 transient-failure test 只確認最終 failed state，沒有斷言 HTTP 呼叫數、sleep 次數或 delay，因而無法偵測 retry 行為回歸。另有已確認的 behavior bug：`fetch_llm_curation()` 對任何非 200 response 建立 `HTTPStatusError`，隨後以廣泛的 `httpx.HTTPError` handler 重試，所以一般 400/401/403/404 也會重試到上限。這與函式內「only 429/5xx retry」的註解及 `EXECUTION_POLICY.md` 所列 transient failure 類型不一致，應以單次呼叫 regression test 與最小修正同一批處理。

### 3.5 Migration、transaction 與 DDL contract 覆蓋不足

`run_migrations()`、`split_sql_statements()`、`transaction()` 及 curate DDL 只被整合流程間接覆蓋。`test_database_check_constraints` 驗證了數個錯誤 status/action 組合，但沒有驗證 approved、rejected 或 withdrawn 使用 `NULL` downstream action 的情況。

SQLite 的 `CHECK` 對 `NULL` 不會視為 false，因此 current DDL 會接受：

```sql
curate_status = 'approved' / 'rejected' / 'withdrawn'
downstream_action = NULL
```

這與 `DATA_CONTRACT.md` 的「failed 以外必須 non-null」不一致。已定案採雙層防線：

- 先新增涵蓋三種 non-failed status 的 regression tests。
- 在 v001 的既有 status/action `CHECK` 後增加 `AND (curate_status = 'failed' OR downstream_action IS NOT NULL)`，以最小改動使 SQLite DDL 成為最後防線。
- 在 `CurationRepository.upsert_curation_decision()` 驗證同一規則，使所有目前與未來的 repository 寫入者在資料庫前收到具體錯誤。
- 同步更新 `DATA_CONTRACT.md` 內嵌的 DDL 副本與文字契約。

改動 v001 不會自動套用到已記錄於 `schema_migrations` 的本機 DB，且 SQLite 不能直接 ALTER 既有 CHECK。實作 PR 必須明確說明 pre-production schema reset：維護者需依既有 reset policy 手動重建已存在的本機 `canonical.db`、`canonical_final.db`、`test_sandbox.db` 或其他使用此 migration 的 DB，不能由測試或程式自動刪除資料庫。

還應直接測試：

- migration re-run idempotency。
- 失敗 migration 不寫入 `schema_migrations`，且同一 migration 內 DDL transaction 回滾。
- SQL splitter 對空白、註解、單一 statement 與多 statement input 的行為。
- transaction 的 commit、exception rollback 與 `commit=False` dry-run rollback。
- `curation_decision`、`editor_brief`、`curation_output` 的 UNIQUE、FK action、CHECK 與必要 index 語意。index test 應鎖欄位與 unique 語意，不鎖純實作性的 index 名稱。

### 3.6 CLI 操作契約尚未覆蓋

目前 CLI tests 只測 withdraw/reapprove 及其 auto-migration。下列 commands 或 options 沒有自動化測試：

- `validate` 對有效設定、缺檔、無效 YAML、缺少 active provider/template 與非法 execution policy 的 exit code 與 stderr。
- `migrate` 對成功、重跑與錯誤 migration 的使用者可見結果。
- `status` 對 pending、locked、approved、withdrawn、rejected、total failed 的計數與零副作用。
- `run --preview-prompts` 不呼叫 LLM、不寫入 DB，且只輸出選定 pending item 的 prompt。
- `run --dry-run` 可以呼叫 mocked LLM，但不會寫入 decision、brief 或 output。
- `run --force` 的 CLI usage error，以及全部 item 失敗與部分失敗時的 exit behavior。

所有 CLI tests 必須使用 temporary config、temporary SQLite DB 與 mocked HTTP，不得載入 workspace canonical DB、發出真實 API request 或依賴 `.env` secret。由於 Click group callback 無條件呼叫 `load_dotenv()`，test 必須 patch 該函式為 no-op，並顯式提供所需的非機密 test environment。由於 lock 路徑由 `db_path.parent.parent` 推導，test DB 應置於 `<temporary-workspace>/data/canonical.db`，讓 lock file 仍位於同一 temporary workspace。

### 3.7 測試支援程式重複，但尚無可刪除的測試

三份 upstream schema definition 與多份近似 LLM response dict 已增加維護成本。應在新增 coverage 時抽取最小、可組合的 test support helper，例如 temporary workspace/DB builder、seed item、valid response factory 與 full-table snapshot helper。

不建議現在刪除任何測試或單純為縮短檔案大規模重排。既有案例各自保護不同的成功、failure、lock、forced re-run 或 operator transition 規則，尚未證明彼此重複。

## 4. 分階段執行方案

### Phase 0：保護基線與最小測試支援

目的：在修改 production behavior 前固定基線，並只抽取後續案例確實需要的共用測試支援。

工作：

1. 記錄 15 項基線、單獨測試指令與 active config validate 結果。
2. 建立最小 helper 或 fixture，集中 temporary DB 路徑、migration 路徑、seed upstream item、valid response factory 與 curate-table snapshot。
3. helper 必須讓 caller 明確提供 downstream action、response content、existing state 與 provider capability，不隱藏關鍵的測試前置條件。
4. 暫時保留三份手動 upstream schema definition，直到 Phase 4 的 migration handoff test 已足以取代相關 contract assertions。
5. 不在此 phase 改 production code 或刪除既有 tests。

驗收：

```powershell
python -m pytest modules/curate/tests -q
python -m modules.curate.src.cli validate
```

預期：測試數量不減少，active config 維持可驗證。

### Phase 1：Response validator、一般 4xx 與 request payload contract

目的：優先修復兩個 production bug——validator 頂層欄位漏洞為 active `json_object` fallback path 的最後一道本地 gate，一般 4xx 重試 bug 則與 provider capability 無關、所有路徑皆受影響——並為 provider capability 路徑建立直接 guard。

工作：

1. 先為缺少 `editor_brief`、缺少 `curation_output`、缺少 `curation_decision` 及頂層型別錯誤新增 direct failure tests。
2. 修正 `validate_curation_response()`，使其與 `PROMPT_CONTRACT.md` 的頂層 required contract 一致；修正與 regression test 必須同一小批提交。
3. 先新增一般 400/401/403/404 僅發出一次 request 的 regression tests，再以最小變更將其排除於 retry handler；修正與 tests 必須同一小批提交。
4. 為每種 downstream action 建立 parameterized 或 subtest matrix，覆蓋有效組合和最小的無效組合。
5. 為 `_build_messages()` 與 `_build_request_payload()` 加入直接 unit tests，驗證 interpolation、config-derived request defaults、`json_object` fallback 與 strict structured-output guard。
6. 為 `_parse_response_content()` 補 empty choices、missing/empty/non-string content、refusal 與 invalid JSON tests。
7. 同步在 `PROMPT_CONTRACT.md` 記錄 fallback `json_object` 路徑目前刻意接受未知 keys 的相容性行為；不將其改為 reject policy。
8. 若 Phase 1 實作發現 `PROMPT_CONTRACT.md` 的 JSON schema 與 outbound `JSON_SCHEMA` 有漂移，僅同步其技術 schema 範例，不改 prompt 的業務語意。

驗收：

- 不發出真實 HTTP request。
- missing top-level output regression 在修正前失敗、修正後通過。
- 一般 4xx regression 在修正前發出至 retry 上限的 request，修正後只發出一次。
- fallback 與 structured-output 測試可清楚區分，且兩者都不依賴 active provider 的真實服務。

### Phase 2：其餘 retry、rate limit、concurrency 與 process lock

目的：讓 `EXECUTION_POLICY.md` 的可觀察執行規則受到 deterministic tests 保護。

工作：

1. 建立可控制的 fake/mocked HTTP client，覆蓋 429、5xx、timeout、network error、invalid JSON、empty choices、model refusal 與 schema validation failure。
2. 斷言每個已決定為 retryable case 的 HTTP 呼叫數、每個 non-retryable case 的單次呼叫行為，以及最終例外或 failed persistence。
3. mock `asyncio.sleep` 與 jitter source，精確斷言 backoff，避免真實 sleep 與時間相依 flake。
4. 以 in-flight counter 驗證 semaphore 不超過 `max_concurrent_requests`，期望值必須由測試 config object 取得。
5. 驗證 rate-limit scheduler 的 delay 由 `rate_limit_per_minute` 推導，不硬編碼 active config 或文件中的歷史數值。
6. 使用 `sys.executable -c` 執行極簡子程序，直接 import `ProcessLock`，不 import CLI。驗證 parent 持鎖時 child `acquire()` 拋出 `RuntimeError`，parent release 後 child 可取得 lock。lock 路徑必須位於 temporary directory，子程序必須設 timeout 並在 finally 終結，避免測試 hang 住。
7. 驗證單一 batch 中一個 item 失敗時，其他 item 仍可成功，且 normal queue failure 正確累加 retry count。

驗收：

- 不使用真實網路、真實等待或隨機 delay。
- 連跑不依賴 task 完成順序。
- 失敗 isolation、retry eligibility、retry 上限、rate-limit 與 semaphore 都有獨立斷言。

### Phase 3：資料庫、migration、transaction 與狀態轉換

目的：讓 DDL、repository 寫入及 `STATE_TRANSITIONS.md` 的資料完整性規則得到直接保護。

工作：

1. 為 `split_sql_statements()`、`run_migrations()` 與 `transaction()` 新增 direct tests，覆蓋 §3.5 所列 cases。
2. 使用 SQLite metadata 與真實 constraint behavior 驗證 curate-owned tables、FK、UNIQUE、CHECK 與 index 語意。
3. 新增 approved、rejected、withdrawn 三種 non-failed status 搭配 `NULL` action 的 regression tests，再於同一批實作 v001 的最小 CHECK 補強及 `CurationRepository.upsert_curation_decision()` validator。同步更新 `DATA_CONTRACT.md` 的文字與內嵌 DDL，並在 PR 說明需手動重建既有本機 DB；不得只新增 test 而保持文件、DDL 與 DB contract 不一致。
4. 對每一種 target state 建立 state transition table-driven tests，至少覆蓋：
   - pending/failed → `publish_link`、`publish_summary`、`edit_rewrite`、`reject_discard`。
   - `publish_link ↔ publish_summary`。
   - approved/rejected/withdrawn → forced `edit_rewrite` 或 `reject_discard` 的 stale row cleanup。
   - `edit_rewrite → publish_*` 的 output 重建。
   - normal queue failed → success 的 retry count 歸零。
5. 對 completed 和 withdrawn forced re-run failure 使用 decision、brief、output 三表 snapshot，斷言資料完全不變。

驗收：

- 以真實 temporary SQLite DB 驗證，不能以 mock `IntegrityError` 取代 DDL 契約。
- 每個 state test 僅鎖定一個轉換與其 side effect。
- cleanup 斷言同時檢查應刪除的 row 與必須保留或更新的 row。

### Phase 4：CLI 與 upstream handoff contract

目的：驗證使用者可見 command contract，並以正式 migration 避免 curate 測試對 ingest/classify schema 的錯誤假設。

前置條件：

1. 使用 active ingest、classify 與 curate migration，依序建立 temporary SQLite DB。
2. 不使用其他模組的內部 test helper，也不讀取 workspace canonical DB。
3. 此 phase 的 handoff test 維持在 curate test suite 內。全 repo 的 pytest package 收集問題仍由後續 CI/package-structure 工作處理。

工作：

1. 對 `validate`、`migrate`、`status`、`run --preview-prompts`、`run --dry-run` 與 `run --force` 補齊 §3.6 的 CLI tests。
2. 以 active migrations 驗證 curate pending selection 只處理 `ingested`、`core`/`adjacent` 且符合 retry eligibility 的 item。
3. 驗證 active upstream FK semantics，例如 source-item deletion 受 ingest-owned `source_item_text` restrict 所阻擋時，curate rows 不會被錯誤視為 cascade deletion 的獨立契約。
4. 同一批修正 `DATA_CONTRACT.md` §5.2 對 `source_item_text` FK delete action 的描述，並以 active migrations 作為 handoff contract 的唯一依據。
5. CLI tests patch `load_dotenv()`，明確設定非機密 test environment，並以 `<temporary-workspace>/data/canonical.db` 避免 lock file 脫離 temporary workspace。
6. 在 migration handoff test 足以取代手動 schema helper 的 contract coverage 後，刪除或縮減三份重複 schema definition；若仍保留 isolated helper，必須在名稱或 docstring 說明其非 canonical schema。
7. 只在 Phase 1 至 4 使現有單檔明顯難以導航時，才按 validator/request、database/migrations、orchestration/state、CLI/handoff 職責拆檔。拆分必須獨立提交，不得改變 test behavior。

驗收：

- CLI tests 不呼叫真實 LLM、不卡住等待 rate limit、也不讀取 secret。
- handoff test 以 migration contract 驗證資料相容性，不建立新的 runtime coupling。
- 若移動 tests，收集總數不減少，且每個搬移 node ID 有明確的新位置對照。

## 5. 建議提交與驗證方式

每個 phase 應可獨立審查與合併。Phase 1 的 validator 與一般 4xx 修正，以及 Phase 3 的 non-failed null-action contract 修正，都屬 production behavior change，必須分別與其 regression tests 放在同一小批提交。Phase 1 的 fallback unknown-key 行為與 Phase 2 的 validation-failure retry 均已定案，必須連同相應 `PROMPT_CONTRACT.md`、`EXECUTION_POLICY.md` 文件更新與 tests 提交。其他測試補強不應夾帶 curation 政策、prompt 業務語意或 provider 選型變更。

每批最少執行：

```powershell
python -m pytest modules/curate/tests -q
python -m modules.curate.src.cli validate
```

涉及 retry、rate limit、semaphore 或 process lock 的修改，額外執行：

```powershell
1..5 | ForEach-Object { python -m pytest modules/curate/tests -q }
```

涉及 migration handoff 的修改，先保持模組單獨執行，直到跨模組 pytest package 收集衝突另案解決：

```powershell
python -m pytest modules/curate/tests -q
```

## 6. 完成定義

本方案完成時應滿足：

1. fallback response validator 會拒絕缺少任一 required top-level field 的 payload，四種 routing action 的有效與無效矩陣都有 deterministic regression tests。
2. request payload、response parsing、已定案的 retry eligibility、retry 上限、一般 4xx 的單次呼叫、backoff、rate limit、semaphore 與跨 process lock 都有不依賴真實網路、時間或 CLI `.env` 載入的 tests。
3. migration、transaction、SQLite constraints、所有 non-failed status 的 downstream-action nullability 與完整 state transition cleanup 皆有直接測試。repository validator、v001 DDL、`DATA_CONTRACT.md` 的內嵌 DDL 及既有本機 DB reset 說明維持一致。
4. forced re-run failure 對 decision、editor brief 與 curation output 的完整保留契約有 snapshot-level regression test。
5. CLI command contract 已以 temporary config、temporary workspace DB 與 mocked LLM 覆蓋，並 patch `load_dotenv()`，不接觸 `.env` secret 或 workspace canonical DB。
6. curate 對 ingest/classify 的關鍵 DB handoff 以 active migration contract 驗證，不再將手動複製 schema 當作 canonical behavior，且 `DATA_CONTRACT.md` §5.2 已與 active ingest FK action 對齊。
7. curate suite 在重複執行抽查中穩定通過，沒有新增 flaky failure。

## 7. 已定案的實作決議

1. **Non-failed null action：採 DDL + repository validator。** v001 的既有 status/action CHECK 增加 `AND (curate_status = 'failed' OR downstream_action IS NOT NULL)`；`CurationRepository.upsert_curation_decision()` 實作同一檢查。實作與測試同步更新 `DATA_CONTRACT.md` 的文字和內嵌 DDL。既有本機 DB 不會自動升級，PR 必須明確要求依 pre-production reset policy 手動重建。
2. **未知 response keys：fallback 維持寬鬆。** `json_object` fallback 接受未知 top-level 與 nested keys，不新增 reject assertion。此相容性行為記錄於 `PROMPT_CONTRACT.md`，未來僅在 provider compatibility policy 明確改變時重新檢討。
3. **Schema validation failure：維持 retry。** 視為 transient failure，依 configured retry limit 重試，最終失敗依既有規則寫入 failed/lock。實作與 test 同步將此規則補入 `EXECUTION_POLICY.md`。
4. **ProcessLock：採受控 subprocess regression test。** 使用 `sys.executable -c` 最小 script 驗證真正跨 process 的拒絕與 release 後重取；以 temporary lock path、timeout 與 guaranteed child cleanup 避免 hang，且不在 child import CLI。
