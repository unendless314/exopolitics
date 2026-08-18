# Classify 模組高配額與派發節奏控制器 (DispatchPacer) 風險分析及改善方案

**日期：** 2026-07-30  
**目標模組：** `modules/classify`  
**狀態：** 已記錄 Known Issue，待工程師排程評估與實作  

---

## 1. 背景與現況

在 `modules/classify/config/model_settings.yaml` 中，`execution_policy` 的執行控制參數已調高至生產等級數值：
- `batch_size`: `200`（原歷史預設 `20`）
- `max_concurrent_requests`: `20`（原歷史預設 `3`）
- `rate_limit_per_minute`: `1200`（原歷史預設 `60`）

在 `curate` 模組最近的重構中，已正式於 `EXECUTION_POLICY.md` 規範並實作了 `DispatchPacer`（派發節奏控制器），確保不論是首次請求還是自動重試（Retry），每次 HTTP 發送皆嚴格依據 `60 / rate_limit_per_minute` 秒進行動態微秒級間隔放行。

然而，`classify` 模組現行的 `modules/classify/src/orchestrator.py` 仍採用較早期的「預先排隊打卡法」（`idx * request_delay`），尚未導入 `DispatchPacer` 動態派發管制。

---

## 2. 風險分析 (Risk Analysis)

在 `20` 併發與 `1200 RPM` 的高流量設定下，缺乏 `DispatchPacer` 會產生以下三項主要風險：

1. **併發瞬間暴衝 (Burst Spikes)**
   - `max_concurrent_requests: 20` 意味著系統會同時開啟 20 個工作通道。
   - 現行 `classify` 實作在批次啟動瞬間，這 20 個通道會在一微秒內同時向 `mini-proxy` (`https://mini-proxy.noeticseeker.com/v1`) 發起請求。
   - 即使平均 RPM 未超標，雲端代理伺服器極易因為「瞬間連線數過高」而直接回傳 `429 Too Many Requests` 或 `503 Service Unavailable`。

2. **重試連鎖效應 (Retry Avalanche)**
   - 現有的 `idx * request_delay` 僅作用於任務首次啟動時的排隊，**並不涵蓋請求失敗後的自動重試 (Retry)**。
   - 當雲端出現短暫網路波動或 500 錯誤時，多個通道會同時進行 3 次重試，重試請求會與正常請求在極短時間內無間隔疊加爆發，瞬間耗盡 Rate Limit 額度。

3. **雲端代理伺服器 (mini-proxy) 的實體承載上限**
   - 本地端的 `model_settings.yaml` 設定調高，並不代表雲端代理與 OpenAI 後端 API Key 支援相同的限額。
   - 若雲端實際限額低於 1200 RPM / 20 併发，會導致頻繁遭遇 transient failure。

---

## 3. 建議改善方案 (Proposed Resolutions)

### 方案 A：短期配置調整（免改程式碼）
若在實際大批次執行時頻繁出現 `429` 或 `503` 錯誤，可適度調低 `modules/classify/config/model_settings.yaml` 中的併發設定：
```yaml
execution_policy:
  batch_size: 200
  max_concurrent_requests: 5 ~ 10  # 從 20 適度降低併發壓力，減少暴衝機率
  rate_limit_per_minute: 1200
```

### 方案 B：長期工程重構（移植 DispatchPacer，推薦）
參考 `modules/curate` 的優良實作，將 `DispatchPacer` 機制移植至 `classify` 模組：

1. **導入 DispatchPacer**：在 `modules/classify/src/orchestrator.py` 之中新增 shared `DispatchPacer` 類別。
2. **重構 HTTP 派發**：在 `classify_item` / `fetch_llm_classification` 發送前呼叫 `await pacer.wait()`，確保首次請求與所有 Retry 均受到動態間隔管制。
3. **規範與測試同步**：
   - 更新 `modules/classify/docs/EXECUTION_POLICY.md`，加入 `Dispatch-Time Rate Pacing` 條款。
   - 在 `modules/classify/tests/test_execution_policy.py` 新增間隔與併發測試。

---

## 4. 參考文件與程式路徑

- 參考實作：[modules/curate/src/orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/curate/src/orchestrator.py) (`DispatchPacer`)
- 參考規範：[modules/curate/docs/EXECUTION_POLICY.md](file:///C:/Users/user/Documents/exopolitics/modules/curate/docs/EXECUTION_POLICY.md#L29)
- 待修改檔：[modules/classify/src/orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py)
- 設定檔路徑：[modules/classify/config/model_settings.yaml](file:///C:/Users/user/Documents/exopolitics/modules/classify/config/model_settings.yaml)
