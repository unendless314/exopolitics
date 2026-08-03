# Known Issue: GPT-5.6-Luna Model Upgrade Parameter Compatibility Risks

## Status
- **State**: Active Known Issue / Remediation patch implemented, pending
  deployment & post-deployment recovery
- **Trigger Commit**: `cb48f1f` (`Update LLM model settings to gpt-5.6-luna`)
- **Remediation Commit**: `c6c3123` (`Make top_p optional across classify,
  curate, and translate`)
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

To restore a compatible request shape while keeping the implementation simple
and model-agnostic, make the following changes:

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

### Step 3: Adopt Module-Wide Request Defaults
Set the request defaults in all three module configuration files to a common
sampling baseline: `temperature: 1.0` and an omitted or null `top_p`.
```yaml
request_defaults:
  temperature: 1.0
  # top_p omitted or set to null
  max_output_tokens: 1024
```

This is configuration policy, not a model-specific code branch. Do not add
logic that checks for `gpt-5.6-luna`. If a future module configuration needs a
numeric `top_p`, it can set one and the generic payload builder will send it.

### Step 4: Unit Test Validation
Update tests across `classify`, `curate`, and `translate` to verify:
1. `top_p = None` loads correctly from YAML without error.
2. HTTP request payloads omit `"top_p"` when `top_p` is `None`.
3. `temperature = 1.0` is correctly propagated.

---

## 5. 2026-08-03 Investigation Addendum

### 5.1 Repository Evidence Confirmed

The active repository independently confirms the **code-level** root cause:

| Finding | Confirmed active paths |
| --- | --- |
| `top_p` is a non-nullable float | `modules/{classify,curate,translate}/src/config.py` |
| Request payloads always send `temperature`, `top_p`, and `max_tokens` | `modules/classify/src/orchestrator.py`, `modules/curate/src/orchestrator.py`, `modules/translate/src/orchestrator.py` |
| Active `mini-proxy` configuration uses `gpt-5.6-luna` with `temperature: 0.7` and `top_p: 0.95` | `modules/{classify,curate,translate}/config/model_settings.yaml` |
| Existing request-payload tests assert that `top_p` is present | `modules/classify/tests/test_request_payload.py`, `modules/curate/tests/test_llm_request.py`, `modules/translate/tests/test_execution.py` |

Commit `cb48f1f` changed the active model names from `gpt-5.4-mini` to
`gpt-5.6-luna`, but did not change the request defaults or request-building
logic. The active `mini-proxy` path uses `response_format:
{"type": "json_object"}`; all three active prompt templates explicitly
include `JSON`/`json`, so the documented JSON-keyword requirement is not the
primary incompatibility currently identified.

### 5.2 Evidence Boundary

This investigation validates the repository's request construction and its
incompatibility with the reported Luna constraints. It does **not**
independently validate the production incident or the proxy's model-specific
rules: the only repository evidence for the live HTTP 400 response and the
exact `gpt-5.6-luna` parameter policy is this incident record.

Before closing the incident, retain a sanitized production error response and
the rejected request field names as an operational fixture. Do not record API
keys, authorization headers, article content, or other sensitive data.

### 5.3 Assessment of the Original Remediation

The remediation in section 4 is appropriate as an **immediate, formal
repository patch**:

- make `top_p` nullable;
- omit it from a request when unset;
- adopt `temperature: 1.0` and `top_p: null` (or omit the YAML key) as the
  common request defaults in all three module configurations; and
- add regression tests in all three affected modules.

Do not apply a cloud-server-only hotfix as the lasting solution. It will drift
from `main`, can be overwritten by deployment, and hides the contract change
from review and test history.

This keeps the immediate patch simple, but it is not a general guarantee of
future-model compatibility. `request_defaults` is module-wide, while provider
and model API constraints may differ by provider route, endpoint, and model. A
future model change could recreate the same failure for another request field.

### 5.4 Recommended Scope for the Next Code Change

Keep the immediate change small. It should update only the three module-local
configuration schemas, payload builders, request-default YAML settings, their
tests, and the corresponding module documentation required by `AGENTS.md`.

The patch must preserve these existing contracts:

- a configured numeric `top_p` is still sent when configuration supplies it;
- `top_p: null` loads successfully and is omitted, not serialized as JSON
  `null`;
- the configured temperature and token limit are propagated unchanged; and
- each module retains ownership of its prompt, output validation, retry
  eligibility, database writes, and state transitions.

Regression coverage should include:

1. loading a `null` `top_p` from YAML;
2. omission of `top_p` from the outbound payload when it is unset;
3. continued inclusion of an explicitly configured numeric `top_p`;
4. propagation of `temperature: 1.0`; and
5. module-local configuration validation and request-payload tests for
   `classify`, `curate`, and `translate`.

Because cross-module pytest collection is a known repository issue, run the
affected module test suites separately until that issue is resolved.

### 5.5 Long-Term Maintainability Direction

Do **not** extract a complete shared LLM execution layer in this incident
patch. The modules have intentionally different prompts, response schemas,
queue semantics, retry eligibility, and persistence/state-transition rules.
A generic runner would centralize business decisions and likely be harder to
change than clear module-local code.

The repeated Luna defect does justify a future, narrowly scoped shared
technical capability: a provider-route-model compatibility profile and a pure
payload-shaping/validation helper. It must not become a new pipeline stage or
own module business logic.

Such a profile should be scoped at least by provider route, endpoint dialect,
and model name, and should be able to express:

- allowed, forbidden, optional, and fixed request parameters;
- parameter omission rules, including unsupported `top_p`;
- response-format capabilities and prompt preconditions; and
- endpoint-specific token parameter names where they differ.

The helper should reject incompatible configuration during a local validation
step, before any live API call, and emit only permitted fields. Prompt
construction, output schemas, retry/state policies, and canonical database
writes must remain module-local.

Adopt this shared capability only after the module test audit and CI
preparation described in
[`CODEBASE_MAINTAINABILITY_DIRECTIONS.md`](./CODEBASE_MAINTAINABILITY_DIRECTIONS.md)
provide a reliable cross-module regression gate, or when a second
provider/model compatibility change demonstrates the same repeated technical
contract. This preserves KISS today while creating a measured path away from
repeat cross-module API failures.

---

## 6. 2026-08-03 Patch Implementation

The section 4 remediation (option (c)) has been implemented as the formal
repository patch on `main`:

- **Commit**: `c6c3123` (`Make top_p optional across classify, curate, and
  translate`) — 21 files across the three modules (config schemas, payload
  builders, active YAMLs, tests, module docs).
- **Implementation plan**:
  [`GPT_5_6_LUNA_TOP_P_PATCH_PLAN.md`](./GPT_5_6_LUNA_TOP_P_PATCH_PLAN.md).
- **Test results**: classify 88 passed; curate 107 passed; translate 152
  passed, 1 skipped — each strictly above the pre-patch baseline
  (85 / 104 / 149+1skip), including new active-config pin tests that fail if
  a shipped YAML reverts to `temperature: 0.7` / `top_p: 0.95`.

### 6.1 Completed

- `top_p` is `Optional[float] = None` in all three module config schemas;
  configured numeric values (0.0–1.0) are still validated and sent unchanged.
- Request payloads omit the `top_p` key entirely when unset (never serialized
  as JSON `null`); no code path branches on a model or provider name.
- All three active `model_settings.yaml` files adopt `temperature: 1.0` and
  `top_p: null`; module documentation updated per `AGENTS.md`.

### 6.2 Outstanding (before this incident can close)

Execute the deployment and recovery checklist (patch plan §6) in order:

1. Reconcile the cloud server (discard any option-(a) hotfix drift) so
   `git pull` lands cleanly.
2. Inventory locked rows (`curate`: failed with `retry_count >= 3`;
   `translate`: failed at retry exhaustion, per language) and verify the
   exact Luna HTTP 400 casualty list via a read-only query plus
   incident-window run logs.
3. Re-run verified victim items with `--force`, one at a time.
4. Small-batch live validation (e.g. `--batch-size 5`) against the production
   proxy; confirm zero HTTP 400 responses and successful DB writes in all
   three modules.
5. Resume the normal pipeline schedule.
6. Close only after a clean observation window (no 400s): retain a sanitized
   production HTTP 400 fixture per §5.2, set this record's State to Resolved,
   and move this record and the patch plan to `known_issues/resolved/`.

This record stays **Active** until step 6 completes.
