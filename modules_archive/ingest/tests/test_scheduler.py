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
            enabled=True
        )
        self.sc_daily = ScheduleClassConfig(name="daily", target_interval_minutes=1440, description="Daily")
        self.sc_hourly = ScheduleClassConfig(name="hourly", target_interval_minutes=60, description="Hourly")

    def test_is_source_due_never_fetched(self) -> None:
        # If never successfully fetched (last_success_at is None), it should always be due
        self.assertTrue(is_source_due(self.source, self.sc_daily, None, "2026-06-02T12:00:00Z"))

    def test_is_source_due_elapsed_time(self) -> None:
        # Daily source fetched 24 hours ago -> due
        self.assertTrue(is_source_due(self.source, self.sc_daily, "2026-06-01T12:00:00Z", "2026-06-02T12:00:00Z"))
        
        # Daily source fetched 24 hours and 1 minute ago -> due
        self.assertTrue(is_source_due(self.source, self.sc_daily, "2026-06-01T11:59:00Z", "2026-06-02T12:00:00Z"))

        # Daily source fetched 23 hours ago -> NOT due
        self.assertFalse(is_source_due(self.source, self.sc_daily, "2026-06-01T13:00:00Z", "2026-06-02T12:00:00Z"))

        # Hourly source fetched 60 minutes ago -> due
        self.assertTrue(is_source_due(self.source, self.sc_hourly, "2026-06-02T11:00:00Z", "2026-06-02T12:00:00Z"))

        # Hourly source fetched 59 minutes ago -> NOT due
        self.assertFalse(is_source_due(self.source, self.sc_hourly, "2026-06-02T11:01:00Z", "2026-06-02T12:00:00Z"))

    def test_should_skip_quarantined(self) -> None:
        # 1. No quarantine -> do not skip
        self.assertFalse(should_skip_quarantined(None, "2026-06-02T12:00:00Z"))

        # 2. Active quarantine (quarantine until 13:00:00, now is 12:00:00) -> should skip
        self.assertTrue(should_skip_quarantined("2026-06-02T13:00:00Z", "2026-06-02T12:00:00Z"))

        # 3. Expired quarantine (quarantine until 11:00:00, now is 12:00:00) -> do not skip
        self.assertFalse(should_skip_quarantined("2026-06-02T11:00:00Z", "2026-06-02T12:00:00Z"))

        # 4. Borderline quarantine (quarantine until 12:00:00, now is 12:00:00) -> do not skip
        self.assertFalse(should_skip_quarantined("2026-06-02T12:00:00Z", "2026-06-02T12:00:00Z"))

    def test_apply_fetch_success(self) -> None:
        failures, status, quarantine = apply_fetch_success(4)
        self.assertEqual(failures, 0)
        self.assertEqual(status, "healthy")
        self.assertIsNone(quarantine)

    def test_apply_fetch_failure_transitions(self) -> None:
        # Failure 1 (from 0): remains healthy
        failures, status, quarantine = apply_fetch_failure(0, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 1)
        self.assertEqual(status, "healthy")
        self.assertIsNone(quarantine)

        # Failure 3 (from 2): becomes degraded
        failures, status, quarantine = apply_fetch_failure(2, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 3)
        self.assertEqual(status, "degraded")
        self.assertIsNone(quarantine)

        # Failure 4 (from 3): remains degraded
        failures, status, quarantine = apply_fetch_failure(3, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 4)
        self.assertEqual(status, "degraded")
        self.assertIsNone(quarantine)

        # Failure 5 (from 4): becomes quarantined for 24 hours
        failures, status, quarantine = apply_fetch_failure(4, "2026-06-02T12:00:00Z")
        self.assertEqual(failures, 5)
        self.assertEqual(status, "quarantined")
        self.assertEqual(quarantine, "2026-06-03T12:00:00Z")  # 12:00:00 + 24 hours

if __name__ == "__main__":
    unittest.main()
