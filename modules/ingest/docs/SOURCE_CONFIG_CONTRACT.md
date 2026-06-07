# Source Config Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines the active configuration contract for `ingest` sources.

It separates four concerns:

- source identity
- fetch scheduling and execution grouping
- fetch policy
- sanitization policy

These concerns must not be silently merged into one overloaded field.

---

## 2. Required Source Fields

Minimum required fields per source:

- `id`
- `title`
- `xml_url`
- `category_id`
- `fetch_group`
- `schedule_class`
- `enabled`
- `sanitization_profile`

Optional fields:

- `html_url`
- `notes`
- `request_headers`
- `request_timeout_seconds`
- `sanitization_overrides`

---

## 3. Independent Axes

### 3.1 Source Identity Axis

- `id`
- `title`
- `xml_url`
- `html_url`
- `category_id`

### 3.2 Execution Axis

- `fetch_group`
- `schedule_class`
- `enabled`

### 3.3 Fetch Policy Axis

- request timeout override when formally supported
- request headers override when formally supported
- future cache or retry policy knobs when formally introduced

### 3.4 Sanitization Axis

- `sanitization_profile`
- `sanitization_overrides`

Important rule:

- `category_id`, `fetch_group`, `schedule_class`, and `sanitization_profile` do different jobs and must not substitute for each other

---

## 4. Sanitization Configuration Model

Default direction:

- most sources should use a shared profile
- only problematic sources should add small overrides
- a source should not require a fully custom parser unless repeated evidence justifies it

Recommended config shape:

```yaml
sources:
  - id: 71
    title: Example Source
    xml_url: https://example.com/feed.xml
    html_url: https://example.com/
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    enabled: true
    sanitization_profile: default_html_article
    sanitization_overrides:
      content_selectors:
        - article
        - .post-body
      remove_selectors:
        - .share-buttons
        - .related-posts
      normalize_whitespace: true
      collapse_blank_lines: true
      max_length: 12000
```

Recommended shared-profile shape:

```yaml
sanitization_profiles:
  default_html_article:
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
```

Important rule:

- `sanitization_overrides` should use the same field names as the referenced shared profile

Merge direction:

- shared profile provides the default sanitization settings
- source overrides may replace or extend those defaults depending on the field definition
- list fields such as selectors must have explicit merge behavior in implementation and tests
- boolean and scalar fields normally override the shared profile value directly

---

## 5. Validation Rules

Validation should fail when any of the following occurs:

- duplicate source `id`
- malformed or non-absolute `xml_url`
- missing `category_id` reference
- invalid `fetch_group`
- unknown `schedule_class`
- invalid `enabled` type
- missing `sanitization_profile`
- unknown `sanitization_profile`
- invalid override structure

Validation may warn for:

- duplicate `xml_url`
- missing `html_url`
- suspiciously empty `title`
- sources whose overrides are unusually large and look like custom parser logic

---

## 6. Design Rules

- source config should express policy, not arbitrary code
- shared profiles should do most of the work
- overrides should stay small and reviewable
- repeated hard-coded exceptions in code should be migrated back into config when stable

---

## 7. Decisions Locked By This Rewrite

- sanitization policy belongs to `ingest` config ownership
- the default direction is shared profiles plus limited source overrides
- source config may influence sanitization behavior without turning every source into a custom implementation
