# Classify 模組測試可維護性改善方案

**狀態：** 建議方案，待工程師排程與實作
**日期：** 2026-07-30
**修訂：** 2026-07-30，依審查結果補入一般 4xx 重試 bug、雲端 mini-proxy 部署脈絡、structured-output guard 定位，以及 ingest 測試重構完成後的 handoff 前置條件；同日二審，澄清既有 validation 拒絕行為的措辭，並補 4xx 修正時的註解同步事項
**範圍：** `modules/classify/` 的 Python 測試、測試支援結構及可直接驗證的測試契約
**非範圍：** 不在本方案中改變分類政策、prompt 的業務語意、LLM provider 選型、資料狀態機或模組邊界；不把 MVP 文件中的保守吞吐參數恢復為現行生產設定

## 1. 背景與目標

目前 `classify` 模組有 17 項 pytest 測試，單獨執行可全數通過；`python -m modules.classify.src.cli validate` 亦可通過。既有測試已有效保護 queue eligibility、分類結果寫入、allowlisted `additional_signals`、單一 item 失敗隔離、dry-run、preview 與 SQLite 寫入鎖。

盤點發現，LLM request 契約、回應型別嚴格性、retry/rate/concurrency 執行規則、migration 工具及 ingest-to-classify handoff 仍缺少直接測試。另有兩項已確認的 production behavior 問題必須以小批修正連同 regression test 處理：response validator 接受 Python boolean，以及一般 HTTP 4xx 被錯誤地重試。

目標：

1. 優先強化雲端 mini-proxy `json_object` fallback 下唯一的 response validator 防線。
2. 修正一般 HTTP 4xx 的 retry 行為，使其符合 `EXECUTION_POLICY.md` 的 eligible-error 規則。
3. 將 schema migration 與上游資料契約從手動 fixture 風險中隔離出來。
4. 為 structured-output 開關建立 guard，避免將來啟用該路徑時重現 schema 拒絕事故。
5. 保留既有測試，只有新測試明確取代其保護規則時才合併或移除。

## 2. 已確認基線

- 模組測試：`python -m pytest modules/classify/tests -q`
- 目前結果：17 項通過。
- Active config 驗證：`python -m modules.classify.src.cli validate` 零 error。
- repo active config 為 `batch_size: 200`、`max_concurrent_requests: 20`、`rate_limit_per_minute: 1200`。維護者已確認此方向是正確的 production behavior，但工程開始前仍應確認雲端部署的非機密 execution policy 是否與 repo 一致。
- 雲端 production 確認經由自建 mini-proxy 呼叫 LLM。repo 中 mini-proxy 設定為 `supports_structured_output: false`，因此 `json_object` fallback 是目前受測與應優先保護的路徑。
- `EXECUTION_POLICY.md` 中的 `20 / 3 / 60` 是文件記載的歷史保守 defaults，不是 active config 的測試期望；所有限流與並行測試須由受測 config object 推導。
- `python -m pytest modules/ingest/tests modules/classify/tests --collect-only -q` 已可成功收集 183 項，故 ingest/classify handoff 可使用單一 invocation 驗證。

這些基線不是未來 CI gate 的替代品，但可用於判斷各 phase 是否引入回歸。

## 3. 已知測試缺口與維護風險

### 3.1 Structured-output request schema 沒有回歸測試

`docs/api_schema_error_analysis.md` 記錄過 OpenAI strict structured output 因 JSON schema 缺少 `additionalProperties: false` 而回傳 HTTP 400。現行 `JSON_SCHEMA` 已包含該欄位，且 experimental fields 已採 required-but-nullable 形式，但目前測試不檢查 outbound `response_format`。

此項不是目前 mini-proxy fallback 路徑的主要保護，而是當 `openai` provider 被啟用，或日後將 mini-proxy 的 `supports_structured_output` 打開時的低成本開關 guard。現行 production 路徑的優先級低於 §3.2 與 §3.3。

應直接驗證：

- `supports_structured_output=True` 時，payload 使用 `json_schema`、`strict: true`、完整 `required` 清單和 `additionalProperties: false`。
- experimental fields 同時接受字串與 `null`。
- `supports_structured_output=False` 時，payload 使用 `json_object`，不附帶 strict schema。
- model、message、temperature、top_p 與 output token 設定都由 active config object 正確帶入。

實作 Phase 1 時，應同步更新 `PROMPT_CONTRACT.md` §3 的 schema 範例，使其包含現行的 `additionalProperties: false` 與 required-but-nullable experimental fields，避免文件再次與 outbound schema 漂移。

### 3.2 Response validator 接受不應接受的 Python boolean

Python 的 `bool` 是 `int` 的子類別。現行 validator 因此接受 `True` 作為 `classification_confidence` 與 `governmental_involvement`，並分別轉為 `1.0`、`1`。這與 JSON schema 的 number/integer 語意不符，也會掩蓋 provider 回傳型別錯誤。

在 `json_object` fallback 下，validator 是模型原始輸出進入 canonical DB 前唯一的 schema gate。應優先修正與直接測試：

- 拒絕 boolean 作為 confidence 或 governmental involvement。
- 維持並回歸測試既有的字串數字、`NaN`、正負 infinity 與超出範圍數字拒絕行為。
- 維持並回歸測試既有的空白 language code 拒絕行為（現行 validator 已拒絕，無需改 code），並保留 classification reason 的既有最大長度契約。
- 確認 optional experimental signals 缺席或為 `null` 時不寫入 `additional_signals`，未知 key 則被安全丟棄。

空白 `classification_reason` 目前通過 schema 與 validator。是否拒絕它是新的品質政策，可能使模型回應增加 retry，不應和 boolean bug 修正混在同一批，保留至 §7 決策後才實作。「單一句子」亦維持 prompt guidance，不以脆弱的句點計數作為 runtime gate。

### 3.3 Retry、rate limit 和 concurrency 契約覆蓋不足

現有測試覆蓋 HTTP 500、non-string content 及 model refusal，但未直接驗證 execution policy 所要求的完整行為。更重要的是，`fetch_llm_classification()` 目前把一般 4xx 與 429/5xx 一樣放進 `httpx.HTTPError` retry handler，因此一般 4xx 會被重試到上限。這違反 `EXECUTION_POLICY.md`，也會在 mini-proxy 回傳 401、403 或 schema/config 400 時放大請求、延遲與 token 成本。

應以 regression test 和明確 production behavior 修正一併處理：

- HTTP 429、timeout 與 connection/network error 會在上限內重試。
- 一般 4xx 不重試。
- invalid JSON、空 choices、schema validation failure 會重試，最後失敗時不寫入結果。
- retry 次數、`asyncio.sleep` 次數與 delay 來源可由設定值推導。
- semaphore 確實不會讓 in-flight HTTP request 超過 `max_concurrent_requests`。
- rate-limit scheduler 的 request 間距符合設定，且測試不使用真實 sleep。

這些測試須注入或 mock `asyncio.sleep`、random jitter 與 HTTP client，避免使用實際時間造成 flake。

### 3.4 Database migration 與 transaction helper 沒有直接測試

`run_migrations()`、`split_sql_statements()` 與 `transaction()` 目前只被整合流程間接覆蓋。應建立直接測試保護：

- migration re-run idempotency。
- 失敗 migration 不寫入 `schema_migrations`，且同一 migration 的 DDL 回滾。
- SQL splitter 對空白、註解、單一 statement 與多 statement input 的行為。
- transaction success commit、exception rollback 及 `commit=False` dry-run rollback。
- `classification_result` 的 UNIQUE、CHECK、FK cascade、index 語意及 upsert 不改變 surrogate key。

### 3.5 Ingest handoff schema 在 classify 測試中被手動複製

`create_mock_ingest_tables()` 在 classify 測試內手動建立最小的 `source_item` 與 `source_item_text` schema。它目前與 ingest v001 migration 大致一致，但上游 schema 改動時，classify 可能在不相容的 mock schema 上繼續測試通過。

ingest 測試重構已完成並合併，且 ingest/classify 可在單一 pytest invocation 收集。因此 Phase 4 可直接以已合併的 ingest migration contract 建立 temporary SQLite DB，再套用 classify migration，驗證 queue eligibility 及 FK 行為；不得依賴 ingest 的內部 test helper 或新增 runtime coupling。

### 3.6 測試支援程式與 response fixture 重複

`seed_test_item()` 在兩個 test class 中重複，成功 LLM response 也有多份近似 dict。這不影響現有正確性，但 provider schema、上游欄位或預設輸出改動時會提高維護成本。應在新增測試案例時才抽取共用 helper，避免只為縮短檔案而進行大規模機械拆分。

## 4. 分階段執行方案

### Phase 0：保護基線與最小測試支援

目的：固定目前可通過的基線，建立後續測試可共用的最小 fixture，不改 production behavior。

工作：

1. 記錄 17 項基線及模組單獨執行指令。
2. 抽取 temporary classify database、seed item、有效 LLM JSON response 的最小 helper 或 fixture。
3. 讓 helper 顯式接受 status、reason、provider capability 與 response content，不隱藏重要測試前置條件。
4. 記錄雲端部署的非機密 execution-policy 值是否與 repo active config 一致，不記錄 API key、proxy credential 或其他 secret。
5. 不在本 phase 改動手動 ingest schema fixture 的 ownership。

驗收：

```powershell
python -m pytest modules/classify/tests -q
python -m modules.classify.src.cli validate
```

預期：既有測試數量不減少，active config 維持可驗證。

### Phase 1：Fallback response validator 與 structured-output guard

目的：先修正雲端 mini-proxy fallback 路徑唯一 schema gate 的 boolean 漏洞，再為可選 strict structured-output 路徑建立開關 guard。

工作：

1. 為 `validate_classification_response()` 新增 direct regression tests，先鎖定拒絕 boolean，並覆蓋既有數值邊界、language code、nullable experimental signals 與 unknown-key filtering。
2. 修正 validator 對 boolean 的接受行為，與相應 tests 同一小批提交。
3. 為 `_build_messages()` 與 `_build_request_payload()` 新增直接 unit tests，覆蓋現行 `json_object` fallback。
4. 另斷言 strict JSON schema 的 `additionalProperties: false`、required fields 與 nullable experimental fields，把 structured-output 路徑定位為 provider capability 的開關 guard。
5. 同步 `PROMPT_CONTRACT.md` §3 的 schema 範例至現行 outbound schema。
6. 保留 unknown response field 被丟棄的現有 policy，不將空白 `classification_reason` 升格為錯誤，除非 §7 已完成決策。

驗收：

- 不發出真實 HTTP request。
- 可明確區分 fallback response regression、structured-output guard regression 與 prompt interpolation failure。
- 舊有 additional-signal filtering 規則維持不變。

### Phase 2：Retry、限流、並行與失敗隔離

目的：修正一般 4xx 被錯誤重試的既有 bug，並使 `EXECUTION_POLICY.md` 的可觀察執行行為受到 deterministic tests 保護。

工作：

1. 先新增一般 4xx 僅呼叫一次的 regression test，確認現有失敗後修正 retry exception classification，兩者必須同一小批提交。修正時一併更新 `fetch_llm_classification()` 內聲稱「4xx 不重試」的既有註解——該註解目前描述的是意圖而非行為，修正後才與實作一致。
2. 以 mock HTTP client 覆蓋 429、timeout、network error、5xx、一般 4xx、invalid JSON、empty choices 與 validation failure。
3. 驗證每個 retryable 情況精確呼叫至設定上限，non-retryable 4xx 與 model refusal 僅呼叫一次。
4. 注入或 mock jitter、sleep 與 clock，斷言 backoff 而不等待真實時間。
5. 建立可控制的 fake client，量測最大同時 `post()` 數，驗證 semaphore。
6. 驗證限流排程的 sleep 值由受測 execution policy 計算，不硬編碼 `20 / 3 / 60` 或未確認的雲端數值。
7. 驗證最終失敗項目不寫入 `classification_result`，成功同批項目不受影響且失敗項仍可由 pending query 取得。

驗收：

- 不使用真實網路或真實等待。
- concurrency/rate 測試可重複執行且不依序敏感。
- 每個測試只鎖定一類可觀察的 retry 或執行規則。

### Phase 3：Database、migration 與 CLI 契約

目的：讓 classify 自有 DDL 與 transaction helper 得到直接保護，降低文件、DDL 與 repository 漂移。

工作：

1. 新增 `test_database.py` 或 `test_migrations.py`，直接測試 §3.4 的 migration 與 transaction cases。
2. 透過 SQLite metadata 驗證 `classification_result` table、FK delete action、UNIQUE/CHECK 與必要 index 的欄位/unique 語意，不鎖定純實作性的 index 名稱。
3. 用 temporary migration directory 建立故意失敗的 migration，驗證 migration log 與 schema 的 rollback。
4. 補 CLI `validate`、`migrate` 及 `run` 的 exit code/summary contract 測試，特別是 config error、migration error、全數 item failure、preview 與 dry-run。

驗收：

- 以真實 temporary SQLite 行為驗證資料庫契約，不以 mock `IntegrityError` 取代。
- 所有 CLI 測試使用 temporary config 和 DB，不觸及 workspace canonical DB 或 `.env` secret。

### Phase 4：Ingest handoff contract 與測試結構收斂

目的：使用已完成並合併的 ingest migration contract，以最少跨模組測試防止 handoff 漂移，並只在新增案例使現有單檔難以維護時再整理測試檔。

前置條件：

1. 使用現行已合併的 ingest migration 與 `source_item` / `source_item_text` handoff contract。
2. 不使用 ingest 的內部 test helper 作為 runtime dependency。

工作：

1. 以真實 ingest migration 加上 classify migration 建立 temporary DB。
2. 驗證 classify pending selection 對 `completed`、各允許的 low-context reason、`failed` 與 `post_cleanup_empty` 的接收/排除規則。
3. 驗證 classify migration 對 ingest-owned `source_item` 的 FK cascade 與上游 text row restrict 行為。
4. 將現有手動 schema fixture 降為純 isolated unit test 專用，或在 handoff test 足以取代時移除。
5. 僅在 Phase 1 至 3 讓 `test_classify.py` 顯著難以導航時，按 config/request-response/database/orchestration 職責拆檔；拆分須獨立提交且不變更測試行為。

驗收：

- 跨模組測試只依賴已合併的 migration contract，不建立新的 runtime coupling。
- 測試檔拆分前後，收集到的測試總數不減少。
- 任何移動的測試都有可對照的新 node ID，且 assertion 語意不變。

## 5. 建議提交與驗證方式

每個 phase 應可獨立審查與合併。Phase 1 的 boolean validator 修正與 Phase 2 的一般 4xx retry 修正都屬 production behavior change，必須各自與相對應 regression tests 放在同一小批變更中；其餘純測試補強不應夾帶分類政策或 prompt 業務語意改動。

每批最少執行：

```powershell
python -m pytest modules/classify/tests -q
python -m modules.classify.src.cli validate
```

涉及 retry、限流或 semaphore 的修改，額外執行：

```powershell
1..5 | ForEach-Object { python -m pytest modules/classify/tests -q }
```

涉及跨模組 handoff 的修改，執行：

```powershell
python -m pytest modules/ingest/tests modules/classify/tests -q
```

ingest/classify 的 183 項 tests 已可合併收集。其他模組間仍可能存在 pytest package 撞名，但不影響 Phase 4。

## 6. 完成定義

本方案完成時應滿足：

1. mini-proxy fallback JSON response validator 會拒絕 boolean 型別漂移，且 fallback payload 與 structured-output capability guard 都有直接、deterministic regression tests。
2. 一般 4xx 僅執行一次；retry eligibility、retry 上限、failure isolation、rate limiting 與 semaphore concurrency 都有對應測試，且不依賴真實時間或網路。
3. migration、transaction、SQLite constraints 與 CLI failure surface 都有直接測試。
4. classify 對 ingest 的關鍵 DB handoff 已以穩定 migration contract 驗證，不再只依賴手動複製 schema。
5. repo active config 與雲端部署設定的差異已由維護者確認，測試不錯誤地把歷史文件 defaults 當成現行要求。
6. classify 套件及 ingest/classify handoff 套件在重複執行抽查中穩定通過，沒有新增 flaky failure。

## 7. 待工程師審查的決策

1. 是否將空白 `classification_reason` 拒絕為 runtime error？建議在明確確認模型回傳空字串應重試的成本與語意後才決定；在此之前，維持現行允許空字串的行為。「單一句子」維持 prompt guidance。
2. Phase 2 是否應將 jitter generator 和 sleep function 注入成明確依賴，或只在測試中 patch module-level 呼叫？建議優先採最小可測改動，避免為測試建立不必要 abstraction。
3. 雲端 deployment 的 non-secret execution-policy values 是否與 repo active config 完全一致？應在 Phase 0 記錄確認結果；無論結果為何，測試均從受測 config object 推導限流與並行期待。
