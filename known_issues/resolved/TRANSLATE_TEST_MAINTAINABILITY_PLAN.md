# Translate 模組測試可維護性補強計畫

**狀態：** 已定案，待工程師排程與實作
**日期：** 2026-07-30
**修訂：** 2026-07-31，文件審查通過後，明確指派 DATA_CONTRACT pre-screen 文件同步、定案 deterministic batch ordering，界定 translate 與 publish 的 stale fallback 責任，並增加 curate handoff follow-up
**範圍：** `modules/translate/` 的 Python 測試、測試支援結構，以及可直接驗證的 translate 資料與執行契約
**非範圍：** 本方案不直接改變翻譯政策、prompt 的業務語意、LLM provider 選型、模組邊界或全 repo CI；不在文件審查完成前修改 runtime code；不以測試重構為由刪除仍保護現行規則的測試

## 1. 背景與目標

`translate` 目前有 48 項 pytest 測試，涵蓋五欄內容 fingerprint、handoff assembler、回應驗證、label guard、self-translation bypass、stale detection、首次失敗資料安全與強制重跑失敗保留。模組單獨執行可全數通過，連續執行五次亦穩定通過。

既有五欄位契約測試對純函式和資料形狀提供良好保護，但測試仍有三類風險：

1. 某些已鎖定的資料契約沒有覆蓋到不合法邊界輸入，可能讓不合法資料通過。
2. 測試大量使用 `MagicMock` config 或直接呼叫內部函式，未充分保護正式 config、CLI 與 orchestrator 路徑。
3. retry、批次、lock、concurrency、migration 和 handoff 的執行規則覆蓋不足，且部分測試已複製同樣的 schema、seed helper 與成功情境。

本計畫的目標：

1. 先以 regression tests 固定 upstream handoff 的五欄 bullet invariant、已確認的 execution bug 與文件漂移。
2. 讓 LLM request、response parsing、retry、rate limit、concurrency 與 process lock 受到 deterministic tests 保護。
3. 以真實 temporary SQLite migration 驗證 translate 自有 schema、transaction、queue selection 與關鍵 handoff contract。
4. 補齊 CLI 的安全操作契約，確保測試不讀取 workspace canonical DB、`.env` secret 或發出真實 HTTP request。
5. 在 coverage 已保留後才收斂重複的測試支援程式與情境。

## 2. 已確認基線

- 模組測試：`python -m pytest modules/translate/tests -q`
- 目前結果：48 項通過。
- 穩定性抽查：同一指令連續五次均通過，未觀察到 flaky failure。
- `python -m modules.translate.src.cli validate` 可成功驗證 active config。
- active config 使用 `mini-proxy`，且 `supports_structured_output: false`，所以目前執行的是 `json_object` fallback 路徑。
- active config 的 execution policy 為 `batch_size: 200`、`max_concurrent_requests: 20`、`rate_limit_per_minute: 1200`、`retry_attempts: 3`。
- `test_translate.py` 有 9 項較寬廣的 module scenario tests；`test_five_field_contract.py` 有 39 項較精確的五欄位契約測試。

以下事實已由 source、tests 與 git 歷史核實：

- commit `bc165eb`（`Relax translation content ratio limit validation`）在 2026-06-23 將 active `content_ratio_limit` 從 1.2 刻意放寬為 5.0。因此正式 policy 是 5.0，過時的是 `TRANSLATION_POLICY.md`、`EXECUTION_POLICY.md`、`config.py` 的 Pydantic default 與兩份測試中的 1.2 fixture。
- 經架構與維運審查確認，`batch_size` 的正式 unit 是來源文章數。它界定本次的業務範圍，`max_concurrent_requests` 與 `rate_limit_per_minute` 則各自負責 API 流量控制。`orchestrate_run()` 目前對已展開的語言 task list 直接切片，與此決議不符。
- `DATA_CONTRACT.md` 的 timestamp pre-screen 描述已過時。現行 assembler 以 `author_metadata.upstream_updated_at` 保存並比較 upstream freshness marker 是正確的，因為 handoff row 的 `updated_at` 每次 assembly 都會刷新，不能作為 upstream change marker。
- 一般 HTTP 4xx 現在會被錯誤重試。non-200 response 會進入廣泛的 `httpx.HTTPError` retry handler，即使不是 429 或 5xx。
- dry-run 不取得 `ProcessLock`，但仍會發出真實 LLM request。因此 dry-run 與正式 run 可併發執行同一 translation，重複消耗 API 額度。
- `test_cli_commands_verification` 經由 CLI group callback 載入 workspace 的 `.env`；測試雖不應依賴 secret，現行測試隔離尚未阻止此讀取。

這些基線不是未來 CI gate 的替代品，但可用於判斷每個 phase 是否引入回歸。

## 3. 已知測試缺口與維護風險

### 3.1 Upstream handoff 的三個 bullet invariant 沒有被完整保護

`DATA_CONTRACT.md` 規定：

- `publish_summary` 必須有三個非空 bullet。
- `publish_link` 必須有三個 `NULL` bullet。
- 部分填入的組合不可接受。

現有 assembler tests 只覆蓋全有與全無。`assemble_approved_content_records()` 目前直接複製 upstream 欄位，沒有測試或 runtime validation 保護下列非法 upstream input：

- 任一或任兩個 bullet 為 `NULL`，其餘存在。
- bullet 為空字串或僅空白。
- `publish_link` 卻包含任何 bullet。
- `publish_summary` 缺少任何一個有效 bullet。

這是 upstream handoff 邊界的契約與測試缺口，不是已證實的 assembler 實作違約。相對地，LLM response 端已對 partial bullet 與 whitespace-only value 進行 validation，這一缺口只適用於 upstream input。

已定案採 assembler 防禦性拒絕。assembler 是 `approved_content_record` 的最後一個寫入邊界，必須以零信任原則驗證 upstream curation payload，禁止不合法 bullet shape 寫入 canonical handoff。不得透過自動填補、截斷、全域字串轉換或將 item 靜默降級為另一種 downstream action 修復資料。

拒絕必須以 item 為單位隔離：不寫入新的 handoff row，且不得覆寫既有有效 handoff row；assembler 應輸出可供 operator 診斷的 source item、downstream action 與違規原因，並在統計結果中明確區分 rejected 與其他 assembly outcomes。其他合法 item 必須能在同一次 assembly 繼續處理。

若 curate 持續產出違規 shape，該 item 會停留在 rejected，直到上游資料被修正後的 assembly 才能進入 handoff。Phase 1 必須在 PR checklist 或 linked issue 中建立給 `curate` owner 的 cross-module follow-up，內容至少包含違規 shape、受影響 source item 與預期的上游契約，避免 rejected diagnostics 只停留在 translate 的操作輸出。

### 3.2 Ratio limit 文件、default 與 fixture 未跟隨已決定的 5.0 policy

git 歷史確認 active YAML 的 5.0 是刻意放寬，不是後續重構的意外漂移。`TRANSLATION_POLICY.md`、`EXECUTION_POLICY.md`、`config.py` 的 `ValidationConfig` default 及兩個測試檔的 mock config 仍保留 1.2，讓程式預設、文件、active runtime 與測試期望不一致。

此項不再是待決策問題。工程應採 5.0 為單一正式 policy，並在同一小批：

1. 更新 `TRANSLATION_POLICY.md`、`EXECUTION_POLICY.md` 及 `config.py` default。
2. 更新 mock fixture 的期望，並新增真實 config loading regression test。
3. 讓 ratio tests 從受測 config object 或 temporary YAML fixture 推導有效上限，而非把手寫 mock 值當成正式 runtime 行為。

不應將 active config 改回 1.2，除非未來另有新的翻譯品質決策與獨立變更理由。

### 3.3 Batch 已決定以來源文章計數，但 orchestrator 尚未遵守

`orchestrate_run()` 先取得或產生每個 `(parent_content_id, language_code)` task，再執行 `all_tasks[:run_batch_size]`。若三個目標語言皆啟用，設定值 200 目前最多會處理 200 個語言 task，不是 200 個來源項目。

正式行為應為：先選取至多 `batch_size` 個具有至少一個 eligible translation 的 distinct `parent_content_id`，再為每一篇選定文章建立其所有 eligible target-language tasks。已完成且未 stale 的語言不重做；因此「文章完整性」表示同次 run 不會因 batch 邊界而人為切開同一文章的待處理語言集合，不保證每個語言在單次 run 都成功，因為個別 API 或 validation failure 仍依既有 state/retry policy 處理。

這符合操作人員以「本次處理 N 篇文章」追蹤進度的 mental model，也將 API 併發與速率控制留給 `max_concurrent_requests`、`rate_limit_per_minute`。現有 CLI test 僅在一筆資料、一個 batch size 下執行 `--preview-prompts`，無法辨識此差異。後續 runtime、文件、CLI help、run summary 與測試必須一致，並明確報告選定 source item 數與產生的 language task 數。

### 3.4 LLM 執行政策與 failure surface 缺少直接測試

下列已實作或有文件契約的行為，目前沒有充分的 direct tests：

- `_build_request_payload()` 的 prompt interpolation、model、temperature、top_p、token limit、timeout 及 fallback/structured-output request shape。
- `_parse_response_content()` 對 empty choices、missing message content、空字串、refusal 與無效 JSON 的處理。
- `fetch_llm_translation()` 對 429、5xx、timeout、network error、一般 4xx、validation failure 與 response parsing failure 的 retry eligibility、呼叫次數與 backoff。
- `asyncio.Semaphore` 是否限制 in-flight HTTP request 不超過 `max_concurrent_requests`。
- rate-limit stagger delay 是否由 `rate_limit_per_minute` 推導，且不依賴實際等待。
- `ProcessLock` 在第二個 process 競爭時是否拒絕，以及 release 後是否可再取得。
- `--dry-run`、`--force` 與完整 bulk queue 的 public CLI/orchestrator 行為。

一般 4xx retry 是已確認的 production bug，不是待選 policy。除 429 外，4xx 預設應直接失敗，只有 provider 明確文件化的例外才可另行加入 retry policy。現有失敗路徑使用未 patch 的 random jitter 與實際 `asyncio.sleep`，已使測試耗時增加並保留時間相依風險。

dry-run 無 lock 也是已確認的 execution bug。雖然 dry-run 不寫入 DB，它仍呼叫 LLM API，故應和正式 run 互斥，或有另一個同樣能避免重複 API execution 的明確協調機制。此行為的修正、regression test 與 `EXECUTION_POLICY.md` 更新必須同一小批進行。

所有上述測試應 mock HTTP client、`asyncio.sleep` 和 jitter source，避免真實網路、sleep 或排程順序造成 flake。跨 process lock 則應使用受 timeout 保護的最小 subprocess test，並讓 lock file 位於 temporary workspace。

### 3.5 State、retry 與 queue selection 的整合覆蓋不足

現有 tests 已保護首次失敗、stale retry failure、直接呼叫 `translate_task()` 的完成列強制重跑失敗，以及 bypass stale/config 規則。但仍未直接驗證：

- retry count 從 0 至 `retry_attempts` 的每次遞增、達到邏輯 lock 後 bulk queue 不再選取。
- locked item 經 operator force 重新成功後，retry count 歸零且資料完整更新。
- `pending`、`stale`、retryable `failed`、locked `failed` 及無 row 的 queue eligibility matrix。
- `--force` 實際經由 CLI 與 `orchestrate_run()` 的選取行為，而非只以手動 task dict 的 `status="completed"` 模擬。
- normal stale retranslation 失敗時，translate row 保留的舊內容與 `failed` 狀態是否正確。

已定案採非持久化 forced rerun。當 `--force` 對來源 fingerprint 與 config 仍相符的 `completed` row 執行時，runner 只在記憶體中把它視為強制重跑，網路呼叫前不把 row 寫成 `pending`。成功時才以單一短 transaction 原子覆寫五欄內容；API、validation、程序中斷或取消失敗時不寫 DB、不增加 retry count，原先的 completed row 保持可發布。

若 stale detection 已發現來源或 config 已變更，該 row 不是資料安全保護模型中的「最新 completed」成果。即使操作員指定 `--force`，它仍依 normal stale retry path 處理，失敗時不得偽裝為 current completed。這保留 stale state 的可見性，避免把舊翻譯當成最新內容。

此模型不需要在 API 呼叫期間持有 DB transaction，也不需要建立 `pending` 中間狀態。若未來需要完整操作稽核，應新增獨立的 attempt log，而不能修改或暫時降級現有可發布的 `translation_output` row。

translate 本計畫只鎖定 row status 與已保留內容的正確性。stale 或 failed row 中的舊內容是否仍能作為網站發布 fallback，是 `publish` 的消費契約，必須在 publish 模組另行定義與驗證，不能由 translate 測試隱含決定。

### 3.6 Migration、transaction、DDL 與 handoff contract 主要只有間接覆蓋

`run_migrations()`、`split_sql_statements()`、`transaction()`、translate DDL constraints、`TranslationRepository.get_pending_translation_tasks()` 與 `detect_and_mark_stale()` 都缺少完整的直接測試。現有 tests 也在兩個檔案中各自手動建立最小 upstream schema。

後續應建立 temporary SQLite tests，至少覆蓋：

- migration re-run idempotency。
- 失敗 migration 不留下 `schema_migrations` row，且同一 transaction 的 DDL 回滾。
- SQL splitter 對空白、註解、單一 statement 與多 statement input 的行為。
- `transaction()` 的 commit、exception rollback、`commit=False` dry-run rollback。
- `translation_output` 的 UNIQUE、CHECK、FK cascade、必要 index 語意與 upsert 行為。
- stale detection 對 fingerprint mismatch、config mismatch、bypass exception 與已 stale rows 的 idempotency。
- pending task selection 對每個狀態、retry count 與多語言 row 的結果。
- translate 對 `approved_content_record` 的 read-only consumer 角色，以及將來 `edit` handoff 接入時的正式 migration contract。

handoff tests 應優先使用已核准的 upstream migration contract，而不是複製其他模組的 private test helper。若 upstream migration 尚未提供可直接套用的穩定契約，保留最小 fixture，但明確標示它只能服務 isolated test，不能代表 canonical schema。

### 3.7 重複 scenario、fixture 與 HTTP client lifecycle 增加維護成本

`test_translate.py` 與 `test_five_field_contract.py` 分別重複建立 source tables、temporary DB、seed helper、mock config、five-field response factory，以及 handoff、validation、bypass、stale、failure safety 情境。兩份檔案的職責目前不夠清楚：

- `test_five_field_contract.py` 已包含精確的 golden vectors、nullability、label guard、bypass 和 failure-safety assertions。
- `test_translate.py` 又覆蓋同類行為，但多數以更寬廣 scenario 斷言，適合收斂為少量 journey tests。

另外，至少七個 tests 建立 `httpx.AsyncClient()` 後未顯式關閉。雖未在本次基線觀察到 warning 或 failure，仍應改成測試可控的 async context manager 或 fake client，避免資源生命週期依賴 garbage collection。

不應在 Phase 0 直接刪除 tests。應先建立 coverage mapping，確認每個 legacy scenario 是否仍提供不同的 public behavior 保護；只有在新測試完整取代相同規則後，才將重複案例合併或移除。

## 4. 分階段執行方案

### Phase 0：保護基線、記錄已確認結論並建立最小測試支援

目的：不修改 production behavior，先固定基線、將已確認的歷史與實作結論寫入文件，並建立後續新增 coverage 所需的最小 helper。

工作：

1. 記錄 48 項基線、五次重複執行結果與 config validate 結果。
2. 建立 coverage mapping，將每個現有 test 對應至五欄 contract、handoff、validation、state、CLI 或 execution policy，暫不刪除任何 test。
3. 將 ratio limit 的 5.0 正式 policy 和 delta pre-screen metadata marker 的正確行為列為已確認基線。Phase 1 必須同步更新 `DATA_CONTRACT.md` §1.1 的 `author_metadata` 說明，以及 §1.5、§2.1.2 的 pre-screen 規則，不再把這些工作留為未指定 owner 的文件待辦。
4. 記錄 fresh completed forced rerun 的非持久化資料安全模型，並確認 `STATE_TRANSITIONS.md`、CLI help 與 operator runbook 的描述一致；batch source item 一律以 `approved_at ASC, parent_content_id ASC` 選取，後者作為同時間戳的 deterministic tie-breaker。
5. 抽取最小 reusable test support，例如 temporary workspace/DB builder、valid five-field response factory、active-config loader、seed approved record、translation row snapshot。
6. helper 必須讓呼叫者明確傳入 bullet shape、status、retry count、provider capability、response content 和 target language，不能隱藏關鍵前置條件。
7. 不匯入其他模組的 test helper，不讀取 workspace canonical DB，不讀取 `.env`。

驗收：

```powershell
python -m pytest modules/translate/tests -q
python -m modules.translate.src.cli validate
```

預期：測試數量不減少，active config 維持可驗證，5.0 ratio、metadata freshness marker 與 deterministic batch order 已明確記錄，未決議事項有書面 owner。

### Phase 1：五欄 upstream shape、ratio 文件同步與 API execution regression

目的：優先保護資料形狀的核心 invariant，將既定的 5.0 ratio policy 轉為可執行測試，並修正會浪費 API 額度的 execution bugs。

Phase 1 應依風險遞增拆成獨立小批：先處理一般 4xx retry 與 dry-run coordination 的最小 bug fix，再進行 ratio 文件/default/fixture 同步，最後才處理 assembler bullet rejection 與 curate follow-up。

工作：

1. 先新增 table-driven regression tests，覆蓋 §3.1 所列的 partial、empty 與 whitespace bullet combinations，並確認有效 `publish_summary` 與 `publish_link` shape 繼續通過。
2. 在 assembler 寫入前實作防禦性 validation。`publish_summary` 只能接受三個非空、非 whitespace bullet，`publish_link` 只能接受三個 `NULL` bullet。
3. 對每個非法 shape 斷言沒有新 handoff row、既有有效 row 不被覆寫、同次 assembly 的合法 item 仍完成，且回傳的 rejected 統計與 operator 診斷資訊包含可行動的違規原因。
4. runtime validation、regression tests 與 `DATA_CONTRACT.md` 的 handoff materialization 規則必須同一小批提交；同時建立給 curate owner 的 cross-module follow-up。不得將 item 靜默改為其他 downstream action。
5. 將 ratio 文件、`ValidationConfig` default 與 test fixtures 同步至已決定的 5.0，並以 temporary YAML 或真實 active config loader 建立 regression test。
6. 保留既有純函式 validator tests，新增的整合測試必須驗證有效 config 確實流入 `fetch_llm_translation()`。
7. 新增一般 400、401、403、404 僅發出一次 request 的 regression tests，再將非 429 的一般 4xx 排除於 retry handler。修正與 tests 必須同一小批提交。
8. 新增 dry-run 與正式 run 互斥的 regression test，修正 lock 或等效協調行為，並同步更新 `EXECUTION_POLICY.md`。

驗收：

- 所有 8 種 null/non-null bullet 組合均有明確預期。
- 空字串與 whitespace-only bullet 不會被當成有效內容。
- 非法 upstream item 不會寫入或覆寫 handoff，且不阻擋同次 assembly 的合法 item。
- config regression test 驗證 5.0，不使用手寫 1.2 `MagicMock` 值替代正式 runtime。
- 一般 4xx 不會耗盡 retry budget；dry-run 不會與正式 run 重複執行 API request。
- 不發出真實 HTTP request。

### Phase 2：request、response、其餘 retry、rate limit、concurrency 與 process lock

目的：在 Phase 1 修正一般 4xx 與 dry-run lock 後，讓其餘 `EXECUTION_POLICY.md` 可觀察的執行規則受到 deterministic tests 保護。

工作：

1. 為 `_build_request_payload()` 新增 tests，驗證所有五個內容欄位、target language label、request defaults、`json_object` fallback 與 optional strict structured-output schema。
2. 為 `_parse_response_content()` 補 empty choices、missing/empty/non-string content、refusal、無效 JSON 與有效五欄 JSON tests。
3. 使用 fake client 或 mocked `AsyncClient.post()` 覆蓋 429、5xx、timeout、network error、validation error 與 parsing error；一般 4xx regression 由 Phase 1 保護。
4. 對每個 retryable 與 non-retryable case 斷言 post 呼叫次數、sleep 次數及由 policy 導出的 backoff。除 429 外，一般 4xx 不重試。
5. patch 或注入 jitter 與 `asyncio.sleep`，測試不得實際等待。
6. 以可控制的 in-flight counter fake client 驗證 semaphore 上限，並從測試 config 取得預期值。
7. 驗證 rate-limit stagger delay 的計算，不將 `20`、`60`、`1200` 等歷史或 active 數值硬編碼為通用契約。
8. 以 temporary lock path 和 timeout 保護的 subprocess test 驗證 ProcessLock 拒絕第二個 process，並於 release 後允許重新取得。
9. 將 tests 中未關閉的 `httpx.AsyncClient` 替換為 explicit async lifecycle 或 fake client。

驗收：

- 不使用真實網路、真實時間等待或未控制的隨機 jitter。
- 每個 retry test 都能辨識 retry eligibility、上限與最終 failure persistence。
- concurrency 與 lock tests 不依 task 完成順序，連跑不造成 flake。

### Phase 3：state machine、queue、CLI 與資料庫工具契約

目的：以 public execution surface 與真實 temporary SQLite behavior 保護狀態與使用者可見操作；migration splitter、index 等內部工具測試在不阻擋前兩個 phase 的前提下漸進補強。

工作：

1. 建立 pending、stale、retryable failed、locked failed、completed、bypassed completed 與無 row 的 queue eligibility matrix。
2. 覆蓋 retry count 遞增、retry limit logical lock、operator force 成功重置 retry count，以及 forced failure 完整保留既有資料。
3. 以 `orchestrate_run()` 與 Click CLI tests 驗證 `--force`、`--dry-run`、`--preview-prompts`、`--batch-size`、缺少 API key、全數失敗與部分失敗的 exit/output contract。force tests 必須分別驗證：(a) fresh completed 成功時原子覆寫並重置 retry count，(b) fresh completed 的 API、validation、取消或程序中斷不改變 row，(c) stale row 即使帶 `--force` 仍走 normal stale retry path。
4. 將 batch selection 改為先以 `approved_at ASC, parent_content_id ASC` 選取至多 `batch_size` 篇 distinct source item，再展開每篇的所有 eligible language tasks。加入多來源、多語言的 batch boundary test，驗證同一篇文章的 eligible tasks 不會被 batch 邊界切開、已完成語言不會重做、每次選定文章數不超過 `batch_size`、相同輸入順序穩定，以及 run summary 同時顯示 source item 與 language task 計數。
5. 對 repository upsert、stale detection 與 pending selection 建立 direct tests。`split_sql_statements()`、`run_migrations()`、`transaction()`、index 語意與 migration failure tests 依風險與變更範圍安排，不阻擋已完成的 state、CLI 和 execution regression。
6. 以 SQLite metadata 與實際寫入行為測試 translate-owned DDL 的 FK、UNIQUE、CHECK、cascade 和 index 語意。index tests 應檢查欄位與 unique behavior，不鎖純實作性的 index 名稱。
7. CLI tests 必須使用 temporary config、temporary workspace 的 `data/canonical.db`、mocked HTTP 與 patched `load_dotenv()`，使 lock path、DB 與環境都不會逸出 temporary workspace。

驗收：

- dry-run 可呼叫 mocked LLM，但不留下 translate 資料庫寫入。
- preview 不呼叫 LLM、不更新 stale state、不寫入 row。
- force 的 selection、fresh completed 失敗保留、成功原子覆寫，以及 stale force 的 normal retry 行為都由 public path 測試，不只靠手動 task dict。
- migration failure 不寫入 migration log，transaction rollback 可由真實 SQLite 驗證。

### Phase 4：upstream handoff contract 與測試結構收斂

目的：降低手動複製 schema 與重複 scenario 的漂移風險，同時保留現有有效 coverage。

前置條件：

1. 使用已核准的 upstream migration contract 或正式 handoff schema fixture。
2. 不建立跨模組 runtime dependency，也不以其他模組的 private test helper 作為契約來源。
3. 先完成 Phase 1 至 3 的 coverage mapping 與 regression additions。

工作：

1. 建立 translate consumer handoff test，驗證 `approved_content_record` 的五欄、fingerprint、content language、FK 和 queue read behavior。
2. 對未來 edit-originated handoff 保留明確的 pending test contract 或 TODO，不假設目前 curate-only assembler 已支援 edit。
3. 將 `test_five_field_contract.py` 維持為精確、低階 contract tests。
4. 將 `test_translate.py` 收斂為少量 public journey tests，例如 assemble → preview → run、stale retry、force rollback。
5. 在共用 fixture 已完整取代的前提下，移除重複 schema setup、seed helper、response factory 和重複 scenario。每次刪除都應在 PR 中列出被取代的 node IDs 與替代測試。
6. 若檔案拆分能明確改善導航，再依 `contract`、`database`、`execution`、`cli` 職責拆檔。拆分必須獨立提交，不得藉機改變 runtime behavior。

驗收：

- 任何移除的測試都有更精確的替代 coverage。
- handoff test 以正式 schema contract 為準，不將手動 fixture 誤作 canonical DB。
- test 檔職責清楚，client lifecycle 與 temporary resource cleanup 皆可預測。

## 5. 建議提交與驗證方式

每個 phase 應可獨立審查與合併。凡是改變 runtime behavior 的工作，例如 bullet shape enforcement、一般 4xx retry、dry-run coordination、batch unit 或 forced rerun state，必須和相對應 regression tests 及必要文件更新位於同一小批提交。ratio limit 5.0 與 metadata freshness marker 已有歷史和實作證據，屬文件、default 與 fixture 同步工作，不得重開為未經證據支持的 runtime policy 變更。純測試支援重構不得夾帶翻譯政策、prompt 業務語意或 provider 選型變更。

每批最少執行：

```powershell
python -m pytest modules/translate/tests -q
python -m modules.translate.src.cli validate
```

涉及 retry、rate limit、semaphore、process lock 或 async task scheduling 時，額外執行：

```powershell
1..5 | ForEach-Object { python -m pytest modules/translate/tests -q }
```

涉及 handoff 或 migration 時，額外執行：

```powershell
python -m pytest modules/translate/tests -q
git diff --check
```

目前其他模組間仍有 pytest `tests` package 收集衝突。此計畫在該問題解決前只要求 translate suite 單獨執行，不將跨模組單一 invocation 視為本計畫的完成前提。

## 6. 完成定義

本方案完成時應滿足：

1. 五欄 bullet 的全有或全無 invariant，包括 `NULL`、空字串與 whitespace edge cases，均有 deterministic regression tests，且 runtime behavior 與 locked contract 一致。
2. ratio limit 的 5.0 policy 與 metadata freshness marker 已在文件、default、active config、runtime 與 tests 中一致；`author_metadata` 明確記錄 upstream freshness marker。batch 依來源文章數，以 `approved_at ASC, parent_content_id ASC` 穩定選取，且不會由 batch 邊界切開同一文章的 eligible language tasks。
3. request construction、response parsing、一般 4xx 單次失敗、dry-run API coordination、其餘 retry eligibility、retry 上限、backoff、rate limit、semaphore 與跨 process lock 都有不依賴真實網路或時間的 tests。
4. state transition、retry lock、force、dry-run、preview、batch selection、CLI failure surface，以及與本輪改動相關的 migration、transaction、SQLite constraints 都有 public 或 direct contract tests。fresh completed force 不會持久化為 `pending`，失敗或中斷時保持既有可發布內容，stale force 則維持 stale retry 語意。
5. translate 對 upstream handoff 的 consumer contract 以正式 migration/schema 驗證，不再只依賴重複的手動 table fixture；rejected upstream bullet shape 有連結至 curate owner 的可追蹤 follow-up。
6. 已移除或合併的測試均由更精確測試取代，沒有因重構而減少契約覆蓋；HTTP client 與 temporary resource lifecycle 不再依賴垃圾回收。
7. translate suite 在重複執行抽查中穩定通過，沒有新增 flaky failure。

## 7. 已確認且不再阻塞的結論

1. **Content ratio policy：** 5.0 是 commit `bc165eb` 刻意設定的正式 limit。工程應同步文件、`ValidationConfig` default 與 fixture，並加入 config-loading regression，不把 active config 改回 1.2。
2. **Delta pre-screen：** `author_metadata.upstream_updated_at` 是現行正確 freshness marker。Phase 1 更新 `DATA_CONTRACT.md` §1.1、§1.5、§2.1.2，因為 handoff row 的 `updated_at` 每次 assembly 都會更新，不能拿來和 upstream timestamp 比較。
3. **一般 HTTP 4xx retry：** 除 429 外，一般 4xx 應直接失敗，不得落入 retry handler。若未來 provider 有明確可重試 4xx，必須以 provider-specific 文件、實作與 regression test 一起加入。
4. **Dry-run API coordination：** dry-run 會呼叫 LLM API，必須和正式 run 互斥或有等效協調，避免重複 API execution。
5. **CLI environment isolation：** 所有 CLI tests 必須 patch `load_dotenv()`，只提供必要的非機密 temporary environment，不讀取 workspace `.env`。
6. **Batch unit：** `batch_size` 代表來源文章數。runner 以 `approved_at ASC, parent_content_id ASC` 先選取至多 N 篇具有 eligible translation 的文章，再展開每篇的所有 eligible target-language tasks。API concurrency 與 request frequency 分別由 `max_concurrent_requests`、`rate_limit_per_minute` 控制。
7. **Illegal bullet shape：** assembler 在寫入 `approved_content_record` 前採零信任防禦性驗證。非法 item 不得寫入或覆寫既有有效 handoff，必須以可診斷的 rejected outcome 回報，且不得阻擋同次 assembly 的合法 item。
8. **Forced rerun state：** fresh completed row 的 `--force` 是非持久化執行模式。成功時原子覆寫，失敗、取消或程序中斷時既有 completed row 完全不變。已 stale row 不適用此保護，依 normal stale retry path 處理。
