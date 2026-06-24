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
| **None / Pending** | Item becomes export-eligible and passes coverage policy | **published** | Insert `publish_record` if absent; insert `publish_language_status` with status=`published`, `published_at`, fingerprint | Write item JSON; rebuild index/stats |
| **withdrawn** | Previously withdrawn item becomes eligible again | **published** | Update `publish_language_status` to status=`published`, retain previous `withdrawn_at` value to preserve audit history, refresh fingerprint, update `published_at` | Recreate item JSON; rebuild index/stats |
| **published** | Current export is re-run with unchanged fingerprint | **published** | No semantic change required; row should keep existing fingerprint and timestamps | File content may remain untouched if identical |
| **published** | Mother-draft fingerprint changes and new completed translation becomes available | **published** | Update `source_fingerprint`, `published_at` | Overwrite item JSON; rebuild index/stats |
| **published** | Upstream curation state changes to `withdrawn` | **withdrawn** | Update `publish_language_status` to status=`withdrawn`, set `withdrawn_at`, preserve prior `published_at` | Delete item JSON; rebuild index/stats |
| **published** | Required language coverage becomes incomplete under `strict_match` | **withdrawn** | Update affected language rows to status=`withdrawn`, set `withdrawn_at` | Delete item JSON for all public languages of that item; rebuild index/stats |
| **published** | Translation row disappears from current eligible set because status is no longer `completed` or fingerprint no longer matches | **withdrawn** | Update `publish_language_status` to status=`withdrawn`, set `withdrawn_at` | Delete item JSON; rebuild index/stats |

---

## 3. Withdrawal Synchronization Rules

When an item was previously exported but is no longer eligible, `publish` must synchronize the public layer by removing the file artifacts. This is a downstream cleanup responsibility, not an editorial decision.

The module must:

1. Identify previously `published` language rows that are absent from the current eligible export set.
2. Resolve the `slug` from `publish_record`.
3. Delete `data/publish_export/<language_code>/items/<slug>.json` if it exists.
4. Mark the corresponding `publish_language_status` row as `withdrawn`.
5. Rebuild language indexes and stats so withdrawn items no longer appear publicly.

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
