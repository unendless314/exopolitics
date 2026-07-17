"""Display formatting helpers shared by dashboard views.

Null metrics (e.g. ``[INSUFFICIENT_DATA]`` cases) render as ``N/A`` per
DATA_CONTRACT.md section 5. All formatting happens here so views never
re-derive metric values.
"""

from __future__ import annotations

from typing import Optional

NA = "N/A"

# Lowercase snake_case quadrant values emitted by the analysis decision model,
# mapped to the display labels from DASHBOARD_DESIGN.md section 4.2.
QUADRANT_LABELS: dict[str, str] = {
    "golden_source": "GOLDEN_SOURCE",
    "filtering_burden": "FILTERING_BURDEN",
    "dead_weight": "DEAD_WEIGHT",
    "needle_in_a_haystack": "NEEDLE_IN_A_HAYSTACK",
}


def fmt_pct(value: Optional[float], digits: int = 1) -> str:
    return NA if value is None else f"{value * 100:.{digits}f}%"


def fmt_num(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return NA
    return f"{value:,.{digits}f}"


def fmt_hours(seconds: Optional[float]) -> str:
    if seconds is None:
        return NA
    return f"{seconds / 3600:,.1f} h"


def fmt_duration(seconds: Optional[float]) -> str:
    """Human-readable duration: seconds below a minute, then minutes, then hours."""
    if seconds is None:
        return NA
    if seconds < 120:
        return f"{seconds:,.1f} s"
    if seconds < 7200:
        return f"{seconds / 60:,.1f} min"
    return f"{seconds / 3600:,.1f} h"


def quadrant_label(quadrant: Optional[str]) -> str:
    if quadrant is None:
        return NA
    return QUADRANT_LABELS.get(quadrant, quadrant.upper())
