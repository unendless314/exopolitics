# Publish State Transitions

**Document version:** v1.1
**Updated:** 2026-08-18
**Status:** Active rewrite draft

---

## 1. Publish Workflow States

The publish layer tracks downstream synchronization state per language using `publish_language_status.publish_status`.

- `pending`: Logical state only. A language artifact is pending if the item is export-eligible but there is no `publish_language_status` row yet for `(publish_record_id, language_code)`.
- `published`: A row exists with `publish_status = 'published'`, and the corresponding public artifact should exist on disk for that language.
- `withdrawn`: A row exists with `publish_status = 'withdrawn'`, meaning the artifact was previously public but has been removed from the export layer due to upstream eligibility loss or explicit withdrawal.

### 1.1 Source Of Truth Rule

The transition trigger for publish eligibility is not `publish_status` itself. It is derived from upstream state:

- `curation_decision.curate_status = 'approved'` means still eligible for public exposure
- `curation_decision.curate_status = 'withdrawn'` means no longer eligible for public exposure
- non-completed, stale, or fingerprint-mismatched translation rows are not exportable

---

## 2. State Transition Matrix

| Old State | Trigger / Event | New State | Publish Table Updates | File-System Side-Effects |
| :--- | :--- | :--- | :--- | :--- |
| **None / Pending** | Item becomes export-eligible and passes coverage policy | **published** | Insert `publish_record` if absent; insert `publish_language_status` with status=`published`, `published_at`, fingerprint | The next generation includes the item JSON; index, monthly archives, archive manifest & stats are built from the post-sync published set |
| **withdrawn** | Previously withdrawn item becomes eligible again | **published** | Update `publish_language_status` to status=`published`, retain previous `withdrawn_at` value to preserve audit history, refresh fingerprint, update `published_at` | The next generation recreates the item JSON; index, monthly archives, archive manifest & stats are built from the post-sync published set |
| **published** | Current export is re-run with unchanged fingerprint | **published** | No semantic change required; row should keep existing fingerprint and timestamps | No new generation is built; the live generation's artifacts stay byte-identical and only the pointer's `last_successful_run_at` advances |
| **published** | Mother-draft fingerprint changes and new completed translation becomes available | **published** | Update `source_fingerprint`, `published_at` | The next generation carries the updated item JSON; index, monthly archives, archive manifest & stats are built from the post-sync published set |
| **published** | Upstream curation state changes to `withdrawn` | **withdrawn** | Update `publish_language_status` to status=`withdrawn`, set `withdrawn_at`, preserve prior `published_at` | The next generation omits the item JSON; index, monthly archives, archive manifest & stats are built from the post-sync published set |
| **published** | Required language coverage becomes incomplete under `strict_match` | **withdrawn** | Update affected language rows to status=`withdrawn`, set `withdrawn_at` | The next generation omits the item JSON for all public languages of that item; aggregates are built from the post-sync published set |
| **published** | Translation row disappears from current eligible set because status is no longer `completed` or fingerprint no longer matches | **withdrawn** | Update `publish_language_status` to status=`withdrawn`, set `withdrawn_at` | The next generation omits the item JSON; index, monthly archives, archive manifest & stats are built from the post-sync published set |

File-system side effects are never per-file in-place mutations: each content-changing run builds a complete new generation from the post-sync database state and atomically switches the `current.json` pointer to it (see [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) Sections 4 and 6). "The next generation" above refers to that atomic switch; the previously live generation is never modified.

---

## 3. Withdrawal Synchronization Rules

When an item was previously exported but is no longer eligible, `publish` must synchronize the public layer by removing the file artifacts. This is a downstream cleanup responsibility, not an editorial decision.

The module must:

1. Identify previously `published` language rows that are absent from the current eligible export set.
2. Resolve the `slug` from `publish_record`.
3. Mark the corresponding `publish_language_status` row as `withdrawn`.
4. Build the next generation from the post-sync set so that it contains no `items/<slug>.json` for the withdrawn language rows — the atomic pointer switch is what removes the item from the public layer; the previously live generation is never modified.
5. The next generation's `index.json` excludes the item.
6. The next generation's monthly archive `archive_YYYY_MM.json` (located by the calendar month derived strictly from the item's `source_published_at` mapping to `source_item.published_at`) is built without the withdrawn item.
7. If the monthly archive becomes empty after removal, the next generation has no `archive_YYYY_MM.json` file for that month and no entry for it in the archives index manifest `archives/index.json` (rather than an empty file or a 0-item count). The archive's `publish_archive_metadata` row is deleted during the same build's metadata sync; if the same month's archive is later recreated, it starts a new row with a new logical write timestamp.
8. The archives index manifest `archives/index.json` and stats `stats.json` of the next generation are built from the post-sync state (see data aggregation source rules in [EXECUTION_POLICY.md](./EXECUTION_POLICY.md)) to reflect the updated metrics.

The module must not delete:

- `approved_content_record`
- `translation_output`
- `publish_record`

Those rows remain part of canonical history and cache preservation.

---

## 4. Rebuild Semantics

During a full rebuild:

- the export tree is treated as disposable output rebuildable from canonical state
- a complete new generation is always built from canonical state and the pointer switches to it atomically, regardless of the fingerprint comparison
- previously withdrawn items remain absent from rebuilt public outputs
- frozen slugs in `publish_record` must be reused rather than regenerated

Old generations are reclaimed by the retention policy (see [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) Section 6.3), not by clearing the export directory.

