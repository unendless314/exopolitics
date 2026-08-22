# Publish B2 Implementation Step Breakdown

**Status:** Implemented and verified on 2026-08-22 — Steps 0–11 complete (code, tests, docs, sandbox rehearsal); two post-review P1 fixes (null `file_hashes` reference and bounded-memory monthly archive serialization) applied with regression tests; ready for final sign-off
**Date:** 2026-08-21
**Parent plan:** [PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md](PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md) (B2 plan v1.6, approved 2026-08-21)

This document decomposes the approved B2 design into concrete implementation steps: the file change list, new tests mapped to the plan's acceptance criteria, execution order, and verification methods. It is the review artifact for the final sign-off before code changes; the parent plan remains the design contract.

## 1. Verified starting facts

- Baseline: `py -3 -m pytest modules/publish/tests -q` → **113 passed / 583 subtests**, all green.
- `generation_store.load_current_generation_hashes` has exactly one call site: `orchestrator.py:224` (live generation only; retention and the site never read `meta.json`).
- `generation.iter_planned_artifact_bytes` has exactly one call site: `orchestrator.py:247`.
- Outside the publish module, only the site test fixture (`modules/site/tests/fixtures/publish_export/generations/2026-07-22T03-00-00Z/meta.json`) and `modules/site/tests/exportRoot.test.ts:299` (the fixture hash-verification test) consume the `aggregate_file_hashes` format.
- Existing failure-injection seams that must survive: tests patch `modules.publish.src.generation_store.write_generation_to_staging` and `...write_pointer_atomic` by name, so both functions keep their module-level names (the former's signature changes; name-based patching is unaffected).
- Existing Windows pattern for creating links in tests: `test_coverage_loss.py:564` (`mklink /J` on NT, `os.symlink` elsewhere), with skip-on-failure as the fallback when the platform refuses.
- Fixed artifact order (defined once, in the fingerprint pass): per configured language (config order) `index.json`, `archives/index.json`, monthly archives month ASC, item payloads slug ASC; `stats.json` last.

## 2. File change list

### 2.1 New module: `modules/publish/src/digest_index.py` (~80 lines)

`DigestIndex`: the disk-backed temporary digest index required by the plan's digest carry-over and bounded-memory rules.

- Backing store: a temporary SQLite file at `export_dir / ".digest-index.tmp.sqlite"` (dotfile at the export root, inert to readers, which enter only through `current.json`). The constructor first creates `export_dir` with `parents=True, exist_ok=True`, because a bootstrap run legitimately begins before the export root exists.
- Tables: `planned(seq INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, digest TEXT NOT NULL)` and `prior(path TEXT PRIMARY KEY, digest TEXT NOT NULL)`.
- Methods: `add_planned_batch(iterable)` (executemany per fingerprint-pass batch), `add_prior(path, digest)`, `prior_digest_for(path) -> Optional[str]`, `iter_planned() -> Iterator[Tuple[path, digest]]` (`ORDER BY seq`), `close()`, `discard()` (close, then best-effort unlink, never raises; the close must precede unlinking because Windows will not delete an open SQLite file). `discard()` and startup recovery remove the index's owned SQLite file together with its `-journal`, `-wal`, and `-shm` sidecars, so a crashed run cannot leave state for the next index instance.

**Why temp SQLite rather than a pure spill file:** the write pass needs by-path lookup into the prior generation's table, and the artifact sets of two generations may differ (added/removed items and months), so a purely sequential merge is insufficient. SQLite is stdlib and already the project's storage tool — no new dependency.

### 2.2 `modules/publish/src/database.py`

Add `fetch_published_payload_by_slug(language_code, slug) -> Optional[sqlite3.Row]`: the same SELECT/JOIN/WHERE as `fetch_published_payload_batch` (`database.py:330`) plus `AND pr.slug = ?`, `LIMIT 1`. The write pass re-reads only items that need a physical write; reused items are never re-read.

### 2.3 `modules/publish/src/generation.py`

- `build_generation_plan`: the `current_hashes: Dict[str, str]` parameter becomes `prior_digest_for: Callable[[str], Optional[str]]` (backed by `DigestIndex`); `_decide_archive_stamp` (`generation.py:136`) consults it instead of dict `.get`. The five-priority stamping logic, including the byte-compare fallback against `fallback_root`, is unchanged.
- The fingerprint pass (`_iter_planned_artifact_digests`) appends every `(path, digest)` to the index via `add_planned_batch` — this is the carry-over write side. `stats.json` is recorded in the excluded-timestamp variant (the fingerprint digest), exactly as the plan's dual-digest rule requires.
- New `planned_bytes_for(plan, repo, config, rel_path) -> bytes`, dispatched on the path grammar:
  - `stats.json` → `serialize_json_bytes(plan.stats)` (with timestamp — real bytes);
  - `{lang}/index.json` → `serialize_json_bytes(plan.index_entries[lang])`;
  - `{lang}/archives/index.json` → `serialize_json_bytes(plan.manifest_entries[lang])`;
  - `{lang}/archives/archive_YYYY_MM.json` → re-stream that month via `_stream_archive_entries` (bounded per month);
  - `{lang}/items/{slug}.json` → `fetch_published_payload_by_slug` + `assemble_item_payload` + `validate_item_payload`. A missing row inside the held snapshot transaction is a runner bug: raise.
- Remove `iter_planned_artifact_bytes` (superseded by carry-over; its only caller is the orchestrator).

### 2.4 `modules/publish/src/generation_store.py` (main surface)

New validators:

- `_STREAM_DIGEST_RE = ^sha256:[0-9a-f]{64}$`
- `_is_legal_artifact_path(value)`: non-empty string, no leading `/`, no `\`, no `:`, no empty/`.`/`..` segments.

Read side — replaces `load_current_generation_hashes` (`generation_store.py:285`):

`load_live_generation_hashes(live_root, digest_index) -> bool` (returns `True` when the live generation is legacy B1 with no reuse information):

- Missing or unparseable `meta.json` → fail-stop (current behavior preserved).
- `file_hashes` key present: the value must be exactly `"file_hashes.jsonl"`, otherwise fail-stop. Stream missing or empty → fail-stop. Parse line by line: every line must be a well-formed record with a legal path and a digest matching `_STREAM_DIGEST_RE`; a duplicate path → fail-stop; each record goes into `digest_index.add_prior`. After the loop, the final record's path must be `stats.json`, otherwise fail-stop (valid-prefix truncation detection).
- `file_hashes` key absent: the four B1 witness checks — `is_valid_generation_id(meta["generation"])`, `is_valid_iso_timestamp(meta["created_at"])`, `_FINGERPRINT_RE` on `content_fingerprint`, and a non-empty `aggregate_file_hashes` object whose keys are legal paths and whose values match the digest regex. All pass → log a notice, return `True` (the prior table stays empty; witness hashes are never used for reuse). Any failure → fail-stop.

Write side — `write_generation_to_staging` (`generation_store.py:217`) rewritten:

```python
def write_generation_to_staging(
    export_dir, *,
    planned_entries,      # digest_index.iter_planned(): (rel_path, planned_digest), fixed order
    bytes_for,            # generation.planned_bytes_for callback
    prior_root,           # Optional[Path]; None for bootstrap/legacy
    digest_index,         # prior-digest lookups
    force_full_write,     # rebuild
    generation, created_at, content_fingerprint, languages,
) -> pathlib.Path
```

Per planned entry:

- Reuse iff: not `force_full_write`, `prior_root` is not None, `digest_index.prior_digest_for(rel_path) == planned_digest`, and the source passes validation. Validation requires that `prior_root` itself is a non-reparse directory; the source exists, is a regular non-reparse file, and its resolved path remains beneath the resolved `prior_root` (so a symlink or junction in a parent directory cannot make a regular-looking source file escape the trusted prior generation).
- Reuse path: `os.link(source, destination)`; any `OSError` → safe fallback to a physical write. Post-link verification: `os.stat(destination)` and `os.stat(source)` must agree on `(st_dev, st_ino)`, and the source must still be a regular, non-reparse file whose resolved path remains beneath the resolved non-reparse `prior_root`; on any mismatch, unlink the destination and raise (fail-stop).
- Physical write path: `body = bytes_for(rel_path)`; write; recorded digest is `sha256:<hex of body>`.
- Linked path: recorded digest is the planned digest (equal to the prior recorded digest by construction). `stats.json`'s excluded-timestamp planned digest can never equal a prior real-bytes digest, so `stats.json` is always physically written — the dual-digest rule needs no special case.
- One JSONL record (`{"path": ..., "digest": ...}`) is appended to the staging `file_hashes.jsonl` per artifact as it is processed (write side stays memory-bounded); `meta.json` (scalar fields plus `"file_hashes": "file_hashes.jsonl"`) is written last. Per-language empty-directory creation and the staging→generations rename are unchanged.
- `_is_aggregate_artifact` and the aggregate-only hash recording are removed (the stream covers all artifacts).
- Module docstring and comments are updated in the same change: the reuse decision, the trusted-recorded-hash assumption (including the accepted integrity risk and the `rebuild` repair path), post-link verification, and the safety-critical immutability rule, in the plan's terms.

### 2.5 `modules/publish/src/orchestrator.py` (~15 lines touched)

- Initialize `index = None` before the **outer** `try` that wraps the whole run body — not merely before the generation-phase `try` — because the teardown `finally` (next to `discard_staging`) also runs on reconciliation-phase failures, and the `index is not None` guard must see a bound variable on every path. After entering the generation-phase transaction, create the `DigestIndex` (creating `export_dir` when bootstrap has not created it yet, and removing the complete owned SQLite set — main file plus `-journal`, `-wal`, and `-shm` sidecars — from a crashed prior run first). The teardown `finally` calls `index.discard()` only when `index is not None`, so an index-creation failure remains the reported fail-stop error rather than being masked by an unbound-local cleanup error.
- Pointer present: `legacy = load_live_generation_hashes(live_root, index)`. Legacy → log notice, `prior_root = None`, `fallback_root = live_root` (the byte-compare stamping fallback still applies). B2 → `prior_root = live_root`, `fallback_root = live_root`.
- `build_generation_plan(repo, config, index.prior_digest_for, fallback_root, run_ts, rebuild)`.
- Build: `write_generation_to_staging(export_dir, planned_entries=index.iter_planned(), bytes_for=..., prior_root=prior_root, digest_index=index, force_full_write=rebuild, generation=..., created_at=..., content_fingerprint=..., languages=...)`.
- Teardown `finally` (next to `discard_staging`): when `index is not None`, call `index.discard()`.
- No-change runs also create and discard the index (the fingerprint pass always appends); the cost is one SQLite insert per artifact — negligible, and it keeps the flow single-shaped.

### 2.6 Tests: existing updates

- `tests/support.py`: add `read_hash_stream(generation_root) -> List[dict]`.
- `tests/test_generation_pointer.py`:
  - `assert_meta_hashes_match` (`:88`) is rewritten as `assert_generation_hash_stream_matches`: `meta.json` carries the reference; the stream parses; every recorded digest matches on-disk bytes; the stream covers every artifact **including items**; the final record is `stats.json`. Its call sites are updated.
  - `test_first_run_with_data_establishes_pointer_and_meta`: new `meta.json` shape.
  - `test_missing_or_corrupt_live_meta_json_fails_stop`: variant set updated to B2 corruption modes (missing/unparseable meta unchanged; the empty-table variant becomes a witness failure; stream-corruption variants live in the new class below).
  - `TestArchiveStamping::test_unchanged_archive_keeps_db_stamp_via_meta_hash_match`: mechanism unchanged (now served via the stream); expected to pass unmodified.

### 2.7 Documentation batch (same change — repository rule)

- `modules/publish/docs/DATA_CONTRACT.md` Section 2.3 (archive stamping reads the live generation's hash stream; byte-compare fallback unchanged) and Section 6.7 (new `meta.json` shape, the JSONL record format and fixed order, the stats.json dual-digest rule, the four-check B1 witness, the as-read stream integrity checks, the safety-critical shared-inode immutability statement).
- `modules/publish/docs/EXECUTION_POLICY.md` Section 6.1 (physical write policy: `run` reuses hash-matched artifacts via hardlink with safe write fallback and post-link verification; `rebuild` always performs a full physical rewrite) and Section 9 (artifact-hash bookkeeping is disk-backed/streamed while payload handling stays batch-bounded).
- `modules/publish/docs/TEST_COVERAGE_MAP.md`: new B2 tests listed; existing entries' terminology aligned with the hash-stream format.

### 2.8 Site batch (same change)

- Regenerate the fixture `meta.json` in the B2 shape and add `file_hashes.jsonl` next to it: real sha256 digests of every fixture artifact (per language en/ja/zh: `index.json`, `archives/index.json`, `archive_2026_07.json`, the two item files; `stats.json` last — 16 records), in fixed artifact order.
- `modules/site/tests/exportRoot.test.ts:299` hash-verification test follows the stream format (including item coverage). No site production-code change (the site never reads generation metadata).

## 3. Implementation choices the plan leaves open (please review explicitly)

1. Temporary digest index = temp SQLite, not a pure spill file (rationale in 2.1).
2. Index file location: dotfile at the export root, discarded at run teardown; a crashed run's complete owned SQLite set (main file plus `-journal`, `-wal`, and `-shm` sidecars) is removed when the next run creates its index.
3. The `meta.json` `file_hashes` value must be exactly `"file_hashes.jsonl"`; any other value fails stop (strictness removes a path-injection surface).
4. New repository method `fetch_published_payload_by_slug` (write pass fetches only changed items by slug).
5. B2 tests live as new classes in `tests/test_generation_pointer.py` (shared fixture reuse).
6. Strictness asymmetry, per the plan's wording: a non-regular link *source* degrades to a physical write; a *post-link verification* mismatch removes the destination and fails stop.

## 4. New tests, mapped to the plan's acceptance criteria

`TestHashStreamFormat`:

- `test_stream_covers_every_artifact_in_fixed_order_with_disk_matching_digests` — acceptance bullet 2.
- `test_stats_record_digest_matches_real_bytes_including_timestamp` — bullet 6.

`TestDigestIndexLifecycle`:

- `test_digest_index_creates_missing_export_root_for_bootstrap` — a first run with no export directory can create the temporary index before staging exists.
- `test_digest_index_cleanup_removes_owned_sqlite_sidecars` — stale `.sqlite`, `.sqlite-journal`, `.sqlite-wal`, and `.sqlite-shm` files from a crashed run are all removed before creating the next index and at teardown.

`TestHashStreamCorruption` (all fail-stop unless noted) — bullet 4:

- `test_referenced_stream_missing_fails_stop`
- `test_referenced_stream_empty_fails_stop`
- `test_malformed_stream_record_fails_stop` (subtests: non-JSON line, missing `path`, missing `digest`, bad digest format)
- `test_illegal_or_duplicate_stream_path_fails_stop` (subtests: `..` segment, leading `/`, backslash, empty segment, duplicate path)
- `test_valid_prefix_truncation_fails_stop` (drop the final line; final record is no longer `stats.json`)
- `test_meta_reference_with_unexpected_value_fails_stop`
- `test_mid_stream_digest_edit_degrades_to_physical_write` (not fail-stop: the safe-degradation half of the corruption rules)

`TestLegacyTransition` — bullet 3:

- `test_b1_live_generation_no_change_run_neither_fails_nor_builds`
- `test_b1_live_generation_first_content_change_writes_everything_and_establishes_stream`
- `test_b1_witness_hashes_are_never_used_for_reuse` (a B1-shaped `meta.json` carrying genuinely matching digests still gets zero links)
- `test_referenceless_meta_failing_witness_fails_stop` (subtests: bad generation id, calendar-invalid `created_at`, bad fingerprint, empty `aggregate_file_hashes`, illegal path key, bad digest value)

`TestHardlinkReuse` — bullets 1 and 5:

- `test_unchanged_artifacts_are_hardlinked_and_changed_ones_rewritten` (NTFS inode comparison locally; Linux on the VPS before rollout)
- `test_reused_items_skip_second_db_read_and_serialization` (spies: `_iter_item_payloads` runs exactly once per run; `fetch_published_payload_by_slug` is called exactly once per changed item)
- `test_reuse_works_with_item_count_several_times_batch_size` (`batch_size=5`, 23 items; reuse across batch boundaries)

`TestLinkSafety` — bullets 7, 8, 9:

- `test_link_failure_falls_back_to_physical_write` (patch `os.link` to raise `OSError`, simulating EXDEV)
- `test_post_link_verification_mismatch_removes_destination_and_fails_stop` (patch `os.link` to copy instead of link)
- `test_non_regular_link_source_falls_back_to_physical_write` (symlink/reparse source; skip when the platform cannot create one)
- `test_nested_reparse_link_source_falls_back_to_physical_write` (a regular artifact reached through a symlink/junction parent must fail source-containment validation and never be linked; skip when the platform cannot create one)

`TestRebuildAndRetentionUnderReuse` — bullets 10, 11, 12:

- `test_rebuild_physically_rewrites_even_when_hashes_match` (item bytes identical, every item inode distinct)
- `test_linked_files_are_never_modified_in_place` (a prior generation's bytes and inodes survive subsequent builds)
- `test_retention_unlinks_shared_inodes_without_breaking_retained_generations`

Bullets 13 (existing behaviors unchanged) and 14 (suite green) are covered by the untouched existing tests plus the final full run.

## 5. Execution order

- **Step 0 — baseline:** run the suite, confirm 113/583 green.
- **Steps 1–6 — one atomic code change**, in dependency order: `digest_index.py` → store read side → `database.py` method → `generation.py` carry-over → store write side → `orchestrator.py` wiring. The suite is expected to be **red in between**; that is normal for an atomic refactor and is only a debugging signal.
- **Step 7 — existing-test updates** (Section 2.6): suite green again at the old scope.
- **Step 8 — new B2 tests** (Section 4): suite green at the new scope.
- **Steps 9–10 — documentation and site fixture batches** (Sections 2.7, 2.8).
- **Step 11 — end-to-end validation** (Section 6).

## 6. Verification

- Every step: `py -3 -m pytest modules/publish/tests -q`.
- Site: `cd modules/site && npm test`.
- End-to-end sandbox rehearsal (**never touching `data/` itself** — everything is copied to a temp dir): the real live generation is B1-format, so the rehearsal exercises the witness path for real — no-change run (neither fails nor builds) → content-changing run (full physical write, stream established) → no-change run → content-changing run (hardlink reuse in effect; verify inodes) → `rebuild` (full physical rewrite).
- Linux hardlink behavior is verified on the target VPS before rollout, per the plan; it is not part of this change.

## 7. Explicit non-changes

- `process_lock.py`, the pointer format and atomic switch, reader contracts, artifact schemas, and the canonical DB schema are untouched.
- The `write_generation_to_staging` / `write_pointer_atomic` failure-injection patch points keep their module-level names.
- No backup-side dedup claims: VPS backup/deploy verification precedes any such claim, per the plan.

---

## 8. Implementation status handoff (2026-08-22, pre-compaction snapshot)

**Status: Steps 0–11 DONE and verified, sandbox rehearsal passed (20/20). Post-review follow-ups applied 2026-08-22: P1 fix #1 (null `file_hashes` reference now fails stop instead of routing to the legacy witness) with regression tests; P1 fix #2 (monthly archives are now serialized as a chunk stream — `_iter_archive_entries` + `_iter_json_array_bytes`, byte-identical to the canonical list serialization and locked by `TestStreamingJsonArraySerialization` — and the write pass consumes artifact bytes as chunk iterators via `planned_chunks_for`); phase-diary wording removed from code comments/docstrings. Final counts: publish 141 passed / 664 subtests / 1 skipped, site 154 passed.**

### Verified state

- Publish suite: **141 passed / 664 subtests / 1 skipped** (baseline was 113/583). The skip is `TestLinkSafety::test_non_regular_link_source_falls_back_to_physical_write` — WinError 1314 (no symlink privilege), the documented skip-on-failure fallback; the junction-based nested variant passes.
- Site suite: **154 passed** after fixture regeneration and the `exportRoot.test.ts` stream-format update.

### Files changed (all in one uncommitted working tree)

1. NEW `modules/publish/src/digest_index.py` — `DigestIndex`: temp SQLite at `export_dir/.digest-index.tmp.sqlite`; `planned(seq AUTOINCREMENT, path UNIQUE, digest)` + `prior(path PRIMARY KEY, digest)`; `add_planned_batch` / `add_prior` / `prior_digest_for` / `iter_planned` / `close` / `discard`; constructor creates export_dir and removes the owned set (`""`, `-journal`, `-wal`, `-shm`). **Deviation fix:** `iter_planned` pages with LIMIT/OFFSET short-lived cursors (page 1000) — a cursor held across yields keeps the SQLite file locked on Windows even after `conn.close()` (verified empirically), which broke teardown unlink when a build fails mid-iteration (post-link-mismatch test caught this).
2. `database.py` — added `fetch_published_payload_by_slug(language_code, slug)` (batch query + `AND pr.slug = ?`, LIMIT 1).
3. `generation.py` — `build_generation_plan(repo, config, prior_digest_for, digest_index, fallback_root, run_ts, rebuild)`. **Deviation:** `digest_index` is passed explicitly (§2.5's listed call omitted it, but the fingerprint pass must append the carry-over). `compute_content_fingerprint(..., digest_index=None)` appends planned digests in batch_size batches. `iter_planned_artifact_bytes` was removed and superseded by `planned_chunks_for(plan, repo, config, rel_path)`: path-grammar dispatch returns byte chunks, monthly archives are serialized and hashed as a bounded stream, and missing item rows raise as runner bugs.
4. `generation_store.py` — `load_current_generation_hashes` REPLACED by `load_live_generation_hashes(live_root, digest_index) -> bool` (True = legacy witness). New: `HASH_STREAM_NAME`, `_STREAM_DIGEST_RE`, `_is_legal_artifact_path`, `_matches_legacy_meta_shape` (4 witness checks), `_load_hash_stream` (line-by-line; **duplicate detection delegated to the prior table PRIMARY KEY** → IntegrityError → RuntimeError, avoiding a resident seen-set per Section 9; blank line = malformed record = fail-stop; final record must be stats.json), `_trusted_prior_root`, `_is_valid_link_source`, `_link_verified`, `_try_link_artifact` (OSError→fallback write; verify mismatch→unlink dest + raise). `write_generation_to_staging` takes `chunks_for` (chunk-streamed planned bytes); stream written with `newline="\n"` (pins LF on Windows); meta.json = scalars + `"file_hashes": "file_hashes.jsonl"`. Module docstring carries the trusted-recorded-hash risk, post-link verification, and safety-critical immutability wording.
5. `orchestrator.py` — `index = None` before the outer try; `DigestIndex(export_dir)` created after `BEGIN IMMEDIATE`; legacy → `prior_root=None`, `fallback_root=live_root`; teardown finally calls `index.discard()` when not None. Failure-injection patch-point names unchanged.
6. Tests — `support.py`: `read_hash_stream`. `test_generation_pointer.py`: `assert_meta_hashes_match` → `assert_generation_hash_stream_matches` (reference + parse + full artifact coverage incl. items + final stats.json + disk-matching digests); bootstrap/corrupt-meta tests updated (empty-table variant is now a witness failure: `del meta["file_hashes"]` + `aggregate_file_hashes={}`); new classes per §4: TestHashStreamFormat(2), TestDigestIndexLifecycle(2), TestHashStreamCorruption(7), TestLegacyTransition(4), TestHardlinkReuse(3), TestLinkSafety(4), TestRebuildAndRetentionUnderReuse(3); module helpers `_file_key`, `_make_dir_link`.
7. Docs — DATA_CONTRACT v3.4 (§2.3 stamping via stream with digest-compare fallback; §6 layout + hardlink bullet; §6.7 rewritten incl. the null-reference fail-stop rule); EXECUTION_POLICY v2.7 (§6.1 physical write policy + rebuild full rewrite + stamping wording; §9.2 hash-bookkeeping bullet); TEST_COVERAGE_MAP v1.10 (§14 intro/rows updated + B2 rows + review-fix rows); docs/DATA_LIFECYCLE.md §9.1 first bullet (file_hashes.jsonl + shared-inode safety note).
8. Site — fixture `meta.json` regenerated to B2 shape + `file_hashes.jsonl` (16 records, real sha256, order zh→en→ja, stats.json last, LF); `exportRoot.test.ts` hash test follows the stream (incl. item coverage, final stats.json), layout test includes the stream file. No site production-code change.

### Step 11 outcome (2026-08-22)

- The e2e sandbox rehearsal ran on a temp copy of the real data (`.b2_rehearsal.py`, deleted afterwards per the B1 precedent): **20/20 checks passed**, including the real legacy witness path, zero-link full write on the transition build, 16,017/16,027 hardlink reuse on the next content change, and a zero-link byte-identical rebuild. One script-side assumption was corrected during the rehearsal (rebuild legitimately restamps the archives manifests' `updated_at`, so the byte-identity check excludes the three `{lang}/archives/index.json` files alongside `stats.json`); no production-code change came out of it.
- Final confirmation after the rehearsal and the post-review fixes: `py -3 -m pytest modules/publish/tests -q` and `cd modules/site && npm test` (see §8 status line for the counts).
- Both plan docs' Status lines updated to "implemented and verified".

### Points flagged for the reviewer

1. `build_generation_plan` gained an explicit `digest_index` parameter (forced deviation from §2.5's listed call shape).
2. Duplicate-path detection uses the SQLite PRIMARY KEY, not an in-memory set (Section 9 bound).
3. `iter_planned` uses paged short-lived cursors (Windows file-lock fix above).
4. Blank stream lines count as malformed records (fail-stop).
5. ~~A JSON-null `file_hashes` value falls into the witness path and fails stop there~~ — **disproven by the post-implementation review**: a null beside a valid-looking `aggregate_file_hashes` table was accepted as legacy. Fixed 2026-08-22: a present `file_hashes` field now must equal exactly `file_hashes.jsonl` (`null` fails stop), covered by `TestLegacyTransition::test_null_file_hashes_reference_fails_stop`.
