# Ingest Source Config Schema

**Document version:** v0.1  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Purpose

Define schema expectations for ingest config files and keep semantic, execution, and schedule dimensions separate.

Config ownership:

- `modules/ingest/config/sources.yaml`
- `modules/ingest/config/categories.yaml`

---

## 2. Design Rule: Three Independent Axes

- `category_id`: semantic grouping
- `fetch_group`: execution shard key
- `schedule_class`: fetch cadence tier

These fields must not substitute for each other.

---

## 3. `sources.yaml` Entry Contract

Minimum required fields per source:

- `id` (int, unique, stable)
- `title` (string)
- `xml_url` (absolute URL)
- `category_id` (must exist in categories config)
- `fetch_group` (int within configured shard range)
- `schedule_class` (must exist in allowed class set)
- `enabled` (bool)

Optional fields:

- `html_url` (source homepage)
- `notes`
- future extension fields (timeouts, headers) only when formally introduced

Example:

```yaml
id: 55
title: AARO Official Releases (DOD)
xml_url: https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?max=10&Categories=UAP
html_url: https://www.aaro.mil/
category_id: 1
fetch_group: 7
schedule_class: hourly
enabled: true
```

---

## 4. `categories.yaml` Contract

Minimum required fields per category:

- `id` (int, unique)
- `slug` (stable key)
- `name`
- `enabled` (bool)

`category_id` in sources must reference an existing enabled category unless explicitly allowed by migration tooling.

---

## 5. Validation Rules (Fail Fast)

Validation should fail execution when any of the following occurs:

- duplicate source `id`
- malformed or non-absolute `xml_url`
- missing `category_id` reference
- out-of-range `fetch_group`
- unknown `schedule_class`
- invalid type for `enabled`

Validation should warn (not fail) for:

- duplicate `xml_url` across multiple source IDs
- missing `html_url`
- suspiciously empty titles

---

## 6. Change Management

When changing config schema:

1. update this document
2. add/adjust validator tests under `modules/ingest/tests/`
3. add migration notes for existing config files
4. mention downstream impact in PR description
