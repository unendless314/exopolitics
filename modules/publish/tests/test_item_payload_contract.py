"""
Contract tests for the publish item payload, locked after the translation
label leakage refactor (known_issues/resolved/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md,
sections 3.1, 3.5 and the publish row of 7.1).

These tests pin the current runtime contract:

- item JSON carries ``summary_short`` + semantic ``bullets`` instead of the
  monolithic ``content`` field (DATA_CONTRACT.md section 6.1).
- ``bullet_1``/``bullet_2``/``bullet_3`` are mapped exactly once inside
  publish to ``key_claim``/``evidence_level``/``objective_impact``.
- ``publish_link`` exports ``bullets: null`` (key present, never omitted,
  never an empty object).
- ``validate_item_payload()`` enforces the EXECUTION_POLICY.md section
  7.2 payload rules before any item JSON is written.
- index.json and monthly archives read ``summary_short`` directly; no
  body-derived summary fallback exists.
- exported string values never contain the "Key Claim" / "Evidence Level" /
  "Objective Impact" presentation labels.

The fixture/schema self-consistency tests validate the schema itself and the
valid/invalid sample payloads against it with jsonschema; the remaining
suites exercise the live runtime against the same contract.
"""

import pathlib
import re
import tempfile
import unittest
from typing import Any, Dict, Iterator, List

import jsonschema

from modules.publish.src import orchestrator
from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.src.orchestrator import (
    validate_item_payload,
    ValidationError,
)
from modules.publish.tests import support

load_json = support.read_json

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = FIXTURES_DIR / "item_payload.schema.json"
VALID_FIXTURES_DIR = FIXTURES_DIR / "valid"
INVALID_FIXTURES_DIR = FIXTURES_DIR / "invalid"

# Presentation labels that must never leak into exported content values:
# the three English labels plus every zh/ja variant observed in
# known_issues/resolved/TRANSLATION_LABEL_LEAKAGE.md section 4.2.
# Kept as a fixed test-local copy (plan section 3.11): the fixtures and this
# tuple intentionally do not derive from the production UI_LABELS tuple, so
# the tests remain an independent check of the runtime and schema lists.
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

# Each invalid fixture names the payload field the validator must reject.
# The token pins that rejection happens for the documented contract reason,
# not because of an unrelated check.
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


class TestItemPayloadSchemaFixtures(unittest.TestCase):
    """
    Self-consistency of the cross-module contract fixtures: the schema is a
    valid draft 2020-12 schema, every fixture parses, valid fixtures conform,
    and invalid fixtures are rejected by the schema.
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
    Runtime behavior of orchestrator.validate_item_payload() per
    EXECUTION_POLICY.md section 7.2: valid fixtures are accepted and every
    invalid fixture is rejected with the offending field named.
    """

    def test_valid_payloads_are_accepted(self) -> None:
        for path in sorted(VALID_FIXTURES_DIR.glob("*.json")):
            with self.subTest(fixture=path.name):
                # Must not raise. The valid fixtures deliberately contain no
                # legacy ``content`` key: the validator must not require it.
                validate_item_payload(load_json(path))

    def test_invalid_payloads_are_rejected_with_field_context(self) -> None:
        for fixture_name, error_token in INVALID_FIXTURE_ERROR_TOKENS.items():
            payload = load_json(INVALID_FIXTURES_DIR / fixture_name)
            with self.subTest(fixture=fixture_name):
                with self.assertRaises(ValidationError) as ctx:
                    validate_item_payload(payload)
                self.assertIn(error_token, str(ctx.exception))


class PublishContractTestBase(unittest.TestCase):
    """Shared seed harness for the end-to-end export tests."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_publish(self, rebuild: bool = False) -> Dict[str, Any]:
        return support.run_publish(self.config, self.db_path, self.export_dir, rebuild=rebuild)

    def read_item_json(self, lang: str, slug: str) -> Dict[str, Any]:
        return support.read_item(self.export_dir, lang, slug)


class TestStructuredContentExport(PublishContractTestBase):
    """
    End-to-end export shape (refactor plan section 3.5, verification matrix
    7.1 publish row): the runtime emits the structured payload
    (``summary_short`` + semantic ``bullets``) and no legacy ``content`` key.
    """

    def test_semantic_mapping_publish_summary(self) -> None:
        """bullet_1/2/3 map exactly once to key_claim/evidence_level/objective_impact."""
        zh_bullets = ("主張內容甲。", "證據內容乙。", "影響內容丙。")
        en_bullets = ("Claim alpha.", "Evidence beta.", "Impact gamma.")
        support.seed_item(
            self.db_path, 1, "Mapping Item", "2026-07-15T10:00:00Z",
            translations={"zh": {"bullets": zh_bullets}, "en": {"bullets": en_bullets}},
        )
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
        support.seed_item(
            self.db_path, 2, "Link Item", "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
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
        support.seed_item(
            self.db_path, 3, "Passthrough Item", "2026-07-15T10:00:00Z",
            translations={"zh": {"summary": zh_summary}, "en": {"summary": en_summary}},
        )
        self.run_publish()

        zh_item = self.read_item_json("zh", "en-passthrough-item")
        self.assertEqual(zh_summary, zh_item["summary_short"])

        zh_index = support.read_index(self.export_dir, "zh")
        index_entry = next(e for e in zh_index if e["slug"] == "en-passthrough-item")
        self.assertEqual(zh_summary, index_entry["summary_short"])

        zh_archive = support.read_archive(self.export_dir, "zh", "2026-07")
        archive_entry = next(e for e in zh_archive if e["slug"] == "en-passthrough-item")
        self.assertEqual(zh_summary, archive_entry["summary_short"])

    def test_extract_summary_short_removed(self) -> None:
        """No body-derived summary fallback may remain in the orchestrator."""
        self.assertFalse(hasattr(orchestrator, "extract_summary_short"))

    def test_exported_values_contain_no_ui_labels(self) -> None:
        """No string value anywhere in item JSON or index.json may carry a
        "Key Claim" / "Evidence Level" / "Objective Impact" label prefix."""
        support.seed_item(self.db_path, 4, "Label Scan Item", "2026-07-15T10:00:00Z")
        self.run_publish()

        scanned = 0
        targets: List[pathlib.Path] = [
            self.export_dir / lang / "items" / "en-label-scan-item.json" for lang in ("zh", "en")
        ] + [self.export_dir / lang / "index.json" for lang in ("zh", "en")]
        for path in targets:
            data = support.read_json(path)
            for s in collect_strings(data):
                scanned += 1
                self.assertFalse(has_ui_label_prefix(s), f"UI label prefix leaked into {path}: {s!r}")
        # Guard against a vacuous pass: the scan must actually see content.
        self.assertGreater(scanned, 0)


class TestFiveColumnSeedRegressions(PublishContractTestBase):
    """
    Regression behavior that exercises the five-column structured-content
    seed: strict-match recovery for publish_link items, frozen slugs with
    bullet update propagation, and author metadata rules independent of the
    bullet shape. Plain strict-match/withdraw/rebuild/frozen-slug scenarios
    under the legacy seed stay in test_publish.py (see TEST_COVERAGE_MAP.md).
    """

    def test_strict_match_blocks_then_publishes_publish_link(self) -> None:
        """strict-match coverage applies to publish_link items; once complete,
        the exported link item carries bullets: null in both languages."""
        support.seed_item(
            self.db_path, 41, "Strict Link Item", "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            translations={"zh": {}, "en": {"status": "failed"}},
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
        support.seed_item(self.db_path, 42, "Frozen Slug Item", "2026-07-15T10:00:00Z")
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
        support.seed_item(
            self.db_path, 43, "Machine Link Item", "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            author_metadata='{"source_module": "curate", "writer_type": "machine"}',
        )
        summary = self.run_publish()
        self.assertEqual(summary["published_count"], 2)
        en_item = self.read_item_json("en", "en-machine-link-item")
        self.assertEqual("This item is AI-generated.", en_item["disclosure_note"])

        support.seed_item(
            self.db_path, 44, "Hybrid Link Item", "2026-07-15T10:00:00Z",
            downstream_action="publish_link",
            author_metadata='{"source_module": "edit", "writer_type": "hybrid"}',
        )
        with self.assertRaises(ValidationError) as ctx:
            self.run_publish()
        self.assertIn("editor", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
