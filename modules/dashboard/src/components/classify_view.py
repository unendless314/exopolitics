"""Classify view: classification volume, relevance mix, confidence, density.

Renders the ``classify`` report family (``CLASSIFY_MONITOR_REPORT.json``).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders.report_loader import ClassifyReport

from .formatting import fmt_num, fmt_pct

_PLOTLY_LAYOUT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")

_TOPIC_CLASSES = ["core", "adjacent", "irrelevant", "unknown"]
_DENSITY_BUCKETS = ["low", "medium", "high"]
_TOP_N_SOURCES = 15


def _frame(report: ClassifyReport) -> pd.DataFrame:
    return pd.DataFrame([{
        "Source": r.source_title or str(r.source_id),
        "Classify Volume": r.classify_volume,
        "Relevance Rate": r.relevance_rate,
        "Average Confidence": r.average_confidence,
        **{f"topic_{k}": (r.topic_class_breakdown or {}).get(k, 0.0) for k in _TOPIC_CLASSES},
        **{f"density_{k}": (r.content_density_distribution or {}).get(k, 0.0) for k in _DENSITY_BUCKETS},
    } for r in report.breakdowns])


def _stacked_bar(top: pd.DataFrame, prefixes: list[str], title: str) -> go.Figure:
    fig = go.Figure()
    for prefix in prefixes:
        label = prefix.split("_", 1)[1]
        fig.add_trace(go.Bar(name=label, x=top["Source"], y=top[prefix]))
    fig.update_layout(**_PLOTLY_LAYOUT, barmode="stack", title=title,
                      margin=dict(l=10, r=10, t=40, b=10), height=380)
    return fig


def render(report: ClassifyReport) -> None:
    m = report.metrics
    cols = st.columns(3)
    cols[0].metric("Total Classified Items", fmt_num(m.total_classified))
    cols[1].metric("Average Confidence", fmt_pct(m.average_confidence))
    cols[2].metric("Relevance Rate", fmt_pct(m.relevance_rate))

    df = _frame(report)
    if df.empty:
        st.info("No classification data available for this lookback window.")
        return

    df = df.sort_values("Classify Volume", ascending=False)
    top = df.head(_TOP_N_SOURCES).copy()
    if len(df) > _TOP_N_SOURCES:
        others = {"Source": f"Others ({len(df) - _TOP_N_SOURCES} sources)"}
        others["Classify Volume"] = int(df["Classify Volume"].iloc[_TOP_N_SOURCES:].sum())
        for col in df.columns:
            if col.startswith(("topic_", "density_")):
                others[col] = float(df[col].iloc[_TOP_N_SOURCES:].mean())
        others["Relevance Rate"] = None
        others["Average Confidence"] = None
        top = pd.concat([top, pd.DataFrame([others])], ignore_index=True)

    st.subheader("Classify Volume by Source")
    fig = go.Figure(go.Bar(x=top["Source"], y=top["Classify Volume"], marker_color="#00A8E8"))
    fig.update_layout(**_PLOTLY_LAYOUT, margin=dict(l=10, r=10, t=10, b=10), height=320)
    st.plotly_chart(fig, width="stretch")

    st.subheader("Relevance Mix by Source")
    st.plotly_chart(_stacked_bar(top, [f"topic_{k}" for k in _TOPIC_CLASSES], "Topic Class Breakdown"),
                    width="stretch")

    st.subheader("Content Density Distribution by Source")
    st.plotly_chart(_stacked_bar(top, [f"density_{k}" for k in _DENSITY_BUCKETS], "Content Density"),
                    width="stretch")

    st.subheader("Confidence by Source")
    table = df[["Source", "Classify Volume", "Relevance Rate", "Average Confidence"]]
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "Relevance Rate": st.column_config.NumberColumn(format="%.3f"),
            "Average Confidence": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    st.caption("Rate columns are stored as 0–1 fractions; multiply by 100 for percentages.")

    st.subheader("Performance by Source")
    breakdown = pd.DataFrame({
        "Source ID": df["Source"],
        "Relevance Breakdown (Core)": df["topic_core"],
        "Relevance Breakdown (Adj)": df["topic_adjacent"],
        "Relevance Breakdown (Irr)": df["topic_irrelevant"],
        "Relevance Breakdown (Unk)": df["topic_unknown"],
        "Content Density (Low)": df["density_low"],
        "Content Density (Med)": df["density_medium"],
        "Content Density (High)": df["density_high"],
    })
    st.dataframe(
        breakdown,
        width="stretch",
        hide_index=True,
        column_config={
            col: st.column_config.NumberColumn(format="%.3f")
            for col in breakdown.columns if col != "Source ID"
        },
    )
