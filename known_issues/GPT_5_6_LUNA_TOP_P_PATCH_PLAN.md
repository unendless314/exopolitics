# Remediation Plan: Nullable `top_p` Patch for `gpt-5.6-luna` (Commit `cb48f1f+1`)

- **Date**: 2026-08-03 (rev. 2, incorporates review feedback)
- **Status**: Implemented on `main` as commit `c6c3123` (classify 88 passed,
  curate 107 passed, translate 152 passed + 1 skipped); pending deployment
  and post-deployment recovery (§6)
- **Source incident record**: [`GPT_5_6_LUNA_PARAMETER_COMPATIBILITY_RISKS.md`](./GPT_5_6_LUNA_PARAMETER_COMPATIBILITY_RISKS.md) (esp. §4 and §5.3–§5.4)
- **Affected modules**: `classify`, `curate`, `translate`
- **Estimated diff**: 21 files (6 source/config, 9 test, 3 YAML, 3 docs)
- **Pre-patch test baseline** (verified 2026-08-03): classify 85 passed; curate
  104 passed; translate 149 passed, 1 skipped. The patch only adds tests, so
  post-patch counts must strictly increase.

---

## 1. Objective

Restore pipeline execution under `gpt-5.6-luna` by making `top_p` an optional
request parameter end-to-end:

1. `top_p` becomes `Optional[float] = None` in all three module config schemas.
2. Request payloads include `"top_p"` only when a numeric value is configured.
3. All three active YAML configurations adopt `temperature: 1.0` and
   `top_p: null`.
4. Regression tests lock the new contract in all three modules, including a
   pin against the shipped YAML so a revert to `0.7` / `0.95` cannot pass CI.
5. Post-deployment recovery restores rows locked by the HTTP 400 incident.

This is the formal repository patch (incident record option (c)). Model names
stay at `gpt-5.6-luna`; no rollback and no cloud-server-only hotfix is the
lasting solution.

## 2. Design Constraints (Binding)

- **No model-name branches.** Do not add code that checks for
  `gpt-5.6-luna` (or any model name). Sampling policy lives in YAML
  configuration; code only honors "set vs. unset".
- **Numeric `top_p` still works.** A configured numeric `top_p` must still be
  validated (0.0–1.0) and sent unchanged.
- **`null` means omitted, not JSON `null`.** When `top_p` is unset, the key
  must be absent from the outbound payload — never serialized as
  `"top_p": null`.
- **Module ownership is preserved.** Each module keeps its own prompts,
  output validation, retry eligibility, database writes, and state
  transitions. No shared LLM execution layer is introduced.
- **Out of scope** (deliberately deferred, see incident record §5.5):
  provider-route-model compatibility profiles, shared payload-shaping helpers,
  endpoint-specific token parameter names (e.g. `max_completion_tokens`),
  cross-module pytest collection fixes.

## 3. Step-by-Step Changes

### Step 1 — Config schemas: make `top_p` nullable (3 files)

Files: `modules/classify/src/config.py`, `modules/curate/src/config.py`,
`modules/translate/src/config.py`

In each file, `RequestDefaults` currently declares (line 8):

```python
    top_p: float = 0.95
```

Change to:

```python
    top_p: Optional[float] = None
```

Notes:

- `Optional` is already imported in all three files
  (`from typing import Dict, Any, Optional`).
- The existing `validate_top_p` field validator (lines 18–23 in each file)
  already tolerates `None` (`if v is not None and not (...)`); **do not
  modify it**. Verify this during review.
- Do **not** change the `temperature` schema defaults (0.1 / 0.2 / 0.3). They
  are fallback-only; the active values come from YAML (Step 3). Keeping them
  unchanged minimizes the diff.

### Step 2 — Payload builders: send `top_p` only when set (3 files)

Files and current code (identical pattern in all three):

- `modules/classify/src/orchestrator.py` — `_build_request_payload()`, lines 102–111
- `modules/curate/src/orchestrator.py` — `_build_request_payload()`, lines 243–252
- `modules/translate/src/orchestrator.py` — `_build_request_payload()`, lines 237–246

Current:

```python
    payload: Dict[str, Any] = {
        "model": provider.model_name,
        "messages": [ ... ],
        "temperature": defaults.temperature,
        "top_p": defaults.top_p,
        "max_tokens": defaults.max_output_tokens,
    }
```

Target (remove `top_p` from the literal; add a conditional immediately after):

```python
    payload: Dict[str, Any] = {
        "model": provider.model_name,
        "messages": [ ... ],
        "temperature": defaults.temperature,
        "max_tokens": defaults.max_output_tokens,
    }
    # Send top_p only when configured; some provider routes reject the
    # parameter outright (see known_issues/GPT_5_6_LUNA_PARAMETER_COMPATIBILITY_RISKS.md).
    if defaults.top_p is not None:
        payload["top_p"] = defaults.top_p
```

Nothing else in these functions changes: `messages`, `temperature`,
`max_tokens`, and the `response_format` branches stay exactly as they are.

### Step 3 — Active YAML configs: new sampling baseline (3 files)

Files: `modules/classify/config/model_settings.yaml`,
`modules/curate/config/model_settings.yaml`,
`modules/translate/config/model_settings.yaml`

Current `request_defaults` block (lines 6–9 in each):

```yaml
request_defaults:
  temperature: 0.7
  top_p: 0.95
  max_output_tokens: 1024   # 2048 in curate, 4096 in translate
```

Target:

```yaml
request_defaults:
  temperature: 1.0
  # top_p intentionally null: the active mini-proxy route (gpt-5.6-luna)
  # rejects this parameter with HTTP 400. Set a numeric value only for
  # providers that support top-p sampling.
  top_p: null
  max_output_tokens: 1024   # unchanged per module: 1024 / 2048 / 4096
```

Notes:

- `top_p: null` (explicit) is preferred over deleting the key, so the policy
  is visibly deliberate rather than apparently forgotten. Both load to `None`
  under the Step 1 schema.
- `max_output_tokens` keeps each module's current value.
- `model_name: gpt-5.6-luna` entries and all `execution_policy`,
  `target_languages`, `validation`, and `providers` blocks are untouched.

### Step 4 — Tests (9 files)

Existing tests that assert a **numeric** `top_p` is sent remain valid and
must keep passing unchanged (contract: numeric `top_p` is still transmitted).
Each module gets three additions: (a) omission coverage, (b) `null`-loading
coverage, and (c) an **active-config pin** that loads the real shipped
`config/model_settings.yaml` and asserts the incident baseline
(`temperature == 1.0`, `top_p is None`) so a future YAML revert cannot pass
while unit tests stay green.

#### 4a. `classify`

1. `modules/classify/tests/helpers.py` — `make_config()` (lines 158–166):
   widen the parameter type only:
   ```python
   top_p: Optional[float] = 0.95,
   ```
   (Default stays `0.95` so all existing callers behave as before; add
   `Optional` to the typing import if not already present.)
2. `modules/classify/tests/test_request_payload.py` — add to
   `TestFallbackPayload`:
   ```python
   def test_top_p_omitted_when_unset(self) -> None:
       config = make_config(supports_structured_output=False, top_p=None)
       payload = _build_request_payload(config, title="t", sanitized_text="x")
       self.assertNotIn("top_p", payload)
   ```
   Keep `test_payload_derives_sampling_and_model_from_config` (asserts
   `payload["top_p"] == 0.77`) as the numeric-contract pin.
3. `modules/classify/tests/test_classify.py` — two additions following the
   existing `GOOD_SETTINGS_YAML` / `validate_and_load_config` patterns:
   - a settings YAML with `top_p: null` must load without error and yield
     `config.request_defaults.top_p is None`;
   - an active-config pin loading the real shipped config
     (`pathlib.Path(__file__).resolve().parent.parent / "config"`) asserting:
     ```python
     self.assertEqual(config.request_defaults.temperature, 1.0)
     self.assertIsNone(config.request_defaults.top_p)
     ```
   The existing `test_invalid_top_p` (`top_p: 5.0` rejected) and the
   `top_p: 0.95` fixtures in `test_classify.py` / `test_cli.py` stay
   unchanged.

#### 4b. `curate`

1. `modules/curate/tests/support.py` — `build_test_config()` (lines 133–134):
   widen the parameter type only:
   ```python
   top_p: Optional[float] = 0.95,
   ```
2. `modules/curate/tests/test_llm_request.py` — add to
   `TestBuildRequestPayload`:
   ```python
   def test_top_p_omitted_when_unset(self):
       config = build_test_config(supports_structured_output=False, top_p=None)
       payload = _build_request_payload(config, ITEM)
       self.assertNotIn("top_p", payload)
       self.assertEqual(payload["temperature"], config.request_defaults.temperature)
       self.assertEqual(payload["max_tokens"], config.request_defaults.max_output_tokens)
   ```
   Keep `test_config_derived_request_defaults` (`top_p=0.5` asserted present)
   as the numeric-contract pin.
3. `modules/curate/tests/test_cli_contract.py` — two additions:
   - a settings-validation case mirroring the existing `VALID_SETTINGS` usage:
     the same dict with
     `request_defaults: {"temperature": 1.0, "top_p": None, "max_output_tokens": 512}`
     must pass validation and load with `top_p is None`;
   - an active-config pin calling `validate_and_load_config` on the real
     shipped config (`pathlib.Path(__file__).resolve().parent.parent / "config"`)
     asserting `temperature == 1.0` and `top_p is None`.
   `VALID_SETTINGS` itself (with `"top_p": 0.95`) stays unchanged.

#### 4c. `translate`

1. `modules/translate/tests/support.py` — two helpers currently hardcode
   `0.95`; give each an explicit knob:
   - `write_config_dir()` (settings dict written at lines 167–171): add
     keyword `top_p: Optional[float] = 0.95` and use it for the `"top_p"`
     entry (writing `None` emits YAML `null`).
   - `build_mock_config()` (signature at lines 483–497, assignment at line
     534): add keyword `top_p: Optional[float] = 0.95` and assign
     `config.request_defaults.top_p = top_p`.
2. `modules/translate/tests/test_execution.py` — add to
   `TestBuildRequestPayload`:
   ```python
   def test_top_p_omitted_when_unset(self) -> None:
       config = _zh_only_config(top_p=None)
       payload = _build_request_payload(config, _payload_item(), "zh")
       self.assertNotIn("top_p", payload)
   ```
   Keep `test_model_and_request_defaults_come_from_config` (asserts
   `payload["top_p"] == 0.95`) as the numeric-contract pin.
3. `modules/translate/tests/test_config.py` — two additions:
   - a loading test using `write_config_dir(top_p=None)`: config loads
     without error and `config.request_defaults.top_p is None`;
   - an active-config pin reusing the existing `support.load_active_config()`
     helper (already loads the real shipped config dir):
     ```python
     config = support.load_active_config()
     self.assertEqual(config.request_defaults.temperature, 1.0)
     self.assertIsNone(config.request_defaults.top_p)
     ```

### Step 5 — Module documentation (3 files)

`AGENTS.md` requires schema changes to update module docs in the same change;
all three modules document `model_settings.yaml` as their configuration
source, so all three get an update.

1. `modules/curate/docs/README.md`, line 65 — update the `request_defaults`
   row:
   - Current: `` `top_p` (float, 0.0-1.0, default 0.95) ``
   - Target: `` `top_p` (float, 0.0-1.0, optional, default `null`; the key is omitted from request payloads when unset) ``
2. `modules/classify/docs/README.md` — extend the Config Map entry
   (lines 54–55):
   ```markdown
   * `config/model_settings.yaml`  
     Stores provider selection, request defaults, and execution defaults.
     Within `request_defaults`, `top_p` is an optional float (0.0–1.0,
     default `null`); when unset, the `top_p` key is omitted from outbound
     request payloads.
   ```
3. `modules/translate/docs/README.md` — add a short section before
   "Document Directory":
   ```markdown
   ## Configuration Notes

   `config/model_settings.yaml` defines the active provider, prompt template,
   request defaults, and execution policy. Within `request_defaults`, `top_p`
   is an optional float (0.0–1.0, default `null`); when unset, the `top_p`
   key is omitted from outbound request payloads (some provider routes reject
   the parameter outright).
   ```

## 4. Acceptance Criteria

All of the following must hold before merge:

1. `top_p: null` (or omitted key) loads successfully in all three modules and
   yields `request_defaults.top_p is None`.
2. Payloads omit the `"top_p"` key entirely when unset (not JSON `null`).
3. A configured numeric `top_p` (e.g. `0.95`, `0.5`, `0.77`) is still sent,
   and out-of-range values (`5.0`) are still rejected.
4. `temperature` and `max_output_tokens` propagate unchanged; active YAMLs
   carry `temperature: 1.0`, `top_p: null`, and each module's active-config
   pin test proves the shipped values.
5. Full per-module suites pass, run separately from the repository root
   (cross-module collection is a known unresolved issue). Post-patch counts
   must exceed the recorded baseline (85 / 104 / 149+1skip):

   ```bash
   python -m pytest modules/classify/tests -q
   python -m pytest modules/curate/tests -q
   python -m pytest modules/translate/tests -q
   ```

6. No code path branches on a model or provider name; `git diff` shows only
   the 21 files listed above.

## 5. File Change Summary

| # | File | Change |
| --- | --- | --- |
| 1 | `modules/classify/src/config.py` | `top_p: Optional[float] = None` |
| 2 | `modules/curate/src/config.py` | `top_p: Optional[float] = None` |
| 3 | `modules/translate/src/config.py` | `top_p: Optional[float] = None` |
| 4 | `modules/classify/src/orchestrator.py` | conditional `top_p` in payload |
| 5 | `modules/curate/src/orchestrator.py` | conditional `top_p` in payload |
| 6 | `modules/translate/src/orchestrator.py` | conditional `top_p` in payload |
| 7 | `modules/classify/config/model_settings.yaml` | `temperature: 1.0`, `top_p: null` |
| 8 | `modules/curate/config/model_settings.yaml` | `temperature: 1.0`, `top_p: null` |
| 9 | `modules/translate/config/model_settings.yaml` | `temperature: 1.0`, `top_p: null` |
| 10 | `modules/classify/tests/helpers.py` | `make_config(top_p: Optional[float])` |
| 11 | `modules/classify/tests/test_request_payload.py` | add omission test |
| 12 | `modules/classify/tests/test_classify.py` | add null-loading + active-config pin tests |
| 13 | `modules/curate/tests/support.py` | `build_test_config(top_p: Optional[float])` |
| 14 | `modules/curate/tests/test_llm_request.py` | add omission test |
| 15 | `modules/curate/tests/test_cli_contract.py` | add null-loading + active-config pin tests |
| 16 | `modules/translate/tests/support.py` | `top_p` kwarg in `write_config_dir` + `build_mock_config` |
| 17 | `modules/translate/tests/test_execution.py` | add omission test |
| 18 | `modules/translate/tests/test_config.py` | add null-loading + active-config pin tests |
| 19 | `modules/curate/docs/README.md` | document optional `top_p` in schema table |
| 20 | `modules/classify/docs/README.md` | document optional `top_p` in Config Map |
| 21 | `modules/translate/docs/README.md` | add Configuration Notes section |

## 6. Deployment & Post-Deployment Recovery Checklist

The patch fixes new requests only. Rows that already failed with HTTP 400
during the incident window stay locked in `curate` and `translate`; they must
be recovered deliberately. Execute in order after merge:

1. **Reconcile the cloud server.** If the option-(a) hotfix was applied on
   the server, discard/stash it so `git pull` lands cleanly; no
   `skip-worktree` drift may survive.
2. **Inventory locked rows** (identify Luna-parameter casualties from the
   incident window via counts plus run logs):
   ```bash
   python -m modules.curate.src.cli status --db-path <prod db>
   python -m modules.translate.src.cli status --db-path <prod db>
   ```
   - `curate` locked rows: `curate_status = 'failed' AND retry_count >= 3`
     (`cli.py` status query, line 198).
   - `translate` locked rows: `translation_status = 'failed' AND
     retry_count >= retry_attempts`, reported per language (`cli.py` status
     query, line 302).
   - `classify` needs no inventory: failed items are never written
     (`classify_item()` writes only on success, `orchestrator.py` lines
     378–380), so they remain unclassified and the next normal run retries
     them automatically. The classify CLI has no `--force` and needs none.
   - Status output supplies counts only. Before any forced re-run, use a
     **read-only** database query and the incident-window run logs to produce
     and manually verify the exact target list: `source_item_id` for
     `curate`, and `(parent_content_id, language_code)` for `translate`.
     Restrict the list to Luna-parameter HTTP 400 casualties from the
     incident window; do not bulk re-run unrelated historical locked rows.
3. **Re-run the verified victim items with `--force`, one at a time** (both
   CLIs restrict `--force` to single-item runs):
   ```bash
   python -m modules.curate.src.cli run --db-path <prod db> --source-item-id <id> --force
   python -m modules.translate.src.cli run --db-path <prod db> --parent-content-id <id> --language-code <code> --force
   ```
   Locked `curate` items require `--force` explicitly (`orchestrator.py`
   lines 698–699); `translate --force` requires `--parent-content-id`
   (`cli.py` lines 184–185).
4. **Small-batch live validation.** Before restoring the schedule, run each
   module once with a small override (e.g. `--batch-size 5`) against the
   production proxy and confirm zero HTTP 400 responses and successful DB
   writes in all three modules.
5. **Resume the normal pipeline schedule.**
6. **Close the incident only after a clean observation window** (no 400s).
   Then update `GPT_5_6_LUNA_PARAMETER_COMPATIBILITY_RISKS.md` from Active to
   Resolved, and retain a sanitized production HTTP 400 fixture (rejected
   field names only — no API keys, headers, or article content) per incident
   record §5.2.

## 7. Deferred (Not Part of This Patch)

- The long-term shared provider/model compatibility profile (incident record
  §5.5) remains a separate, future change gated on the module test audit and
  CI preparation in
  [`CODEBASE_MAINTAINABILITY_DIRECTIONS.md`](./CODEBASE_MAINTAINABILITY_DIRECTIONS.md).
- Cross-module pytest collection unification (module suites are run
  separately until then).
