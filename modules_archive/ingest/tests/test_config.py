import unittest
import pathlib
import tempfile
from modules.ingest.src.config import load_config, IngestConfig
from modules.ingest.src.validator import validate_config

class TestConfigLoader(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = pathlib.Path(self.temp_dir.name)

        # Write dummy categories.yaml
        self.categories_yaml = """
schema_version: 2
categories:
  0:
    name: Disabled Category
    slug: disabled-cat
    enabled: false
  1:
    name: Enabled Category
    enabled: true
    slug: enabled-cat
"""
        with open(self.config_path / "categories.yaml", "w", encoding="utf-8") as f:
            f.write(self.categories_yaml)

        # Write dummy sources.yaml
        self.sources_yaml = """
schema_version: 2
schedule_classes:
  hourly:
    target_interval_minutes: 60
    description: Hourly cadence
  daily:
    target_interval_minutes: 1440
    description: Daily cadence
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    html_url: https://example.com
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    enabled: true
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(self.sources_yaml)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_config_success(self) -> None:
        config = load_config(self.config_path)

        self.assertIsInstance(config, IngestConfig)
        
        # Test Categories
        self.assertEqual(len(config.categories), 2)
        self.assertIn(0, config.categories)
        self.assertIn(1, config.categories)
        self.assertEqual(config.categories[0].name, "Disabled Category")
        self.assertFalse(config.categories[0].enabled)
        self.assertEqual(config.categories[1].name, "Enabled Category")
        self.assertTrue(config.categories[1].enabled)
        self.assertEqual(config.categories[1].slug, "enabled-cat")

        # Test Schedule Classes
        self.assertEqual(len(config.schedule_classes), 2)
        self.assertIn("hourly", config.schedule_classes)
        self.assertIn("daily", config.schedule_classes)
        self.assertEqual(config.schedule_classes["hourly"].target_interval_minutes, 60)
        self.assertEqual(config.schedule_classes["daily"].target_interval_minutes, 1440)

        # Test Sources
        self.assertEqual(len(config.sources), 1)
        source = config.sources[0]
        self.assertEqual(source.id, 101)
        self.assertEqual(source.title, "Test Feed")
        self.assertEqual(source.xml_url, "https://example.com/rss")
        self.assertEqual(source.html_url, "https://example.com")
        self.assertEqual(source.category_id, 1)
        self.assertEqual(source.fetch_group, 3)
        self.assertEqual(source.schedule_class, "daily")
        self.assertTrue(source.enabled)

    def test_load_config_preserves_raw_types_and_no_value_error(self) -> None:
        # Write malformed/string types for numeric/boolean fields
        bad_sources_yaml = """
schema_version: 2
schedule_classes:
  daily:
    target_interval_minutes: "not-an-int"
sources:
  - id: "not-an-int-id"
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: "bad-cat-id"
    fetch_group: "bad-fg"
    schedule_class: daily
    enabled: "yes"
"""
        bad_categories_yaml = """
schema_version: 2
categories:
  "bad-cat-key":
    name: Bad Category
    enabled: "sure"
"""
        with open(self.config_path / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)
        with open(self.config_path / "categories.yaml", "w", encoding="utf-8") as f:
            f.write(bad_categories_yaml)

        # Assert load_config does NOT raise ValueError but parses correctly preserving types
        try:
            config = load_config(self.config_path)
        except ValueError as e:
            self.fail(f"load_config raised ValueError: {e}")

        # Check that types are raw strings as in YAML
        self.assertEqual(config.sources[0].id, "not-an-int-id")
        self.assertEqual(config.sources[0].category_id, "bad-cat-id")
        self.assertEqual(config.sources[0].fetch_group, "bad-fg")
        self.assertEqual(config.sources[0].enabled, "yes")
        self.assertIn("bad-cat-key", config.categories)
        self.assertEqual(config.categories["bad-cat-key"].enabled, "sure")

    def test_load_real_project_config_and_validate(self) -> None:
        # Load canonical project config files
        project_config_dir = pathlib.Path(__file__).parent.parent / "config"
        if project_config_dir.exists():
            config = load_config(project_config_dir)
            self.assertGreater(len(config.categories), 0)
            self.assertGreater(len(config.sources), 0)
            self.assertGreater(len(config.schedule_classes), 0)
            
            # Validate loaded config against actual schema rules
            errors, warnings = validate_config(config)
            self.assertEqual(len(errors), 0, f"Expected real config to have no errors, but got: {errors}")

if __name__ == "__main__":
    unittest.main()
