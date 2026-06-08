# Source Config Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-08

---

## 1. Purpose

This document defines the implementation-facing YAML configuration contract for the rewritten `ingest` module.

It locks:

- configuration file boundaries
- top-level YAML shape
- required and optional fields
- cross-file references
- validation and merge direction

It does not yet lock:

- the exact loader implementation
- CLI flag syntax
- every future policy knob not yet justified by active needs

---

## 2. Config Design Principles

- source identity, scheduling, fetch behavior, and sanitization behavior must stay separate
- category labels are semantic only and must not silently control fetch cadence
- shared definitions should do most of the work; per-source overrides should stay small
- configuration should express stable policy, not ad hoc code behavior
- validation must fail fast on broken references or ambiguous settings

---

## 3. Active File Set

The active `ingest` config set should be split into at least these files under `modules/ingest/config/`:

1. `sources.yaml`
2. `categories.yaml`
3. `retention_policy.yaml`

Optional future files may be added later if a concern becomes large enough to justify separate ownership.

Current direction:

- keep source definitions, schedule classes, and sanitization profiles in `sources.yaml`
- keep semantic category definitions in `categories.yaml`
- keep cleanup and raw-retention policy in `retention_policy.yaml`

This split matches the current module boundaries more cleanly than pushing cleanup policy into per-source configuration.

---

## 4. File Contracts

### 4.1 `sources.yaml`

Purpose:

- define source records
- define schedule classes referenced by sources
- define shared sanitization profiles referenced by sources

Required top-level keys:

- `schema_version`
- `schedule_classes`
- `sanitization_profiles`
- `sources`

Recommended shape:

```yaml
schema_version: 1

schedule_classes:
  hourly:
    target_interval_minutes: 60
    description: High-signal sources fetched every hour.
  daily:
    target_interval_minutes: 1440
    description: Default cadence for most sources.

sanitization_profiles:
  default_html_article:
    input_preference:
      - summary
      - content
    decode_entities: true
    content_selectors: []
    remove_selectors:
      - script
      - style
      - nav
      - footer
    normalize_whitespace: true
    collapse_blank_lines: true
    max_length: 12000

sources:
  - id: 71
    title: Example Source
    xml_url: https://example.com/feed.xml
    html_url: https://example.com/
    category_id: 1
    enabled: true
    fetch_group: 3
    schedule_class: daily
    request_timeout_seconds: 20
    sanitization_profile: default_html_article
    sanitization_overrides:
      content_selectors:
        - article
        - .post-body
      remove_selectors:
        - .share-buttons
        - .related-posts
      max_length: 10000
    notes: Stable source with minor HTML boilerplate.
```

### 4.2 `categories.yaml`

Purpose:

- define semantic category labels referenced by `category_id`

Required top-level keys:

- `schema_version`
- `categories`

Optional top-level keys:

- `category_policy`

Recommended shape:

```yaml
schema_version: 1

category_policy:
  purpose: semantic_label_only
  scheduling_decoupled: true

categories:
  1:
    name: Government Policy & Official Disclosure
    slug: gov-disclosure
    enabled: true
  2:
    name: Civilian Investigation & Databases
    slug: civilian-investigation
    enabled: true
```

### 4.3 `retention_policy.yaml`

Purpose:

- define raw-retention and cleanup policy used by ingest cleanup operations

Required top-level keys:

- `schema_version`
- `raw_retention`

Recommended shape:

```yaml
schema_version: 1

raw_retention:
  default_days: 14
  delete_batch_size: 500
  dry_run: false
  audit_log: true
  exception_classes:
    - investigation
    - operator_frozen
```

Important rule:

- retention policy belongs to module operations, not to individual source records

---

## 5. `sources.yaml` Detailed Schema

### 5.1 Top-Level Key: `schema_version`

- required
- integer
- must match the loader-supported contract version

### 5.2 Top-Level Key: `schedule_classes`

- required
- mapping keyed by schedule class name
- must contain at least one class

Each schedule class value must contain:

- `target_interval_minutes`: required positive integer
- `description`: optional string

Validation rules:

- class names must be unique
- `target_interval_minutes` must be greater than zero
- source records may reference only declared class names

### 5.3 Top-Level Key: `sanitization_profiles`

- required
- mapping keyed by profile name
- must contain at least one profile

Each profile may contain:

- `input_preference`: optional ordered list of allowed raw field names
- `decode_entities`: optional boolean
- `content_selectors`: optional list of strings
- `remove_selectors`: optional list of strings
- `normalize_whitespace`: optional boolean
- `collapse_blank_lines`: optional boolean
- `max_length`: optional positive integer

Validation rules:

- profile names must be unique
- list values must contain strings only
- `max_length` must be positive when provided
- sources may reference only declared profile names

Selector default behavior:

- if `content_selectors` is empty or omitted, the parser must read the entire raw text of the preferred input field by default
- if `content_selectors` is provided and non-empty, the parser must extract text only from elements matching the specified selectors
- if `remove_selectors` is empty or omitted, no element-level removal is performed

Input preference direction:

- allowed values should be restricted to feed text sources explicitly supported by parser logic
- current recommended values are `summary`, `content`, and `title`
- `title` may be used only as supplemental context, not as a silent replacement for missing body text

### 5.4 Top-Level Key: `sources`

- required
- non-empty list of source records

Each source record must contain:

- `id`: required integer
- `title`: required non-empty string
- `xml_url`: required absolute URL string
- `category_id`: required integer reference to `categories.yaml`
- `enabled`: required boolean
- `fetch_group`: required positive integer
- `schedule_class`: required string reference to `schedule_classes`
- `sanitization_profile`: required string reference to `sanitization_profiles`

Each source record may contain:

- `html_url`: optional absolute URL string
- `notes`: optional string
- `request_headers`: optional mapping of string keys to string values
- `request_timeout_seconds`: optional positive integer
- `sanitization_overrides`: optional mapping using the same field names as the referenced sanitization profile

Validation rules:

- `id` values must be unique
- `xml_url` values must be absolute URLs
- `html_url` must be absolute when present and non-empty
- blank strings should be treated as invalid for required string fields
- `category_id` must exist in `categories.yaml`
- `schedule_class` must exist in `schedule_classes`
- `sanitization_profile` must exist in `sanitization_profiles`
- `fetch_group` must be greater than zero
- `request_timeout_seconds` must be greater than zero when present
- `request_headers` keys and values must be strings
- `sanitization_overrides` must use known sanitization field names only

Warning-level checks:

- duplicate `xml_url`
- duplicate non-empty `html_url`
- missing `html_url`
- unusually large selector override lists
- titles that look suspiciously empty after trimming

---

## 6. `sanitization_overrides` Merge Rules

Merge behavior must be deterministic and testable.

Default merge direction:

- scalar fields replace the shared profile value
- boolean fields replace the shared profile value
- list fields replace the shared profile value unless the implementation later introduces an explicit append-style field

Important rule:

- do not silently combine selector lists by guesswork

Reason:

- replacement is easier to reason about and avoids duplicated or contradictory selectors

If future evidence shows a need for append semantics, that behavior should be introduced as an explicit new field or rule.

---

## 7. Cross-File Reference Rules

- every `sources[].category_id` must resolve to `categories.yaml`
- every `sources[].schedule_class` must resolve within `sources.yaml.schedule_classes`
- every `sources[].sanitization_profile` must resolve within `sources.yaml.sanitization_profiles`
- retention policy must not be referenced per source unless a later contract explicitly adds that feature

Cross-file load order direction:

1. load and validate `categories.yaml`
2. load and validate `sources.yaml`
3. resolve cross-file references
4. load and validate `retention_policy.yaml`

The exact implementation order may vary, but validation must produce clear reference errors.

---

## 8. Relationship To Storage Schema

The config contract must map cleanly into ingest storage and execution behavior:

- `sources[].id` maps to `source_state.source_id`, `source_item.source_id`, and `fetch_attempt.source_id`
- `sources[].schedule_class` affects due-source resolution, not semantic categorization
- `sources[].fetch_group` affects execution grouping, not category meaning
- `sources[].sanitization_profile` and `sanitization_overrides` affect how `source_item_text` is produced
- `retention_policy.yaml` affects creation and cleanup policy for `source_item_raw`

Important rule:

- config fields must not imply hidden storage semantics beyond what module docs already define

---

## 9. Failure And Validation Expectations

Validation should fail before any network or database work when:

- required files are missing
- required top-level keys are missing
- source records fail type or reference checks
- profile overrides use unknown keys
- schema versions are unsupported

Validation output should be specific enough to identify:

- filename
- source `id` when applicable
- offending field name
- why the value is invalid

---

## 10. Decisions Locked By This Rewrite

- active config is split across `sources.yaml`, `categories.yaml`, and `retention_policy.yaml`
- `category_id`, `fetch_group`, `schedule_class`, and `sanitization_profile` are independent axes
- shared sanitization profiles are the default; per-source overrides stay limited
- retention policy is module-level config, not per-source fetch config
- config validation must resolve cross-file references before runtime execution
