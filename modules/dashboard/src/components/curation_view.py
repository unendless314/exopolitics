"""Curation view: approval rate, rejection mix, and delay distribution.

Renders the ``curation_diagnostics`` report family (``CURATION_PERFORMANCE_REPORT.json``).
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from loaders.report_loader import CurationReport

from .formatting import fmt_duration, fmt_num, fmt_pct

_PLOTLY_LAYOUT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")


def render(report: CurationReport) -> None:
    m = report.metrics
    latency = m.curation_latency_seconds

    cols = st.columns(3)
    cols[0].metric("Curation Approval Rate", fmt_pct(m.curation_approval_rate))
    cols[1].metric("Curation Character Volume Proxy", fmt_num(m.curation_character_volume_proxy))
    cols[2].metric("Average Delay", fmt_duration(latency.average))

    st.subheader("Rejection Mix")
    if not report.curation_rejection_mix:
        st.info("No rejection records in this lookback window.")
    else:
        mix = pd.DataFrame([{
            "Downstream Action": r.downstream_action,
            "Count": r.count,
        } for r in report.curation_rejection_mix])
        fig = px.pie(mix, names="Downstream Action", values="Count", hole=0.35)
        fig.update_layout(**_PLOTLY_LAYOUT, margin=dict(l=10, r=10, t=10, b=10), height=320)
        st.plotly_chart(fig, width="stretch")

    st.subheader("Curation Delay")
    st.dataframe(
        pd.DataFrame([{
            "Average": fmt_duration(latency.average),
            "Median": fmt_duration(latency.median),
            "P90": fmt_duration(latency.p90),
        }]),
        width="stretch",
        hide_index=True,
    )

    if not report.breakdowns:
        st.info("No per-source curation breakdown available for this lookback window.")
