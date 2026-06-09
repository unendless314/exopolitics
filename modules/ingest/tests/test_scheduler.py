import unittest
import datetime
from modules.ingest.src.config import SourceConfig, ScheduleClassConfig
from modules.ingest.src.scheduler import (
    is_source_due,
    should_skip_quarantined,
    apply_fetch_success,
    apply_fetch_failure,
    parse_utc_timestamp,
    format_utc_timestamp
)

class TestScheduler(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SourceConfig(
            id=101,
            title="Test Feed",
            xml_url="https://example.com/rss",
            category_id=1,
            fetch_group=3,
            schedule_class="daily",
            enabled=True,
            sanitization_profile="default_html_article"
        )
        self.sc_daily = ScheduleClassConfig(target_interval_minutes=1440, description="Daily")
        self.sc_hourly = ScheduleClassConfig(target_interval_minutes=60, description="Hourly")

    def test_is_source_due_never_fetched(self) -> None:
        self.assertTrue(is_source_due(self.source, self.sc_daily, None, "2026-06-02T12:00:00Z"))

    def test_is_source_due_elapsed_time(self) -> None:
        self.assertTrue(is_source_due(self.source, self.sc_daily, "2026-06-01T12:00:00Z", "2026-06-02T12:00:00Z"))
        self.assertTrue(is_source_due(self.source, self.sc_daily, "2026-06-01T11:59:00Z", "2026-06-02T12:00:00Z"))
        self.assertFalse(is_source_due(self.source, self.sc_daily, "2026-06-01T13:00:00Z", "2026-06-02T12:00:00Z"))
        self.assertTrue(is_source_due(self.source, self.sc_hourly, "2026-06-02T11:00:00Z", "2026-06-02T12:00:00Z"))
        self.assertFalse(is_source_due(self.source, self.sc_hourly, "2026-06-02T11:01:00Z", "2026-06-02T12:00:00Z"))

    def test_should_skip_quarantined(self) -> None:
        self.assertFalse(should_skip_quarantined(None, "2026-06-02T12:00:00Z"))
        self.assertTrue(should_skip_quarantined("2026-06-02T13:00:00Z", "2026-06-02T12:00:00Z"))
        self.assertFalse(should_skip_quarantined("2026-06-02T11:00:00Z", "2026-06-02T12:00:00Z"))
        self.assertFalse(should_skip_quarantined("2026-06-02T12:00:00Z", "2026-06-02T12:00:00Z"))

    def test_apply_fetch_success(self) -> None:
        failures, status, quarantine = apply_fetch_success(4)
        self.assertEqual(failures, 0)
        self.assertEqual(status, "healthy")
        self.assertIsNone(quarantine)

    def test_apply_fetch_failure_transitions(self) -> None:
        failures, status, quarantine = apply_fetch_failure(0, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 1)
        self.assertEqual(status, "healthy")
        self.assertIsNone(quarantine)

        failures, status, quarantine = apply_fetch_failure(2, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 3)
        self.assertEqual(status, "degraded")
        self.assertIsNone(quarantine)

        failures, status, quarantine = apply_fetch_failure(3, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 4)
        self.assertEqual(status, "degraded")
        self.assertIsNone(quarantine)

        failures, status, quarantine = apply_fetch_failure(4, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 5)
        self.assertEqual(status, "quarantined")
        self.assertEqual(quarantine, "2026-06-03T12:00:00Z")

if __name__ == "__main__":
    unittest.main()
