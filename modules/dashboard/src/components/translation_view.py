"""Translation view: language-level success, completion, latency, and volume.

Renders the ``translation`` report family (``TRANSLATION_PERFORMANCE_REPORT.json``).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders.report_loader import TranslationReport

from .formatting import fmt_duration, fmt_num, fmt_pct

_PLOTLY_LAYOUT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")


def render(report: TranslationReport) -> None:
    m = report.metrics
    cols = st.columns(3)
    cols[0].metric("Overall Translation Success Rate", fmt_pct(m.overall_translation_success_rate))
    cols[1].metric("Overall Translation Completion Rate", fmt_pct(m.overall_translation_completion_rate))
    cols[2].metric("Average Latency", fmt_duration(m.average_latency_seconds))

    df = pd.DataFrame([{
        "Language": r.language_code,
        "Success Rate": r.translation_success_rate,
        "Completion Rate": r.translation_completion_rate,
        "Avg Latency (s)": r.average_latency_seconds,
        "Stale Rate": r.stale_rate,
        "Char Volume Proxy": r.translation_character_volume_proxy,
    } for r in report.breakdowns])

    if df.empty:
        st.info("No translation data available for this lookback window.")
        return

    st.subheader("Success / Completion Rate by Language")
    fig = go.Figure()
    for col in ("Success Rate", "Completion Rate"):
        fig.add_trace(go.Bar(name=col, x=df["Language"], y=df[col]))
    fig.update_layout(**_PLOTLY_LAYOUT, barmode="group", yaxis_title="Rate (0–1)",
                      margin=dict(l=10, r=10, t=10, b=10), height=320)
    st.plotly_chart(fig, width="stretch")

    st.subheader("Average Latency by Language")
    latency_df = df.dropna(subset=["Avg Latency (s)"])
    if latency_df.empty:
        st.info("No latency data available for this lookback window.")
    else:
        fig = go.Figure(go.Bar(x=latency_df["Language"], y=latency_df["Avg Latency (s)"], marker_color="#00A8E8"))
        fig.update_layout(**_PLOTLY_LAYOUT, yaxis_title="Seconds", margin=dict(l=10, r=10, t=10, b=10), height=300)
        st.plotly_chart(fig, width="stretch")

    st.subheader("Volume and Staleness by Language")
    st.dataframe(
        df[["Language", "Char Volume Proxy", "Stale Rate"]],
        width="stretch",
        hide_index=True,
        column_config={"Stale Rate": st.column_config.NumberColumn(format="%.3f")},
    )
    st.caption(f"Character volume proxy total: {fmt_num(df['Char Volume Proxy'].sum())}")
