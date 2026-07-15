import pytest
import datetime
from unittest.mock import MagicMock
from modules.analysis.src.services.classify_service import ClassifyService
from modules.analysis.src.config import SourceMeta

def test_classify_service_overall_and_breakdowns(seeded_db_conn):
    # Mock source meta data
    sources_meta = {
        1: SourceMeta(id=1, title="Source Alpha", xml_url="http://alpha", category_id=1, enabled=True, fetch_group=1, schedule_class="daily"),
        2: SourceMeta(id=2, title="Source Beta", xml_url="http://beta", category_id=2, enabled=True, fetch_group=2, schedule_class="daily")
    }

    service = ClassifyService(seeded_db_conn, sources_meta=sources_meta)
    
    # Force the lookback window to be 7 days from 2026-07-15 16:38:10 UTC
    # Since get_lookback_window uses datetime.now(timezone.utc), we can mock it or just patch the method.
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_classify_analysis(days=7)

    # 1. Assert overall metrics (items 101, 102, 103, 104 are within window. item 105 is out of window)
    metrics = report["metrics"]
    assert metrics["total_classified"] == 4
    # Character volume proxy: 101(17+500=517) + 102(17+600=617) + 103(22+700=722) + 104(13+800=813) = 2669
    assert metrics["classification_character_volume_proxy"] == 2669
    # Relevance rate: (101: core, 102: adjacent, 104: core) / 4 = 3/4 = 0.75
    assert pytest.approx(metrics["relevance_rate"]) == 0.75
    # Average confidence: (0.95 + 0.85 + 0.75 + 0.90) / 4 = 0.8625
    assert pytest.approx(metrics["average_confidence"]) == 0.8625

    # 2. Assert source breakdowns
    breakdowns = report["breakdowns"]
    assert len(breakdowns) == 2

    # Find Source Alpha (ID 1)
    alpha = next(b for b in breakdowns if b["source_id"] == 1)
    assert alpha["classify_volume"] == 3
    assert alpha["classification_character_volume_proxy"] == 1856
    # Relevance rate: 101 (core) + 102 (adjacent) / 3 = 0.6667
    assert pytest.approx(alpha["relevance_rate"]) == 2.0 / 3.0
    # Avg confidence: (0.95 + 0.85 + 0.75) / 3 = 0.85
    assert pytest.approx(alpha["average_confidence"]) == 0.85
    
    # Content density distribution: 101 (high), 102 (medium), 103 (low) => 1/3 each
    density = alpha["content_density_distribution"]
    assert pytest.approx(density["low"]) == 1.0 / 3.0
    assert pytest.approx(density["medium"]) == 1.0 / 3.0
    assert pytest.approx(density["high"]) == 1.0 / 3.0

    # Find Source Beta (ID 2)
    beta = next(b for b in breakdowns if b["source_id"] == 2)
    assert beta["classify_volume"] == 1
    assert beta["classification_character_volume_proxy"] == 813
    assert pytest.approx(beta["relevance_rate"]) == 1.0
    assert pytest.approx(beta["average_confidence"]) == 0.90
    assert beta["content_density_distribution"]["medium"] == 1.0
    assert beta["content_density_distribution"]["low"] == 0.0
    assert beta["content_density_distribution"]["high"] == 0.0

def test_classify_service_empty_db(empty_db_conn):
    service = ClassifyService(empty_db_conn)
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_classify_analysis(days=7)
    
    metrics = report["metrics"]
    assert metrics["total_classified"] == 0
    assert metrics["classification_character_volume_proxy"] == 0
    assert metrics["relevance_rate"] is None
    assert metrics["average_confidence"] is None
    assert len(report["breakdowns"]) == 0

def test_markdown_report_formatting():
    # Verify that formatting runs without issues and handles missing metadata correctly
    sources_meta = {
        1: SourceMeta(id=1, title="Alpha Source", xml_url="http://alpha", category_id=1, enabled=True, fetch_group=1, schedule_class="daily")
    }
    
    service = ClassifyService(None, sources_meta=sources_meta)
    
    data = {
        "report_type": "classify",
        "schema_version": "1.0.0",
        "generated_at": "2026-07-15T12:00:00Z",
        "lookback_days": 7,
        "window_start": "2026-07-08T00:00:00Z",
        "window_end": "2026-07-15T00:00:00Z",
        "metrics": {
            "total_classified": 10,
            "classification_character_volume_proxy": 5000,
            "relevance_rate": 0.50,
            "average_confidence": 0.85
        },
        "breakdowns": [
            {
                "source_id": 1,
                "classify_volume": 6,
                "classification_character_volume_proxy": 3000,
                "relevance_rate": 0.6667,
                "average_confidence": 0.88,
                "content_density_distribution": {"low": 0.1, "medium": 0.5, "high": 0.4}
            },
            {
                "source_id": 999,  # Missing from config metadata
                "classify_volume": 4,
                "classification_character_volume_proxy": 2000,
                "relevance_rate": 0.25,
                "average_confidence": 0.80,
                "content_density_distribution": {"low": 0.5, "medium": 0.5, "high": 0.0}
            }
        ]
    }
    
    report = service.format_markdown_report(data)
    assert "# LLM Classification Workload Report" in report
    assert "Alpha Source" in report
    assert "Unknown Source (ID: 999) [INSUFFICIENT_DATA]" in report
    assert "5000" in report
    assert "3000" in report
    assert "2000" in report
    assert "66.67%" in report
    assert "25.00%" in report

def test_check_positive_days():
    import argparse
    from modules.analysis.src.cli import check_positive_days

    # Valid values
    assert check_positive_days("1") == 1
    assert check_positive_days("7") == 7
    assert check_positive_days(10) == 10

    # Invalid values should raise ArgumentTypeError
    with pytest.raises(argparse.ArgumentTypeError):
        check_positive_days("0")

    with pytest.raises(argparse.ArgumentTypeError):
        check_positive_days("-5")

    with pytest.raises(argparse.ArgumentTypeError):
        check_positive_days("abc")

