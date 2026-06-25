# Publish State Transitions

**Document version:** v1.0  
**Updated:** 2026-06-24  
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
| **None / Pending** | Item becomes export-eligible and passes coverage policy | **published** | Insert `publish_record` if absent; insert `publish_language_status` with status=`published`, `published_at`, fingerprint | Write item JSON; rebuild index, affected monthly archives, archive manifest & stats |
| **withdrawn** | Previously withdrawn item becomes eligible again | **published** | Update `publish_language_status` to status=`published`, retain previous `withdrawn_at` value to preserve audit history, refresh fingerprint, update `published_at` | Recreate item JSON; rebuild index, affected monthly archives, archive manifest & stats |
| **published** | Current export is re-run with unchanged fingerprint | **published** | No semantic change required; row should keep existing fingerprint and timestamps | File content may remain untouched if identical |
| **published** | Mother-draft fingerprint changes and new completed translation becomes available | **published** | Update `source_fingerprint`, `published_at` | Overwrite item JSON; rebuild index, affected monthly archives, archive manifest & stats |
| **published** | Upstream curation state changes to `withdrawn` | **withdrawn** | Update `publish_language_status` to status=`withdrawn`, set `withdrawn_at`, preserve prior `published_at` | Delete item JSON; rebuild index, affected monthly archives, archive manifest & stats |
| **published** | Required language coverage becomes incomplete under `strict_match` | **withdrawn** | Update affected language rows to status=`withdrawn`, set `withdrawn_at` | Delete item JSON for all public languages of that item; rebuild index, affected monthly archives, archive manifest & stats |
| **published** | Translation row disappears from current eligible set because status is no longer `completed` or fingerprint no longer matches | **withdrawn** | Update `publish_language_status` to status=`withdrawn`, set `withdrawn_at` | Delete item JSON; rebuild index, affected monthly archives, archive manifest & stats |

---

## 3. Withdrawal Synchronization Rules

When an item was previously exported but is no longer eligible, `publish` must synchronize the public layer by removing the file artifacts. This is a downstream cleanup responsibility, not an editorial decision.

The module must:

1. Identify previously `published` language rows that are absent from the current eligible export set.
2. Resolve the `slug` from `publish_record`.
3. Delete `data/publish_export/<language_code>/items/<slug>.json` if it exists.
4. Mark the corresponding `publish_language_status` row as `withdrawn`.
5. Remove the item from `index.json`.
6. Locate the monthly archive file `archive_YYYY_MM.json` (using the calendar month derived strictly from the item's `source_published_at` mapping to `source_item.published_at`) and rewrite it with the withdrawn item removed.
7. If the monthly archive file becomes empty after removal, the runner **must delete** the empty `archive_YYYY_MM.json` file from disk and **must remove** its corresponding entry from the archives index manifest `archives/index.json` (rather than keeping an empty file or registering a 0-item count).
8. Rebuild the archives index manifest `archives/index.json` and stats `stats.json` (see data aggregation source rules in [EXECUTION_POLICY.md](./EXECUTION_POLICY.md)) to reflect the updated metrics.

The module must not delete:

- `approved_content_record`
- `translation_output`
- `publish_record`

Those rows remain part of canonical history and cache preservation.

---

## 4. Rebuild Semantics

During a full rebuild:

- the export directory is treated as disposable
- public files are regenerated from canonical state
- previously withdrawn items remain absent from rebuilt public outputs
- frozen slugs in `publish_record` must be reused rather than regenerated

If the rebuild implementation clears the export directory first, it must still reconstruct the public layer in a way that matches the current database truth and then rebuild all aggregate files.
