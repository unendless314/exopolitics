import unittest
from modules.ingest.src.config import IngestConfig, CategoryConfig, ScheduleClassConfig, SourceConfig
from modules.ingest.src.validator import validate_config

class TestValidator(unittest.TestCase):
    def setUp(self) -> None:
        # Standard valid categories and schedule classes
        self.categories = {
            1: CategoryConfig(id=1, name="Valid Enabled Category", enabled=True),
            2: CategoryConfig(id=2, name="Disabled Category", enabled=False)
        }
        self.schedule_classes = {
            "daily": ScheduleClassConfig(name="daily", target_interval_minutes=1440, description="Daily"),
            "hourly": ScheduleClassConfig(name="hourly", target_interval_minutes=60, description="Hourly")
        }

    def test_valid_config_no_errors_no_warnings(self) -> None:
        source = SourceConfig(
            id=1,
            title="AARO Official Releases",
            xml_url="https://www.defense.gov/uap.xml",
            html_url="https://www.aaro.mil/",
            category_id=1,
            fetch_group=3,
            schedule_class="daily",
            enabled=True
        )
        config = IngestConfig(
            categories=self.categories,
            schedule_classes=self.schedule_classes,
            sources=[source]
        )

        errors, warnings = validate_config(config)
        self.assertEqual(len(errors), 0, f"Expected no errors, got: {errors}")
        self.assertEqual(len(warnings), 0, f"Expected no warnings, got: {warnings}")

    def test_fail_fast_duplicate_source_id(self) -> None:
        source1 = SourceConfig(
            id=42,
            title="Feed 1",
            xml_url="https://example.com/feed1.xml",
            html_url="https://example.com/1",
            category_id=1,
            fetch_group=3,
            schedule_class="daily",
            enabled=True
        )
        source2 = SourceConfig(
            id=42, # DUPLICATE ID
            title="Feed 2",
            xml_url="https://example.com/feed2.xml",
            html_url="https://example.com/2",
            category_id=1,
            fetch_group=4,
            schedule_class="daily",
            enabled=True
        )
        config = IngestConfig(
            categories=self.categories,
            schedule_classes=self.schedule_classes,
            sources=[source1, source2]
        )

        errors, warnings = validate_config(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("Duplicate source ID 42" in err for err in errors))

    def test_fail_fast_malformed_xml_url(self) -> None:
        invalid_urls = [
            "",
            "ftp://example.com/feed.xml",  # Invalid scheme (only http/https allowed)
            "relative/path/to/feed.xml",   # Not absolute
            "https://",                    # Missing netloc
        ]
        
        for url in invalid_urls:
            source = SourceConfig(
                id=1,
                title="Feed",
                xml_url=url,
                html_url="https://example.com",
                category_id=1,
                fetch_group=3,
                schedule_class="daily",
                enabled=True
            )
            config = IngestConfig(
                categories=self.categories,
                schedule_classes=self.schedule_classes,
                sources=[source]
            )
            errors, warnings = validate_config(config)
            self.assertGreater(len(errors), 0, f"Expected error for URL: '{url}'")

    def test_fail_fast_missing_or_disabled_category(self) -> None:
        # Category 999 does not exist
        source_missing = SourceConfig(
            id=1,
            title="Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
            category_id=999,
            fetch_group=3,
            schedule_class="daily",
            enabled=True
        )
        # Category 2 exists but is disabled
        source_disabled = SourceConfig(
            id=2,
            title="Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
            category_id=2,
            fetch_group=3,
            schedule_class="daily",
            enabled=True
        )

        for source in [source_missing, source_disabled]:
            config = IngestConfig(
                categories=self.categories,
                schedule_classes=self.schedule_classes,
                sources=[source]
            )
            errors, warnings = validate_config(config)
            self.assertGreater(len(errors), 0, f"Expected error for source: {source}")

    def test_fail_fast_out_of_range_fetch_group(self) -> None:
        invalid_groups = [
            0,   # Out of [1, 8] range
            9,   # Out of [1, 8] range
            "3", # Not an integer
        ]

        for fg in invalid_groups:
            source = SourceConfig(
                id=1,
                title="Feed",
                xml_url="https://example.com/feed.xml",
                html_url="https://example.com",
                category_id=1,
                fetch_group=fg,
                schedule_class="daily",
                enabled=True
            )
            config = IngestConfig(
                categories=self.categories,
                schedule_classes=self.schedule_classes,
                sources=[source]
            )
            errors, warnings = validate_config(config)
            self.assertGreater(len(errors), 0, f"Expected error for fetch_group: {fg}")

    def test_fail_fast_unknown_schedule_class(self) -> None:
        source = SourceConfig(
            id=1,
            title="Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
            category_id=1,
            fetch_group=3,
            schedule_class="unknown_class",
            enabled=True
        )
        config = IngestConfig(
            categories=self.categories,
            schedule_classes=self.schedule_classes,
            sources=[source]
        )
        errors, warnings = validate_config(config)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("unknown_class" in err for err in errors))

    def test_fail_fast_invalid_enabled_type(self) -> None:
        source = SourceConfig(
            id=1,
            title="Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
            category_id=1,
            fetch_group=3,
            schedule_class="daily",
            enabled="yes" # String instead of boolean
        )
        config = IngestConfig(
            categories=self.categories,
            schedule_classes=self.schedule_classes,
            sources=[source]
        )
        errors, warnings = validate_config(config)
        self.assertGreater(len(errors), 0)

    def test_warnings_missing_fields_and_duplicates(self) -> None:
        source1 = SourceConfig(
            id=1,
            title="",  # WARNING: suspiciously empty title
            xml_url="https://example.com/feed.xml",
            html_url="",  # WARNING: missing html_url
            category_id=1,
            fetch_group=3,
            schedule_class="daily",
            enabled=True
        )
        source2 = SourceConfig(
            id=2,
            title="Feed 2",
            xml_url="https://example.com/feed.xml", # WARNING: duplicate xml_url
            html_url="https://example.com",
            category_id=1,
            fetch_group=4,
            schedule_class="daily",
            enabled=True
        )
        config = IngestConfig(
            categories=self.categories,
            schedule_classes=self.schedule_classes,
            sources=[source1, source2]
        )

        errors, warnings = validate_config(config)
        self.assertEqual(len(errors), 0)
        self.assertGreater(len(warnings), 0)
        self.assertTrue(any("Suspiciously empty title" in warn for warn in warnings))
        self.assertTrue(any("Missing html_url" in warn for warn in warnings))
        self.assertTrue(any("Duplicate xml_url" in warn for warn in warnings))

if __name__ == "__main__":
    unittest.main()
