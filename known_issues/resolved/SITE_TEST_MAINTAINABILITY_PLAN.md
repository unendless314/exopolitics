# Site 模組測試可維護性改善方案

**狀態：** 已完成（2026-08-02，Phase 0–3 實作並通過兩輪 Code Review，LGTM）；Phase 4 依方案前置條件待新 UI 架構落地後執行；review 中浮現的 release-directory／pointer 原子切換提案另立後續文件（`SITE_RELEASE_POINTER_PROMOTION_PROPOSAL.md`）  
**日期：** 2026-08-01  
**修訂：** 2026-08-02，依工程審查釐清 fixture mode、統一 export-root loader、slug 明文契約與 deterministic translation map 要求；同日 owner 確認 URL／SEO 契約暫時維持不變（§7.1.7），並採納 resolver、fixture wrapper、stats schema 與部署／文件同步要求  
**實作註記（2026-08-02）：** Phase 0–3 已按本方案落地：`src/utils/export_root.js` 單一 resolver、`tests/fixtures/publish_export/` committed fixture、`scripts/dev-fixture.js` 與 `npm run dev:fixture`、`src/utils/exportData.ts` 共用 loader／hard-fail、`summary_short` fallback 移除、slug 契約 `^[a-z0-9][a-z0-9-]*$` 寫入 `DATA_HANDOFF_CONTRACT.md` §1.6 並由 adapter 拒絕不合法值、generator 核心抽為 `scripts/lib/generate_posts_core.js`（staging＋成功後才 promote、重複 slug hard-fail、translation map locale list 排序）。Code Review 四項 P1 已同批修正：catalog slug 同受格式契約約束、archive manifest `file_name` 拒絕 path traversal（`loadArchiveMonth` 另加直接呼叫防禦）、translation map 改 null-prototype 物件以支援 `constructor` 等 prototype-named 合法 slug、promotion 改為 map 先行且目錄切換失敗時 rollback 回上一版 map＋Markdown。後續複查再修正一項：rollback 改以旗標追蹤本次 run 是否實際搬動 live 目錄／promote staging，避免 stale backup 清理失敗時誤刪 live 並扶正舊 backup（附失敗注入 regression test）。最終複查將 promotion 重構為交易式流程：pre-commit 先清 stale backup（失敗即在未動 live 前中止）→ 舊 map／目錄各自改名為 per-run backup → promote staged artifacts → commit 後的 backup 清理僅屬 housekeeping（失敗只記 warning，絕不觸發 rollback）；交易中段失敗時反向還原，rollback 自身再失敗則彙整兩個錯誤並保留 backups 供人工復原，不再靜默壓抑。測試由 36 項增至 138 項，`npm test` 連跑 5 次全數通過，`npm run type-check` 零 diagnostics，production `npm run build`（9,697 頁）驗證通過。§7.2 的 VPS 部署順序確認仍需 owner／維運核實（site build 必須在 publish export 成功後觸發），此項無法由本機驗證。  
**範圍：** `modules/site/` 的資料交接、建置 adapter／generator 與可直接驗證的測試契約  
**非範圍：** 不在本方案中決定或固定視覺設計、元件樹、CSS、前端框架、互動細節或未來 UI 改版時的 render snapshot；不改變 `publish` 的資料語意、slug 產生責任、翻譯政策或 site 以外的模組邊界

## 1. 背景與目標

`site` 是 pipeline 的最終下游 consumer，只讀取 `publish` 產出的靜態 `data/publish_export/`，並在建置時產生網站。現有 36 項 Vitest 測試已保護：

- CJK／Latin 混合閱讀時間估算的基本行為。
- UI i18n helper 的基本翻譯、路徑與 UTC 時間格式。
- JSON item 到 Markdown 的 adapter，包括語言一致性、bullet 結構、label parity，以及 `summary_short` 直接成為 description 的規則。

在 2026-08-01 的盤點中，`npm test` 36 項均通過，連續執行 10 次沒有發現 flaky failure；`npm run type-check` 亦為零 error、零 warning。

然而，現有測試集中於小型純函式與 adapter，尚未充分保護 `publish_export -> site` 的資料邊界及建置輸出。這些不足大多不是 UI 測試缺口，未來替換頁面外觀、元件或前端框架後仍有價值。

目標：

1. 以小而穩定的測試保護 site 對上游 export 的 fail-fast 行為，避免壞資料被靜默顯示為空頁或猜測內容。
2. 保護 JSON-to-Markdown adapter、generated artifact 與 translation map 的建置契約，讓 UI 可以獨立演進。
3. 將資料驗證從 Astro page 的實作細節中整理為可獨立測試的邊界。
4. 不預先為短期內可能重做的 UI 投資大量 DOM、snapshot 或瀏覽器測試。
5. 保留現有有意義的測試；只有被更精確測試取代時才合併或移除。
6. 讓 production build 的 fail-fast 與 UI 開發所需的 fixture mode 明確分離，且兩者共用同一份資料契約驗證。

## 2. 已確認基線

- 測試指令：`npm test`
- 目前結果：3 個 test files、36 項通過；連跑 10 次均通過。
- 型別檢查：`npm run type-check`，0 errors、0 warnings。
- 目前測試檔：
  - `tests/readingTime.test.ts`，5 項。
  - `tests/i18n.test.ts`，8 項。
  - `tests/generatePosts.contract.test.ts`，23 項。
- adapter 實作位於 `scripts/lib/post_adapter.js`；generator 位於 `scripts/generate-posts.js`。
- generated Markdown 與 `_translation_map.json` 已由 `modules/site/.gitignore` 排除追蹤，且目前的 build／dev script 會刪除並重建 `src/content/posts/generated/`。
- `DATA_HANDOFF_CONTRACT.md` 與 `BUILD_AND_ROUTING_POLICY.md` 定義 `publish_export` 為唯一輸入來源，並要求 item 缺欄、bullet 不完整、locale 不一致時 hard-fail。
- 現行 export root 有三條不一致的存取路徑：`scripts/generate-posts.js` 自行組合 workspace 下的 `data/publish_export/`、`src/utils/paths.ts` 輸出相同預設路徑、`Footer.astro` 則靜態 import `data/publish_export/stats.json`。後者在 stats 檔完全缺失時會於 Vite module resolution 階段失敗，不能視為支援「無 export 的 dev mode」。
- 現行 repo 的 `pipeline.sh` 已按 publish（Step 5）→ site-build service（Step 6，`--wait`）的順序執行，且 `site-build.sh` 以 `set -e` 執行 `npm run build`。hard-fail 實作前仍須確認實際 VPS service／排程使用同一順序，避免獨立 site deploy 在 publish export 尚未就緒時失敗。
- UI 有高度機率重做。因此本方案的優先項目應測試輸入、轉換和輸出契約，而非目前 Astro 元件輸出的精確 HTML。

這些基線不是未來 CI gate 的替代品，但可用於判斷後續改動是否引入測試回歸。

## 3. 已知測試缺口與維護風險

### 3.1 Catalog、archive 與 stats 輸入沒有直接測試

`src/utils/validation.ts` 的 `validateCatalogItem()` 尚無 direct test。首頁與 archive page 直接解析 `index.json`、archive manifest、月 archive JSON 與 `stats.json`，但沒有 fixture 驗證合法輸入和失敗 surface。

此外，首頁、archive index 與月 archive 在輸入檔不存在時會靜默使用空陣列；archive index 的 `JSON.parse()` 也沒有與其他 route 一致的錯誤包裝。`stats.astro` 對 `stats.json` 沒有存在性或 schema 驗證，並直接假設所有目前 configured locale 都存在統計 map key。首頁與月 archive 又使用：

```ts
validated.summary_short || validated.display_title
```

作為 timeline description fallback。這與目前 handoff 文件的方向不一致：`summary_short` 是經驗證的必填欄位，site 不應猜測內容或把壞 export 靜默變成空白 UI。

應直接驗證：

- 合法 catalog item 可正確轉為 site 所需的 metadata。
- 缺失、空白或非字串的 `slug`、`display_title`、`summary_short` 會明確失敗。
- 無法由 `Date.parse()` 解析的 `source_published_at` 必須失敗，不得被當成有效資料靜默顯示；嚴格 UTC ISO-8601 格式則不在目前 site validator 自行施行。
- `index.json`、archive manifest、月 archive JSON 與 `stats.json` 的 JSON 解析失敗、根節點型別錯誤、缺少必要欄位，皆有清楚錯誤。
- 對預設 production export root，各語言預期存在的 export 檔缺失時一律 hard-fail，而不是假裝該語言沒有文章。
- `stats.json` 對每個目前 configured locale 都具備 page 所需的統計 map key；該要求應由 validator 集中處理，而非只由目前表格的硬編碼存取間接暴露。
- stats validator 固定要求 `last_export_run_timestamp` 為可由 `Date.parse()` 解析的字串，及五個 per-locale maps：`total_active_published_items_by_language`、`total_withdrawn_items_by_language`、`latest_index_count_by_language`、`archive_month_count_by_language`、`oldest_archive_month_by_language`。前四個 map 的每個 configured locale 值必須是非負整數；最後一個 map 的值可為 `YYYY-MM` 或暫時允許 `null`，直到 publish owner 確認無 archive 的新 locale 表示法。
- validator 從 `localeProfiles` 推導必要 locale，不從目前 stats page 的三欄硬編碼推導；上游 map 中額外、未知的 locale key 不構成錯誤，避免上游先新增語言時無故阻塞 site。
- timeline description 不再以 `display_title` 取代缺少的 `summary_short`；`CatalogItem.summary_short` 應成為必填欄位。
- 顯式指定的 development fixture export root 同樣通過上述 validation，不以跳過驗證來換取 dev server 可用。

驗證邏輯與 export root resolution 應抽成不依賴 `Astro` globals 的小型 helper。page 檔、Footer 與 generator 都必須透過同一 resolution policy 取得輸入路徑，並把已驗證模型傳給 UI，避免同一份 schema 規則與環境變數解析散落在各處。

### 3.2 Adapter 未拒絕不安全 slug，且部分輸入驗證分支未受測

`validateItem()` 僅要求 slug 為非空字串，而 generator 直接以：

```js
path.join(targetLangDir, `${item.slug}.md`)
```

決定輸出檔案。若上游出現 `../`、反斜線、絕對路徑或保留路徑片段，generator 可能寫到預期語言輸出目錄之外。雖然 slug 的 canonical 產生責任仍屬 `publish`，site 應在檔案系統邊界採取防禦性驗證。

現有 adapter tests 亦只測試正常 labels loading、部分 item invalid case 與輸出內容，沒有直接驗證：

- `loadPostLabels()` 對缺檔、無法解析 JSON、空 locale map、未知／缺漏／空白 label key 的錯誤。
- slug 含 path separator、`.`／`..`、絕對路徑或空白前後綴時的拒絕行為。
- `language_code` 缺失或非字串時，對 `assembleMarkdown()` 的失敗訊息與行為。
- `source_published_at` 的集中 `Date.parse()` validation，以及不可解析輸入的拒絕行為。

目前 publish 的 `slugify()` 與 `generate_slug()` 已穩定產出 ASCII lower-case slug，CJK 標題在 ASCII normalization 後為空時使用 `item`／`item-N` fallback。此格式應先寫入 `DATA_HANDOFF_CONTRACT.md`，成為明文 handoff contract：

```text
^[a-z0-9][a-z0-9-]*$
```

site 隨後以同一規則拒絕不合格值，不重算、不 sanitize、也不改寫 slug。這是內部資料完整性與檔案輸出防禦，不應誇大為外部攻擊面的資安事件。

timestamp 目前維持「`Date.parse()` 可解析」的集中 validator 規則；嚴格 UTC ISO-8601 驗證應等 publish contract 明確化後再加入，避免先在 site 創造未協調的新格式政策。

### 3.3 Generator 的 I/O 與 translation map 沒有整合契約測試

`scripts/generate-posts.js` 負責清除舊 generated directory、逐語言轉換 item JSON、寫出 Markdown，並生成 `_translation_map.json`。現有 tests 只直接呼叫 adapter，不測 generator 的實際檔案輸出。

這使以下行為沒有 regression protection：

- 每種存在的 locale、每個有效 item 都產生正確相對路徑的 Markdown。
- 同一 slug 在多語言出現時，translation map 合併所有語言，不遺漏或重複。
- 同一語言目錄內的兩個 item 若攜帶相同 slug，流程明確失敗，不可靜默覆寫 Markdown 或重複加入同一 locale。
- 缺少整個語言目錄、缺少 items directory、單一 item JSON 無法解析、item 驗證失敗時的 process outcome。
- rebuild 會清除舊的 derived Markdown，且不把舊 slug 留在 translation map。
- 產生失敗時不留下誤導性的 partial Markdown、partial map 或不完整成功訊號。
- translation map 每個 slug 的 locale array 有明確、排序後的順序，不依賴 `post_labels.json` 的 object key insertion order。

不應讓測試操作實際 `src/content/posts/generated/` 或工作區 `data/publish_export/`。建議把 generator 的核心流程抽成接受 `exportBaseDir`、staging directory、`generatedDir`、`translationMapFile` 與 logger 的函式，使用 temporary directory 建置小型 export fixture。僅在所有輸入驗證與輸出寫入成功後，才以 staged replacement 推廣到正式 derived artifact 目錄；CLI wrapper 保持目前的正式預設路徑。

### 3.4 現有測試有少量過時或低辨識力 assertion

這些問題不需要獨立的大型重構，但應與 Phase 1 的資料驗證工作同批處理，及早消除誤導與低訊號 assertion：

1. `generatePosts.contract.test.ts` 的檔案註解仍稱 adapter 與 labels「尚未存在，預期失敗」，但實作已存在且目前 23 項均通過。應更新為現行契約說明，避免測試意圖誤導維護者。
2. 同一檔案以 hardcoded `EXPECTED_LABELS` 重複 `post_labels.json` 的字串內容。它應保留至少一個明確 locale rendering assertion，但其餘期待值可從實際 label config 取得，以避免只是維護兩份相同 locale 文案。
3. `validateItem.length === 1` 與 `assembleMarkdown.length === 3` 是脆弱的 JavaScript implementation-shape assertion。它不能直接證明舊 `summaryMap` fallback 沒被使用，卻可能因無害的預設參數或函式重構而失敗。應改為行為測試：即使提供與 item `summary_short` 相衝突的額外歷史資料，frontmatter 與 Markdown body 仍只使用 `summary_short`。
4. `readingTime.test.ts` 的「strip markdown and html」案例只驗證結果為 1 分鐘，未能區分是否真的移除 markup。應使用接近 200 Latin words 或 300 CJK characters 的邊界 fixture，使未移除 URL、tag 或 Markdown punctuation 時會得到不同結果。
5. 測試名稱稱「empty or null content」，但 TypeScript API 接受 `string` 且案例只傳入空字串。應改為「empty content」，或經明確 API 決策後再支援並測試 nullable input。

以上屬測試可讀性與訊號品質改善，不能取代 §3.1 至 §3.3 的資料邊界測試。

### 3.5 UI、SEO 與 Phase 2 routing coverage 應延後，不應阻擋資料契約補強

目前沒有 LanguageSelector、BaseHead、Timeline、Header、Footer 或 page 的 render smoke tests，也未自動驗證 locale profile、hardcoded locale list、hreflang、canonical URL、輕量 route props 與 listing-page metadata-only load 的規則。

這些契約有部分長期價值，尤其是文件鎖定的 URL／SEO invariants；但現有 UI 很可能更新。現在為其建立大量 DOM snapshot、CSS 或元件結構測試，會把短期 UI 實作固化為維護成本。

本方案將它們列為後續決策：

- 若 URL、SEO 或多語言路徑在新 UI 中仍屬固定 public contract，於新架構確認後新增少量行為導向 smoke tests。
- 若 UI 改版同時改變 URL／SEO，必須先以跨模組變更更新 `DATA_HANDOFF_CONTRACT.md`、`BUILD_AND_ROUTING_POLICY.md` 與相應 tests。
- 不應在目前 UI 上新增 snapshot baseline、像素比較或大量 component test。

## 4. 分階段執行方案

### Phase 0：保護基線與最小測試支援

目的：固定目前健康的 suite，建立不會碰工作區產物的 fixture／temporary-directory 支援，以及跨 Node／Astro 使用的單一 export-root policy。

工作：

1. 記錄本文件 §2 的 36 項測試、type-check 與重複執行基線。
2. 建立最小、committed 的 development publish-export fixture，包含每個 configured locale 的 1 至 2 筆 detail item、index、archive manifest／archive item 與 stats；fixture 表示 contract，不複製 production export 大型資料。
3. 建立 temporary directory fixture，所有 generator test 僅能在其中讀寫。
4. 實作無依賴的 plain ESM JavaScript export-root resolver（例如 `src/utils/export_root.js`）：未設定 `SITE_PUBLISH_EXPORT_DIR` 時回傳 workspace 預設路徑，設定時解析為絕對路徑並對不存在目錄給出明確錯誤。`generate-posts.js` 直接 import；`paths.ts` 轉出口給 Astro loader。相鄰 JSDoc 或 `.d.ts` 必須提供 TypeScript 型別，確保 `astro check` 維持零 diagnostics。
5. 新增跨平台 Node wrapper，例如 `scripts/dev-fixture.js` 與 `npm run dev:fixture`。wrapper 設定 `SITE_PUBLISH_EXPORT_DIR` 至 committed fixture，再以 inherited stdio 啟動 `npm run dev`；Windows 使用 `shell: process.platform === "win32"` 解析 `npm.cmd`，並正確轉送 exit code、SIGINT 與 SIGTERM。不得在 package scripts 寫入 POSIX-only inline environment-variable syntax。
6. fixture 自身必須通過與 production export 相同的 validator 測試，避免 fixture drift。
7. 不在此 phase 改動 UI、route URL 或 publish export 語意。

驗收：

```powershell
Set-Location modules/site
npm test
npm run type-check
```

預期：既有測試不減少，type-check 維持零 diagnostics。

### Phase 1：publish export 資料驗證與 fail-fast 行為

目的：使 site 對 catalog、archive、stats 與必填 summary 的消費符合文件中的資料完整性方向，並統一 production 與 fixture mode 的資料根目錄處理。

工作：

1. 為 `validateCatalogItem()` 建立 direct, table-driven tests，覆蓋合法 item、各必填欄位缺失、空白值、錯誤型別與錯誤日期。
2. 抽取並測試 archive manifest、archive file、stats 所需的 schema validation；不要只以 page-level `JSON.parse()` 隱含驗證。stats fixture 必須覆蓋全部五個 per-locale map、負數／非整數 count、缺少 configured locale、額外未知 locale，以及 `oldest_archive_month_by_language` 的 `YYYY-MM` 與暫允許 `null` 行為。
3. 建立唯一 export-root resolver，預設為 workspace 的 `data/publish_export/`，但可由顯式 `SITE_PUBLISH_EXPORT_DIR` 覆寫。使 `generate-posts.js`、`src/utils/paths.ts` 與取代 Footer 靜態 import 的 stats loader 都使用它。
4. 移除 Footer 對 stats JSON 的靜態 import。Footer、stats page、首頁及 archive page 必須使用同一 loader／validator，讓缺檔和 schema error 有一致的可測 error surface。
5. production `npm run build` 和未設 override 的 `npm run dev` 對預期 export 檔缺失一律 hard-fail；`npm run dev:fixture` 以明確 fixture root 啟動，不能讓 fixture mode 默默套用到預設 production root。
6. 移除 `summary_short || display_title` fallback，將缺失 summary 視為輸入契約違反並新增 regression test。
7. 在本 phase 一併處理 §3.4 的過時註解、label 期待值重複、function arity assertion 與低辨識力 reading-time case。
8. 同批更新 `modules/site/docs/README.md` 與／或 `modules/site/docs/BUILD_AND_ROUTING_POLICY.md`：說明預設 dev/build 的 hard-fail、`npm run dev:fixture` 的適用情境與啟動方式、fixture 不得作為 production build 的預設輸入。
9. 保持 site 僅讀取 publish outputs，不讀 canonical DB，不在 site 重建或修正上游內容。

驗收：

- 所有 fixture 都不使用工作區實際 publish data。
- 每個失敗測試檢查可辨識的錯誤訊息或 error class，不鎖定無關的完整 stack trace。
- default production root 與 explicit fixture root 都通過相同的 loader／validator test matrix，且兩者不可能在同一個啟動流程各自指向不同資料根目錄。
- site 部署使用的實際 VPS service／排程已確認維持 publish 成功後才觸發 site build；若此順序變動，部署設定須與 hard-fail 改動同批修正。
- 不新增 UI DOM snapshot。

### Phase 2：adapter 防禦性驗證與既有測試訊號改善

目的：保護 site 寫檔前的資料安全與 adapter 轉換語意，並移除過時、脆弱的 assertion。

工作：

1. 更新 `DATA_HANDOFF_CONTRACT.md`，將 slug format `^[a-z0-9][a-z0-9-]*$` 與 CJK title 的 `item`／`item-N` fallback 寫成 publish-to-site 的明文契約；建立拒絕 path traversal 和不合法 slug 的 regression tests，再以相同小批修改 validator。
2. 為 `loadPostLabels()` 補上缺檔、非法 JSON、空 map、錯誤 key set、空白 value 的 tests。
3. 為 `assembleMarkdown()` 補上缺少或非字串 `language_code`、已知 locale mismatch，以及確認 canonical／disclosure／author metadata JSON frontmatter 的輸出語意。
4. 將 timestamp validation 集中為 `Date.parse()` 可解析的目前行為；不在本 phase 自行升格為嚴格 UTC ISO-8601 格式 gate。

驗收：

- slug validation 不改變 publish 對 canonical slug 的 ownership，也不 silently sanitize／改寫值。
- adapter tests 不讀寫真實 generated Markdown。
- 每個新增 invalid case 都對應 handoff 或檔案系統邊界的具體規則。

### Phase 3：generator 與 derived artifact 端對端契約

目的：在 isolated filesystem 下驗證 JSON input、Markdown output 與 translation map 的完整轉換流程。

工作：

1. 將 generator 核心封裝為可傳入目錄與 logger 的可測函式，保留 CLI entry point 和正式 build script 行為。
2. 以 temporary export fixture 驗證多語言、多 slug、`publish_summary` 與 `publish_link` 的輸出檔、Markdown body、label language 與 translation map。
3. 驗證 rebuild 清除舊的 generated file 與 stale translation map entry。
4. 先寫入 staging directory，僅在所有檔案與 translation map 都成功產生後才 replace 正式 output；驗證 malformed item JSON、invalid item、locale mismatch 與不可用 labels 失敗時，不會推廣 partial output。
5. 驗證同一語言中重複 slug 會失敗，不會覆寫既有 Markdown 或在 map 內加入重複 locale。
6. 對每個 map entry 的 languages 排序，並測試輸出 JSON 的順序不依賴 labels object 的 key order。
7. 對預設 production root，缺少 language 或 items directory 視為 export completeness failure；fixture mode 亦需提供完整 configured locale 結構，不以 warning-and-skip 降低資料契約。

驗收：

- 測試只使用 temporary paths，不得呼叫實際 `npm run build` 或刪除工作區 generated artifacts。
- 成功案例同時檢查 file content 和 translation map，不只檢查 process exit code。
- 失敗案例不使用真實 process exit；核心函式應回傳或 throw 可 assertion 的錯誤，CLI wrapper 才負責轉換成 exit code。
- translation map 的語言順序為明確、可重複的 artifact contract。

### Phase 4：待 UI 架構穩定後的最小 presentation smoke tests

目的：只在新的 UI 與 public route contract 已確定時，保護不隨視覺排版變動的 public behavior。

前置條件：

1. 新 UI 的前端框架、route 方案與 SEO policy 已確認。
2. 若變更既有 `/[lang]/posts/[slug]/`、canonical、hreflang 或 locale routing，相關 site 文件與跨模組契約已先同步更新。
3. Phase 1 至 Phase 3 的資料邊界測試已完成，UI smoke tests 不重複驗證 adapter 細節。

工作：

1. 視最終架構，新增少量路由／頁面 smoke tests，確認可從合法 publish fixture render 首頁、archive、post detail 與 stats。
2. 若 URL／SEO 仍是 contract，驗證 locale 的 canonical／hreflang、可用翻譯篩選及 `x-default` 的行為。
3. 若 Phase 2 hybrid ingestion 仍保留，驗證 listing 不依賴 full-content collection，post route props 只傳必要識別字。
4. 刪除或不建立依賴 CSS class、精確元件樹、文字斷行或視覺 pixel 的 snapshot。

驗收：

- smoke tests 斷言 public behavior，而非目前 Astro markup。
- 資料 fixture 和 Phase 1／3 共用，不新增第二套幾乎相同的 export fixture。
- UI 改版可在不修改資料邊界 tests 的情況下完成。

## 5. 建議提交與驗證方式

每個 phase 應可獨立審查與合併。涉及 production behavior 的變更，例如把遺失 export 檔改為 hard-fail、移除 summary fallback、加入 slug reject rule 或調整 generator 的 partial-output 行為，必須和直接 regression test 在同一小批變更中。純測試結構調整不應混入 UI redesign。

每批至少執行：

```powershell
Set-Location modules/site
npm test
npm run type-check
```

涉及 filesystem fixture 或 generator 的修改，額外執行：

```powershell
Set-Location modules/site
1..5 | ForEach-Object { npm test }
```

涉及 fixture mode 的修改，額外驗證：

```powershell
Set-Location modules/site
npm run dev:fixture
```

此 script 應由跨平台 Node wrapper 顯式設定 fixture export root，再啟動既有 generator 與 Astro dev 流程；不得依賴 shell-specific inline environment-variable syntax。驗證完成後應停止 dev server。

wrapper 的驗證須確認 child exit code／SIGINT／SIGTERM 會正確傳遞，避免 fixture mode 在 Windows 或 Ctrl+C 時遺留 dev server。

涉及實際 build script 的修改，工程師應先確認目前 ignored generated artifacts 可安全重建，再執行：

```powershell
Set-Location modules/site
npm run build
```

`npm run build` 會依設計清除並重建 `src/content/posts/generated/`，因此它不是本方案的 isolated generator test 替代品，也不得在未確認產物可覆寫時用於日常測試。

## 6. 完成定義

本方案完成時應滿足：

1. site 對 index、archive、stats 與 detail item 的必要 publish export 結構有直接、deterministic tests，壞輸入不會靜默顯示為空頁或猜測內容。
2. `summary_short`、locale、bullet 與 frontmatter 的 handoff 語意均由 adapter tests 保護；unsafe slug 無法逃離 generated output 目錄。
3. generator 在 temporary filesystem 下可驗證地建立／清理 Markdown 與 translation map，且 failure policy 有明確 regression tests。
4. default production root 與 explicit development fixture root 經同一 resolver 與 schema validator 消費；fixture 不會在未明確啟用時影響 production build，且 committed fixture 自身持續通過 contract validation。
5. 同一語言的重複 slug 會在 promotion 前失敗；translation map 每個 locale list 都是排序後、deterministic 的 artifact。
6. 現有過時註解、低辨識力 reading-time case 與脆弱 function arity assertion 已被更新或以行為測試替代。
7. 資料邊界測試不依賴現有 UI 的 CSS、HTML 結構或視覺設計，未來 UI 改版無須重寫它們。
8. module docs 已記載 hard-fail 與 `dev:fixture` 的行為；實際 VPS service／排程已確認 publish 成功後才執行 site build。
9. suite 與 type-check 在重複執行抽查中穩定通過，沒有新增 flaky failure。

## 7. 已確認方向與待工程師審查的實作決策

### 7.1 已確認方向

1. **production 與 fixture mode：** 預設 production root 的缺失 export 一律 hard-fail。UI 開發透過顯式 `npm run dev:fixture` 取得 committed fixture root；fixture 不得隱式覆蓋 production build。
2. **`summary_short`：** 移除 listing 的 `display_title` fallback。`summary_short` 是必填 handoff 欄位，缺失即為驗證錯誤。
3. **slug 契約：** 在 `DATA_HANDOFF_CONTRACT.md` 寫入 `^[a-z0-9][a-z0-9-]*$` 與 CJK title 的 `item`／`item-N` fallback。site 只拒絕不合格值，不改寫它。
4. **timestamp：** 目前集中採 `Date.parse()` 可解析規則；嚴格 UTC ISO-8601 validation 延後至 publish contract 有明確決議。
5. **generator atomicity：** 採 staged output。任何 item 或輸出失敗都不得推廣 partial Markdown 或 translation map。
6. **translation map 順序：** 每個 slug 的 locale list 必須排序，使 JSON artifact 不依賴 labels config 的 insertion order。
7. **URL 與 SEO 契約：** 專案 owner 於 2026-08-02 決定：既有 `/[lang]/posts/[slug]/` 路由與 SEO metadata schema（title、description、canonical、hreflang）暫時維持不變。Phase 4 可將其視為固定 public contract 設計行為導向 smoke tests。此為暫時性決定；UI 改版啟動時應重新確認，若屆時變更，須先更新 `DATA_HANDOFF_CONTRACT.md`、`BUILD_AND_ROUTING_POLICY.md` 與相應 tests（§3.5）。
8. **共用 resolver：** 採無依賴的 plain ESM JavaScript resolver，供 generator 與 Astro loader 共用；以 JSDoc 或相鄰 `.d.ts` 提供 TypeScript 型別，並對顯式 override 的不存在目錄產生明確錯誤。
9. **fixture 啟動：** 採無新依賴的 `scripts/dev-fixture.js` wrapper。它設定 fixture root、以 inherited stdio spawn `npm run dev`、在 Windows 啟用 `shell: process.platform === "win32"`，並轉送 child exit code 與 termination signals。
10. **stats schema：** validator 要求全域 `last_export_run_timestamp` 與五個規定的 per-locale maps；每個 `localeProfiles` locale 均須出現在各 map。四個 count maps 為非負整數；`oldest_archive_month_by_language` 暫允許 `YYYY-MM` 或 `null`；未知額外 locale key 可保留。

### 7.2 待工程師排程的執行時機

1. **UI smoke tests：** URL 與 SEO 契約已由 owner 確認暫時維持（§7.1.7），Phase 4 的契約前置問題已解；待新 UI 架構落地後加入少量行為導向 smoke tests，仍不阻擋 Phase 1 至 Phase 3。
