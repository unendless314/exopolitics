# Ingest Dedup Policy

**Document version:** v0.1  
**Updated:** 2026-05-30  
**Status:** Draft

---

## 1. Purpose

Define the MVP dedup policy for `ingest` so repeated polls and overlapping feeds do not create duplicate logical `source_item` records.

This document defines the logical matching order and conflict handling direction.  
It does not freeze final database indexes or implementation-specific SQL.

---

## 2. Design Goals

- keep ingest inserts idempotent across repeated runs
- avoid obvious duplicate `source_item` records
- prefer deterministic rules over fuzzy matching
- keep policy simple enough for MVP troubleshooting
- leave room for later refinement with real feed samples

---

## 3. MVP Dedup Scope

MVP dedup applies at ingest time when deciding whether a normalized item should create a new `source_item` record.

It should protect against:

- unchanged feed entries reappearing on later polls
- the same entry appearing with stable GUID on repeated runs
- the same entry appearing with no GUID but stable canonical URL
- the same source emitting slightly inconsistent raw payloads for the same logical item

MVP does not try to solve:

- semantic near-duplicate detection across different articles
- content similarity matching using body text
- aggressive cross-source duplicate collapse for syndicated coverage

---

## 4. Dedup Precedence

Recommended precedence:

1. trusted feed GUID
2. normalized final canonical URL
3. normalized title plus published timestamp heuristic
4. source-scoped fallback hash

The first stable match found should define the ingest dedup key used for that item.

---

## 5. Rule Details

### 5.1 Trusted Feed GUID

Use feed GUID as the first candidate when:

- the feed provides a non-empty GUID or equivalent item identifier

MVP assumption:

- trust feed GUID by default when present

MVP explicitly does not try to auto-detect unstable GUID behavior.

If a source is later observed to rotate GUIDs for unchanged items, handle it as a source-specific override rather than expanding the default global policy.

This keeps the implementation simple:

- default behavior uses GUID first when present
- exceptional sources may later opt out of GUID-first matching through an explicit source-level override
- MVP does not require this override to be part of the active config schema on day one

### 5.2 Normalized Canonical URL

If GUID is missing or unusable, use normalized canonical URL.

MVP normalization should be conservative:

- trim whitespace
- lowercase scheme and host
- remove URL fragment
- normalize obvious trailing slash variance
- treat empty URL as unavailable rather than inventing one

MVP should avoid aggressive query-string stripping unless later evidence shows a stable need.

### 5.3 Title Plus Published Timestamp Heuristic

If GUID and canonical URL are unavailable, fall back to:

- normalized title
- published timestamp, if present

This heuristic should only be used when both fields exist after normalization.

Title normalization should remain simple:

- trim leading and trailing whitespace
- collapse repeated internal whitespace
- preserve visible wording rather than applying heavy rewriting

Published timestamp should be normalized to a single internal time representation before matching.

### 5.4 Source-Scoped Fallback Hash

If none of the higher-precedence keys are available, use a source-scoped fallback hash built from the best available normalized fields.

Recommended input candidates:

- `source_id`
- normalized title if present
- normalized canonical URL if present
- normalized published timestamp if present
- summary if it is the only remaining stable field

This fallback exists to keep inserts deterministic, not to provide high-confidence semantic identity.

---

## 6. Cross-Source Matching

MVP policy should stay conservative:

- dedup strongly within the same source
- allow URL-based dedup across sources when the final normalized canonical URL is identical
- do not attempt title-only cross-source dedup

Rationale:

- exact URL collisions are easy to explain and audit
- title-only cross-source dedup is much more error-prone for aggregated news coverage

---

## 7. Insert Decision Rules

For each normalized item:

1. compute the highest-precedence available dedup candidate
2. compare against existing dedup markers or `source_item` records
3. if a stable match exists, treat the item as already ingested
4. if no stable match exists, create a new `source_item`

Repeated polls of the same unchanged feed should therefore create zero new logical items.

---

## 8. Conflict Handling

MVP conflict handling should be simple:

- keep the earliest existing `source_item` as the canonical ingest record
- do not overwrite historical source identity with a later duplicate
- record the current fetch attempt outcome normally
- avoid creating a second logical item for the same dedup key

If later runs produce improved raw metadata for a previously seen item, that should be treated as a separate update-policy question, not as a reason to create duplicates.

---

## 9. Auditability Requirements

MVP dedup behavior should remain explainable.

At minimum, stored ingest data should allow operators to determine:

- which dedup key was used
- which precedence rule produced it
- whether the item created a new `source_item` or matched an existing one

This can be implemented through dedicated dedup markers, fields on `source_item`, or equivalent persistence structures.

---

## 10. Non-Goals For MVP

- machine-learning similarity scoring
- automatic detection of unstable GUID behavior
- body-text fingerprinting
- complex canonicalization rules for every publisher

If real feed behavior later forces exceptions, add a small source-level escape hatch from observed evidence rather than pre-optimizing the global policy.
