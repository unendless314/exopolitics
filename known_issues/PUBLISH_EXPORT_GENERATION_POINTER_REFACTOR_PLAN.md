# Publish Export Generation Pointer Refactor — Phase B2 Plan

**Status:** Active planning only — not started and not approved
**Version:** B2 plan v1.0
**Date:** 2026-08-19
**B1 record:** Closed after final review on 2026-08-19. See [resolved B1 working note](resolved/PUBLISH_B1_IMPLEMENTATION_WORKING_NOTE.md).

## Purpose

Phase B2 optimizes physical storage for generation exports. It must preserve the established generation-and-pointer contract while reusing unchanged files from the live generation through hardlinks. This is an implementation optimization, not a change to export contents or reader behavior.

## Current baseline

B1 is closed and validated at **117 publish tests / 585 subtests**. The current publish export contract provides:

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

Extend each generation's `meta.json` hash table from aggregate artifacts to **all emitted artifacts**, including item files. Use these hashes to decide whether a planned artifact is unchanged from the trusted preceding generation.

### B1 metadata transition

Existing B1 generations record hashes only for aggregate artifacts. A missing item hash is not evidence that an item is reusable: B2 must physically write that artifact in the first B2 generation that needs it, then record its hash in the new generation's `meta.json`. B2 must never mutate an older generation to backfill hashes. A no-change `run` still creates no generation and therefore performs no hash backfill; a content-changing build or a forced `rebuild` establishes the expanded hash table naturally.

`meta.json` is builder-owned metadata, not a reusable export artifact. It is written fresh for every new generation after the artifact hash table is known. `current.json` remains the separately and atomically refreshed export-root pointer.

### Reuse eligibility

For each planned artifact:

1. Compare its planned content hash with the matching path in the preceding generation's `meta.json`.
2. Reuse with `os.link()` only when the hashes match, the source is a regular file inside the trusted preceding generation directory, and source and destination are on the same filesystem.
3. Write a new file for changed or new artifacts.
4. If linking fails for any reason (for example, cross-volume placement, filesystem policy, NTFS limitation, or network storage), safely fall back to copying/writing the planned bytes.

Never link a symlink, junction, reparse point, or source outside the trusted preceding generation directory.

### Immutability and rebuild

Generation contents are immutable after creation. A builder must only create new destination files; it must never overwrite, truncate, chmod, or replace a generation file in place, because doing so could mutate a shared inode.

`rebuild` remains the physical-write escape hatch: it must force a full physical rewrite rather than reuse hardlinks, even when hashes match. This supports serializer changes, hash-algorithm upgrades, and repair operations.

### Retention and backups

Retention removes old generation entries by unlinking them. A linked artifact remains available while any retained generation references it. Existing live-generation protection, safe deletion checks, numeric suffix ordering, and no-gap-refill behavior remain mandatory.

Backup and rsync tooling may copy hardlinked files as separate files or otherwise fail to preserve link relationships. Validate actual VPS backup behavior before claiming storage savings; B2 must not assume that backup storage deduplicates hardlinks.

## Acceptance and validation

B2 is acceptable only when all of the following are demonstrated:

- unchanged files in a new generation are correctly hardlinked on Linux and NTFS, while changed files are physically written;
- cross-filesystem and injected link failures fall back to safe copy/write without affecting generation correctness;
- source validation rejects symlinks, junctions, reparse points, and paths outside the trusted prior generation;
- linked files are never modified in place, including across subsequent builds;
- `rebuild` produces a fully physically rewritten generation;
- retention unlinks retired generations without breaking artifacts still referenced by retained generations, including link-count behavior where supported;
- no-change runs, pointer atomicity, lock/snapshot safety, full-generation output, reader TOCTOU retry, live protection, numeric suffix ordering, and no-gap-refill behavior remain covered and unchanged; and
- the publish test suite passes, with B2-specific tests reviewed independently.

## Non-goals

- Changing artifact schemas, pointer fields, reader contracts, API contracts, or canonical database schema.
- Replacing generation/pointer semantics with incremental live-tree mutation.
- Promising backup-side disk savings without environment-specific verification.

## Revision history

- **v1.0 (2026-08-19):** Reorganized the completed A/B1 proposal into the active B2 hardlink-reuse plan. Historical B1 implementation and review detail remains in the resolved B1 working note.