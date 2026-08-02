# Known Issue: GPT-5.6-Luna Model Upgrade Parameter Compatibility Risks

## Status
- **State**: Active Known Issue / Pending Dev Remediation Patch
- **Trigger Commit**: `cb48f1f` (`Update LLM model settings to gpt-5.6-luna`)
- **Affected Modules**: `classify`, `curate`, `translate`

---

## 1. Issue Overview & Symptom
In commit `cb48f1f`, the LLM model configuration across `classify`, `curate`, and `translate` modules was updated from `gpt-5.4-mini` to `gpt-5.6-luna`. When executed in production via the OpenAI-compatible proxy (`mini-proxy`), the API requests failed with HTTP 400 Bad Request errors due to strict parameter validation constraints in `gpt-5.6-luna`.

### Parameter Compatibility Breakdown
| Parameter | `gpt-5.4-mini` Behavior | `gpt-5.6-luna` Requirement | Status |
| :--- | :--- | :--- | :--- |
| `temperature` | Accepts `0.7` | Must be `1.0` (non-1 values cause error/rejection) | ❌ Incompatible default (`0.7`) |
| `top_p` | Accepts `0.95` | **Not supported**. Any presence in payload triggers `400 Bad Request` | ❌ Incompatible schema/payload |
| `max_tokens` | Accepts `1024` ~ `4096` | Works as expected | ✅ Compatible |
| `response_format` | Supports `json_object` | Requires prompt text to contain the word "json" | ✅ Compatible (`classify` prompts contain "json") |

---

## 2. Root Cause Analysis
The current module implementations (`classify`, `curate`, `translate`) hardcode `top_p` as a required non-null float in configuration definitions and unconditionally send `"top_p"` in API HTTP request payloads.

### Affected Python Files & Schemas

1. **Configuration Schemas (`config.py`)**:
   - `modules/classify/src/config.py`: `top_p: float = 0.95`
   - `modules/curate/src/config.py`: `top_p: float = 0.95`
   - `modules/translate/src/config.py`: `top_p: float = 0.95`

2. **Payload Construction Logic (`orchestrator.py` / `database.py` / `execution.py`)**:
   - Requests unconditionally serialize `"top_p": defaults.top_p` into request JSON dictionaries.
   - There is no logic to omit `top_p` when set to `None` or when the active provider/model does not support top-p sampling.

3. **YAML Configuration Defaults (`model_settings.yaml`)**:
   - `modules/classify/config/model_settings.yaml`: `temperature: 0.7`, `top_p: 0.95`
   - `modules/curate/config/model_settings.yaml`: `temperature: 0.7`, `top_p: 0.95`
   - `modules/translate/config/model_settings.yaml`: `temperature: 0.7`, `top_p: 0.95`

---

## 3. Production Incident Report & Operational Options

The cloud deployment report evaluated three potential recovery strategies:

### Option (a): Cloud Server Local Hotfix *(Temporary Mitigation)*
- **Action**: Modify 6 Python files to make `top_p` optional and 3 YAML configuration files on the cloud server directly.
- **Pros**: Immediately restores pipeline execution with `gpt-5.6-luna`.
- **Cons/Risks**: Server-side changes will conflict or be overwritten upon next `git pull`. Requires `skip-worktree` or manual stash/merge management.

### Option (b): Quick Rollback to `gpt-5.4-mini` *(Immediate Stability)*
- **Action**: Revert `cb48f1f` model name configuration back to `gpt-5.4-mini`.
- **Pros**: Immediate zero-risk restoration of stable pipeline execution.
- **Cons**: Defers the `gpt-5.6-luna` model upgrade until code patch is ready.

### Option (c): Formal Development Patch `cb48f1f+1` *(Recommended Long-term Solution)*
- **Action**: Keep site/pipeline in maintenance window while engineers implement a formal code patch on `main`, pass unit tests, and push `cb48f1f+1`.
- **Pros**: Clean code architecture with proper optional parameter support and git traceability.

---

## 4. Engineering Remediation Action Plan (Patch `cb48f1f+1`)

To make the codebase fully compatible with `gpt-5.6-luna` and future models with custom parameter constraints, the following changes must be implemented:

### Step 1: Update Configuration Models (`config.py`)
Allow `top_p` to be `Optional[float] = None`:
```python
top_p: Optional[float] = None
```

### Step 2: Conditionally Build Request Payloads
In request building helpers (`orchestrator.py` / payload builders), only include `"top_p"` if `top_p is not None`:
```python
payload = {
    "model": provider_config.model_name,
    "messages": messages,
    "temperature": request_defaults.temperature,
    "max_tokens": request_defaults.max_output_tokens,
}
if request_defaults.top_p is not None:
    payload["top_p"] = request_defaults.top_p
```

### Step 3: Update `model_settings.yaml` Configurations
Set `temperature: 1.0` and omit or set `top_p: null` for `gpt-5.6-luna` providers:
```yaml
request_defaults:
  temperature: 1.0
  # top_p omitted or set to null
  max_output_tokens: 1024
```

### Step 4: Unit Test Validation
Update tests across `classify`, `curate`, and `translate` to verify:
1. `top_p = None` loads correctly from YAML without error.
2. HTTP request payloads omit `"top_p"` when `top_p` is `None`.
3. `temperature = 1.0` is correctly propagated.
