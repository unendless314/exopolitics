"""Overview view: pipeline funnel, KPIs, latency, and language coverage.

Renders the ``funnel`` report family (``PIPELINE_FUNNEL_REPORT.json``).
Rate metrics come from ``matured_metrics`` exactly as emitted by the
analysis module; nothing is re-derived here.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders.report_loader import FunnelReport

from .formatting import fmt_num, fmt_pct

_PLOTLY_LAYOUT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")

_FUNNEL_STAGES: list[tuple[str, str]] = [
    ("Ingested", "total_ingested"),
    ("Low-Context Bypass", "low_context_bypass_count"),
    ("Classified", "total_classified"),
    ("Relevant", "relevant_classified"),
    ("Curated", "total_curated"),
    ("Approved", "curation_approved"),
    ("Translated", "total_translated"),
    ("Published", "total_published"),
]


def render(report: FunnelReport) -> None:
    m = report.matured_metrics

    cols = st.columns(5)
    cols[0].metric("Total Ingested", fmt_num(m.total_ingested))
    cols[1].metric("Total Published", fmt_num(m.total_published))
    cols[2].metric("Classification Rate", fmt_pct(m.classification_rate))
    cols[3].metric("Curation Approval Rate", fmt_pct(m.curation_approval_rate))
    cols[4].metric("Publication Rate", fmt_pct(m.publish_rate))

    st.subheader("Pipeline Funnel")
    labels = [label for label, _ in _FUNNEL_STAGES]
    counts = [getattr(m, field) for _, field in _FUNNEL_STAGES]
    fig = go.Figure(go.Bar(x=counts, y=labels, orientation="h", marker_color="#00A8E8"))
    fig.update_layout(**_PLOTLY_LAYOUT, yaxis=dict(autorange="reversed"), xaxis_title="Items",
                      margin=dict(l=10, r=10, t=10, b=10), height=340)
    st.plotly_chart(fig, width="stretch")

    st.subheader("Stage Latency (raw window)")
    latency = (report.raw_latency_metrics or {}).get("stage_latency_breakdown_seconds") or {}
    if not latency:
        st.info("No latency data available for this lookback window.")
    else:
        stages = list(latency.keys())
        fig = go.Figure()
        for stat_key in ("average", "median", "p90"):
            fig.add_trace(go.Bar(
                name=stat_key,
                y=stages,
                x=[(latency[s] or {}).get(stat_key) for s in stages],
                orientation="h",
            ))
        fig.update_layout(**_PLOTLY_LAYOUT, barmode="group", xaxis_type="log",
                          xaxis_title="Seconds (log scale)", margin=dict(l=10, r=10, t=10, b=10), height=320)
        st.plotly_chart(fig, width="stretch")

    st.subheader("Language Coverage")
    if not report.published_by_language:
        st.info("No language coverage data available for this lookback window.")
    else:
        df = pd.DataFrame([{
            "Language": row.language_code,
            "Published": row.published_count,
            "Coverage Rate": fmt_pct(row.coverage_rate),
        } for row in report.published_by_language])
        st.dataframe(df, width="stretch", hide_index=True)

    if report.data_quality_anomalies:
        st.subheader("Data Quality Anomalies")
        st.warning(f"{len(report.data_quality_anomalies)} anomaly record(s) reported by the analysis module.")
        st.dataframe(pd.DataFrame(report.data_quality_anomalies), width="stretch", hide_index=True)
