# Publish Module

**Document version:** v2.0  
**Updated:** 2026-06-24  
**Status:** Active rewrite draft

---

## 1. Module Positioning

`publish` is the downstream export module in the active pipeline:

`ingest -> classify -> curate -> edit (when needed) -> translate -> publish -> site`

The module reads canonical downstream-ready content from the database, materializes public export artifacts, and keeps a publish-layer projection that records stable slugs and per-language export state.

In the current architecture:

- upstream editorial approval remains owned by `curate`
- multilingual content generation remains owned by `translate`
- `publish` owns only export eligibility evaluation, slug persistence, public file emission, and cleanup synchronization
- `site` must consume publish outputs only and must not read canonical operational tables directly

### 1.1 Current Upstream Handoff

The effective handoff for `publish` is:

- `approved_content_record` as the canonical approved mother-draft anchor
- `translation_output` as the per-language translated content store
- `curation_decision` as the source of truth for whether the item is still actively approved for public exposure

This means `publish` must not treat translation completion alone as publication eligibility. A translated item is exportable only when the upstream editorial state is still actively approved.

### 1.2 Boundary Rules

- `publish` does not decide approval, rejection, rewrite, or withdrawal
- `publish` does not invoke LLMs or regenerate translated content
- `publish` does not render pages, compile frontend assets, or own routing UI
- `publish` may delete or recreate static export files as a downstream synchronization effect, but it must not mutate upstream editorial meaning

---

## 2. Key Responsibilities

1. Select export-eligible translated records whose upstream curation status remains active.
2. Generate and persist a stable slug on first publication.
3. Emit per-language public artifacts under the export directory.
4. Rebuild language indexes, feeds, and global stats from canonical publish-layer state.
5. Detect upstream withdrawals or eligibility loss and remove corresponding public files.
6. Preserve attribution, disclosure, and source provenance fields in exported payloads.
7. Keep the export layer rebuildable without turning publish-layer tables into the editorial source of truth.

---

## 3. Current Publishing Model

### 3.1 Publication Unit

The publish unit is one `source_item_id` with:

- one shared slug across all languages
- zero or more language exports
- one upstream approval state
- one canonical source provenance chain

### 3.2 Active Eligibility Rule

An item is eligible for public export only if all of the following are true:

- a matching `approved_content_record` row exists
- a matching `curation_decision` row exists with `curate_status = 'approved'`
- a matching `translation_output` row exists for the language with `translation_status = 'completed'`
- the translation row still matches the current mother-draft version through the `source_fingerprint` copied from `approved_content_record.content_fingerprint`

### 3.3 Language Coverage Policy

The module should support a configurable language coverage policy.

For the current MVP direction, the default policy is `strict_match`:

- an article is exported only when every configured public language has a completed translation
- if any required language is missing, stale, or failed, the article is not considered fully publishable
- withdrawn items remain preserved in canonical tables but must be removed from public export artifacts

If the repository later adopts partial-language export, that must be introduced explicitly as a contract update rather than inferred by implementation.

---

## 4. Document Map

- [DATA_CONTRACT.md](./DATA_CONTRACT.md): Publish-layer tables, upstream read dependencies, export file schemas, and slug rules.
- [STATE_TRANSITIONS.md](./STATE_TRANSITIONS.md): Publish states, withdrawal synchronization, and file cleanup behavior.
- [EXECUTION_POLICY.md](./EXECUTION_POLICY.md): Runner sequencing, rebuild rules, transaction boundaries, and idempotency expectations.
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md): Development phases, test focus, and implementation milestones.

Historical planning material has been moved under `modules/publish/archive/docs/` and is no longer the active source of truth.

---

## 5. Minimal CLI Usage

Validate publish configuration and export directory assumptions:

```text
python -m modules.publish.src.cli validate
```

Run publish migrations:

```text
python -m modules.publish.src.cli migrate --db-path data/canonical.db
```

Run an incremental publish synchronization:

```text
python -m modules.publish.src.cli run --db-path data/canonical.db --export-dir data/publish_export
```

Run a full rebuild of public artifacts:

```text
python -m modules.publish.src.cli rebuild --db-path data/canonical.db --export-dir data/publish_export
```

Inspect publish-layer queue and state counts:

```text
python -m modules.publish.src.cli status --db-path data/canonical.db
```
