# Ingest 模組測試可維護性改善方案

**狀態：** 完成 — Phase 0–4 已完成並通過兩輪 Code Review（LGTM），Phase 6 拆分已執行並通過驗收  
**日期：** 2026-07-30  
**修訂：** 2026-07-30 審查修訂，Phase 1 增補 fetcher 時鐘注入、§7 決策 3 補上建議方向、原 Phase 5（可選整理）改為明確排程的 Phase 6（機械拆分）；2026-07-30 執行進度更新（詳 §8）；2026-07-30 Phase 6 拆分執行更新（詳 §8）  
**範圍：** `modules/ingest/` 的 Python 測試與測試支援結構  
**非範圍：** 不在本方案中改變 ingest 的資料契約、執行語意、清理流程或模組邊界

## 1. 背景與目標

目前 ingest 模組的 73 項 pytest 測試可通過，連續執行五次也沒有發現 flaky failure。現有測試已有效保護主要的成功流程、dedup race、item savepoint rollback、sanitization fallback 與 error-class persistence。

不過，測試盤點發現若干已實作或已由文件鎖定的契約尚未被直接驗證。此方案以小批、低風險、可獨立合併的方式補齊缺口，不將「測試補強」和「程式重構」混為一談。

目標：

1. 為純函式與明確分支建立直接、穩定的單元測試。
2. 為 `FETCH_EXECUTION.md`、`SOURCE_CONFIG_CONTRACT.md`、`STORAGE_SCHEMA.md` 已鎖定的行為補上契約測試。
3. 保留既有測試，只有在新測試明確取代其保護規則時才考慮合併或移除。
4. 在不先拆分 `test_integration.py` 的前提下，讓後續修改更容易定位失敗原因。

## 2. 已確認基線

- 模組測試：`python -m pytest modules/ingest/tests -q`
- 目前結果：73 項測試通過，另有 9 個 subtests 通過。
- 穩定性抽查：相同套件連跑 5 次，5 次皆通過。
- Active config：`python -m modules.ingest.src.cli validate` 零 error、存在既有 warning（缺少 `html_url` 與重複 `html_url`）。

這些基線不是長期 CI gate 的替代品，但可用於判斷本方案各階段是否引入回歸。

## 3. 已知測試缺口

### 3.1 Parser 沒有直接測試

`src/parser.py` 是純函式，但沒有 `test_parser.py`。應驗證：

- RSS 與 Atom 的基本 entry 解析。
- `description`、`summary`、`content` 的輸入 fallback。
- 無 link 時的 enclosure URL fallback。
- title、GUID、URL、日期缺失或無效時的正規化結果。
- URL tracking parameter 去除後的 parser-to-dedup 交接。

`FETCH_EXECUTION.md` 規定 parse failure 為 source-level failure，但 `feedparser` 對 malformed XML 可能透過 `bozo` 狀態回傳空 entries 而非 raise。實作前必須先確認並明定「哪些 parser 狀態構成 `parse_error`」，再為該規則撰寫測試。不得用測試假設 malformed feed 必然拋出例外。

### 3.2 Fetcher 缺少 network error 分支測試

`src/fetcher.py` 處理 `httpx.NetworkError` 並輸出 `network_error`，但 `test_fetcher.py` 未覆蓋此分支。其他主要分類已有測試，包括 timeout、HTTP 404、HTTP 500、HTTP 429 與 unexpected error。

### 3.3 Config 的負向與 warning 契約覆蓋不足

`src/config.py` 已實作但尚未直接測試的案例包括：

- 重複 source ID。
- 不存在的 schedule class 或 sanitization profile。
- 非法 `fetch_group`、`request_timeout_seconds`、`max_length`。
- 未知或型別錯誤的 `sanitization_overrides`。
- 缺少設定檔、空 YAML root、YAML 解析錯誤。
- 缺少或重複 `html_url` 與重複 `xml_url` 的 warning。

### 3.4 Orchestrator 的執行範圍與狀態路徑覆蓋不足

現有整合測試主要使用 `force=True`。這驗證強制執行時的主流程，但沒有證明 `force` 對 not-due 與 quarantined source 的 bypass 行為。

尚未有端到端測試的主要路徑：

- 304 cache hit 更新 `source_state` 與 `fetch_attempt`。
- `groups` 與 `source_ids` 的 scope filtering。
- disabled source、not-due source、quarantined source 的 skipped summary。
- `force=True` 確實略過 not-due 與 quarantine 檢查。
- `dry_run=True` 不建立 `fetch_run`，不寫入 source state、item 或 attempt。
- 連續 source failure 達門檻、轉為 quarantined、後續 run 被跳過的完整循環。

### 3.5 Migration 與資料庫契約覆蓋不足

現有整合測試只確認七個主要資料表存在。`STORAGE_SCHEMA.md` 已鎖定的 DDL 契約應直接測試：

- unique key、foreign-key restrict/cascade direction、bounded-value CHECK。
- 規定 index 的語意存在，包括資料表、欄位順序與 unique 屬性，而不依賴 SQLite index 名稱。
- migration idempotency。
- 失敗 migration 不寫入 `schema_migrations` 記錄，且交易回滾。
- `split_sql_statements()` 對註解、空白、multi-statement input 的行為。

Cleanup execution 已明確 deferred。因此本方案不新增 purge/cleanup workflow 測試。資料表的 FK 與 raw record 刪除安全性，僅以已存在的 DDL 契約為限。

## 4. 分階段執行方案

### Phase 0：保護基線與測試支援

目的：在新增測試前建立可重複使用的 fixture 與執行準則，不改動既有斷言。

工作：

1. 記錄現有測試數量與執行指令。
2. 在 `modules/ingest/tests/` 建立最小共用 fixture 或 helper，集中 temporary config、SQLite migration 與 mock feed 建立。
3. 僅在新增案例需要時抽取 helper；不單獨為縮短檔案而拆分 `test_integration.py`。

驗收：

```powershell
python -m pytest modules/ingest/tests -q
```

預期：測試數量不減少，既有測試全數通過。

### Phase 1：低成本直接單元測試

目的：優先保護純函式及 fetcher 的明確錯誤分類。

工作：

1. 新增 `test_parser.py`，覆蓋 RSS、Atom、body fallback、enclosure URL、遺漏欄位與正規化交接。
2. 在 `test_fetcher.py` 新增 `httpx.NetworkError` 的 retry 與最終 `network_error` 結果斷言。
3. 在 `test_scheduler.py` 移除未使用 import，或新增 timestamp parse/format round-trip 與 offset timestamp 測試。
4. 為 `add_days_to_iso8601()` 增加日期跨月、跨年與 UTC 輸出測試，或將其納入已有的 orchestrator helper test。
5. 為 `test_fetch_transient_429_retry_after_http_date` 注入或 mock 時鐘，消除對真實 `datetime.now()` 的依賴。目前斷言以容差（`20 < sleep <= 30`）吸收執行耗時，連跑雖穩定，但在高負載環境仍有邊界時間 flake 風險。

parse failure 決策：

1. 先以 fixture 確認 `feedparser` 對 malformed input 的實際回傳。
2. 若要將 `bozo` 或空內容視為 parse failure，先更新 parser/orchestrator 的明確規則與相應文件，再加入 source-level `parse_error` 測試。
3. 若不視為 failure，則測試應記錄該輸入被當作零 entries 的既定行為，避免假陽性期待。

驗收：

- 新測試不依賴真實網路、系統時間或來源資料。
- `network_error` 的 retry count、sleep 次數與 error class 都有明確斷言。
- Parser 測試可獨立執行。

### Phase 2：Config 契約與 active-config 檢查

目的：使設定驗證的已實作分支和文件規則得到保護。

工作：

1. 以 parameterized test 或 subtest 補足 §3.3 的負向案例。
2. 分別測試 error 與 warning，避免只檢查 `errors == 0` 而忽略 warnings。
3. 新增一個測試，載入 active `modules/ingest/config/` 並要求零 validation error。
4. active config 的既有 warnings 不升格為 error；應以明確 allowlist 或期望 warning 類別斷言，避免資料維護改動被無意阻擋。

驗收：

- 每一個重要的 cross-file reference failure 都指出檔案、source ID 與欄位。
- active config 不進行網路或資料庫操作。
- 測試不將目前的 warning 誤判為 schema error。

### Phase 3：Orchestrator 操作契約整合測試

目的：補齊 fetch execution 的 scope、cache 與 source-state 行為。

工作：

1. 在 `test_integration.py` 或按職責新增的整合測試檔中，使用 temporary SQLite DB 與 mocked `fetch_feed`。
2. 測試 304 分支的 ETag / Last-Modified 保留、health reset、attempt outcome 與零 item count。
3. 測試 `groups` 和 `source_ids` 只選擇預期 source，並正確輸出 `run_scope` 與 due count。
4. 測試 disabled、not-due、quarantined 的 skip reason 和 summary count。
5. 建立既有 state 後，測試 `force=True` 對 not-due 與 quarantined source 的 bypass。
6. 測試 dry-run 不建立 fetch run 或任何 ingest 寫入。
7. 測試連續失敗的 health transition，直到 quarantine，並驗證下一次非 force run 不發出 HTTP request。

驗收：

- 每個測試只針對一個操作契約。
- 所有時間使用固定值或可控制時鐘，避免睡眠或真實時間邊界。
- 不對既有成功流程、dedup race 與 item rollback 測試做行為弱化。

### Phase 4：Migration 與 repository 契約測試

目的：讓資料庫 schema 受到自動化保護，防止文件、DDL 與 repository 漂移。

工作：

1. 新增 `test_database.py` 或 `test_migrations.py`，直接檢查 SQLite metadata。
2. 驗證七個 domain table、`schema_migrations`、必要 index 的資料表/欄位順序/unique 屬性、unique constraints 與 FK actions。
3. 用真實 SQLite constraint，而非 mock exception，驗證代表性 CHECK、unique 與 FK restriction。
4. 驗證 migration 重跑不重複套用。
5. 以 temporary migration directory 建立故意失敗的 migration，確認 schema migration log 不記錄失敗檔且 DDL transaction 回滾。
6. 直接測試 `split_sql_statements()` 的 SQL 註解、空白、單一與多個 statements。

驗收：

- 測試以 schema metadata 和真實 SQLite 行為驗證，而非只判斷資料表名稱存在。
- 不為尚未實作的 cleanup execution 建立 workflow 測試。

### Phase 6：測試結構拆分（機械重構）

（原「Phase 5：可選的測試結構整理」已併入本 phase，由條件式改為明確排程，故 phase 編號跳過 5。）

目的：Phase 3 允許新整合測試按職責進新檔；Phase 1 至 4 完成後，`test_integration.py` 將成為未按職責組織的遺留層，新舊兩套結構並存。為維持長期可維護性，拆分是必要的後續 phase，但不得阻擋 Phase 1 至 4 的契約測試補強。

時機與護欄：

1. 在 Phase 1 至 4 全部完成後才執行——只有那時才看得到測試套件的完整形狀，按職責分檔才分得對。
2. 測試行為不變：可搬移測試函式並抽取共用 helper 或 fixture，但不得改變斷言、測試涵蓋的規則、被測 production behavior 或測試名稱的語意。若目前的 `unittest.TestCase` setup 無法直接轉成 pytest fixture，應保留相容的 setup 方式。
3. 獨立提交：拆分單獨成一個 commit/PR，只包含測試檔重組與支援 helper/fixture 抽取，不夾帶 production behavior 變更。

工作：

1. 依責任將 integration cases 分為 success/cache、scope/state、dedup、persistence isolation、error contract。
2. 抽出重複的 temporary config、mock feed、migration setup fixture 到共用位置。
3. 保持測試名稱描述規則與可觀察結果，不以抽象 helper 隱藏重要前置條件。

驗收：

- 拆分前後 `python -m pytest modules/ingest/tests --collect-only -q` 的測試總數一致，且每個舊 node ID 都有一個明確的新 node ID 對照。檔案路徑變更會改變 pytest node ID，因此不得要求字串名稱完全相同。
- 拆分後完整套件一次跑通。

## 5. 建議提交與驗證方式

每個 phase 應可單獨審查、獨立合併，且不混入 production behavior change，除非 parser failure 語意已先完成明確決策。Phase 6 的拆分必須為獨立提交，只含測試檔重組與 helper/fixture 抽取，便於審查者確認未夾帶 production behavior 變更。

每批最少執行：

```powershell
python -m pytest modules/ingest/tests -q
```

涉及 timing、retry 或 concurrency 的修改，額外執行：

```powershell
1..5 | ForEach-Object { python -m pytest modules/ingest/tests -q }
```

涉及設定測試的修改，額外執行：

```powershell
python -m modules.ingest.src.cli validate
```

## 6. 完成定義

本方案完成時應滿足：

1. Parser、network error、config validation、scope/state、304、dry-run、quarantine 與 DDL contract 都有對應的自動化測試。
2. 所有測試使用本地 fixture、temporary SQLite DB 或 mock HTTP，不依賴真實外部來源。
3. ingest 文件已鎖定的規則有可追溯的測試保護，且沒有為 deferred cleanup 功能虛構測試。
4. 完整 ingest 套件測試穩定通過，並在重複執行抽查中沒有新 flake。
5. 整合測試已按職責拆分完成（Phase 6），拆分前後測試總數一致、舊新 node ID 可一對一對照，且無測試邏輯或 production behavior 改寫。

## 7. 待工程師審查的決策

1. `feedparser.bozo`、無法解析的 XML、空 entries 是否應以何種條件映射為 `parse_error`？
2. active config warning 的 allowlist 應放在測試中，還是只斷言 warning 類別，避免來源資料維護頻繁修改測試？
3. Phase 4 的 schema metadata assertions 應鎖定完整 index 名稱，還是只鎖定等價的 indexed column / uniqueness / FK 語意？
   - 審查建議：鎖語意、不鎖名稱。`STORAGE_SCHEMA.md` 已聲明 SQL 語法細節可因引擎而異，索引名稱屬實作細節；測試應鎖定每個規定 index 的資料表、欄位順序與 unique 屬性，以及 FK delete action 與 CHECK 接受值，避免未來索引改名造成無意義的測試失敗。

在上述決策完成前，可先執行 Phase 1 中不涉及 parse-failure 語意的案例，以及 Phase 2 的純 config 測試。

**決議（2026-07-30 由維護者裁定）：**

1. bozo 映射為 `parse_error`；Code Review 進一步限定為**僅語法錯誤**（`SAXParseException`），可回復 bozo（如 `CharacterEncodingOverride`）不算 parse failure。規則已鎖入 `FETCH_EXECUTION.md` §9。
2. 嚴格映射：active config 的既有 warning 逐字鎖定於 `test_config.py::EXPECTED_ACTIVE_CONFIG_WARNINGS`，任何增減都使測試失敗。
3. 採審查建議：鎖語意、不鎖名稱。

## 8. 執行進度（2026-07-30 更新）

測試基線：73 passed → **166 passed, 104 subtests passed**；完整套件五連跑全綠；`python -m modules.ingest.src.cli validate` 零 error。

Phase 6 拆分後基線不變：**166 passed, 104 subtests passed**；拆分前後 `--collect-only` 皆 166 項，152 個未搬移 node ID 逐字相同，14 個搬移測試舊新 node ID 一對一對照（同名方法、僅檔案與類別前綴變更）；五連跑全綠；`cli validate` 零 error。

| Phase | 狀態 | 產出 |
| --- | --- | --- |
| Phase 0 | 完成 | `tests/feed_samples.py` 共用 mock feed 樣本 |
| Phase 1 | 完成 | `tests/test_parser.py`；`test_fetcher.py` 補 `network_error` 分支、429 HTTP-date 改 mock 時鐘（精確斷言 30.0）；`test_scheduler.py` 補 round-trip/offset 與 `add_days_to_iso8601` 邊界測試 |
| Phase 2 | 完成 | `test_config.py` 新增負向案例（subTest 覆蓋）、warning 獨立斷言、active config 嚴格映射測試 |
| Phase 3 | 完成 | `tests/test_orchestrator_operations.py`（304、scope filtering、skip paths、force bypass、dry-run、quarantine 循環、bozo→parse_error） |
| Phase 4 | 完成 | `tests/test_database.py`（表集合、index 語意、真實 SQLite unique/CHECK/FK、migration 幂等與失敗回滾、`split_sql_statements()`） |
| Phase 6 | 完成 | `test_integration.py` 14 項測試依職責拆分：`test_success_flow.py`、`test_persistence_isolation.py` 新檔，dedup（3）／error contract（5）／sanitizer 計數（3）整合測試併入對應既有測試檔的新類別；新增 `tests/integration_helpers.py` 集中 temporary config 與 migration 路徑 helper（`test_orchestrator_operations.py` 同步改用），`feed_samples.py` 補 `RSS_TWO_ARTICLES`；斷言與測試名稱語意不變 |

Production 改動（皆經決策或 review 授權）：`parser.py` 新增 `FeedParseError`（bozo 語法錯誤 → parse_error）；`database.py` 修 `split_sql_statements()` 同行多 statements 合併的缺陷。文件同步：`FETCH_EXECUTION.md` §9、`STORAGE_SCHEMA.md`（§4.4 `updated_at`、§4.1/§8 `dedup_rule` 值集）。

Code Review 記錄：

- 第一輪：[P1] bozo 不可一律視為 malformed（已限縮為語法錯誤並補測試）；[P1] `split_sql_statements` 同行 statements（已修 splitter 並補回歸測試）；spec drift 兩項（文件已對齊 DDL）。
- 第二輪：LGTM，無遺留事項。

Review 中發現但**僅鎖定現狀、未改 code**的行為落差，留待後續評估（不阻塞本方案）：disabled source 在 dispatch 前被過濾、不出現於 skip summary；`due_source_count` 實為 in-scope 數；304 寫回的 validators 取自舊 state。另發現 feedparser 會把 Atom `<id>` 提升為 `link`，非 URL 的 id 可能成為全域 `url:` dedup key，建議另案記錄。
