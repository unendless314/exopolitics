import unittest
import pathlib
import tempfile
from modules.ingest.src.config import validate_and_load_config, IngestConfig, SanitizationProfile

def _sources_yaml(sources_block: str) -> str:
    """Composes a minimal valid sources.yaml around the given sources block."""
    return (
        "schema_version: 1\n"
        "schedule_classes:\n"
        "  daily:\n"
        "    target_interval_minutes: 1440\n"
        "sanitization_profiles:\n"
        "  default_html_article:\n"
        "    input_preference: [summary]\n"
        "sources:\n"
        + sources_block
    )

_SOURCE_TEMPLATE = """  - id: {id}
    title: {title}
    xml_url: {xml_url}
    category_id: 1
    fetch_group: {fetch_group}
    schedule_class: {schedule_class}
    sanitization_profile: {sanitization_profile}
    enabled: true
"""

def _source_block(**overrides) -> str:
    values = {
        "id": 101,
        "title": "Test Feed",
        "xml_url": "https://example.com/rss",
        "fetch_group": 3,
        "schedule_class": "daily",
        "sanitization_profile": "default_html_article",
    }
    values.update(overrides)
    return _SOURCE_TEMPLATE.format(**values)

class TestConfigLoader(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = pathlib.Path(self.temp_dir.name)

        # Write dummy categories.yaml
        self.categories_yaml = """
schema_version: 1
categories:
  0:
    name: Disabled Category
    slug: disabled-cat
    enabled: false
  1:
    name: Enabled Category
    slug: enabled-cat
    enabled: true
"""
        with open(self.config_path / "categories.yaml", "w", encoding="utf-8") as f:
            f.write(self.categories_yaml)

        # Write dummy sources.yaml
        self.sources_yaml = """
schema_version: 1
schedule_classes:
  hourly:
    target_interval_minutes: 60
    description: Hourly cadence
  daily:
    target_interval_minutes: 1440
    description: Daily cadence
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
    normalize_whitespace: true
    collapse_blank_lines: true
    max_length: 12000
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    html_url: https://example.com
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(self.sources_yaml)

        # Write dummy retention_policy.yaml
        self.retention_yaml = """
schema_version: 1
raw_retention:
  default_days: 14
  delete_batch_size: 500
  dry_run: false
  audit_log: true
  exception_classes:
    - investigation
"""
        with open(self.config_path / "retention_policy.yaml", "w", encoding="utf-8") as f:
            f.write(self.retention_yaml)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_config_success(self) -> None:
        config, errors, warnings = validate_and_load_config(self.config_path)

        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
        self.assertIsNotNone(config)
        
        # Test Categories
        self.assertEqual(len(config.categories), 2)
        self.assertEqual(config.categories[1].name, "Enabled Category")
        self.assertTrue(config.categories[1].enabled)

        # Test Schedule Classes
        self.assertEqual(config.schedule_classes["hourly"].target_interval_minutes, 60)

        # Test Sources
        self.assertEqual(len(config.sources), 1)
        source = config.sources[0]
        self.assertEqual(source.id, 101)
        self.assertEqual(source.xml_url, "https://example.com/rss")
        self.assertEqual(source.sanitization_profile, "default_html_article")

        # Test Retention Policy
        self.assertEqual(config.raw_retention.default_days, 14)

    def test_validation_errors(self) -> None:
        # 1. Invalid XML URL
        bad_sources_yaml = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources:
  - id: 101
    title: Test Feed
    xml_url: not-a-valid-url
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("xml_url" in err for err in errors))

    def test_validation_reference_errors(self) -> None:
        # 2. Missing category reference
        bad_sources_yaml = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: 999  # Does not exist
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("category_id" in err for err in errors))

    def test_validation_disabled_category_error(self) -> None:
        # Source refers to disabled category 0
        bad_sources_yaml = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources:
  - id: 102
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: 0  # Disabled category
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("disabled" in err.lower() for err in errors))

    def test_merge_sanitization_profile_overrides(self) -> None:
        sources_yaml_with_overrides = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference:
      - summary
      - content
    decode_entities: true
    content_selectors: []
    remove_selectors:
      - script
    normalize_whitespace: true
    collapse_blank_lines: true
    max_length: 12000
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
    sanitization_overrides:
      max_length: 5000
      remove_selectors:
        - script
        - style
        - nav
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(sources_yaml_with_overrides)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertEqual(len(errors), 0)
        self.assertIsNotNone(config)

        source = config.sources[0]
        merged_profile = config.get_merged_sanitization_profile(source)
        self.assertEqual(merged_profile.max_length, 5000)
        self.assertEqual(merged_profile.remove_selectors, ["script", "style", "nav"])
        self.assertTrue(merged_profile.decode_entities)

    def test_unsupported_schema_version(self) -> None:
        bad_sources_yaml = """
schema_version: 2
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("schema_version" in err.lower() or "validation failed" in err.lower() for err in errors))

    def test_empty_config_fields(self) -> None:
        bad_sources_yaml = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources: []
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("sources" in err.lower() or "validation failed" in err.lower() for err in errors))

    def _write_sources(self, sources_block: str) -> None:
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(_sources_yaml(sources_block))

    def test_duplicate_source_id_error(self) -> None:
        block = _source_block(id=101, title="Feed A", xml_url="https://example.com/a.xml")
        block += _source_block(id=101, title="Feed B", xml_url="https://example.com/b.xml")
        self._write_sources(block)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any("Duplicate source ID 101" in err for err in errors), errors)
        self.assertTrue(any("sources.yaml" in err for err in errors), errors)

    def test_unknown_schedule_class_reference_error(self) -> None:
        self._write_sources(_source_block(schedule_class="nightly"))

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any(
            "sources.yaml" in err and "101" in err and "schedule_class" in err and "nightly" in err
            for err in errors
        ), errors)

    def test_unknown_sanitization_profile_reference_error(self) -> None:
        self._write_sources(_source_block(sanitization_profile="no_such_profile"))

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any(
            "sources.yaml" in err and "101" in err and "sanitization_profile" in err and "no_such_profile" in err
            for err in errors
        ), errors)

    def test_invalid_fetch_group_error(self) -> None:
        for bad_value in (0, -3):
            with self.subTest(fetch_group=bad_value):
                self._write_sources(_source_block(fetch_group=bad_value))

                config, errors, warnings = validate_and_load_config(self.config_path)
                self.assertIsNone(config)
                self.assertTrue(any("fetch_group" in err for err in errors), errors)

    def test_invalid_request_timeout_seconds_error(self) -> None:
        for bad_value in (0, -10):
            with self.subTest(request_timeout_seconds=bad_value):
                block = _source_block().rstrip("\n") + f"\n    request_timeout_seconds: {bad_value}\n"
                self._write_sources(block)

                config, errors, warnings = validate_and_load_config(self.config_path)
                self.assertIsNone(config)
                self.assertTrue(any("request_timeout_seconds" in err for err in errors), errors)

    def test_invalid_max_length_error(self) -> None:
        # max_length: 0 in the shared profile definition.
        sources_yaml = (
            "schema_version: 1\n"
            "schedule_classes:\n"
            "  daily:\n"
            "    target_interval_minutes: 1440\n"
            "sanitization_profiles:\n"
            "  default_html_article:\n"
            "    input_preference: [summary]\n"
            "    max_length: 0\n"
            "sources:\n"
            + _source_block()
        )
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(sources_yaml)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any("max_length" in err for err in errors), errors)

        # max_length: 0 in per-source overrides.
        block = _source_block().rstrip("\n") + "\n    sanitization_overrides:\n      max_length: 0\n"
        self._write_sources(block)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any("max_length" in err for err in errors), errors)

    def test_unknown_sanitization_override_key_error(self) -> None:
        block = _source_block().rstrip("\n") + "\n    sanitization_overrides:\n      unknown_knob: true\n"
        self._write_sources(block)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any("unknown_knob" in err for err in errors), errors)

    def test_wrong_type_sanitization_override_error(self) -> None:
        overrides = (
            "max_length: not-an-int",
            "content_selectors: article",
        )
        for override_line in overrides:
            with self.subTest(override=override_line):
                block = _source_block().rstrip("\n") + f"\n    sanitization_overrides:\n      {override_line}\n"
                self._write_sources(block)

                config, errors, warnings = validate_and_load_config(self.config_path)
                self.assertIsNone(config)
                self.assertTrue(any("sanitization_overrides" in err for err in errors), errors)

    def test_missing_config_file_error(self) -> None:
        with tempfile.TemporaryDirectory() as empty_dir:
            config, errors, warnings = validate_and_load_config(pathlib.Path(empty_dir))

        self.assertIsNone(config)
        self.assertEqual(len(errors), 3)
        for name in ("categories.yaml", "sources.yaml", "retention_policy.yaml"):
            self.assertTrue(any(name in err for err in errors), errors)

    def test_empty_yaml_root_error(self) -> None:
        base_files = {
            "categories.yaml": self.categories_yaml,
            "sources.yaml": self.sources_yaml,
            "retention_policy.yaml": self.retention_yaml,
        }
        for target in base_files:
            with self.subTest(file=target):
                for name, content in base_files.items():
                    with open(self.config_path / name, "w", encoding="utf-8") as f:
                        f.write("" if name == target else content)

                config, errors, warnings = validate_and_load_config(self.config_path)
                self.assertIsNone(config)
                self.assertTrue(any(
                    target in err and "Invalid YAML root" in err for err in errors
                ), errors)

    def test_yaml_parse_error_mentions_file(self) -> None:
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write("schema_version: [unclosed\n")

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertIsNone(config)
        self.assertTrue(any("sources.yaml" in err for err in errors), errors)


class TestConfigWarnings(unittest.TestCase):
    """Warning-level checks must not produce errors and must name the source."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = pathlib.Path(self.temp_dir.name)

        with open(self.config_path / "categories.yaml", "w", encoding="utf-8") as f:
            f.write("schema_version: 1\ncategories:\n  1:\n    name: Cat\n    slug: cat\n    enabled: true\n")
        with open(self.config_path / "retention_policy.yaml", "w", encoding="utf-8") as f:
            f.write("schema_version: 1\nraw_retention:\n  default_days: 14\n  delete_batch_size: 500\n  dry_run: false\n  audit_log: true\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_sources(self, sources_block: str) -> None:
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(_sources_yaml(sources_block))

    def test_missing_html_url_warning(self) -> None:
        self._write_sources(_source_block())

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertEqual(errors, [])
        self.assertIsNotNone(config)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Missing html_url", warnings[0])
        self.assertIn("101", warnings[0])

    def test_duplicate_html_url_warning(self) -> None:
        block = "  - id: 101\n    title: Feed A\n    xml_url: https://example.com/a.xml\n    html_url: https://example.com/\n"
        block += "    category_id: 1\n    fetch_group: 1\n    schedule_class: daily\n    sanitization_profile: default_html_article\n    enabled: true\n"
        block += "  - id: 102\n    title: Feed B\n    xml_url: https://example.com/b.xml\n    html_url: https://example.com/\n"
        block += "    category_id: 1\n    fetch_group: 1\n    schedule_class: daily\n    sanitization_profile: default_html_article\n    enabled: true\n"
        self._write_sources(block)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertEqual(errors, [])
        self.assertIsNotNone(config)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Duplicate html_url", warnings[0])
        self.assertIn("101", warnings[0])
        self.assertIn("102", warnings[0])

    def test_duplicate_xml_url_warning(self) -> None:
        block = "  - id: 101\n    title: Feed A\n    xml_url: https://example.com/feed.xml\n    html_url: https://a.example.com/\n"
        block += "    category_id: 1\n    fetch_group: 1\n    schedule_class: daily\n    sanitization_profile: default_html_article\n    enabled: true\n"
        block += "  - id: 102\n    title: Feed B\n    xml_url: https://example.com/feed.xml\n    html_url: https://b.example.com/\n"
        block += "    category_id: 1\n    fetch_group: 1\n    schedule_class: daily\n    sanitization_profile: default_html_article\n    enabled: true\n"
        self._write_sources(block)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertEqual(errors, [])
        self.assertIsNotNone(config)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Duplicate xml_url", warnings[0])
        self.assertIn("101", warnings[0])
        self.assertIn("102", warnings[0])

    def test_large_selector_override_list_warning(self) -> None:
        selectors = "\n".join(f"        - .sel-{i}" for i in range(11))
        block = _source_block().rstrip("\n") + f"\n    sanitization_overrides:\n      content_selectors:\n{selectors}\n"
        self._write_sources(block)

        config, errors, warnings = validate_and_load_config(self.config_path)
        self.assertEqual(errors, [])
        self.assertIsNotNone(config)
        # Missing html_url warning is expected alongside the selector warning.
        selector_warnings = [w for w in warnings if "Unusually large selector override list" in w]
        self.assertEqual(len(selector_warnings), 1)
        self.assertIn("content_selectors", selector_warnings[0])
        self.assertIn("11", selector_warnings[0])


# Warning set currently emitted by the active config. Pinned exactly (decision:
# strict mapping) so any new or resolved warning fails this test and forces a
# conscious update of the source data or this expectation list.
EXPECTED_ACTIVE_CONFIG_WARNINGS = [
    "sources.yaml [Source ID 19 ('Space.com')]: Missing html_url",
    "sources.yaml [Source ID 24 ('NASA Breaking News')]: Missing html_url",
    "sources.yaml [Source ID 58 ('Nature')]: Missing html_url",
    "sources.yaml [Source ID 66 ('New Scientist - Space')]: Missing html_url",
    "sources.yaml [Source ID 69 ('MIT 科技评论 - 本周热榜')]: Missing html_url",
    "sources.yaml [Source ID 70 ('cnBeta')]: Missing html_url",
    "sources.yaml [Source ID 71 ('果壳网 科学人')]: Missing html_url",
    "sources.yaml [Source ID 79 ('Scientific American Content: Global')]: Missing html_url",
    "sources.yaml [Source ID 82 ('Sky & Telescope')]: Missing html_url",
    "sources.yaml [Source ID 83 ('奇客Solidot–传递最新科技情报')]: Missing html_url",
    "Duplicate html_url 'https://www.theblackvault.com/' found across multiple source IDs: [33, 34]",
    "Duplicate html_url 'https://www.cbsnews.com/' found across multiple source IDs: [43, 44, 45, 46]",
    "Duplicate html_url 'https://news.google.com/' found across multiple source IDs: [60, 61, 62, 63, 64]",
    "Duplicate html_url 'https://www.popularmechanics.com/' found across multiple source IDs: [72, 73, 74, 75]",
    "Duplicate html_url 'https://www.universetoday.com/' found across multiple source IDs: [94, 95]",
]


class TestActiveConfig(unittest.TestCase):
    def test_active_config_has_no_errors_and_only_known_warnings(self) -> None:
        active_config_dir = pathlib.Path(__file__).resolve().parents[1] / "config"

        config, errors, warnings = validate_and_load_config(active_config_dir)

        self.assertEqual(errors, [])
        self.assertIsNotNone(config)
        self.assertEqual(sorted(warnings), sorted(EXPECTED_ACTIVE_CONFIG_WARNINGS))

if __name__ == "__main__":
    unittest.main()
