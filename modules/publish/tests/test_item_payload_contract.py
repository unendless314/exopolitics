"""
Contract tests for the publish item payload after the translation label
leakage refactor (known_issues/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md,
sections 3.1, 3.5 and the publish row of 7.1).

These tests pin the TARGET behavior:

- item JSON carries ``summary_short`` + semantic ``bullets`` instead of the
  monolithic ``content`` field (DATA_CONTRACT.md section 6.1).
- ``bullet_1``/``bullet_2``/``bullet_3`` are mapped exactly once inside
  publish to ``key_claim``/``evidence_level``/``objective_impact``.
- ``publish_link`` exports ``bullets: null`` (key present, never omitted,
  never an empty object).
- ``validate_item_payload()`` enforces the four EXECUTION_POLICY.md section
  7.2 payload rules before any item JSON is written.
- index.json and monthly archives read ``summary_short`` directly; no
  body-derived summary fallback exists.
- exported string values never contain the "Key Claim" / "Evidence Level" /
  "Objective Impact" presentation labels.

The fixture/schema self-consistency tests are expected to PASS already.
Everything that exercises the target runtime is expected to FAIL until
Phase 3 implements the new export shape; failures must come from the
missing target implementation (e.g. ``t.content`` no longer existing in the
five-column seed, or ``validate_item_payload`` still requiring ``content``),
not from test code errors.
"""

import asyncio
import json
import pathlib
import re
import tempfile
import unittest
from typing import Any, Dict, Iterator, List, Optional, Tuple

import jsonschema

from modules.publish.src import orchestrator
from modules.publish.src.config import (
    PublishConfig,
    PublishSettingsYaml,
    ExecutionPolicy,
    IndexPolicy,
)
from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.src.orchestrator import (
    orchestrate_run,
    validate_item_payload,
    ValidationError,
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = FIXTURES_DIR / "item_payload.schema.json"
VALID_FIXTURES_DIR = FIXTURES_DIR / "valid"
INVALID_FIXTURES_DIR = FIXTURES_DIR / "invalid"

DEFAULT_PUBLISH_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

# Presentation labels that must never leak into exported content values:
# the three English labels plus every zh/ja variant observed in
# known_issues/TRANSLATION_LABEL_LEAKAGE.md section 4.2.
UI_LABELS = (
    "Key Claim",
    "Evidence Level",
    "Objective Impact",
    # zh variants (section 4.2)
    "主要主張",
    "關鍵主張",
    "核心主張",
    "證據層級",
    "證據等級",
    "客觀影響",
    "實際影響",
    # ja variants (section 4.2)
    "主要な主張",
    "主張の要点",
    "証拠の水準",
    "証拠レベル",
    "証拠水準",
    "エビデンスレベル",
    "客観的な影響",
    "客観的影響",
    "目的上の影響",
)

# Each invalid fixture names the payload field the target validator must
# reject. The token pins that rejection happens for the new-contract reason,
# not because of an unrelated legacy check.
INVALID_FIXTURE_ERROR_TOKENS: Dict[str, str] = {
    "missing_summary_short.json": "summary_short",
    "blank_summary_short.json": "summary_short",
    "bullets_empty_object.json": "bullets",
    "bullets_partial_keys.json": "bullets",
    "bullets_extra_key.json": "bullets",
    "bullets_blank_value.json": "bullets",
    "bullets_omitted.json": "bullets",
    "invalid_downstream_action.json": "downstream_action",
    "bullets_null_for_publish_summary.json": "bullets",
    "bullets_object_for_publish_link.json": "bullets",
    "bullets_label_emphasis_prefix.json": "bullets",
    "summary_short_label_zh_prefix.json": "summary_short",
}


def load_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_strings(value: Any) -> Iterator[str]:
    """Yield every string value nested anywhere inside a JSON structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from collect_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from collect_strings(v)


def has_ui_label_prefix(value: str) -> bool:
    """
    True when a content value starts with one of the presentation UI labels
    followed by a colon (ASCII ``:`` or fullwidth ``：``), after stripping
    leading whitespace and optional Markdown emphasis/list markers
    (``**``, ``__``, ``* ``, ``- ``), matching the label-prefix guard in
    refactor plan section 3.4 and the schema fixture's ``not`` pattern.
    """
    return re.match(
        r"^[\s*_-]*(" + "|".join(UI_LABELS) + r")[\s*_]*[:：]",
        value,
    ) is not None


def create_five_column_upstream_tables(db_path: pathlib.Path) -> None:
    """
    Seed the minimal upstream schema in the TARGET five-column shape
    (refactor plan section 3.1): approved_content_record and
    translation_output carry display_title/summary_short/bullet_1..3
    instead of content_body/content.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                source_item_guid TEXT,
                canonical_url TEXT,
                title TEXT NOT NULL,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                ingest_dedup_key TEXT NOT NULL,
                dedup_rule TEXT NOT NULL,
                ingest_status TEXT NOT NULL
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approved_content_record (
                parent_content_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                display_title TEXT NOT NULL,
                summary_short TEXT NOT NULL,
                bullet_1 TEXT,
                bullet_2 TEXT,
                bullet_3 TEXT,
                content_fingerprint TEXT NOT NULL,
                content_language_code TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                author_metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_output (
                translation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_content_id INTEGER NOT NULL,
                source_item_id INTEGER NOT NULL,
                language_code TEXT NOT NULL,
                display_title TEXT,
                summary_short TEXT,
                bullet_1 TEXT,
                bullet_2 TEXT,
                bullet_3 TEXT,
                source_fingerprint TEXT NOT NULL,
                translation_status TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                translated_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (parent_content_id) REFERENCES approved_content_record (parent_content_id) ON DELETE CASCADE,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id),
                UNIQUE (parent_content_id, language_code)
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_decision (
                curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                curate_status TEXT NOT NULL,
                downstream_action TEXT,
                decision_reason TEXT,
                decision_actor TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                curated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


class TestItemPayloadSchemaFixtures(unittest.TestCase):
    """
    Self-consistency of the cross-module contract fixtures. These tests are
    expected to PASS before Phase 3: they validate the schema itself and the
    valid/invalid sample payloads against it with jsonschema.
    """

    def setUp(self) -> None:
        self.schema = load_json(SCHEMA_PATH)

    def test_schema_is_valid_draft202012(self) -> None:
        jsonschema.Draft202012Validator.check_schema(self.schema)

    def test_all_fixtures_are_valid_json(self) -> None:
        fixture_files = sorted(FIXTURES_DIR.rglob("*.json"))
        self.assertGreater(len(fixture_files), 0)
        for path in fixture_files:
            with self.subTest(fixture=path.name):
                load_json(path)

    def test_valid_fixtures_conform_to_schema(self) -> None:
        validator = jsonschema.Draft202012Validator(self.schema)
        valid_files = sorted(VALID_FIXTURES_DIR.glob("*.json"))
        self.assertEqual(
            {"item_publish_summary.json", "item_publish_link.json"},
            {p.name for p in valid_files},
        )
        for path in valid_files:
            with self.subTest(fixture=path.name):
                errors = list(validator.iter_errors(load_json(path)))
                self.assertEqual([], errors)

    def test_invalid_fixtures_do_not_conform_to_schema(self) -> None:
        validator = jsonschema.Draft202012Validator(self.schema)
        invalid_files = sorted(INVALID_FIXTURES_DIR.glob("*.json"))
        self.assertEqual(
            set(INVALID_FIXTURE_ERROR_TOKENS.keys()),
            {p.name for p in invalid_files},
        )
        for path in invalid_files:
            with self.subTest(fixture=path.name):
                self.assertFalse(validator.is_valid(load_json(path)))


class TestValidateItemPayloadRules(unittest.TestCase):
    """
    Target behavior of orchestrator.validate_item_payload() per
    EXECUTION_POLICY.md section 7.2. Expected to FAIL before Phase 3:
    the current implementation still requires the legacy ``content`` field.
    """

    def test_valid_payloads_are_accepted(self) -> None:
        for path in sorted(VALID_FIXTURES_DIR.glob("*.json")):
            with self.subTest(fixture=path.name):
                # Must not raise. The valid fixtures deliberately contain no
                # legacy ``content`` key: the target validator must not
                # require it.
                validate_item_payload(load_json(path))

    def test_invalid_payloads_are_rejected_with_field_context(self) -> None:
        for fixture_name, error_token in INVALID_FIXTURE_ERROR_TOKENS.items():
            payload = load_json(INVALID_FIXTURES_DIR / fixture_name)
            with self.subTest(fixture=fixture_name):
                with self.assertRaises(ValidationError) as ctx:
                    validate_item_payload(payload)
                self.assertIn(error_token, str(ctx.exception))


class PublishContractTestBase(unittest.TestCase):
    """Shared five-column seed harness for the end-to-end export tests."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        create_five_column_upstream_tables(self.db_path)
        run_migrations(self.db_path, DEFAULT_PUBLISH_MIGRATIONS)

        self.settings = PublishSettingsYaml(
            target_languages={"zh": "Traditional Chinese", "en": "English"},
            coverage_policy="strict_match",
            execution_policy=ExecutionPolicy(default_export_dir=str(self.export_dir), batch_size=10),
            index_policy=IndexPolicy(latest_limit=5, archive_granularity="month"),
        )
        self.config = PublishConfig(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_item(
        self,
        item_id: int,
        title: str,
        published_at: str,
        *,
        downstream_action: str = "publish_summary",
        zh_summary: Optional[str] = None,
        en_summary: Optional[str] = None,
        zh_bullets: Optional[Tuple[str, str, str]] = (
            "第一條結構化重點。",
            "第二條結構化重點。",
            "第三條結構化重點。",
        ),
        en_bullets: Optional[Tuple[str, str, str]] = (
            "First structured bullet.",
            "Second structured bullet.",
            "Third structured bullet.",
        ),
        curate_status: str = "approved",
        translation_status_zh: str = "completed",
        translation_status_en: str = "completed",
        content_fingerprint: str = "fp_123",
        trans_fingerprint_zh: str = "fp_123",
        trans_fingerprint_en: str = "fp_123",
        author_metadata: str = '{"source_module": "edit", "writer_type": "human", "editor": "john_doe"}',
    ) -> None:
        zh_summary = zh_summary if zh_summary is not None else f"ZH 摘要 {title}。"
        en_summary = en_summary if en_summary is not None else f"EN summary for {title}."

        # Five-column target semantics: a failed translation row preserves
        # NULL content fields (plan section 3.1).
        zh_fields: Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]
        en_fields: Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]
        zh_fields = (
            (zh_summary,) + tuple(zh_bullets)  # type: ignore[operator]
            if translation_status_zh == "completed" and zh_bullets is not None
            else (zh_summary, None, None, None)
            if translation_status_zh == "completed"
            else (None, None, None, None)
        )
        en_fields = (
            (en_summary,) + tuple(en_bullets)  # type: ignore[operator]
            if translation_status_en == "completed" and en_bullets is not None
            else (en_summary, None, None, None)
            if translation_status_en == "completed"
            else (None, None, None, None)
        )

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO source_item (source_item_id, source_id, title, canonical_url, published_at, fetched_at, ingest_dedup_key, dedup_rule, ingest_status)
                VALUES (?, 1, ?, ?, ?, '2026-07-20T10:00:00Z', ?, 'guid', 'ingested')
            """, (item_id, title, f"https://example.com/{item_id}", published_at, f"key_{item_id}"))

            cursor.execute("""
                INSERT OR REPLACE INTO approved_content_record (
                    parent_content_id, source_item_id, display_title, summary_short,
                    bullet_1, bullet_2, bullet_3,
                    content_fingerprint, content_language_code, approved_at,
                    author_metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'zh', '2026-07-20T12:00:00Z', ?, '2026-07-20T12:00:00Z', '2026-07-20T12:00:00Z')
            """, (
                item_id * 10,
                item_id,
                title,
                zh_summary,
                zh_bullets[0] if zh_bullets else None,
                zh_bullets[1] if zh_bullets else None,
                zh_bullets[2] if zh_bullets else None,
                content_fingerprint,
                author_metadata,
            ))

            cursor.execute("""
                INSERT OR REPLACE INTO curation_decision (source_item_id, curate_status, downstream_action, decision_reason, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at)
                VALUES (?, ?, ?, 'Approved', 'operator', 'curator', 'v1', '2026-07-20T12:00:00Z', '2026-07-20T12:00:00Z', '2026-07-20T12:00:00Z')
            """, (item_id, curate_status, downstream_action))

            cursor.execute("""
                INSERT OR REPLACE INTO translation_output (
                    translation_output_id, parent_content_id, source_item_id, language_code,
                    display_title, summary_short, bullet_1, bullet_2, bullet_3,
                    source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
                )
                VALUES (?, ?, ?, 'zh', ?, ?, ?, ?, ?, ?, ?, 'translator', 'v2', '2026-07-20T12:00:00Z', '2026-07-20T12:00:00Z')
            """, (
                item_id * 100,
                item_id * 10,
                item_id,
                title,
                zh_fields[0],
                zh_fields[1],
                zh_fields[2],
                zh_fields[3],
                trans_fingerprint_zh,
                translation_status_zh,
            ))

            cursor.execute("""
                INSERT OR REPLACE INTO translation_output (
                    translation_output_id, parent_content_id, source_item_id, language_code,
                    display_title, summary_short, bullet_1, bullet_2, bullet_3,
                    source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
                )
                VALUES (?, ?, ?, 'en', ?, ?, ?, ?, ?, ?, ?, 'translator', 'v2', '2026-07-20T12:00:00Z', '2026-07-20T12:00:00Z')
            """, (
                item_id * 100 + 1,
                item_id * 10,
                item_id,
                f"EN {title}",
                en_fields[0],
                en_fields[1],
                en_fields[2],
                en_fields[3],
                trans_fingerprint_en,
                translation_status_en,
            ))

            conn.commit()
        finally:
            conn.close()

    def run_publish(self, rebuild: bool = False) -> Dict[str, Any]:
        return asyncio.run(orchestrate_run(self.config, self.db_path, self.export_dir, rebuild=rebuild))

    def read_item_json(self, lang: str, slug: str) -> Dict[str, Any]:
        path = self.export_dir / lang / "items" / f"{slug}.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


class TestStructuredContentExport(PublishContractTestBase):
    """
    End-to-end target export shape (plan section 3.5, verification matrix
    7.1 publish row). Expected to FAIL before Phase 3: the current runtime
    still reads ``t.content`` and emits a monolithic ``content`` key.
    """

    def test_semantic_mapping_publish_summary(self) -> None:
        """bullet_1/2/3 map exactly once to key_claim/evidence_level/objective_impact."""
        zh_bullets = ("主張內容甲。", "證據內容乙。", "影響內容丙。")
        en_bullets = ("Claim alpha.", "Evidence beta.", "Impact gamma.")
        self.seed_item(1, "Mapping Item", "2026-07-15T10:00:00Z", zh_bullets=zh_bullets, en_bullets=en_bullets)
        summary = self.run_publish()
        self.assertEqual(summary["published_count"], 2)

        zh_item = self.read_item_json("zh", "en-mapping-item")
        self.assertEqual(
            {
                "key_claim": zh_bullets[0],
                "evidence_level": zh_bullets[1],
                "objective_impact": zh_bullets[2],
            },
            zh_item["bullets"],
        )
        self.assertEqual(
            {"key_claim", "evidence_level", "objective_impact"},
            set(zh_item["bullets"].keys()),
        )

        en_item = self.read_item_json("en", "en-mapping-item")
        self.assertEqual(
            {
                "key_claim": en_bullets[0],
                "evidence_level": en_bullets[1],
                "objective_impact": en_bullets[2],
            },
            en_item["bullets"],
        )

        # The monolithic content field must be gone from the export.
        self.assertNotIn("content", zh_item)
        self.assertNotIn("content", en_item)

    def test_publish_link_bullets_null(self) -> None:
        """publish_link exports bullets as JSON null: key present, not omitted, not an empty object."""
        self.seed_item(
            2,
            "Link Item",
            "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            zh_bullets=None,
            en_bullets=None,
        )
        summary = self.run_publish()
        self.assertEqual(summary["published_count"], 2)

        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                item = self.read_item_json(lang, "en-link-item")
                self.assertIn("bullets", item)
                self.assertIsNone(item["bullets"])

    def test_summary_short_passthrough_to_index_and_archive(self) -> None:
        """index.json and monthly archives carry translation_output.summary_short verbatim."""
        zh_summary = "獨特摘要七三四,不得被反推改寫。"
        en_summary = "Distinctive passthrough summary seven three four."
        self.seed_item(
            3,
            "Passthrough Item",
            "2026-07-15T10:00:00Z",
            zh_summary=zh_summary,
            en_summary=en_summary,
        )
        self.run_publish()

        zh_item = self.read_item_json("zh", "en-passthrough-item")
        self.assertEqual(zh_summary, zh_item["summary_short"])

        with open(self.export_dir / "zh" / "index.json", "r", encoding="utf-8") as f:
            zh_index = json.load(f)
        index_entry = next(e for e in zh_index if e["slug"] == "en-passthrough-item")
        self.assertEqual(zh_summary, index_entry["summary_short"])

        archive_path = self.export_dir / "zh" / "archives" / "archive_2026_07.json"
        with open(archive_path, "r", encoding="utf-8") as f:
            zh_archive = json.load(f)
        archive_entry = next(e for e in zh_archive if e["slug"] == "en-passthrough-item")
        self.assertEqual(zh_summary, archive_entry["summary_short"])

    def test_extract_summary_short_removed(self) -> None:
        """No body-derived summary fallback may remain in the orchestrator."""
        self.assertFalse(hasattr(orchestrator, "extract_summary_short"))

    def test_exported_values_contain_no_ui_labels(self) -> None:
        """No string value anywhere in item JSON or index.json may carry a
        "Key Claim" / "Evidence Level" / "Objective Impact" label prefix."""
        self.seed_item(4, "Label Scan Item", "2026-07-15T10:00:00Z")
        self.run_publish()

        scanned = 0
        targets: List[pathlib.Path] = [
            self.export_dir / lang / "items" / "en-label-scan-item.json" for lang in ("zh", "en")
        ] + [self.export_dir / lang / "index.json" for lang in ("zh", "en")]
        for path in targets:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for s in collect_strings(data):
                scanned += 1
                self.assertFalse(has_ui_label_prefix(s), f"UI label prefix leaked into {path}: {s!r}")
        # Guard against a vacuous pass: the scan must actually see content.
        self.assertGreater(scanned, 0)


class TestFiveColumnSeedRegressions(PublishContractTestBase):
    """
    Regression behavior that can only be expressed with the five-column
    seed. Plain strict-match/withdraw/rebuild/frozen-slug/author-metadata
    scenarios under the legacy seed stay in test_publish.py and are tracked
    in the Phase 3 disposal list instead of being duplicated here.
    """

    def test_strict_match_blocks_then_publishes_publish_link(self) -> None:
        """strict-match coverage applies to publish_link items; once complete,
        the exported link item carries bullets: null in both languages."""
        self.seed_item(
            41,
            "Strict Link Item",
            "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            zh_bullets=None,
            en_bullets=None,
            translation_status_en="failed",
        )
        self.run_publish()

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(41))
        conn.close()

        conn = get_connection(self.db_path)
        conn.execute("""
            UPDATE translation_output
            SET translation_status = 'completed', summary_short = 'Recovered EN summary.'
            WHERE source_item_id = 41 AND language_code = 'en'
        """)
        conn.commit()
        conn.close()

        summary = self.run_publish()
        self.assertEqual(summary["published_count"], 2)
        en_item = self.read_item_json("en", "en-strict-link-item")
        self.assertIn("bullets", en_item)
        self.assertIsNone(en_item["bullets"])

    def test_frozen_slug_and_bullet_update_propagation(self) -> None:
        """Slug stays frozen while a fingerprint change re-exports updated
        structured bullets (only expressible with five-column rows)."""
        self.seed_item(42, "Frozen Slug Item", "2026-07-15T10:00:00Z")
        self.run_publish()

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        first_slug = repo.get_publish_record_by_source_item_id(42)["slug"]
        conn.close()

        conn = get_connection(self.db_path)
        conn.execute("UPDATE approved_content_record SET content_fingerprint = 'fp_789' WHERE source_item_id = 42")
        conn.execute("""
            UPDATE translation_output
            SET source_fingerprint = 'fp_789', display_title = 'EN Retitled', bullet_1 = '更新後的第一條。'
            WHERE source_item_id = 42 AND language_code = 'zh'
        """)
        conn.execute("""
            UPDATE translation_output
            SET source_fingerprint = 'fp_789', display_title = 'EN Retitled'
            WHERE source_item_id = 42 AND language_code = 'en'
        """)
        conn.commit()
        conn.close()

        summary = self.run_publish()
        self.assertEqual(summary["published_count"], 2)

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertEqual(first_slug, repo.get_publish_record_by_source_item_id(42)["slug"])
        conn.close()

        zh_item = self.read_item_json("zh", first_slug)
        self.assertEqual("更新後的第一條。", zh_item["bullets"]["key_claim"])

    def test_author_metadata_rule_independent_of_bullet_shape(self) -> None:
        """The human/hybrid editor rule still binds publish_link payloads
        (bullets: null), and machine writer_type exports the AI note."""
        self.seed_item(
            43,
            "Machine Link Item",
            "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            zh_bullets=None,
            en_bullets=None,
            author_metadata='{"source_module": "curate", "writer_type": "machine"}',
        )
        summary = self.run_publish()
        self.assertEqual(summary["published_count"], 2)
        en_item = self.read_item_json("en", "en-machine-link-item")
        self.assertEqual("This item is AI-generated.", en_item["disclosure_note"])

        self.seed_item(
            44,
            "Hybrid Link Item",
            "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            zh_bullets=None,
            en_bullets=None,
            author_metadata='{"source_module": "edit", "writer_type": "hybrid"}',
        )
        with self.assertRaises(ValidationError) as ctx:
            self.run_publish()
        self.assertIn("editor", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
