import unittest
import pathlib
import tempfile
from modules.ingest.src.config import validate_and_load_config, IngestConfig, SanitizationProfile

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

if __name__ == "__main__":
    unittest.main()
