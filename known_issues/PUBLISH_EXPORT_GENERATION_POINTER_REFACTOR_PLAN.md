# Publish Export Generation Pointer Refactor — Phase B2 Plan

**Status:** Implemented and verified on 2026-08-22 — publish suite 141 passed / 664 subtests / 1 skipped (documented no-symlink-privilege skip), site suite 154 passed, and a 20-check end-to-end sandbox rehearsal on a copy of the real data passed (legacy-transition witness path exercised for real, hardlink reuse confirmed by inode). Post-implementation review found two P1s — a null hash-stream reference accepted as legacy, and monthly-archive serialization accumulating a whole month in memory — both fixed with regression tests the same day
**Version:** B2 plan v1.6
**Date:** 2026-08-21
**B1 record:** Closed after final review on 2026-08-19. See [resolved B1 working note](resolved/PUBLISH_B1_IMPLEMENTATION_WORKING_NOTE.md).

## Purpose

Phase B2 optimizes physical storage for generation exports. It must preserve the established generation-and-pointer contract while reusing unchanged files from the live generation through hardlinks. This is an implementation optimization, not a change to export contents or reader behavior.

## Current baseline

B1 is closed and validated at **113 publish tests / 583 subtests** (the B1 closure baseline was 117/585; the 2026-08-20 removal of the legacy flat-export migration layer, commit `5b3975e`, dropped four migration tests). The current publish export contract provides:

- immutable, complete generation directories selected only through atomically replaced `current.json`;
- `run` state comparison: unchanged content does not create a generation and only refreshes pointer freshness; `rebuild` always creates a generation;
- single-writer lock and a stable generation snapshot during planning and build;
- full-generation semantics for every new generation, including bootstrap and language shrinkage;
- retention that protects the live generation, unlinks only safe retired generations, orders numeric `-rN` suffixes numerically, and never refills retired same-second ID gaps;
- reader protection for the residual pointer-to-generation TOCTOU: retry the complete read flow once after re-resolving the pointer, then fail cleanly; and
- generation changes, including forced rebuilds, invalidate generation-bound reader cursors.

B2 must not weaken or bypass any of these constraints.

## B2 design

### Goal

For a content-changing `run`, write physical bytes only for new or changed artifacts where possible. Logically, each generation remains a complete independent snapshot.

### Content hashes

Extend the per-generation content hash bookkeeping from aggregate artifacts to **all emitted artifacts**, including item files. These hashes decide whether a planned artifact is unchanged from the trusted preceding generation.

The hash table lives in its own stream artifact, not inside `meta.json`: each generation carries **`file_hashes.jsonl`** — newline-delimited JSON records, one per artifact in fixed artifact order, each `{"path": "<generation-relative path>", "digest": "sha256:<hex>"}` — and `meta.json` keeps the scalar fields (`generation`, `created_at`, `content_fingerprint`) plus a `"file_hashes": "file_hashes.jsonl"` reference. This shape is deliberate:

- **Bounded memory on both sides.** The writer appends one record per artifact as it is linked or written, and the reader streams records line by line into the run's temporary digest index; neither side ever holds a structure proportional to item count in memory (EXECUTION_POLICY Section 9). A whole-document JSON table would force exactly that: `json.loads` on a table with one entry per artifact is a linear resident allocation on the read side, and accumulating the table before writing `meta.json` is the same on the write side.
- **No third-party streaming JSON parser.** The hash table is builder-owned (only the runner ever reads it), so changing its format is cheaper than adding a dependency to stream-parse a format we control.

The corruption rules stay strict, and the stream is validated as it is read:

- A missing or unparseable `meta.json` on the live generation is fail-stop.
- A `meta.json` that carries the `file_hashes` reference but whose stream is missing or empty is fail-stop (every legitimately built generation records at least `stats.json` and the per-language aggregate files, so an empty stream is corruption, not a zero-data state).
- Every stream line must be a well-formed record with a legal generation-relative path, and paths must not repeat. (Path validation here is corruption detection: link source paths always come from the plan, never from the stream.)
- The final record must be `stats.json` — it is always the last artifact in the fixed artifact order, so any suffix truncation of the stream is detected, including a "valid-prefix" truncation that happens to land on a line boundary.
- Full expected-sequence validation is deliberately out of scope: the expected artifact set is not known when the stream is read (hashes are loaded before the plan exists). A missing or digest-mismatched entry degrades safely to a physical write — but note the boundary of that safety: an entry whose digest was **forged to match** planned content is indistinguishable from a valid record and would cause the old bytes to be linked. That case is not detectable by stream validation; it is part of the trusted-recorded-hash risk accepted under Reuse eligibility. Recording a digest of the stream in `meta.json` would detect accidental content corruption of the stream (not a forger who can also rewrite `meta.json`); it is deferred as optional hardening, not part of this plan.

A `meta.json` without the `file_hashes` reference is legacy **only when it positively matches the B1 shape**, and "matches" is defined precisely — all of the following must hold:

- `generation` matches the strict generation id format (`GENERATION_ID_RE`);
- `created_at` is a calendar-valid ISO-8601 UTC timestamp (the same validation the pointer receives);
- `content_fingerprint` matches `^sha256-exportstate-v1:[0-9a-f]{64}$`;
- `aggregate_file_hashes` is a non-empty object whose keys are legal generation-relative paths and whose values all match `^sha256:[0-9a-f]{64}$`.

All four checks reuse the existing id/timestamp/fingerprint validators plus the stream's path and digest validators. The legacy table is read solely as a format witness; its hashes are never consulted for reuse. This witness is corruption disambiguation, not a security boundary: even a forged but valid-looking B1 `meta.json` only routes the run to the safe no-reuse path (nothing is ever linked from unverified hashes), so its failure mode cannot produce wrong output — it exists to keep genuinely corrupt metadata fail-stop. Any reference-less `meta.json` failing a witness check (for example a B2-era file whose reference field was lost) is corruption and fails stop. See the transition rules below.

Digests recorded in the stream are of the **actual written bytes** of every artifact. One artifact legitimately has two digests: the fingerprint pass deliberately computes stats.json's digest over content excluding `last_export_run_timestamp` (so run wall-clock never perturbs the build decision), while the file on disk includes that timestamp. The excluded-timestamp variant is used only inside `content_fingerprint`; the stats.json record in the hash stream always records the digest of the real written bytes, so the recorded stream never disagrees with the disk. A corollary: because the stats timestamp advances with every build, stats.json bytes almost always differ between generations, so stats.json is physically written rather than linked in practice — a single small file per generation.

### B1 metadata transition

Existing B1 generations record hashes only for aggregate artifacts, as the `aggregate_file_hashes` table inside `meta.json`. B2 never uses that table for reuse decisions: a live generation is treated as pre-B2 **only** when its `meta.json` positively matches the B1 shape described above. Such a generation is treated as having **no reuse information** — the run logs a notice and proceeds with an empty hash table. This is safe by construction:

- A no-change `run` neither fails nor spuriously builds: with no recorded hashes, archive stamping falls back to the existing byte-compare against the live generation (equal bytes keep their recorded DB stamps), so the planned fingerprint still matches the pointer and only `last_successful_run_at` advances.
- The first content-changing build (or a `rebuild`) physically writes **every** artifact — items and aggregates alike — and records the full hash stream in the new generation. From that generation on, normal hardlink reuse applies.

B2 must never mutate an older generation — neither to backfill hashes nor to convert its format. Retired generations are deleted by retention without ever being read, and since retention keeps only the newest 5 generations plus the live one, every B1-format generation ages out of the tree within a few builds; no legacy-format code may linger to accommodate them.

`meta.json` and its hash stream are builder-owned metadata, not reusable export artifacts. They are written fresh for every new generation after the artifact digests are known. `current.json` remains the separately and atomically refreshed export-root pointer.

### Reuse eligibility

For each planned artifact:

1. Compare its planned content hash with the matching path in the preceding generation's hash stream.
2. Reuse with `os.link()` only when the hashes match, the source is a regular file inside the trusted preceding generation directory, and source and destination are on the same filesystem.
3. Write a new file for changed or new artifacts.
4. If linking fails for any reason (for example, cross-volume placement, filesystem policy, NTFS limitation, or network storage), safely fall back to copying/writing the planned bytes.

Never link a symlink, junction, reparse point, or source outside the trusted preceding generation directory.

**Link-time verification.** Every `os.link()` is followed by a post-link check: the destination and source must resolve to the same `(st_dev, st_ino)`, and the source must still be a regular, non-reparse file inside the trusted preceding generation directory; on any mismatch the destination file is removed and the run fails stop. This is the portable backstop for the check-then-act gap between source validation and linking. A fully race-free descriptor-based link was considered and rejected: `os.link()` cannot disable symlink following on Windows/NTFS, and the residual race requires a hostile local process racing a single-writer build, which the deployment model excludes — post-link verification turns it into a loud failure instead of silent cross-generation contamination.

Two further rules keep the reuse decision sound and worthwhile:

- **Trusted recorded hashes.** The decision trusts the hashes recorded in the preceding generation's hash stream; it does not re-hash source bytes at link time. Re-hashing would read the entire previous tree on every build and negate much of the saving, and generations are immutable with fail-stop handling for missing or corrupt hash metadata, so the recorded table is trustworthy by construction. The accepted residual risk is an **integrity** risk, accepted under the single-operator deployment: if a live-generation file is corrupted or tampered with out of band (bit rot, manual edit, a bad restore), the corruption propagates into future generations through hardlinks instead of being healed by a rewrite. Symmetrically, if the recorded stream itself is tampered with so that an entry's digest matches planned content its old bytes no longer equal, the runner links those old bytes and the pointer fingerprint no longer matches the physical output. Neither case is detectable without re-hashing source bytes; the repair path is `rebuild`, which physically rewrites everything and re-establishes verified bytes. An optional spot-check mode (re-hash a sample of link sources before linking) may be added later if operations call for it; it is not part of this plan.
- **Digest carry-over.** The fingerprint pass already computes the planned sha256 of every artifact. Those per-artifact digests are carried into the write pass through a **temporary disk-backed digest index** (a sequential spill file or temp SQLite next to the export root: appended in fixed artifact order during the fingerprint pass, consumed in the same order by the write pass, discarded at run teardown), so a reused artifact is linked without being re-serialized and without its database row being re-read. The preceding generation's hash stream is likewise **streamed line by line** into the temporary index for lookups — never parsed as a whole JSON document — and the new generation's hash stream is appended record by record during the write pass. No structure proportional to item count is ever resident: EXECUTION_POLICY Section 9 forbids emission-time memory that scales linearly with item count, and that bound covers hash bookkeeping, not just payloads. Without carry-over B2 would still save disk and write calls but would keep the current double serialization of every artifact, giving up most of the CPU and database-read saving.

### Immutability and rebuild

Generation contents are immutable after creation. A builder must only create new destination files; it must never overwrite, truncate, chmod, or replace a generation file in place. Under B1 an in-place edit merely violated the contract; under B2 the same edit silently rewrites every generation sharing the inode. Immutability is therefore safety-critical, and the data contract and the generation-store code comments must say so in these terms.

`rebuild` remains the physical-write escape hatch: it must force a full physical rewrite rather than reuse hardlinks, even when hashes match. This supports serializer changes, hash-algorithm upgrades, and repair operations.

### Retention and backups

Retention removes old generation entries by unlinking them. A linked artifact remains available while any retained generation references it. Existing live-generation protection, safe deletion checks, numeric suffix ordering, and no-gap-refill behavior remain mandatory.

Backup and rsync tooling may copy hardlinked files as separate files or otherwise fail to preserve link relationships. In particular, `rsync` expands hardlinks into independent copies unless invoked with `-H`, and because every generation lives under a freshly named directory, uploading `generations/` as a tree re-transfers unchanged artifacts under new paths unless the toolchain uses a mechanism such as `--link-dest`. Storage-savings claims therefore apply to the live export disk only; validate the actual VPS backup and deployment behavior before extending any claim beyond it. B2 must not assume that backup storage deduplicates hardlinks.

## Acceptance and validation

B2 is acceptable only when all of the following are demonstrated:

- unchanged files in a new generation are correctly hardlinked on Linux and NTFS, while changed files are physically written (the local test suite covers NTFS; Linux behavior is verified on the target VPS before rollout);
- every new generation records hashes for all emitted artifacts in its `file_hashes.jsonl` stream (one record per artifact in fixed artifact order, digests of actual written bytes), referenced from its `meta.json`;
- the pre-B2 transition requires a positive B1-shape witness — strict generation id, calendar-valid `created_at`, well-formed `content_fingerprint`, and a non-empty `aggregate_file_hashes` table with legal paths and `sha256:<64 hex>` digests, consulted only as a format witness: such a live generation is tolerated as a no-reuse-information source — no-change runs against it neither fail nor spuriously build, and the first content-changing build physically writes every artifact and establishes the full stream — while any reference-less `meta.json` failing a witness check fails stop, covered by a malformed-witness injection test;
- the hash stream is validated as read: a missing or empty stream, malformed records, illegal or duplicate paths, or a final record that is not `stats.json` (valid-prefix truncation) all fail stop — covered by truncation and corruption-injection tests;
- a reused artifact is linked without a second serialization or database re-read of its payload (digest carry-over from the fingerprint pass), demonstrated by instrumented tests, and all hash bookkeeping — the planned-digest carry-over, the prior generation's hash stream being read, and the new generation's stream being written — is disk-backed or streamed so that no structure proportional to item count is ever resident (a build with item counts several times `batch_size` demonstrates this);
- the stats.json record in the hash stream equals the digest of the actual on-disk bytes (including `last_export_run_timestamp`), and stats.json is physically written whenever its bytes differ;
- every hardlink passes post-link verification (same device and inode; source still a regular, non-reparse file inside the trusted prior generation), and an injected mismatch removes the destination and fails stop;
- cross-filesystem and injected link failures (including failures simulated by patching `os.link`, since no real cross-device setup exists on the development machine) fall back to safe copy/write without affecting generation correctness;
- source validation rejects symlinks, junctions, reparse points, and paths outside the trusted prior generation;
- linked files are never modified in place, including across subsequent builds;
- `rebuild` produces a fully physically rewritten generation;
- retention unlinks retired generations without breaking artifacts still referenced by retained generations, including link-count behavior where supported;
- no-change runs, pointer atomicity, lock/snapshot safety, full-generation output, reader TOCTOU retry, live protection, numeric suffix ordering, and no-gap-refill behavior remain covered and unchanged; and
- the publish test suite passes, with B2-specific tests reviewed independently.

## Documentation and contract updates

The B2 implementation must update these in the same change (repository rule):

- `modules/publish/docs/DATA_CONTRACT.md` Sections 2.3 and 6.7: Section 2.3's archive-stamping rule reads the live generation's hash stream instead of a `meta.json` table (the byte-compare fallback is unchanged); Section 6.7 documents `meta.json` keeping scalar metadata plus the `"file_hashes": "file_hashes.jsonl"` reference, the hash stream's record format (newline-delimited JSON, fixed artifact order, digests of actual written bytes including the stats.json dual-digest rule), the four-check B1 witness (tolerated as no reuse information; any other reference-less `meta.json` is fail-stop), the stream integrity checks (well-formed records, legal non-repeating paths, mandatory final `stats.json` record), and the safety-critical shared-inode immutability statement.
- `modules/publish/docs/EXECUTION_POLICY.md`: the physical write policy — `run` reuses hash-matched artifacts via hardlink with safe write fallback and post-link verification, `rebuild` always performs a full physical rewrite; Section 6.1's stamping wording follows the hash stream; and Section 9 wording records that artifact-hash bookkeeping is disk-backed/streamed while payload handling stays batch-bounded.
- `modules/publish/docs/TEST_COVERAGE_MAP.md`: list the new B2 tests and align existing entries' terminology with the hash-stream format.
- `modules/site` test fixture and `exportRoot.test.ts`: the committed generation fixture is regenerated in the B2 format (`meta.json` plus hash stream), and the fixture hash-verification test follows the stream format. The site's production code never reads generation metadata, so no site runtime change is needed.
- Code comments in `generation_store.py`: the reuse decision, the trusted-recorded-hash assumption (including the accepted integrity risk and its `rebuild` repair path), post-link verification, and the safety-critical immutability rule, in the terms used above.

## Non-goals

- Changing artifact schemas, pointer fields, reader contracts, API contracts, or canonical database schema.
- Replacing generation/pointer semantics with incremental live-tree mutation.
- Promising backup-side disk savings without environment-specific verification.

## Revision history

- **v1.0 (2026-08-19):** Reorganized the completed A/B1 proposal into the active B2 hardlink-reuse plan. Historical B1 implementation and review detail remains in the resolved B1 working note.
- **v1.1 (2026-08-21):** Pre-implementation review supplements: renamed the expanded hash table to `file_hashes`; stated the trusted-recorded-hash assumption and its corruption-propagation risk explicitly; required digest carry-over so reused artifacts skip re-serialization and database re-reads; upgraded the immutability rule to safety-critical; added backup/deploy verification specifics (`rsync -H`, freshly named generation paths); extended the acceptance criteria.
- **v1.2 (2026-08-21):** Simplified the hash-table transition per owner direction: dropped the legacy `aggregate_file_hashes` compatibility reader entirely. Only the live generation's hash metadata is ever read (`orchestrator`), the B1-designed fallbacks (archive byte-compare, physical write on missing hashes) already cover a hash-less source correctly, and B1 generations age out of the tree within keep=5 builds — a dual-key reader would have served exactly one transition period and then lingered as dead code. A legacy live `meta.json` is now treated as "no reuse information": no-change runs neither fail nor spuriously build, and the first content-changing build physically writes every artifact. Noted the site test fixture as a same-change touch point.
- **v1.3 (2026-08-21):** External review round, adopted in part: digest carry-over and the prior hash table move to a disk-backed temporary index to preserve the bounded-memory contract (a resident digest map would violate EXECUTION_POLICY Section 9 at the 100k+ envelope); the hash table is defined over actual written bytes with the stats.json dual-digest rule stated explicitly (the excluded-timestamp variant stays internal to `content_fingerprint`); post-link inode/file-type verification added as the portable fail-stop backstop for the check-then-act link race — a fully race-free descriptor-based link was rejected as non-portable to Windows/NTFS and disproportionate to the single-operator deployment. Baseline corrected to 113 tests / 583 subtests after the 2026-08-20 removal of the legacy flat-export migration layer (commit `5b3975e`).
- **v1.4 (2026-08-21):** External review round 2, adopted: the per-generation hash table moves out of `meta.json` into a sibling newline-delimited `file_hashes.jsonl` stream (referenced from `meta.json`), so both sides stay bounded in memory — the writer appends one record per artifact and the reader streams records into the temporary digest index, with no whole-document JSON parse anywhere. A whole-document table would force a linear resident allocation on both sides (the v1.3 "transient parse" carve-out contradicted Section 9 as written, and the same unremarked accumulation existed on the write side); a third-party streaming JSON parser was rejected as a dependency worse than changing a builder-owned format. Corruption rules are preserved via the `meta.json` reference: referenced-but-missing/empty/malformed stream is fail-stop; absent reference is the pre-B2 transition case.
- **v1.5 (2026-08-21):** External review round 3, adopted: the pre-B2 transition now requires a positive B1-shape witness (valid non-empty `aggregate_file_hashes` table, read only as a format witness — never as a hash source), so a B2-era `meta.json` that lost its reference fails stop instead of masquerading as legacy; the hash stream gains as-read integrity validation (well-formed records, legal non-repeating paths, mandatory final `stats.json` record) so valid-prefix truncation is corruption rather than silent degraded reuse — full expected-sequence validation was scoped out (the expected artifact set is unknown when the stream is read, and undetected mid-stream edits degrade safely to physical writes), with a `meta.json`-recorded stream digest noted as deferred optional hardening; the same-change documentation batch now includes DATA_CONTRACT Section 2.3 and EXECUTION_POLICY Section 6.1, whose archive-stamping wording references the old `meta.json` table.
- **v1.6 (2026-08-21):** External review round 4, adopted: the B1 witness is now defined by four concrete checks — strict generation id, calendar-valid `created_at`, well-formed `content_fingerprint`, and a non-empty legacy table with legal paths and `sha256:<64 hex>` digests — all reusing existing validators, with a malformed-witness fail-stop test; the witness is documented as corruption disambiguation, not a security boundary (its worst-case bypass only reaches the safe no-reuse path). Corrected the v1.5 overclaim that all mid-stream edits degrade safely: a stream record forged to match planned digests would link bytes the plan did not produce and desync the pointer fingerprint from the physical output — this is now stated as part of the accepted trusted-recorded-hash integrity risk under the single-operator deployment, with `rebuild` as the repair path. Reviewer sign-off received for implementation breakdown.
