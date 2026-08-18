# Cross-Module Follow-Up: Curate Handoff Bullet Shape Violations

**Owner:** `curate` module owner
**Filed by:** `translate` module (TRANSLATE_TEST_MAINTAINABILITY_PLAN Phase 1)
**Date:** 2026-08-01
**Status:** Open — awaiting upstream contract enforcement

## Background

`modules/translate/src/approved_content_record.py`
(`assemble_approved_content_records()`) is the final write boundary of the
shared `approved_content_record` handoff table. Per
[TRANSLATE_TEST_MAINTAINABILITY_PLAN.md](./TRANSLATE_TEST_MAINTAINABILITY_PLAN.md)
section 3.1 (decided 2026-07-31), the assembler now applies zero-trust
defensive validation to the upstream curation payload before any write:

- `publish_summary` requires exactly three non-empty, non-whitespace bullets.
- `publish_link` requires exactly three `NULL` bullets.
- Every other combination (one or two NULL bullets, empty/whitespace-only
  bullet values, populated bullets under `publish_link`) is **rejected**.

## Violation Shapes Rejected

For a `curation_decision` row with `curate_status = 'approved'`:

| `downstream_action` | Illegal bullet shape (any one suffices) |
| :--- | :--- |
| `publish_summary` | any of `bullet_1`/`bullet_2`/`bullet_3` is `NULL` |
| `publish_summary` | any bullet is `''` or whitespace-only |
| `publish_link` | any of `bullet_1`/`bullet_2`/`bullet_3` is non-NULL |

## Impact on Affected Source Items

- A rejected item produces **no new handoff row** and never overwrites an
  existing valid handoff row; other legal items in the same assembly run are
  unaffected.
- The item stays rejected on every subsequent assembly run **until the
  upstream data is corrected**; the assembler never repairs payloads
  (no padding, truncation, string conversion, or silent action downgrade).
- Each rejection is reported in the assembly statistics
  (`stats['rejected']` / `stats['rejected_items']` and the
  `translate assemble` CLI summary) with `source_item_id`,
  `downstream_action`, and the violating slot, e.g.:
  `source_item_id=42 action=publish_summary: publish_summary requires three
  non-empty bullets; bullet_2 is NULL`.

## Requested Upstream Contract (for `curate`)

1. Guarantee the all-or-none bullet invariant at approval time: a
   `curation_decision` transitioning to `approved` with
   `downstream_action = 'publish_summary'` must be backed by a
   `curation_output` row whose three bullets are all non-empty,
   non-whitespace strings; with `downstream_action = 'publish_link'` the
   three bullets must all be `NULL`.
2. Treat translate's `rejected` diagnostics as actionable signals: operators
   seeing repeated rejections for a source item should route the item back to
   curation review rather than expecting translate to materialize it.
3. Confirm whether `curate` can enforce this invariant in its own validation
   or DB constraints, so translate's defensive rejection becomes a safety net
   rather than the primary enforcement point.

## References

- [modules/translate/docs/DATA_CONTRACT.md](../modules/translate/docs/DATA_CONTRACT.md)
  sections 1.1 (five-field shape) and 1.5 (handoff materialization rules)
- [TRANSLATE_TEST_MAINTAINABILITY_PLAN.md](./TRANSLATE_TEST_MAINTAINABILITY_PLAN.md)
  section 3.1 and Phase 1
