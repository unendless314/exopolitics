# Ingest Error-Class Contract Analysis (HTTP 429 Fix Review)

**Status:** RESOLVED (2026-07-27) — Step 1 (2026-07-26) and Step 2 (2026-07-27) implemented, reviewed, and verified; Step 3 stands as a design direction requiring no action (see §7 closure note)
**Created:** 2026-07-26
**Updated:** 2026-07-27 (second round of reviewer feedback incorporated: contract-exception escalation, enum/CHECK relationship wording, reproducible test records, §4 wording fix; Step 1 implemented and verified in both interpreters; third round: P1 Retry-After HTTP-date parsing + non-finite rejection implemented with tests; environment decision: standardized on system Python 3.12, `.venv` removed — §7 item 2; Step 2 design finalized: engineer proposal verified against code and adopted in full, with two recorded clarifications — §5 Step 2, §7; cross-review round: conditional approval, corrections incorporated — existing-state rollback semantics, explicit Step 2.5 / pre-seeded-state tests, run-count and `error_summary` assertions, literal count corrected to 11, §1-§2 marked superseded; Step 2 implemented, final review completed, and ingest suite verified at 73 passed plus 9 subtests; closure: issue resolved — Step 3 requires no implementation, Reddit UA placeholder deferred by owner decision, document archived to `known_issues/resolved/`)
**Author:** exopolitics Development Team

---

## 1. Background

On 2026-07-26 a manual `python -m modules.ingest.src.cli fetch` run ended in `partial_failure`: source 77 (Reddit /r/space) failed with `http_error_4xx: HTTP 429` (rate limiting). A subsequent fix (currently **uncommitted** working-tree changes) modified:

- `modules/ingest/config/sources.yaml` — corrected source 77 `xml_url`, added `html_url`, added Reddit-style `request_headers` User-Agent to sources 76 and 77.
- `modules/ingest/src/fetcher.py` — default Chrome User-Agent, HTTP 429 treated as transient/retriable, dynamic backoff parsed from `retry-after` / `x-ratelimit-reset` headers.
- `modules/ingest/tests/test_fetcher.py` — 9 new unit tests.

This document records a code review of those changes: one **critical defect**, several quality issues, the root-cause analysis, downstream impact assessment, and the remediation plan. Two independent reviewers reproduced the critical defect and converged on the plan in Section 5.

---

## 2. Review Findings

> **Historical note (2026-07-26):** Sections 1-2 describe the **pre-Step-1** working-tree state, kept for the record. Step 1 (implemented 2026-07-26) superseded the described behavior — the `http_error_429` emission, the string-parsed delay, and the hardcoded Chrome UA no longer exist, and `fetcher.py` line numbers cited in §2 refer to the pre-Step-1 file. For the current code state see §5 Step 1 and the §6 test records.

### 2.1 CRITICAL: `http_error_429` violates the database CHECK constraints

The fix introduces a new error class `http_error_429` (`fetcher.py:94`), but the v001 schema enforces a bounded set that does not include it:

- `modules/ingest/src/migrations/v001_initial_ingest_tables.sql:13-16` (`source_state.last_error_class`)
- `modules/ingest/src/migrations/v001_initial_ingest_tables.sql:47-50` (`fetch_attempt.error_class`)

Allowed set: `network_error`, `timeout_error`, `http_error_4xx`, `http_error_5xx`, `parse_error`, `validation_error`, `persistence_error`, `unexpected_error`.

Verified empirically against the migration SQL (sqlite3 in-memory):

```
INSERT ... error_class='http_error_429'
-> IntegrityError: CHECK constraint failed: error_class IN (...)
```

**Blast radius when a source exhausts 429 retries:**

1. `orchestrator.py:462` `state_repo.upsert(... last_error_class='http_error_429')` raises `sqlite3.IntegrityError`.
2. The failure-isolation handler (`orchestrator.py:502`) catches it and re-records the source as `unexpected_error` with `error_detail = "CHECK constraint failed: ..."`.
3. The run survives (isolation works), but the recorded data is misleading: the real 429 diagnosis, `http_status=429`, and the true `retry_count` are all lost. The CHECK error is only visible in logs.

The new tests did not catch this because every new test mocks the HTTP layer only; nothing exercises the "FetchResult -> persistence" path.

### 2.2 MEDIUM: retry delay smuggled through a human-readable string

`fetcher.py:139-143` recovers the dynamic delay by parsing the error message:

```python
if error_class == "http_error_429" and "Suggested wait:" in error_detail:
    sleep_time = float(error_detail.split("Suggested wait: ")[1].replace("s", ""))
```

Fragile: depends on message formatting; `.replace("s", "")` strips every `s`. Should be a proper loop-local variable (e.g. `suggested_delay: Optional[float]`, reset per attempt) assigned in the 429 branch.

### 2.3 MINOR: documentation drift in `fetcher.py`

- `fetcher.py:15` — `error_class` comment still lists only the five old classes.
- `fetcher.py:33-35` — Retry Policy docstring still says "Immediately fails and does not retry for 4xx errors", contradicting the new 429 behavior.
- `git diff --check` flags two trailing-whitespace spots in the uncommitted changes (found by the second reviewer).

### 2.4 Configuration and header decisions

- **Hardcoded default Chrome UA (`fetcher.py:40`) — remove it (second-reviewer verdict, adopted).** It changes behavior for all ~98 sources, not just Reddit, and the fingerprint will age (`Chrome/120.0.0.0`), which some WAFs flag. The original failing run had 97/98 sources succeed with the default httpx UA (confirmed by the second reviewer against the read-only DB: `fetch_run_id=2` = 98 attempted / 97 succeeded, source 77 = HTTP 429), so a global UA change is unneeded scope expansion; Reddit's need is already covered by per-source `request_headers`. (Override order — defaults first, then `custom_headers` — was correct.)
- `x-ratelimit-reset` semantics vary by API: Reddit returns seconds-until-reset (correct as a delay), but APIs returning epoch timestamps would be clamped to the 60s cap. Not catastrophic; worth a code comment stating Reddit semantics are assumed.
- `sources.yaml` Reddit UA `pc:exopolitics.ingest:v1.0.0 (by /u/exopolitics_bot)` follows Reddit's format, but `/u/exopolitics_bot` is presumably not a real account; Reddit asks for a contactable username.
- Good parts: `request_headers` uses the existing config contract (`config.py:82`, `SOURCE_CONFIG_CONTRACT.md:273`); `asyncio.sleep` runs outside the semaphore so 429 waits don't block other sources; delay is clamped to 1-60s.

---

## 3. Root Cause: `error_class` Is an Unowned Contract

The taxonomy currently lives in three places with no single source of truth:

1. `fetcher.py:15` code comment (no enforcement)
2. `v001_initial_ingest_tables.sql` CHECK constraints (the only enforced — and least visible — copy)
3. `orchestrator.py` string literals (`parse_error`, `unexpected_error`)

Documentation gap, with evidence:

- `modules/ingest/docs/STORAGE_SCHEMA.md:446-454` ("Minimum bounded-value sets for first-migration checks") lists `health_status`, `outcome`, `trigger_type`, `run_status`, `ingest_status`, `text_processing_*` — **but not `error_class`**.
- `modules/ingest/docs/FETCH_EXECUTION.md:88` requires recording "error class and detail when failed" but never enumerates the classes.

So the v001 DDL added CHECK constraints beyond the documented set, creating an undocumented bounded contract. "Follow the tech docs" was not possible here — the docs never state the allowed classes or that the DB enforces them.

Corroborating evidence that the taxonomy was speculative rather than derived from code: `validation_error` and `persistence_error` appear in the CHECK constraints but are **emitted by no code**, and a read-only inspection of `data/canonical.db` on 2026-07-26 shows they have **never been stored** either (`fetch_attempt.error_class`: only `http_error_4xx` x5 plus NULL x197; `source_state.last_error_class`: NULL on all 98 rows).

A second design observation: CHECK constraints are the right tool for **state-machine enums** that drive logic (`health_status`, `outcome`, `run_status`), but `error_class` is a **diagnostic enum** — nothing branches on it (orchestrator and analysis only record/group it), and its value set is pressured by external reality (Reddit's rate limiting forced this very change). The agreed direction (Section 5, Step 3) resolves this tension by keeping the category bounded while moving fine-grained diagnosis to `http_status` and, if ever needed, a separate free-form column.

Contributing factors:

- Verification gap: tests mocked only the HTTP layer; no test persists a failed FetchResult.
- Masking effect: the isolation handler re-classifies persistence failures as `unexpected_error`, so contract violations fail quietly in the data.

---

## 4. Downstream Impact Assessment

All consumers of `error_class` / `last_error_class` were surveyed:

| Consumer | Dependency | Affected by taxonomy change? |
|---|---|---|
| `modules/analysis` | Generic `GROUP BY error_class, http_status` (`ingest_queries.py:30-36`); passthrough in service; report contract types the field as `["string","null"]` with no enum | No — tolerates any value |
| `modules/dashboard` | Zero references | No |
| `modules/classify` / `curate` / `translate` / `publish` / `site` | Zero runtime references (only an example SQL in a `site` notes doc) | No |
| Top-level `docs/` contracts | Zero references | No |
| ingest DB CHECK constraints | The only hard constraint on values | **No schema change under the agreed plan** — but new code must remain compatible with it |

Conclusion: `error_class` is produced only by ingest and policed only by ingest's own schema. **No downstream module is coupled to the taxonomy, and the remediation plan deliberately leaves the schema untouched.**

Live DB state (`data/canonical.db`): `fetch_attempt` 202 rows, `source_state` 98 rows. Migrations auto-apply via `run_migrations` on CLI startup (`cli.py:103,123`).

---

## 5. Recommended Remediation

### Step 1 — Minimal fix (do now; zero schema change, zero downstream impact)

1. In `fetcher.py`, stop emitting `http_error_429`; classify 429 failures as the existing `http_error_4xx`. Keep 429 retriable inside `fetch_feed` and keep the dynamic backoff. Diagnostics are fully preserved via `http_status=429` + `error_detail`; `modules/analysis` already groups by `(error_class, http_status)` (`ingest_queries.py:30-36`), so rate limiting stays precisely visible in reports.
2. Replace the string-parsing delay hack (2.2) with a loop-local `suggested_delay` variable.
3. Remove the hardcoded default Chrome UA (see 2.4); Reddit-specific headers stay at source level via `request_headers`.
4. Fix the docstring drift and the two trailing-whitespace spots (2.3).
5. Update the new tests: assert `error_class == "http_error_4xx"` for exhausted 429s; keep the dynamic-sleep assertions. Add request-construction coverage confirming that a request without `custom_headers` does not receive a forced Chrome `User-Agent`, while a source-provided `User-Agent` is forwarded unchanged.

### Step 2 — Contract governance refactor (design agreed 2026-07-26; separate change; ~half day, low risk; no schema change)

Design status: an engineer's Step 2 proposal was verified item-by-item against the code and **adopted in full**, with two clarifications recorded below (NULL-only boundary validation in item 2; the annotation precondition in item 5). A cross-review (2026-07-26) then conditionally approved the design; its conditions are incorporated below (existing-state rollback semantics in item 2, explicit tests in items 3 and 5, literal count in item 1). Verified facts the design relies on: the only two writers of error-class columns are `SourceStateRepository.upsert()` (`database.py:124`) and `FetchAttemptRepository.insert()` (`database.py:209`); the fetch-failure flow writes `upsert` first inside a single transaction (`orchestrator.py:460-492`); `orchestrate_source` / `orchestrate_run` open multiple independent connections (`orchestrator.py:96,624,704`), which rules out `:memory:` test databases.

1. **Single source of truth in code — defined narrowly.** A new `modules/ingest/src/errors.py`:
   - `class ErrorClass(str, Enum)` holding exactly the six values code may newly emit/write: `network_error`, `timeout_error`, `http_error_4xx`, `http_error_5xx`, `parse_error`, `unexpected_error`.
   - A derived immutable validation set (frozenset of the enum values) as the single validation reference, plus `ErrorClassContractError` in the same module. `errors.py` imports nothing from `database` / `orchestrator` (no import cycles; `database.py` imports from `errors.py`).
   - Producers reference `ErrorClass.X.value`, replacing the 12 string literals: six in `fetcher.py` (`network_error`, `timeout_error`, `http_error_5xx`, `unexpected_error`, and `http_error_4xx` twice — the 429 branch and the non-retryable 4xx branch), plus six in `orchestrator.py` (`parse_error` x3 and `unexpected_error` x3 — each appears in the state upsert, the attempt insert, and the `SourceExecutionResult`).
   - `FetchResult.error_class` stays `Optional[str]` — no dataclass/type churn.
   - The 8-value SQLite CHECK is **not** a co-equal source of truth: it is a deliberately retained compatibility superset. DB inspection (Section 3) shows `validation_error` / `persistence_error` were never emitted and never stored; they remain in the CHECK solely to avoid a schema-only migration. Do not "re-sync" them from the CHECK constraint back into code, and do not treat divergence between the two sets as an error.
2. **Validate at the persistence boundary — and make violations escape isolation.**
   - `SourceStateRepository.upsert()` validates `last_error_class` and `FetchAttemptRepository.insert()` validates `error_class` against the enum-derived set before executing SQL. **Clarification: validation applies to non-NULL values only — NULL is always legal** (every success path writes NULL, e.g. `orchestrator.py:187,198,426,437`). Violations raise `ErrorClassContractError` naming the offending value and the target table/column.
   - Add `except ErrorClassContractError: raise` **before** the broad handler in `orchestrate_source` (`orchestrator.py:502`), so the validator's exception is not re-classified as `unexpected_error` — the same masking with a new message.
   - **Escape semantics (explicit):** the escaped exception is collected by `asyncio.gather(return_exceptions=True)` (`orchestrator.py:652`), recorded at run level as `Source {id} OrchestrationException: ...` in `error_summary` (`orchestrator.py:672`), and sets `run_status = "failed"` (`orchestrator.py:693-694`). `orchestrate_run()` itself does **not** re-raise toward the CLI — this preserves the existing concurrency-isolation design. Other sources still complete.
   - Stated consequence (a conscious decision): a contract violation is a programming error, not an operational source failure. Because the failure flow writes `upsert` first within one transaction (`orchestrator.py:460-492`), the transaction rollback (`database.py:23-35`: `BEGIN IMMEDIATE` with rollback-on-exception) means: a **new** source is left with no state row and no attempt row, while an **existing** source keeps its prior state row fully intact at its pre-transaction values — rollback restores, it does not delete previously committed rows. In both cases no attempt row is written and the committed `consecutive_failures` is not incremented; the run report fails loudly instead.
3. **Round-trip integration tests**, using a temp-file SQLite DB built by the real `run_migrations` — the existing `test_integration.py` pattern (`tempfile.TemporaryDirectory` + `run_migrations`, `test_integration.py:49,117`). **Do not use `:memory:`**: the flow opens multiple independent connections, and each `:memory:` connection would see a separate empty database.
   - For each of the six emittable error classes, run the failure-persistence path and verify writes to both `source_state` and `fetch_attempt`. (This is the test that would have caught the 2.1 defect.)
   - "Illegal `error_class` is not re-classified": a **two-source** run — one source's mocked `fetch_feed` returns an out-of-enum `error_class`, the other succeeds. Assert: `run_status == "failed"`; run-level counts `attempted=2, succeeded=1, failed=1`; the healthy source's state/attempt rows exist with `outcome="success"`; the violating source has **no** state row (new-source case), **no** attempt row, and **no** `unexpected_error` row; `error_summary` surfaces the violation as an `OrchestrationException` containing the source ID, the offending field, and the offending value (carried by the `ErrorClassContractError` message from item 2).
   - Existing-source variant of the illegal-value test: pre-seed a committed `source_state` row for the violating source (via a prior mocked success or a direct repository write), feed the out-of-enum value, and assert the state row is unchanged in every column afterwards — including `consecutive_failures` — with no attempt row added.
   - Two small repository unit tests: `upsert()` and `insert()` each reject an out-of-enum value with `ErrorClassContractError`, and each accepts NULL.
4. **Document the contract** (required by AGENTS.md: "Any new scaffold, schema, or state transition must update `modules/<module>/docs/`"): in `STORAGE_SCHEMA.md` §8 — the six-value emittable enum, the enum-vs-CHECK compatibility-superset relationship (item 1), and that `http_status` carries the precise HTTP-layer cause (the category-vs-detail split from Step 3). In `FETCH_EXECUTION.md` §6-§7 — the enumerated error classes, and the rule that a contract violation is a programming error that fails the run via the escalation path (item 2) without affecting other sources.
5. **Step 2.5 — adopted, with detail-hygiene rules.** For **non-contract** persistence failures (e.g. I/O errors) recorded via the isolation handler (`orchestrator.py:502`), keep writing `unexpected_error`, but annotate the new `error_detail` with the original `error_class` and `http_status` so those masked failures remain diagnosable from the DB alone. Rules:
   - **Clarification:** annotate **only when** `fetch_result` exists in locals **and** `fetch_result.error_class` is not None (a crash during the success flow has `error_class=None`; annotating it would be noise). The `'x' in locals()` guard has precedent at `orchestrator.py:512`.
   - **Never copy the original `error_detail`** — it embeds a remote response excerpt (`e.response.text[:200]`).
   - Contract violations are excluded — they escalate per item 2.
   - Explicit tests: (a) a failed fetch (known `error_class` + `http_status`) followed by a non-contract persistence error → the fallback `unexpected_error` row's `error_detail` contains the original `error_class` and `http_status` and does **not** contain the original remote `error_detail`; (b) a success-flow persistence failure → no fetch-error annotation appears. The contract-violation direction (no fallback `unexpected_error` written at all) is already asserted by the item 3 tests.

### Step 3 — Agreed long-term direction: keep `error_class` coarse; add `error_code` only if ever needed

Adopted from the second reviewer's proposal; this supersedes the earlier "relax the CHECK vs. keep and migrate" (Option A/B) debate:

- `error_class` stays **stable, coarse-grained, and DB-CHECK-bounded**. No new persisted `http_error_429`; no table-rebuild migration. The bounded CHECK becomes a feature: it forces any taxonomy change to be an explicit, reviewed contract decision.
- `http_status` remains the precise machine-readable cause for HTTP-layer failures; `modules/analysis` already groups by `(error_class, http_status)`.
- If cross-protocol fine-grained diagnostics ever become necessary (e.g. non-HTTP failure detail), add a **new nullable `error_code` column** (application-validated, not DB-CHECKed) rather than continuously inflating `error_class`. In SQLite this is a cheap `ALTER TABLE ... ADD COLUMN` migration — no table rebuild required.

The original Option A/B analysis is retained for the record:

- ~~Option A: relax the DB; validate diagnostic enums at the application layer.~~
- ~~Option B: keep DB enforcement; run a table-rebuild migration per taxonomy change.~~
- Superseded because the `error_code` direction provides the same diagnostic freedom with less churn: the category stays bounded, the detail stays free-form.

Migration safety notes (only if an `error_code` column is ever added): back up the DB file first; verify on a copy of `canonical.db`; a nullable `ADD COLUMN` needs no data copy.

---

## 6. Verification Evidence (Reproducible Records)

Test runs — interpreter matters, both records are accurate for their environment:

- **System Python 3.12** (`C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe`; has `feedparser` + `bs4`): `python -m pytest modules/ingest/tests/ -q` -> **61 passed** (first reviewer's record).
- **Project venv** (`.venv/Scripts/python.exe`; pytest 9.1.1, **missing** `feedparser` / `bs4`): `python -m pytest modules/ingest/tests/test_fetcher.py -q` -> **9 passed**; `python -m pytest modules/ingest/tests/ -q` -> collection fails with 3 errors (`test_cli.py`, `test_integration.py`, `test_sanitizer.py` import `feedparser`/`bs4` transitively). This matches the second reviewer's record.
- Gap identified: the ingest module has **no dependency manifest** (no `requirements*.txt` / `pyproject.toml` under `modules/ingest/` or the repo root; only `modules/dashboard` and `modules/analysis` declare requirements), so the project venv cannot currently reproduce the full suite. Remediation is listed in Section 7.

Other evidence:

- sqlite3 in-memory replay of `v001_initial_ingest_tables.sql`: inserts with `error_class='http_error_429'` / `last_error_class='http_error_429'` both raise `IntegrityError: CHECK constraint failed`. **Independently reproduced by the second reviewer.**
- `data/canonical.db` read-only inspection: live schema contains the same CHECK constraints; `fetch_run_id=2` shows 98 attempted / 97 succeeded with source 77 = HTTP 429 (second reviewer's confirmation); `error_class` distribution shows `validation_error` / `persistence_error` never stored (Section 3).
- `git diff --check` on the uncommitted changes: two trailing-whitespace spots (second reviewer's finding; cleanup folded into Step 1).
- grep survey of `modules/`, `docs/`: no hardcoded error-class taxonomies outside `fetcher.py`, `orchestrator.py`, and the migration SQL.

---

## 7. Decisions Reached and Remaining Items

Resolved by the second review (2026-07-26):

1. ~~Distinct persisted `http_error_429` class?~~ -> No. `error_class` stays coarse; `http_status` carries the precise cause (Step 3).
2. ~~Relax vs. keep DB CHECK (Option A/B)?~~ -> Superseded by the `error_code`-column direction (Step 3).
3. ~~Default Chrome UA for all sources?~~ -> Remove; per-source `request_headers` only (Step 1.3).
4. ~~Is repository-layer validation sufficient?~~ -> No; it must be paired with a dedicated contract exception that escapes the isolation handler (Step 2.2).

Resolved during Step 2 design finalization (2026-07-26):

5. ~~Optional hardening (Step 2.5) — include in Step 2 or drop?~~ -> Included, with the detail-hygiene rules folded into Step 2 item 5.
6. ~~Step 2 design open points (enum shape, test-DB strategy, validation semantics, escape semantics)~~ -> Agreed: the engineer's Step 2 proposal was verified item-by-item against the code and adopted in full, with two recorded clarifications (NULL-only boundary validation, Step 2 item 2; annotation precondition, Step 2 item 5). Cross-review and implementation are complete.

Cross-review (2026-07-26) — conditionally approved; all conditions incorporated:

7. Rollback semantics corrected (Step 2 item 2): rollback **restores** an existing state row rather than leaving "no state row"; the pre-seeded-state integration test was added to Step 2 item 3 to lock this in.
8. Step 2.5 tests made explicit (Step 2 item 5), including the no-annotation success-flow case; run-count (`attempted=2, succeeded=1, failed=1`) and `error_summary` content assertions (source ID, field, offending value) added to the two-source test (Step 2 item 3).
9. Editorial fixes: `fetcher.py` error-class literal count corrected to six (Step 2 item 1); §1-§2 marked as superseded by Step 1 (historical note at the top of §2). No outstanding design decisions remain. (Implementation review, 2026-07-26: the total was further corrected to 12 — `orchestrator.py` has `parse_error` x3, including the `SourceExecutionResult` literal, which the implementation also replaced.)

Remaining items at closure:

1. ~~Scheduling of Step 1~~ -> Done 2026-07-26 (`fetcher.py` + `test_fetcher.py`: 429 classified as `http_error_4xx`, loop-local `suggested_delay`, Chrome UA removed, docstrings and trailing whitespace fixed, UA request-construction tests added; project venv `test_fetcher.py` 11 passed, system interpreter full suite 63 passed, `git diff --check` clean). Follow-up (third review round, P1): `Retry-After` now supports HTTP-date per RFC 7231 via a `_parse_retry_delay` helper and rejects NaN/Infinity with `math.isfinite()` before the 1-60s clamp, with coverage for HTTP-date and non-finite values (venv 13 passed + 3 subtests, system interpreter 65 passed + 3 subtests, `git diff --check` clean). Step 2 was implemented, reviewed, and verified on 2026-07-27 (`73 passed, 9 subtests passed`; `git diff --check` clean).
2. ~~**Test-environment debt**~~ -> Resolved 2026-07-26 by environment decision rather than by manifest: the project standardizes on the **system Python 3.12 interpreter** (no virtualenv — the repo `.venv` was a later addition, now deleted). Dashboard dependencies (`modules/dashboard/requirements.txt`: streamlit, plotly, pandas) were installed into the system interpreter, and after `.venv` removal all module suites pass there (analysis 27, classify 17, curate 15, dashboard 17, ingest 65+3, publish 27+45, translate 48). No ingest manifest added; the venv-based records in §6 are historical and no longer reproducible as recorded.
3. ~~The Reddit UA placeholder `/u/exopolitics_bot` (2.4) should become a real, contactable account.~~ -> Deferred 2026-07-27 by owner decision: placeholder left in place; any Reddit-side blocking would surface as fetch errors on sources 76/77, and the decision will be revisited then.

**Closure note (2026-07-27):** all tracked items are either done or consciously deferred. Step 3 requires no implementation — it is a standing design direction (coarse `error_class` + `http_status` detail; add a nullable `error_code` column only if a real cross-protocol diagnostic need emerges). Issue closed; this document is archived under `known_issues/resolved/`.
