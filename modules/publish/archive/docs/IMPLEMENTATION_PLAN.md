# Publish Implementation Plan

**Document version:** v1.0  
**Updated:** 2026-06-16  
**Status:** Active planning & design

---

## 1. Implementation Steps

The development of the `publish` module is structured into the following sequence of epics:

### Epic 1: Database Migration & Schema
- Create `modules/publish/src/migrations/v002_initial_publish_tables.sql`.
- Write Python SQLite migration script runner in `modules/publish/src/database.py` to create the `publish_record` and `publish_language_status` tables.
- Verify migrations run correctly and tables are discoverable in `data/canonical.db`.

### Epic 2: Core Exporter Logic
- Implement `modules/publish/src/orchestrator.py` to query approved curation decisions with completed translations from the database.
- Implement slugification logic (convert the English `display_title` to lower-case, remove non-alphanumeric chars, and replace whitespace with dashes).
- Handle unique slug collisions by checking existing slugs in `publish_record` and appending an incremental suffix (e.g., `-1`, `-2`) if a duplicate is found.
- Format static JSON output according to [DATA_CONTRACT.md](file:///C:/Users/user/Documents/derived-work/modules/publish/docs/DATA_CONTRACT.md).
- Write exported items to `data/publish_export/<language_code>/items/<slug>.json` for each available language.

### Epic 3: Index & RSS Feed Generation
- Aggregate all active published records to generate `data/publish_export/<language_code>/index.json` sorted by `published_at DESC` for each active language.
- Generate RSS 2.0 XML in `data/publish_export/<language_code>/feed.xml` for each active language.
- Export basic stats to `data/publish_export/stats.json`.

### Epic 4: CLI Interface & Commands
- Implement `modules/publish/src/cli.py` exposing:
  - `migrate`: Runs the schema migrations.
  - `run`: Scans for new approved curation decisions with completed translations, exports them incrementally across all languages, and updates indexes/RSS.
  - `rebuild`: Clears the export directory, marks all records for full export, and rebuilds everything from history.
  - `status`: Queries and prints publication statistics.
- Wire up entry point in `modules/publish/src/__init__.py`.

---

## 2. Testing & Verification

### Unit Tests
Create unit tests under `modules/publish/tests/` to verify:
1. **Slugification**: Non-English characters, punctuation removal, whitespace replacement, and duplicate resolution.
2. **Database Queries**: Correct loading of approved/unpublished rows and exclusion of withdrawn/failed items.
3. **JSON Structure**: Ensuring output properties match the schema contract.
4. **Idempotency**: Running `cli run` repeatedly should not produce redundant file updates or duplicate SQLite records.

### Manual Verification
Run the CLI on the existing SQLite database containing curation outputs:
- Run `python -m modules.publish.src.cli migrate` to check schema creation.
- Run `python -m modules.publish.src.cli run` and inspect `data/publish_export/` contents.
- Verify the individual items and `index.json` contain the expected content.
