"""Streamlit entrypoint for the UAP Aggregation Pipeline Dashboard.

Launch with::

    streamlit run modules/dashboard/src/app.py

Reports must be generated beforehand via the analysis CLI; this app never
invokes the analysis module, opens the canonical database, or recomputes
metrics (see docs/DATA_CONTRACT.md).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import streamlit as st

# Make sibling packages (loaders/, components/) importable regardless of how
# the script is launched (streamlit run, AppTest, plain python).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loaders.report_loader import (
    ENV_REPORT_DIR,
    REPORT_FILENAMES,
    ReportLoadResult,
    load_all_reports,
    load_settings,
    resolve_report_dir,
)
from components import (
    classify_view,
    curation_view,
    funnel_view,
    sources_view,
    translation_view,
)

_LOG_LEVEL = os.environ.get("DASHBOARD_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_VIEWS = {
    "Overview": ("funnel", funnel_view),
    "Sources": ("sources", sources_view),
    "Classify": ("classify", classify_view),
    "Translation": ("translation", translation_view),
    "Curation": ("curation_diagnostics", curation_view),
}


@st.cache_data(show_spinner="Loading reports…")
def _load_reports_cached(report_dir: str, file_mtimes: tuple[tuple[str, float], ...]) -> dict[str, ReportLoadResult]:
    """Cache wrapper: the mtime tuple is part of the cache key, so any report
    file change on disk invalidates the cache automatically."""
    settings = load_settings()
    return load_all_reports(Path(report_dir), settings.get("supported_schema_versions") or {})


def _load_reports(report_dir: Path) -> dict[str, ReportLoadResult]:
    mtimes = tuple(
        (filename, (report_dir / filename).stat().st_mtime if (report_dir / filename).exists() else 0.0)
        for filename in sorted(REPORT_FILENAMES.values())
    )
    return _load_reports_cached(str(report_dir), mtimes)


def _render_section(label: str, report_type: str, view_module, result: ReportLoadResult) -> None:
    st.header(label)
    for message in result.messages:
        if result.status == "warning":
            st.warning(message)
        elif result.status == "error":
            st.error(message)
    if not result.ok:
        return
    view_module.render(result.model)


def main() -> None:
    settings = load_settings()
    ui = settings.get("ui") or {}
    st.set_page_config(page_title=ui.get("page_title", "Pipeline Dashboard"), layout="wide")

    report_dir = resolve_report_dir(settings)
    results = _load_reports(report_dir)

    st.title(ui.get("page_title", "UAP Aggregation Pipeline Dashboard"))

    funnel = results.get("funnel")
    if funnel and funnel.ok:
        st.caption(
            f"Generated: {funnel.model.generated_at or 'unknown'} | "
            f"Lookback: {funnel.model.lookback_days or '?'} days | "
            f"Report dir: {report_dir}"
            + (f" (override ${ENV_REPORT_DIR})" if os.environ.get(ENV_REPORT_DIR) else "")
        )
    else:
        st.caption(f"Report dir: {report_dir}")

    with st.sidebar:
        st.title(ui.get("sidebar_title", "Navigation"))
        if st.button("Refresh Reports", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        available = [label for label, (rtype, _) in _VIEWS.items() if results[rtype].status != "missing"]
        if not available:
            st.error("No report files found. Generate them with the analysis CLI first.")
            return
        selection = st.radio("View", available, label_visibility="collapsed")
        missing = [label for label, (rtype, _) in _VIEWS.items() if results[rtype].status == "missing"]
        for label in missing:
            st.caption(f"⚠ {label}: report file missing")

    report_type, view_module = _VIEWS[selection]
    _render_section(selection, report_type, view_module, results[report_type])


main()
