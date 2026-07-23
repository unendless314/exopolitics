"""Unit tests for the dashboard report loader.

Covers the validation and compatibility rules from DATA_CONTRACT.md
sections 4 and 7. All fixtures are synthetic JSON payloads; one optional
integration test validates the real reports in ``reports/analysis/`` when
they have been generated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.dashboard.src.loaders.report_loader import (
    DEFAULT_SETTINGS_PATH,
    FunnelReport,
    load_all_reports,
    load_report,
    load_settings,
    resolve_report_dir,
)

SUPPORTED = {
    "sources": "2.0.0",
    "funnel": "3.0.0",
    "translation": "1.0.0",
    "classify": "2.0.0",
    "curation_diagnostics": "2.0.0",
}

FUNNEL_PAYLOAD = {
    "report_type": "funnel",
    "schema_version": "3.0.0",
    "generated_at": "2026-07-17T03:24:37Z",
    "lookback_days": 7,
    "raw_metrics": {"total_ingested": 10, "low_context_observation_count": 1},
    "matured_metrics": {"total_ingested": 10, "classification_rate": 0.8},
    "raw_latency_metrics": {},
    "published_by_language": [],
    "data_quality_anomalies": [],
}


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_settings_reads_supported_versions():
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    assert settings["supported_schema_versions"]["funnel"] == "3.0.0"
    assert settings["supported_schema_versions"]["sources"] == "2.0.0"
    assert settings["paths"]["report_dir"]


def test_load_settings_missing_file_uses_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.yaml")
    assert settings["paths"]["report_dir"] == "reports/analysis/"


def test_resolve_report_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_REPORT_DIR", str(tmp_path))
    assert resolve_report_dir({"paths": {"report_dir": "ignored/"}}) == tmp_path


def test_resolve_report_dir_settings_relative(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHBOARD_REPORT_DIR", raising=False)
    resolved = resolve_report_dir({"paths": {"report_dir": "reports/analysis/"}}, repo_root=tmp_path)
    assert resolved == tmp_path / "reports/analysis"


def test_missing_file_is_tolerated(tmp_path):
    result = load_report(tmp_path / "absent.json", "funnel", SUPPORTED)
    assert result.status == "missing"
    assert not result.ok


def test_valid_payload_loads(tmp_path):
    path = _write(tmp_path, "PIPELINE_FUNNEL_REPORT.json", FUNNEL_PAYLOAD)
    result = load_report(path, "funnel", SUPPORTED)
    assert result.status == "ok", result.messages
    assert isinstance(result.model, FunnelReport)
    assert result.model.matured_metrics.classification_rate == 0.8
    assert result.model.raw_metrics.low_context_observation_count == 1


def test_missing_schema_version_is_error(tmp_path):
    payload = {**FUNNEL_PAYLOAD}
    del payload["schema_version"]
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.status == "error"
    assert "schema_version" in result.messages[0]


def test_invalid_semver_is_error(tmp_path):
    payload = {**FUNNEL_PAYLOAD, "schema_version": "2.0"}
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.status == "error"


def test_major_mismatch_is_refused(tmp_path):
    payload = {**FUNNEL_PAYLOAD, "schema_version": "4.0.0"}
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.status == "error"
    assert not result.ok


def test_minor_mismatch_loads_with_warning(tmp_path):
    payload = {**FUNNEL_PAYLOAD, "schema_version": "3.1.0"}
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.status == "warning"
    assert result.ok  # forward-compatible minor change must still render


def test_unknown_report_type_is_skipped(tmp_path):
    payload = {**FUNNEL_PAYLOAD, "report_type": "horoscope"}
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.status == "error"
    assert "Unknown report_type" in result.messages[0]


def test_report_type_mismatch_is_error(tmp_path):
    payload = {**FUNNEL_PAYLOAD, "report_type": "sources", "metrics": {}, "breakdowns": []}
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.status == "error"


def test_invalid_json_is_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    result = load_report(path, "funnel", SUPPORTED)
    assert result.status == "error"


def test_forward_compatible_extra_fields_are_allowed(tmp_path):
    payload = {**FUNNEL_PAYLOAD, "brand_new_metric": {"x": 1}}
    result = load_report(_write(tmp_path, "f.json", payload), "funnel", SUPPORTED)
    assert result.ok


def test_load_all_reports_tolerates_missing_files(tmp_path):
    _write(tmp_path, "PIPELINE_FUNNEL_REPORT.json", FUNNEL_PAYLOAD)
    results = load_all_reports(tmp_path, SUPPORTED)
    assert results["funnel"].ok
    assert results["sources"].status == "missing"


def test_sources_payload_with_observation_rate_loads(tmp_path):
    """sources 2.0.0 renames the bypass rate to low_context_observation_rate."""
    payload = {
        "report_type": "sources",
        "schema_version": "2.0.0",
        "metrics": {"total_ingested_items": 4, "low_context_observation_rate": 0.25},
        "breakdowns": [],
    }
    result = load_report(_write(tmp_path, "SOURCE_QUALITY_REPORT.json", payload), "sources", SUPPORTED)
    assert result.status == "ok", result.messages
    assert result.model.metrics.low_context_observation_rate == 0.25


@pytest.mark.skipif(not Path("reports/analysis").is_dir(), reason="analysis reports not generated")
def test_real_reports_validate_against_supported_versions():
    results = load_all_reports(Path("reports/analysis"), SUPPORTED)
    statuses = {rtype: r.status for rtype, r in results.items()}
    assert all(s in ("ok", "warning") for s in statuses.values()), statuses
