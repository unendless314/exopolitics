"""JSON report loader and schema validator for the dashboard module.

Pure loading/validation logic lives here (no Streamlit imports) so it can be
unit-tested directly. The Streamlit caching wrapper lives in ``app.py`` and
delegates to :func:`load_report`; its cache key includes the file path and
last-modified timestamp, per DATA_CONTRACT.md section 3.1.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "dashboard_settings.yaml"
DEFAULT_REPORT_DIR = "reports/analysis/"
ENV_REPORT_DIR = "DASHBOARD_REPORT_DIR"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# JSON filename expected for each report family (DATA_CONTRACT.md section 1).
REPORT_FILENAMES: dict[str, str] = {
    "funnel": "PIPELINE_FUNNEL_REPORT.json",
    "sources": "SOURCE_QUALITY_REPORT.json",
    "classify": "CLASSIFY_MONITOR_REPORT.json",
    "translation": "TRANSLATION_PERFORMANCE_REPORT.json",
    "curation_diagnostics": "CURATION_PERFORMANCE_REPORT.json",
}


# ---------------------------------------------------------------------------
# Pydantic models (permissive: forward-compatible with minor schema additions)
# ---------------------------------------------------------------------------


class _PermissiveModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class LatencyStats(_PermissiveModel):
    average: Optional[float] = None
    median: Optional[float] = None
    p90: Optional[float] = None


class StageCounts(_PermissiveModel):
    total_ingested: int = 0
    low_context_bypass_count: int = 0
    total_classified: int = 0
    relevant_classified: int = 0
    total_curated: int = 0
    curation_approved: int = 0
    total_translated: int = 0
    total_published: int = 0
    classification_readiness_breakdown: dict[str, int] = Field(default_factory=dict)


class MaturedMetrics(StageCounts):
    classification_rate: Optional[float] = None
    curation_rate: Optional[float] = None
    curation_approval_rate: Optional[float] = None
    translation_rate: Optional[float] = None
    publish_rate: Optional[float] = None


class LanguageCoverage(_PermissiveModel):
    language_code: str
    published_count: int = 0
    coverage_rate: Optional[float] = None


class FunnelReport(_PermissiveModel):
    report_type: Literal["funnel"]
    schema_version: str
    generated_at: Optional[str] = None
    lookback_days: Optional[int] = None
    maturation_offset_hours: Optional[float] = None
    raw_window: dict[str, Any] = Field(default_factory=dict)
    matured_window: dict[str, Any] = Field(default_factory=dict)
    raw_metrics: StageCounts = Field(default_factory=StageCounts)
    matured_metrics: MaturedMetrics = Field(default_factory=MaturedMetrics)
    raw_latency_metrics: dict[str, Any] = Field(default_factory=dict)
    published_by_language: list[LanguageCoverage] = Field(default_factory=list)
    data_quality_anomalies: list[Any] = Field(default_factory=list)


class DecisionModel(_PermissiveModel):
    quadrant: Optional[str] = None
    analysis_flags: list[str] = Field(default_factory=list)


class SourceRow(_PermissiveModel):
    source_id: int
    source_title: Optional[str] = None  # absent in current analysis JSON
    fetch_success_rate: Optional[float] = None
    ingest_volume: int = 0
    relevance_rate: Optional[float] = None
    curation_approval_rate: Optional[float] = None
    overall_yield: Optional[float] = None
    classification_character_volume_proxy: Optional[float] = None
    curation_character_volume_proxy: Optional[float] = None
    classification_filtering_overhead: Optional[float] = None
    topic_class_breakdown: dict[str, Optional[float]] = Field(default_factory=dict)
    decision_model: DecisionModel = Field(default_factory=DecisionModel)


class SourcesMetrics(_PermissiveModel):
    overall_fetch_success_rate: Optional[float] = None
    total_ingested_items: int = 0
    low_context_bypass_rate: Optional[float] = None


class SourcesReport(_PermissiveModel):
    report_type: Literal["sources"]
    schema_version: str
    generated_at: Optional[str] = None
    lookback_days: Optional[int] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    metrics: SourcesMetrics = Field(default_factory=SourcesMetrics)
    breakdowns: list[SourceRow] = Field(default_factory=list)


class ClassifyRow(_PermissiveModel):
    source_id: int
    source_title: Optional[str] = None
    classify_volume: int = 0
    classification_character_volume_proxy: Optional[float] = None
    relevance_rate: Optional[float] = None
    average_confidence: Optional[float] = None
    content_density_distribution: dict[str, Optional[float]] = Field(default_factory=dict)
    topic_class_breakdown: dict[str, Optional[float]] = Field(default_factory=dict)


class ClassifyMetrics(_PermissiveModel):
    total_classified: int = 0
    classification_character_volume_proxy: Optional[float] = None
    relevance_rate: Optional[float] = None
    average_confidence: Optional[float] = None
    overall_topic_class_breakdown: dict[str, Optional[float]] = Field(default_factory=dict)


class ClassifyReport(_PermissiveModel):
    report_type: Literal["classify"]
    schema_version: str
    generated_at: Optional[str] = None
    lookback_days: Optional[int] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    metrics: ClassifyMetrics = Field(default_factory=ClassifyMetrics)
    breakdowns: list[ClassifyRow] = Field(default_factory=list)


class TranslationRow(_PermissiveModel):
    language_code: str
    translation_success_rate: Optional[float] = None
    translation_completion_rate: Optional[float] = None
    average_latency_seconds: Optional[float] = None
    stale_rate: Optional[float] = None
    translation_character_volume_proxy: Optional[float] = None


class TranslationMetrics(_PermissiveModel):
    overall_translation_success_rate: Optional[float] = None
    overall_translation_completion_rate: Optional[float] = None
    average_latency_seconds: Optional[float] = None


class TranslationReport(_PermissiveModel):
    report_type: Literal["translation"]
    schema_version: str
    generated_at: Optional[str] = None
    lookback_days: Optional[int] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    metrics: TranslationMetrics = Field(default_factory=TranslationMetrics)
    breakdowns: list[TranslationRow] = Field(default_factory=list)


class RejectionMixRow(_PermissiveModel):
    downstream_action: str
    count: int = 0


class CurationMetrics(_PermissiveModel):
    curation_approval_rate: Optional[float] = None
    curation_character_volume_proxy: Optional[float] = None
    curation_latency_seconds: LatencyStats = Field(default_factory=LatencyStats)


class CurationReport(_PermissiveModel):
    report_type: Literal["curation_diagnostics"]
    schema_version: str
    generated_at: Optional[str] = None
    lookback_days: Optional[int] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    metrics: CurationMetrics = Field(default_factory=CurationMetrics)
    curation_rejection_mix: list[RejectionMixRow] = Field(default_factory=list)
    breakdowns: list[dict[str, Any]] = Field(default_factory=list)


REPORT_MODELS: dict[str, type[BaseModel]] = {
    "funnel": FunnelReport,
    "sources": SourcesReport,
    "classify": ClassifyReport,
    "translation": TranslationReport,
    "curation_diagnostics": CurationReport,
}


# ---------------------------------------------------------------------------
# Settings and path resolution
# ---------------------------------------------------------------------------


def load_settings(settings_path: Path = DEFAULT_SETTINGS_PATH) -> dict[str, Any]:
    """Load dashboard settings YAML; fall back to built-in defaults."""
    defaults: dict[str, Any] = {
        "paths": {"report_dir": DEFAULT_REPORT_DIR},
        "supported_schema_versions": {},
        "ui": {"page_title": "UAP Aggregation Pipeline Dashboard", "sidebar_title": "Navigation"},
    }
    try:
        with open(settings_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("Settings file %s not found; using defaults", settings_path)
        return defaults
    for key, value in defaults.items():
        if isinstance(value, dict):
            value.update(data.get(key) or {})
            data[key] = value
    data.setdefault("supported_schema_versions", {})
    return data


def resolve_report_dir(settings: dict[str, Any], repo_root: Path = REPO_ROOT) -> Path:
    """Resolution order: env override -> settings paths.report_dir -> default.

    Relative paths are resolved against the repository root.
    """
    override = os.environ.get(ENV_REPORT_DIR)
    raw = override or (settings.get("paths") or {}).get("report_dir") or DEFAULT_REPORT_DIR
    path = Path(raw)
    return path if path.is_absolute() else (repo_root / path)


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


@dataclass
class ReportLoadResult:
    """Outcome of loading one report file.

    ``status`` is one of ``ok`` / ``warning`` / ``error`` / ``missing``.
    """

    report_type: str
    status: str
    model: Optional[BaseModel] = None
    messages: list[str] = field(default_factory=list)
    source_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "warning") and self.model is not None


def _check_schema_version(report_type: str, schema_version: Any, supported: str | None) -> tuple[str, list[str]]:
    """Apply DATA_CONTRACT.md section 4.2 compatibility rules."""
    if schema_version is None:
        return "error", [f"Report '{report_type}' is missing 'schema_version'."]
    if not isinstance(schema_version, str) or not SEMVER_RE.match(schema_version):
        return "error", [f"Report '{report_type}' has invalid schema_version '{schema_version}' (expected MAJOR.MINOR.PATCH)."]
    if not supported:
        return "warning", [f"No supported schema version declared for '{report_type}'; loaded without compatibility check."]
    if schema_version.split(".")[0] != supported.split(".")[0]:
        return "error", [
            f"Report '{report_type}' schema_version {schema_version} is incompatible with supported {supported} "
            "(major version differs). Update the dashboard before consuming this report."
        ]
    if schema_version != supported:
        return "warning", [
            f"Report '{report_type}' schema_version {schema_version} differs from supported {supported} "
            "(minor/patch change); loaded with forward-compatibility."
        ]
    return "ok", []


def load_report(path: Path, report_type: str, supported_versions: dict[str, str]) -> ReportLoadResult:
    """Load, validate, and parse one JSON report file."""
    if not path.exists():
        return ReportLoadResult(report_type=report_type, status="missing",
                                messages=[f"Report file not found: {path}"], source_path=path)
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return ReportLoadResult(report_type=report_type, status="error",
                                messages=[f"Invalid JSON in {path}: {exc}"], source_path=path)

    actual_type = payload.get("report_type")
    if actual_type not in REPORT_MODELS:
        return ReportLoadResult(report_type=report_type, status="error",
                                messages=[f"Unknown report_type '{actual_type}' in {path}; file skipped."],
                                source_path=path)
    if actual_type != report_type:
        return ReportLoadResult(
            report_type=report_type, status="error",
            messages=[f"Expected report_type '{report_type}' but file contains '{actual_type}'."],
            source_path=path)

    status, messages = _check_schema_version(actual_type, payload.get("schema_version"),
                                             supported_versions.get(actual_type))
    if status == "error":
        return ReportLoadResult(report_type=report_type, status="error", messages=messages, source_path=path)

    try:
        model = REPORT_MODELS[actual_type].model_validate(payload)
    except ValidationError as exc:
        return ReportLoadResult(report_type=report_type, status="error",
                                messages=[f"Payload failed schema validation: {exc}"], source_path=path)

    return ReportLoadResult(report_type=report_type, status=status, model=model,
                            messages=messages, source_path=path)


def load_all_reports(report_dir: Path, supported_versions: dict[str, str]) -> dict[str, ReportLoadResult]:
    """Load every known report file from ``report_dir``; missing files are tolerated."""
    results: dict[str, ReportLoadResult] = {}
    for report_type, filename in REPORT_FILENAMES.items():
        results[report_type] = load_report(report_dir / filename, report_type, supported_versions)
    return results
