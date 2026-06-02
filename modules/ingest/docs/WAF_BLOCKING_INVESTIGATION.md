# WAF Blocking Investigation: The Black Vault (ID 7)

**文件版本：** v1.0  
**記錄日期：** 2026-06-02  
**狀態：** 待決定（Pending Decision）

---

## 1. 問題描述 (Problem Statement)

在執行 `ingest` 模組抓取任務時，特定訂閱源 **Source ID 7 (The Black Vault Case Files (FOIA))** 持續抓取失敗，而同網域的另一個訂閱源 **Source ID 65 (The Black Vault Document Archive)** 卻能成功抓取。

* **受影響訂閱源**：`The Black Vault Case Files (FOIA)` (ID 7)
* **訂閱源網址**：`https://www.theblackvault.com/casefiles/feed/`
* **異常現象**：資料庫中無任何該來源的文章條目（`source_item` 數量為 0），且健康狀態被標記為異常。

---

## 2. 資料庫事證 (Database Evidence)

經查詢本地 SQLite 資料庫 `data/canonical.db`，確認抓取引擎在請求該 URL 時被外部伺服器拒絕存取：

### 2.1 `source_state` 表紀錄
```json
{
  "source_id": 7,
  "health_status": "healthy",  /* 失敗次數尚未達到隔離閾值 */
  "last_fetch_at": "2026-06-02T08:15:39Z",
  "last_success_at": null,
  "last_http_status": 403,
  "consecutive_failures": 1,
  "last_error_class": "http_error_4xx",
  "last_error_at": "2026-06-02T08:15:53Z"
}
```

### 2.2 `fetch_attempt` 表紀錄
* **HTTP 狀態碼**：`403` (Forbidden，拒絕存取)
* **錯誤詳情 (`error_detail`)**：
  回傳內容包含 `HTTP 403: <!DOCTYPE html>\n<html lang="en">\n<head>...`。該 HTML 結構為典型的 **WAF (Web Application Firewall) 防火牆阻擋頁面**。

---

## 3. 根本原因分析 (Root Cause Analysis)

經過在本地環境進行控制組與實驗組的 HTTP 模擬測試，得出了明確的結論：

1. **阻擋機制**：該網站使用安全防護伺服器（例如 Cloudflare 或 WordPress 的 Wordfence 插件）。針對敏感路徑（如調查卷宗 `/casefiles/`），設有嚴格的反爬蟲偵測規則。
2. **阻擋依據**：當前 `fetcher.py` 在使用 `httpx.AsyncClient` 發送請求時，並未設定客製化的 `User-Agent` 標頭，因此預設會發送含有 Python 爬蟲特徵的字串（如 `python-httpx/1.x`）。防火牆識別到此特徵後，便直接判定為爬蟲並回傳 `403`。
3. **對比實驗結果**：
   - **無 User-Agent (預設狀態)** ── ❌ **403 Forbidden**
   - **攜帶標準 Chrome User-Agent** ──  **200 OK** (成功獲取並解析 RSS XML 數據)

---

## 4. 建議方案 (Proposed Solutions)

為了解決未來可能普遍存在的 WAF 阻擋爬蟲問題，我們有以下兩種潛在方案：

### 方案 A：全局偽裝 User-Agent（推薦 ⭐）
直接修改 `modules/ingest/src/fetcher.py`，在 `httpx` 客戶端初始化時，預設為所有外發請求加上常見的現代瀏覽器（例如 Chrome）的 User-Agent 標頭。
* **優點**：簡單粗暴且一勞永逸，能有效減少多數 RSS 來源對爬蟲的惡意封鎖。
* **缺點**：如果某些 RSS 來源有極為嚴格的行為分析，單純變更 UA 依然可能被標記（但對目前絕大多數 WordPress 網站非常有效）。

### 方案 B：動態配置 User-Agent（選配）
在 `sources.yaml` 設定檔中，為特定訂閱源擴充一個可選的欄位（如 `custom_headers`），僅在必要時為特定 ID 傳入客製化的瀏覽器特徵。
* **優點**：高度精準，只在需要時進行偽裝，遵守最小驚訝原則。
* **缺點**：會增加 `SOURCE_CONFIG_SCHEMA` 的複雜度與 `fetcher` 的實作成本。

---

## 5. 後續行動計畫 (Next Steps)

1. **本地觀察**：保持當前程式碼不變，讓 ingest 系統在 scheduled 模式下多跑幾天。
2. **收集日誌**：觀察是否還有其他 Source IDs 因為 `http_error_4xx` (特別是 403 或 401) 而被標記為 `degraded` 或 `quarantined`。
3. **統一收口**：在幾天後，統一根據受影響來源的比例與數量，決定是否一併在 `fetcher.py` 中進行全局偽裝，或是採取特定來源的動態配置。
