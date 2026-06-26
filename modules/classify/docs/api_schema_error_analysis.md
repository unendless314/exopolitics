# OpenAI 結構化輸出 API 錯誤分析報告

## 1. 錯誤現象
在執行 `classify` 模組進行 API 請求時，前端或日誌中出現以下錯誤訊息：
```
Error: HTTP 400: Invalid schema for response_format 'classification_result': 
In context=(), 'additionalProperties' is required to be supplied and to be false.
```

---

## 2. 根本原因分析 (Root Cause)
此錯誤發生在 Client 端的 [orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py) 模組，具體與 [JSON_SCHEMA](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py#L21-L71) 的定義及呼叫參數有關。

### (1) OpenAI 結構化輸出 (Structured Outputs) 的嚴格限制
在 `orchestrator.py` 中，系統判斷當 Provider 支援結構化輸出時，會使用以下設定進行 API 請求：
```python
payload["response_format"] = {
    "type": "json_schema",
    "json_schema": {
        "name": "classification_result",
        "strict": True, # 啟用了嚴格模式
        "schema": JSON_SCHEMA,
    },
}
```
當 `strict: True`（嚴格模式）啟用時，OpenAI 的 JSON Schema 必須嚴格遵循以下兩點規範：
1. **必須顯式聲明不允許額外屬性**：所有 Object 節點必須包含 `"additionalProperties": false`。目前的 `JSON_SCHEMA` 中漏掉了此宣告，因而觸發了該錯誤。
2. **所有屬性必須為必填**：在 `properties` 中定義的所有欄位，都必須列在 `required` 陣列中。目前 Schema 中的實驗性欄位（如 `content_timeliness` 與 `primary_evidence_type`）僅定義在 `properties` 中，卻未包含於 `required` 陣列中。

### (2) 代理伺服器 (Proxy) 的角色定位
經確認，[openai-shared-proxy](file:///C:/Users/user/Documents/openai-shared-proxy)（[openai.ts](file:///C:/Users/user/Documents/openai-shared-proxy/src/openai.ts)）在此請求中僅作為**轉發代理**（Forwarding Proxy），負責 Key 輪替與基本參數標準化（如 `max_tokens` 與 `max_completion_tokens` 的轉換），並未對 `response_format` 內部的 JSON Schema 做任何修改。因此此 Bug **並非** Proxy 程式碼造成，而是 OpenAI 拒絕了 Client 端傳入的 Schema 格式。

---

## 3. 解決方案對比

針對此問題，有以下兩種修改方向（依據業務需求選擇）：

### 方案 A：修正 Schema 以符合 `strict: True` 的規範（推薦）
若要保留 `strict: True` 以確保 LLM 輸出的 100% 穩定度，需要調整 [JSON_SCHEMA](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py#L21-L71) 定義：

1. 最外層加入 `"additionalProperties": false`。
2. 將所有屬性加入 `"required"` 列表。
3. 對於「可空 / 試驗性」的欄位，將其 `type` 宣告為 `["string", "null"]` 或 `["integer", "null"]`。

**修改後的 Schema 範例：**
```python
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "topic_class": {
            "type": "string",
            "enum": ["core", "adjacent", "irrelevant", "unknown"]
        },
        "classification_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0
        },
        "classification_reason": {
            "type": "string",
            "maxLength": 300
        },
        "content_density": {
            "type": "string",
            "enum": ["low", "medium", "high"]
        },
        "source_text_quality": {
            "type": "string",
            "enum": ["poor", "usable", "strong"]
        },
        "primary_language_code": {
            "type": "string"
        },
        "governmental_involvement": {
            "type": "integer",
            "enum": [0, 1]
        },
        "content_timeliness": {
            "type": ["string", "null"],
            "enum": ["current", "evergreen", "historical", "unclear", None]
        },
        "primary_evidence_type": {
            "type": ["string", "null"],
            "enum": ["physical_material", "radar_sensor", "video_photo", "eyewitness", "official_document", "scientific_paper", "media_report", "none", None]
        }
    },
    "required": [
        "topic_class", 
        "classification_confidence", 
        "classification_reason", 
        "content_density", 
        "source_text_quality", 
        "primary_language_code", 
        "governmental_involvement",
        "content_timeliness",
        "primary_evidence_type"
    ],
    "additionalProperties": False
}
```

### 方案 B：關閉嚴格模式 (`strict: False`)
如果不希望每次調整欄位時都將 Schema 寫死，可以將 `strict` 設為 `False`（或將其移除，預設為 `False`）：
```python
    if provider.supports_structured_output:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "classification_result",
                "strict": False, # 關閉嚴格模式
                "schema": JSON_SCHEMA,
            },
        }
```
* **影響**：OpenAI 將不會對 Schema 進行語法級的硬性校驗，因此原本的 Schema 可以直接通過。
* **副作用**：LLM 的輸出格式可能偶爾會偏離 Schema。不過，由於 `orchestrator.py` 內建有 [validate_classification_response](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py#L164-L242) 的手動校驗，若遇到格式錯誤會觸發自動重試，但這會增加額外的 Token 開銷和延遲。
