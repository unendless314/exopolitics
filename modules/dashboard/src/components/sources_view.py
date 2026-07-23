"""Sources view: source health table, quadrant scatter plot, flag filtering.

Renders the ``sources`` report family (``SOURCE_QUALITY_REPORT.json``).
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from loaders.report_loader import SourcesReport

from .formatting import NA, fmt_num, fmt_pct, quadrant_label

_PLOTLY_LAYOUT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")


def _to_frame(report: SourcesReport) -> pd.DataFrame:
    rows = []
    for r in report.breakdowns:
        rows.append({
            # Current analysis JSON carries source_id only; source_title is
            # rendered automatically once the upstream schema adds it.
            "Source": r.source_title or str(r.source_id),
            "Fetch Success": r.fetch_success_rate,
            "Ingest Volume": r.ingest_volume,
            "Relevance Rate": r.relevance_rate,
            "Curation Approval": r.curation_approval_rate,
            "Overall Yield": r.overall_yield,
            "Quadrant": quadrant_label(r.decision_model.quadrant),
            "Flags": ", ".join(f"[{f}]" for f in r.decision_model.analysis_flags),
            "_flags": r.decision_model.analysis_flags,
        })
    return pd.DataFrame(rows)


def render(report: SourcesReport) -> None:
    m = report.metrics
    cols = st.columns(3)
    cols[0].metric("Overall Fetch Success Rate", fmt_pct(m.overall_fetch_success_rate))
    cols[1].metric("Total Ingested Items", fmt_num(m.total_ingested_items))
    cols[2].metric("Low-Context Observation Rate", fmt_pct(m.low_context_observation_rate))

    df = _to_frame(report)
    if df.empty:
        st.info("No source data available for this lookback window.")
        return

    all_flags = sorted({flag for flags in df["_flags"] for flag in flags})
    selected_flags = st.multiselect("Filter by analysis flags", options=all_flags, default=[])
    if selected_flags:
        df = df[df["_flags"].apply(lambda flags: any(f in flags for f in selected_flags))]
        if df.empty:
            st.info("No sources match the selected flags.")
            return

    st.subheader("Source Health")
    display = df.drop(columns=["_flags"])
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "Fetch Success": st.column_config.NumberColumn(format="%.3f"),
            "Relevance Rate": st.column_config.NumberColumn(format="%.3f"),
            "Curation Approval": st.column_config.NumberColumn(format="%.3f"),
            "Overall Yield": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    st.caption("Rate columns are stored as 0–1 fractions; multiply by 100 for percentages.")

    st.subheader("Source Quadrants")
    scatter_df = df.dropna(subset=["Relevance Rate", "Overall Yield"])
    if scatter_df.empty:
        st.info(f"Quadrant data is {NA} for the selected sources (insufficient data).")
        return
    fig = px.scatter(
        scatter_df,
        x="Relevance Rate",
        y="Overall Yield",
        color="Quadrant",
        size="Ingest Volume",
        hover_name="Source",
        hover_data={"Flags": True, "Relevance Rate": ":.2f", "Overall Yield": ":.2f"},
    )
    fig.update_layout(**_PLOTLY_LAYOUT, margin=dict(l=10, r=10, t=10, b=10), height=480)
    st.plotly_chart(fig, width="stretch")
