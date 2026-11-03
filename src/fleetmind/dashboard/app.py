"""FleetMind Streamlit Control Tower.

Launch from the repository root with::

    streamlit run dashboard/app.py -- --database outputs/fleetmind.duckdb

The dashboard is strictly read-only and queries the DuckDB warehouse directly.
Data access and frame shaping live in :mod:`fleetmind.dashboard.data`; the
visual language (tokens, cards, tables and Plotly themes) lives in
:mod:`fleetmind.dashboard.ui`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fleetmind.dashboard import ui
from fleetmind.dashboard.data import (
    anomaly_metrics_frame,
    daily_frame,
    issue_summary,
    list_runs,
    load_run_data,
    route_map_data,
    route_summary_frame,
    with_dimensions,
)

DEFAULT_DB = Path("outputs") / "fleetmind.duckdb"
CHART_HEIGHT = 300
TALL_CHART_HEIGHT = 340
MAP_HEIGHT = 480

PRIORITY_ORDER = ("URGENT", "HIGH", "STANDARD", "LOW")
HANDLING_ORDER = ("AMBIENT", "FRAGILE", "BULK", "REFRIGERATED")
SEVERITY_ORDER = ("CRITICAL", "ERROR", "WARNING", "INFO", "UNKNOWN")
SEVERITY_RANK = {severity: index for index, severity in enumerate(SEVERITY_ORDER)}
FINDING_LABELS = {
    "ARRIVAL_BEFORE_PREVIOUS_DEPARTURE": "Arrival before previous departure",
    "ARITHMETIC_RECONCILIATION_ERROR": "Wait minutes differ from timestamps",
    "COORDINATE_OUT_OF_REGION": "Coordinate outside DFW region",
    "DRIVER_SHIFT_EXCEEDED": "Route end exceeds driver shift",
    "INVALID_FOREIGN_KEY": "Foreign key does not resolve",
    "ROUTE_CAPACITY_EXCEEDED": "Route load exceeds vehicle capacity",
    "STOP_DUPLICATED": "Delivery duplicated across routes",
    "STOP_OMITTED": "Assigned delivery has no route stop",
    "VOLUME_CAPACITY_EXCEEDED": "Route volume over advisory limit",
}


# ---------------------------------------------------------------------------
# Cached, read-only data access
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=8)
def _runs_cached(db_path: str, fingerprint: int) -> pd.DataFrame:
    """List persisted runs; ``fingerprint`` invalidates the cache on writes."""
    return list_runs(Path(db_path))


@st.cache_data(show_spinner="Loading run snapshot...", max_entries=4)
def _run_cached(db_path: str, run_id: str, fingerprint: int) -> dict[str, pd.DataFrame]:
    """Load one run's tables; the warehouse timestamp keeps the cache honest."""
    return load_run_data(Path(db_path), run_id)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _pct(value: Any, digits: int = 1) -> str:
    if _is_missing(value):
        return "n/a"
    return f"{float(value):.{digits}%}"


def _num(value: Any, digits: int = 0) -> str:
    if _is_missing(value):
        return "n/a"
    return f"{float(value):,.{digits}f}"


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if _is_missing(numerator) or _is_missing(denominator):
        return None
    denominator = float(denominator)
    if denominator == 0:
        return None
    return float(numerator) / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    total = precision + recall
    if total == 0:
        return 0.0
    return 2 * precision * recall / total


def _truthy(value: Any) -> bool:
    if _is_missing(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "pass"}
    return bool(value)


def _date_iso(value: Any) -> str:
    if _is_missing(value):
        return ""
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if pd.isna(timestamp):
        return ""
    return timestamp.date().isoformat()


def _date_only(value: Any) -> str:
    return _date_iso(value) or "n/a"


def _duration_text(minutes: Any) -> str:
    if _is_missing(minutes):
        return "n/a"
    minutes = float(minutes)
    if minutes < 1:
        return f"{minutes * 60:.0f} s"
    if minutes < 60:
        return f"{minutes:.1f} min"
    return f"{minutes / 60:.1f} h"


def _truncate(value: Any, limit: int = 140) -> str:
    text = "" if _is_missing(value) else str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _clean_issue_message(message: Any) -> str:
    """Normalise validator prose for display without changing its meaning."""
    text = "" if _is_missing(message) else str(message)
    text = re.sub(r"np\.(?:float|int)\d*\(([^()]*)\)", r"\1", text)
    return " ".join(text.split())


def _shorten(value: Any, limit: int = 38) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip(" ,;:") + "…"


def _issue_finding(issue_type: Any, message: Any) -> str:
    """One-line, width-safe finding; the raw validator message stays in details."""
    key = "" if _is_missing(issue_type) else str(issue_type).upper()
    text = _clean_issue_message(message)

    if key == "VOLUME_CAPACITY_EXCEEDED":
        match = re.search(
            r"route volume ([\d.]+) m3 exceeds advisory vehicle volume ([\d.]+)",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"{float(match.group(1)):,.1f} m³ over {float(match.group(2)):,.0f} m³ advisory"
    elif key == "ROUTE_CAPACITY_EXCEEDED":
        match = re.search(
            r"route load ([\d.]+) units exceeds vehicle capacity ([\d.]+)",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"{float(match.group(1)):,.0f} units over {float(match.group(2)):,.0f} capacity"
    elif key == "ARITHMETIC_RECONCILIATION_ERROR":
        match = re.search(
            r"actual_wait_minutes=([\d.]+).*\(([\d.]+)\)",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"Wait {float(match.group(1)):,.1f} vs {float(match.group(2)):,.1f} min"
    elif key == "INVALID_FOREIGN_KEY":
        match = re.search(
            r"\.(\w+)='[^']*' does not exist in ([\w.]+)",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"{match.group(1)} missing in {match.group(2).split('.')[0]}"
    elif key == "STOP_DUPLICATED":
        match = re.search(r"duplicate ([\w-]+)", text, re.IGNORECASE)
        if match:
            return f"Duplicate {match.group(1)}"
        if "multiple route stops" in text.lower():
            return "Delivery has multiple route stops"
    elif key == "COORDINATE_OUT_OF_REGION":
        match = re.search(
            r"(latitude|longitude)=(-?[\d.]+) is outside",
            text,
            re.IGNORECASE,
        )
        if match:
            return f"{match.group(1).title()} {match.group(2)} outside DFW region"

    label = FINDING_LABELS.get(key) or ui.ISSUE_TYPE_LABELS.get(key) or ui.humanize(key or text)
    return _shorten(label)


def _observed_summary(issue_type: Any, observed: Any) -> str:
    """Concise detector evidence; full values stay in the detail inspector."""
    key = "" if _is_missing(issue_type) else str(issue_type).upper()
    text = _clean_issue_message(observed)

    if key == "ROUTE_CAPACITY_EXCEEDED":
        match = re.search(
            r"load_units=([\d.]+) > capacity_units=([\d.]+)", text, re.IGNORECASE
        )
        if match:
            return f"{float(match.group(1)):,.0f} vs {float(match.group(2)):,.0f} units"
    elif key == "STOP_DUPLICATED":
        match = re.search(r"stop_count=(\d+)", text, re.IGNORECASE)
        if match:
            return f"{int(match.group(1))} route stops per delivery"
    elif key == "DRIVER_SHIFT_EXCEEDED":
        return "Route ends after shift"
    elif key == "COORDINATE_OUT_OF_REGION":
        match = re.search(
            r"latitude=(-?[\d.]+), longitude=(-?[\d.]+)", text, re.IGNORECASE
        )
        if match:
            return f"{float(match.group(1)):.2f}, {float(match.group(2)):.2f} outside region"
    elif key == "STOP_OMITTED":
        return "Assigned, no route stop"
    elif key == "ARRIVAL_BEFORE_PREVIOUS_DEPARTURE":
        return "Arrival before prior departure"

    return _shorten(text or ui.humanize(key) or "No value", 32)


def _injected_summary(issue_type: Any, injected: Any) -> str:
    """Concise ground-truth description; full text stays in the inspector."""
    key = "" if _is_missing(issue_type) else str(issue_type).upper()
    text = _clean_issue_message(injected)

    if key == "ROUTE_CAPACITY_EXCEEDED":
        match = re.search(
            r"demand_units=([\d.]+).*capacity=([\d.]+)", text, re.IGNORECASE
        )
        if match:
            return (
                f"Demand {float(match.group(1)):,.0f} vs "
                f"{float(match.group(2)):,.0f} capacity"
            )
    elif key == "STOP_DUPLICATED":
        match = re.search(r"at sequence (\d+)", text, re.IGNORECASE)
        if match:
            return f"Second stop at sequence {match.group(1)}"
        return "Second route stop added"
    elif key == "STOP_OMITTED":
        return "Route stop removed"
    elif key == "ARRIVAL_BEFORE_PREVIOUS_DEPARTURE":
        return "Arrival moved earlier"
    elif key == "DRIVER_SHIFT_EXCEEDED":
        return "Shift end moved earlier"
    elif key == "COORDINATE_OUT_OF_REGION":
        match = re.search(
            r"latitude=(-?[\d.]+), longitude=(-?[\d.]+)", text, re.IGNORECASE
        )
        if match:
            return f"{float(match.group(1)):.2f}, {float(match.group(2)):.2f} outside region"

    return _shorten(text or ui.humanize(key) or "No value", 40)


def _pair_text(left: Any, right: Any, digits: int = 0) -> str:
    if _is_missing(left) or _is_missing(right):
        return "—"
    return f"{float(left):,.{digits}f} / {float(right):,.{digits}f}"


def _safe_json(value: Any) -> Any:
    if _is_missing(value):
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------
def _metric_row(
    metrics: pd.DataFrame,
    category: str,
    name: str,
    dimensions: str = "",
) -> pd.Series | None:
    if metrics is None or metrics.empty:
        return None
    if not {"category", "metric_name", "value"}.issubset(metrics.columns):
        return None
    dims = (
        metrics["dimensions"].fillna("").astype(str)
        if "dimensions" in metrics
        else pd.Series([""] * len(metrics), index=metrics.index)
    )
    found = metrics[
        (metrics["category"].astype(str) == category)
        & (metrics["metric_name"].astype(str) == name)
        & (dims == dimensions)
    ]
    return found.iloc[0] if len(found) else None


def _metric_value(metrics: pd.DataFrame, category: str, name: str) -> float | None:
    row = _metric_row(metrics, category, name)
    if row is None or _is_missing(row.get("value")):
        return None
    return float(row["value"])


def _metric_help(
    metrics: pd.DataFrame,
    category: str,
    name: str,
    label: str,
    dimensions: str = "",
) -> str:
    row = _metric_row(metrics, category, name, dimensions)
    if row is None:
        return label
    numerator = row.get("numerator")
    denominator = row.get("denominator")
    if _is_missing(numerator) or _is_missing(denominator):
        return label
    return f"{label} ({_num(numerator)} ÷ {_num(denominator)})."


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame is None or frame.empty or column not in frame:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _sum(frame: pd.DataFrame, column: str) -> float:
    values = _numeric(frame, column)
    return float(values.fillna(0).sum()) if not values.empty else 0.0


def _status_count(frame: pd.DataFrame, status: str) -> int | None:
    if frame is None or frame.empty or "status" not in frame:
        return None
    return int((frame["status"].astype(str).str.upper() == status).sum())


def _assigned_count(tables: dict[str, pd.DataFrame]) -> int | None:
    assignments = tables.get("delivery_assignments", pd.DataFrame())
    count = _status_count(assignments, "ASSIGNED")
    if count is not None:
        return count
    outcomes = tables.get("delivery_outcomes", pd.DataFrame())
    return len(outcomes) if not outcomes.empty else None


# ---------------------------------------------------------------------------
# Dimension helpers
# ---------------------------------------------------------------------------
def _dimension_frame(
    metrics: pd.DataFrame,
    category: str,
    name: str,
) -> pd.DataFrame:
    if metrics is None or metrics.empty:
        return pd.DataFrame()
    frame = with_dimensions(metrics, category, name)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["entity"] = frame["entity"].astype(str)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["value"])
    frame["label"] = frame["entity"].map(ui.humanize)
    return frame


def _dimension_bar(
    metrics: pd.DataFrame,
    category: str,
    name: str,
    *,
    axis_title: str,
    orientation: str = "h",
    value_kind: str = "rate",
    color: str = ui.ACCENT,
    limit: int | None = None,
    entity_order: Sequence[str] | None = None,
    ascending: bool = True,
) -> go.Figure | None:
    frame = _dimension_frame(metrics, category, name)
    if frame.empty:
        return None

    if entity_order:
        ordered = [value for value in entity_order if value in set(frame["entity"])]
        ordered.extend(value for value in frame["entity"] if value not in ordered)
        frame["_order"] = pd.Categorical(frame["entity"], categories=ordered, ordered=True)
        frame = frame.sort_values("_order")
    elif orientation == "h":
        if limit:
            frame = frame.sort_values("value", ascending=False).head(limit)
        frame = frame.sort_values("value", ascending=ascending)
    else:
        if limit:
            frame = frame.nlargest(limit, "value")
        frame = frame.sort_values("value", ascending=ascending)

    values = frame["value"].astype(float)
    labels = frame["label"].tolist()

    if value_kind == "rate":
        text = [_pct(value) for value in values]
        tickformat = ".0%"
        axis_range = [0, 1.15] if orientation == "h" else [0, 1.12]
        hover = (
            "%{y}<br>Rate %{customdata:.1%}<extra></extra>"
            if orientation == "h"
            else "%{x}<br>Rate %{customdata:.1%}<extra></extra>"
        )
    else:
        text = [f"{value:,.0f}" for value in values]
        tickformat = None
        maximum = float(values.max()) if len(values) else 0.0
        axis_range = [0, maximum * 1.18 if maximum > 0 else 1.0]
        hover = (
            "%{y}: %{customdata:,.0f}<extra></extra>"
            if orientation == "h"
            else "%{x}: %{customdata:,.0f}<extra></extra>"
        )

    if orientation == "h":
        figure = go.Figure(
            go.Bar(
                x=values,
                y=labels,
                orientation="h",
                marker_color=color,
                marker_line_width=0,
                text=text,
                textposition="outside",
                cliponaxis=False,
                textfont={"color": ui.MUTED, "size": 11},
                customdata=values,
                hovertemplate=hover,
            )
        )
        figure.update_xaxes(title_text=axis_title, range=axis_range, tickformat=tickformat)
        figure.update_yaxes(title_text="", automargin=True)
    else:
        figure = go.Figure(
            go.Bar(
                x=labels,
                y=values,
                marker_color=color,
                marker_line_width=0,
                text=text,
                textposition="outside",
                cliponaxis=False,
                textfont={"color": ui.MUTED, "size": 11},
                customdata=values,
                hovertemplate=hover,
            )
        )
        figure.update_yaxes(title_text=axis_title, range=axis_range, tickformat=tickformat)
        figure.update_xaxes(title_text="", automargin=True)

    figure.update_layout(showlegend=False, bargap=0.45)
    return figure


def _simple_count_bar(
    labels: Sequence[str],
    values: Sequence[int],
    *,
    axis_title: str,
    color: str = ui.DANGER,
) -> go.Figure | None:
    if not values or max(values) <= 0:
        return None
    maximum = max(values)
    figure = go.Figure(
        go.Bar(
            x=list(values),
            y=list(labels),
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            text=[f"{value:,}" for value in values],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": ui.MUTED, "size": 11},
            customdata=list(values),
            hovertemplate="%{y}: %{customdata:,.0f}<extra></extra>",
        )
    )
    figure.update_xaxes(title_text=axis_title, range=[0, maximum * 1.2])
    figure.update_yaxes(title_text="", automargin=True)
    figure.update_layout(showlegend=False, bargap=0.45)
    return figure


# ---------------------------------------------------------------------------
# Overview charts
# ---------------------------------------------------------------------------
def _daily_delivery_figure(daily: pd.DataFrame) -> go.Figure | None:
    if daily is None or daily.empty or "service_date" not in daily:
        return None
    frame = daily.copy()
    frame["service_date"] = pd.to_datetime(frame["service_date"], errors="coerce")
    frame = frame.dropna(subset=["service_date"]).sort_values("service_date")
    if frame.empty:
        return None

    figure = go.Figure()
    for column, name, color in (
        ("delivered", "Delivered", ui.SUCCESS),
        ("failed", "Failed", ui.DANGER),
    ):
        if column not in frame:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").fillna(0)
        figure.add_bar(
            x=frame["service_date"],
            y=values,
            name=name,
            marker_color=color,
            marker_line_width=0,
            hovertemplate=f"%{{x|%b %d}}<br>{name}: %{{y:,.0f}}<extra></extra>",
        )
    if not figure.data:
        return None

    figure.update_layout(barmode="group", bargap=0.22, bargroupgap=0.08)
    figure.update_xaxes(tickformat="%b %d", nticks=8, tickangle=-30)
    figure.update_yaxes(title_text="Deliveries")
    return figure


# ---------------------------------------------------------------------------
# Routing map
# ---------------------------------------------------------------------------
def _route_palette(route_ids: Sequence[Any]) -> dict[str, str]:
    """Stable, shared colours for route lines and the map legend."""
    ordered = sorted(
        {
            str(route_id)
            for route_id in route_ids
            if not _is_missing(route_id) and str(route_id)
        }
    )
    return {
        route_id: ui.ROUTE_COLORS[index % len(ui.ROUTE_COLORS)]
        for index, route_id in enumerate(ordered)
    }


def _stop_hover(route_id: str, row: Any, status: str) -> str:
    def value(name: str) -> Any:
        return row.get(name) if isinstance(row, pd.Series) else getattr(row, name, None)

    site = value("site_id")
    if _is_missing(site) or not str(site):
        fallback = value("stop_id")
        site = (
            fallback
            if not _is_missing(fallback) and str(fallback)
            else "Stop"
        )
    city = value("city")
    city_text = (
        f" · {ui.escape(city)}"
        if not _is_missing(city) and str(city)
        else ""
    )
    sequence = value("stop_sequence")
    sequence_text = f"{int(sequence)}" if not _is_missing(sequence) else "—"
    planned = ui.format_timestamp(value("planned_arrival"))
    status_label = ui.STATUS_LABELS.get(status, ui.humanize(status))
    return (
        f"<b>{ui.escape(route_id)}</b>"
        f"<br>Stop {sequence_text} · {ui.escape(site)}{city_text}"
        f"<br>{status_label} · Planned {planned}"
    )


def _route_map(
    tables: dict[str, pd.DataFrame],
    service_date: str,
    palette: dict[str, str],
) -> go.Figure | None:
    map_data = route_map_data(tables, service_date)
    stops = map_data["stops"]
    depot = map_data["depot"]
    if stops.empty or not {"latitude", "longitude", "route_id"}.issubset(stops.columns):
        return None
    stops = stops.dropna(subset=["latitude", "longitude"]).copy()
    if stops.empty:
        return None

    stops["route_id"] = stops["route_id"].astype(str)
    stops["latitude"] = pd.to_numeric(stops["latitude"], errors="coerce")
    stops["longitude"] = pd.to_numeric(stops["longitude"], errors="coerce")
    stops = stops.dropna(subset=["latitude", "longitude"])
    if stops.empty:
        return None

    stops["_status"] = (
        stops["status"].fillna("ASSIGNED").astype(str).str.upper()
        if "status" in stops
        else "ASSIGNED"
    )
    depot_lat = float(depot["latitude"].iloc[0]) if not depot.empty else 32.7767
    depot_lon = float(depot["longitude"].iloc[0]) if not depot.empty else -96.797
    figure = go.Figure()

    for route_id, group in stops.groupby("route_id", sort=True):
        group = (
            group.sort_values("stop_sequence", kind="stable")
            if "stop_sequence" in group
            else group
        )
        color = palette.get(str(route_id), ui.NEUTRAL)
        figure.add_trace(
            go.Scatter(
                x=[depot_lon, *group["longitude"].tolist(), depot_lon],
                y=[depot_lat, *group["latitude"].tolist(), depot_lat],
                mode="lines",
                line={"width": 1.7, "color": color},
                opacity=0.8,
                name=str(route_id),
                legendgroup=str(route_id),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    ordered_statuses = [*ui.STATUS_LABELS, "UNKNOWN"]
    extra_statuses = sorted(set(stops["_status"]) - set(ordered_statuses))
    for status in (*ordered_statuses, *extra_statuses):
        group = stops[stops["_status"] == status]
        if group.empty:
            continue
        figure.add_trace(
            go.Scatter(
                x=group["longitude"],
                y=group["latitude"],
                mode="markers",
                marker={
                    "size": 8,
                    "color": ui.STATUS_COLORS.get(status, ui.NEUTRAL),
                    "symbol": ui.STATUS_SYMBOLS.get(status, "circle"),
                    "line": {"width": 1, "color": ui.SURFACE},
                },
                text=[
                    _stop_hover(str(row.route_id), row, status)
                    for row in group.itertuples(index=False)
                ],
                hovertemplate="%{text}<extra></extra>",
                name=ui.STATUS_LABELS.get(status, ui.humanize(status)),
                showlegend=False,
            )
        )

    figure.add_trace(
        go.Scatter(
            x=[depot_lon],
            y=[depot_lat],
            mode="markers+text",
            marker={
                "symbol": "star",
                "size": 15,
                "color": ui.WARNING,
                "line": {"width": 1, "color": ui.SURFACE},
            },
            text=["Depot"],
            textposition="top center",
            textfont={"color": ui.WARNING, "size": 11},
            hovertemplate=f"Depot<br>{depot_lat:.4f}, {depot_lon:.4f}<extra></extra>",
            name="Depot",
            showlegend=False,
        )
    )

    latitudes = [depot_lat, *stops["latitude"].tolist()]
    longitudes = [depot_lon, *stops["longitude"].tolist()]
    lat_min, lat_max = min(latitudes), max(latitudes)
    lon_min, lon_max = min(longitudes), max(longitudes)
    lat_padding = max((lat_max - lat_min) * 0.08, 0.008)
    lon_padding = max((lon_max - lon_min) * 0.08, 0.008)
    figure.update_xaxes(range=[lon_min - lon_padding, lon_max + lon_padding])
    figure.update_yaxes(range=[lat_min - lat_padding, lat_max + lat_padding])
    return figure


# ---------------------------------------------------------------------------
# Execution charts
# ---------------------------------------------------------------------------
def _outcome_figure(counts: dict[str, int]) -> go.Figure | None:
    order = ("DELIVERED", "FAILED", "UNSERVED")
    items = [(status, counts.get(status, 0)) for status in order if counts.get(status, 0) > 0]
    items.extend(
        (status, count)
        for status, count in counts.items()
        if status not in order and count > 0
    )
    if not items:
        return None
    labels = [ui.STATUS_LABELS.get(status, ui.humanize(status)) for status, _ in items]
    values = [count for _, count in items]
    colors = [ui.STATUS_COLORS.get(status, ui.NEUTRAL) for status, _ in items]
    figure = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.64,
            sort=False,
            direction="clockwise",
            marker={"colors": colors, "line": {"color": ui.SURFACE, "width": 2}},
            textinfo="none",
            hovertemplate="%{label}: %{value:,.0f} (%{percent})<extra></extra>",
        )
    )
    figure.add_annotation(
        text=(
            f"<b>{sum(values):,}</b><br>"
            f"<span style='font-size:11px;color:{ui.MUTED}'>outcomes</span>"
        ),
        showarrow=False,
        font={"color": ui.TEXT, "size": 20},
    )
    return figure


def _failure_reason_figure(outcomes: pd.DataFrame) -> go.Figure | None:
    if outcomes is None or outcomes.empty or "status" not in outcomes:
        return None
    failed = outcomes[outcomes["status"].astype(str).str.upper() == "FAILED"]
    if failed.empty or "reason_code" not in failed:
        return None
    counts = failed["reason_code"].fillna("UNKNOWN").astype(str).value_counts()
    return _simple_count_bar(
        [ui.humanize(reason) for reason in counts.index],
        [int(value) for value in counts.tolist()],
        axis_title="Deliveries",
    )


def _planned_actual_figure(routes: pd.DataFrame) -> go.Figure | None:
    if routes is None or routes.empty:
        return None
    planned_service = _sum(routes, "service_minutes") / 60
    actual_service = _sum(routes, "actual_service_minutes") / 60
    planned_wait = _sum(routes, "wait_minutes") / 60
    actual_wait = _sum(routes, "actual_wait_minutes") / 60
    if not any((planned_service, actual_service, planned_wait, actual_wait)):
        return None

    categories = ["Service", "Wait"]
    planned = [planned_service, planned_wait]
    actual = [actual_service, actual_wait]
    figure = go.Figure()
    for name, values, color in (
        ("Planned", planned, ui.MUTED),
        ("Actual", actual, ui.ACCENT),
    ):
        figure.add_bar(
            x=categories,
            y=values,
            name=name,
            marker_color=color,
            marker_line_width=0,
            text=[f"{value:,.1f} h" for value in values],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": ui.MUTED, "size": 11},
            hovertemplate=f"%{{x}} · {name}: %{{y:,.2f}} h<extra></extra>",
        )
    figure.update_layout(barmode="group", bargap=0.28, bargroupgap=0.08)
    figure.update_yaxes(title_text="Hours", range=[0, max(*planned, *actual) * 1.18 or 1.0])
    return figure


# ---------------------------------------------------------------------------
# Data-quality charts
# ---------------------------------------------------------------------------
def _severity_comparison_figure(
    clean_issues: pd.DataFrame,
    dirty_issues: pd.DataFrame,
) -> go.Figure | None:
    if clean_issues.empty and dirty_issues.empty:
        return None
    severities = [
        severity
        for severity in SEVERITY_ORDER
        if any(
            "severity" in frame
            and (frame["severity"].astype(str).str.upper() == severity).any()
            for frame in (clean_issues, dirty_issues)
        )
    ]
    if not severities:
        return None

    snapshots = (("Clean snapshot", clean_issues), ("Anomaly snapshot", dirty_issues))
    figure = go.Figure()
    for severity in severities:
        values = [
            int((frame["severity"].astype(str).str.upper() == severity).sum())
            if not frame.empty and "severity" in frame
            else 0
            for _, frame in snapshots
        ]
        figure.add_bar(
            x=[label for label, _ in snapshots],
            y=values,
            name=severity.title(),
            marker_color=ui.SEVERITY_COLORS.get(severity, ui.NEUTRAL),
            marker_line_width=0,
            text=[f"{value:,}" if value else "" for value in values],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": ui.MUTED, "size": 11},
            hovertemplate=f"%{{x}}<br>{severity.title()}: %{{y:,}}<extra></extra>",
        )
    maximum = max(
        (
            int((frame["severity"].astype(str).str.upper() == severity).sum())
            for _, frame in snapshots
            if not frame.empty and "severity" in frame
            for severity in severities
        ),
        default=0,
    )
    figure.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    figure.update_yaxes(title_text="Issues", range=[0, maximum * 1.2 if maximum else 1.0])
    return figure


def _issue_type_figure(issues: pd.DataFrame) -> go.Figure | None:
    summary = issue_summary(issues)
    if summary.empty:
        return None
    summary = summary.copy()
    summary["severity"] = summary["severity"].fillna("UNKNOWN").astype(str).str.upper()
    pivot = summary.pivot_table(
        index="issue_type",
        columns="severity",
        values="count",
        aggfunc="sum",
        fill_value=0,
    )
    if pivot.empty:
        return None
    totals = pivot.sum(axis=1)
    pivot = pivot.loc[totals.sort_values(ascending=True).index]
    severities = [severity for severity in SEVERITY_ORDER if severity in pivot.columns]
    labels = [ui.humanize(value) for value in pivot.index]

    figure = go.Figure()
    for severity in severities:
        values = pivot[severity].astype(int).tolist()
        figure.add_bar(
            x=values,
            y=labels,
            orientation="h",
            name=severity.title(),
            marker_color=ui.SEVERITY_COLORS.get(severity, ui.NEUTRAL),
            marker_line_width=0,
            text=[f"{value:,}" if value else "" for value in values],
            textposition="inside",
            insidetextanchor="middle",
            textfont={"color": ui.TEXT, "size": 10},
            hovertemplate=(
                f"%{{y}}<br>{severity.title()}: %{{x:,}}<extra></extra>"
            ),
        )
    figure.update_layout(barmode="stack", bargap=0.35)
    figure.update_xaxes(title_text="Issues")
    figure.update_yaxes(title_text="")
    return figure


# ---------------------------------------------------------------------------
# Anomaly charts
# ---------------------------------------------------------------------------
def _confusion_figure(metrics: pd.DataFrame) -> go.Figure | None:
    if metrics is None or metrics.empty:
        return None
    frame = metrics.copy()
    frame["issue_type"] = frame["issue_type"].astype(str)
    frame = frame.sort_values("issue_type")
    rows = [ui.humanize(value) for value in frame["issue_type"]]
    columns = ["True positive", "False positive", "False negative"]
    values: list[list[int]] = []
    text: list[list[str]] = []
    for _, row in frame.iterrows():
        counts = [
            int(row.get("tp", 0) or 0),
            int(row.get("fp", 0) or 0),
            int(row.get("fn", 0) or 0),
        ]
        values.append(counts)
        text.append([f"{value:,}" for value in counts])
    maximum = max((max(row) for row in values), default=0) or 1

    return go.Figure(
        go.Heatmap(
            z=values,
            x=columns,
            y=rows,
            text=text,
            texttemplate="%{text}",
            textfont={"color": ui.TEXT, "size": 13},
            colorscale=[
                [0, "rgba(79, 140, 255, 0.06)"],
                [0.55, "rgba(79, 140, 255, 0.35)"],
                [1, ui.ACCENT],
            ],
            zmin=0,
            zmax=maximum,
            showscale=False,
            xgap=4,
            ygap=4,
            hovertemplate="%{y}<br>%{x}: %{z:,}<extra></extra>",
        )
    )


def _truth_detection_figure(
    truth: pd.DataFrame,
    detections: pd.DataFrame,
) -> go.Figure | None:
    if truth.empty and detections.empty:
        return None
    truth_counts = (
        truth["issue_type"].astype(str).value_counts()
        if not truth.empty and "issue_type" in truth
        else pd.Series(dtype="int64")
    )
    detection_counts = (
        detections["issue_type"].astype(str).value_counts()
        if not detections.empty and "issue_type" in detections
        else pd.Series(dtype="int64")
    )
    types = sorted(set(truth_counts.index) | set(detection_counts.index))
    if not types:
        return None
    counts = pd.DataFrame(
        {
            "truth": [int(truth_counts.get(value, 0)) for value in types],
            "detected": [int(detection_counts.get(value, 0)) for value in types],
        },
        index=types,
    ).sort_values(["truth", "detected"], ascending=True)
    labels = [ui.humanize(value) for value in counts.index]

    figure = go.Figure()
    for name, column, color in (
        ("Injected", "truth", ui.MUTED),
        ("Detected", "detected", ui.SUCCESS),
    ):
        values = counts[column].astype(int).tolist()
        figure.add_bar(
            x=values,
            y=labels,
            orientation="h",
            name=name,
            marker_color=color,
            marker_line_width=0,
            text=[f"{value:,}" if value else "" for value in values],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": ui.MUTED, "size": 11},
            hovertemplate=f"%{{y}} · {name}: %{{x:,}}<extra></extra>",
        )
    maximum = int(counts[["truth", "detected"]].to_numpy().max()) if len(counts) else 0
    figure.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    figure.update_xaxes(title_text="Entities", range=[0, maximum * 1.2 if maximum else 1.0])
    figure.update_yaxes(title_text="")
    return figure


def _anomaly_quality_figure(metrics: pd.DataFrame) -> go.Figure | None:
    if metrics is None or metrics.empty:
        return None
    frame = metrics.copy()
    frame["label"] = frame["issue_type"].map(ui.humanize)
    frame = frame.sort_values("issue_type")

    figure = go.Figure()
    for column, name, color in (
        ("precision", "Precision", ui.INFO),
        ("recall", "Recall", ui.ACCENT),
        ("f1", "F1", ui.SUCCESS),
    ):
        if column not in frame:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").fillna(0).tolist()
        figure.add_bar(
            x=frame["label"],
            y=values,
            name=name,
            marker_color=color,
            marker_line_width=0,
            text=[_pct(value) for value in values],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": ui.MUTED, "size": 10},
            hovertemplate=f"%{{x}}<br>{name}: %{{y:.1%}}<extra></extra>",
        )
    if not figure.data:
        return None
    figure.update_layout(barmode="group", bargap=0.24, bargroupgap=0.08)
    figure.update_yaxes(title_text="Score", range=[0, 1.12], tickformat=".0%")
    figure.update_xaxes(tickangle=-20)
    return figure


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _render_overview(
    tables: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    run_id: str,
) -> None:
    ui.section_header(
        "01",
        "Run performance",
        "Delivery service, on-time performance, capacity and fleet utilisation "
        "for the selected snapshot.",
    )
    if metrics.empty:
        ui.status_banner(
            "Performance metrics are unavailable",
            "This snapshot has no report_metrics rows; outcome tables and "
            "validation views may still be available.",
            "warning",
        )

    outcomes = tables.get("delivery_outcomes", pd.DataFrame())
    delivered = _metric_value(metrics, "service", "delivered_deliveries")
    failed = _metric_value(metrics, "service", "failed_deliveries")
    unserved = _metric_value(metrics, "service", "unserved_deliveries")
    requested = _metric_value(metrics, "service", "total_deliveries")

    if delivered is None:
        delivered = _status_count(outcomes, "DELIVERED")
    if failed is None:
        failed = _status_count(outcomes, "FAILED")
    if unserved is None:
        unserved = _status_count(outcomes, "UNSERVED")
    if requested is None and not outcomes.empty:
        requested = len(outcomes)

    service_rate = _metric_value(metrics, "service", "delivery_service_rate")
    if service_rate is None:
        service_rate = _ratio(delivered, requested)
    on_time_rate = _metric_value(metrics, "service", "on_time_rate")
    if on_time_rate is None:
        on_time = (
            int(
                outcomes.loc[
                    outcomes["status"].astype(str).str.upper() == "DELIVERED", "on_time"
                ]
                .fillna(False)
                .map(bool)
                .sum()
            )
            if not outcomes.empty
            and "status" in outcomes
            and "on_time" in outcomes
            else None
        )
        on_time_rate = _ratio(on_time, delivered)
    capacity_rate = _metric_value(metrics, "routing", "capacity_utilization_units")
    if capacity_rate is None:
        routes = tables.get("routes", pd.DataFrame())
        capacity_rate = _ratio(_sum(routes, "load_units"), _sum(routes, "capacity_units"))
    fleet_rate = _metric_value(metrics, "fleet", "fleet_utilization")
    failure_rate = _metric_value(metrics, "service", "failure_rate")
    if failure_rate is None:
        failure_rate = _ratio(failed, _assigned_count(tables))

    ui.metric_grid(
        [
            {
                "label": "Service rate",
                "value": _pct(service_rate),
                "help": _metric_help(
                    metrics,
                    "service",
                    "delivery_service_rate",
                    "Delivered outcomes ÷ requested deliveries",
                ),
            },
            {
                "label": "On-time rate",
                "value": _pct(on_time_rate),
                "help": _metric_help(
                    metrics,
                    "service",
                    "on_time_rate",
                    "On-time delivered outcomes ÷ delivered outcomes",
                ),
            },
            {
                "label": "Capacity utilization",
                "value": _pct(capacity_rate),
                "help": _metric_help(
                    metrics,
                    "routing",
                    "capacity_utilization_units",
                    "Assigned load units ÷ route capacity units",
                ),
            },
            {
                "label": "Fleet utilization",
                "value": _pct(fleet_rate),
                "help": _metric_help(
                    metrics,
                    "fleet",
                    "fleet_utilization",
                    "Vehicles used ÷ active vehicles",
                ),
            },
            {
                "label": "Unserved deliveries",
                "value": _num(unserved),
                "help": _metric_help(
                    metrics,
                    "service",
                    "unserved_rate",
                    "Unserved assignments ÷ requested deliveries",
                ),
            },
            {
                "label": "Failure rate",
                "value": _pct(failure_rate),
                "help": _metric_help(
                    metrics,
                    "service",
                    "failure_rate",
                    "Failed outcomes ÷ assigned deliveries",
                ),
            },
        ]
    )
    st.caption(
        "Rates use different denominators: service rate is delivered ÷ requested, "
        "failure rate is failed ÷ assigned, and on-time rate is on-time delivered ÷ delivered."
    )

    daily = daily_frame(metrics)
    priority = _dimension_bar(
        metrics,
        "service_priority",
        "delivery_service_rate",
        axis_title="Service rate",
        orientation="v",
        value_kind="rate",
        color=ui.ACCENT,
        entity_order=PRIORITY_ORDER,
    )

    left, right = st.columns((1.45, 1.0), gap="medium")
    with left:
        with ui.panel("Daily execution", "Delivered and failed deliveries by service date"):
            figure = _daily_delivery_figure(daily)
            if figure is None:
                ui.empty_state(
                    "No daily delivery metrics",
                    "This run does not contain daily delivered or failed counts.",
                )
            else:
                ui.plot(figure, CHART_HEIGHT)
                st.caption(
                    "Failure counts share the same axis as delivered volume and can "
                    "appear small; hover for exact values."
                )
    with right:
        with ui.panel("Service rate by priority", "Delivery service rate by priority class"):
            if priority is None:
                ui.empty_state(
                    "No priority breakdown",
                    "Priority service metrics are not available for this snapshot.",
                )
            else:
                ui.plot(priority, CHART_HEIGHT)

    city = _dimension_bar(
        metrics,
        "site_city",
        "delivered_deliveries",
        axis_title="Deliveries",
        orientation="h",
        value_kind="count",
        color=ui.ACCENT,
        limit=8,
        ascending=True,
    )
    handling = _dimension_bar(
        metrics,
        "service_handling",
        "delivery_service_rate",
        axis_title="Service rate",
        orientation="v",
        value_kind="rate",
        color=ui.SUCCESS,
        entity_order=HANDLING_ORDER,
    )

    left, right = st.columns((1.45, 1.0), gap="medium")
    with left:
        with ui.panel("Delivered volume by city", "Top eight destination cities"):
            if city is None:
                ui.empty_state(
                    "No city breakdown",
                    "City-level delivered volume is not available for this snapshot.",
                )
            else:
                ui.plot(city, CHART_HEIGHT)
    with right:
        with ui.panel(
            "Service rate by handling class",
            "Delivery service rate for each handling class",
        ):
            if handling is None:
                ui.empty_state(
                    "No handling breakdown",
                    "Handling-class service metrics are not available for this snapshot.",
                )
            else:
                ui.plot(handling, CHART_HEIGHT)

    with st.expander("Run metadata, stage timings and audit trail"):
        _render_run_details(tables, run_id)


def _render_route_workload(day_routes: pd.DataFrame) -> None:
    if day_routes.empty:
        ui.empty_state(
            "No routes on this date",
            "Choose another service date or inspect a different run snapshot.",
        )
        return

    view = day_routes.copy()
    view["vehicle_type"] = view["vehicle_type"].fillna("—").map(ui.humanize)
    view["load_capacity"] = [
        _pair_text(load, capacity)
        for load, capacity in zip(
            view.get("load_units", pd.Series([None] * len(view))),
            view.get("capacity_units", pd.Series([None] * len(view))),
            strict=True,
        )
    ]
    view["capacity_utilization"] = pd.to_numeric(
        view.get("capacity_utilization"), errors="coerce"
    )
    view = view.sort_values("route_id")

    config = {
        "route_id": st.column_config.TextColumn("Route", width=105),
        "vehicle_id": st.column_config.TextColumn("Vehicle", width=90),
        "vehicle_type": st.column_config.TextColumn("Type", width=95),
        "stop_count": st.column_config.NumberColumn("Stops", format="%d", width=60),
        "delivered_stops": st.column_config.NumberColumn(
            "Delivered", format="%d", width=75
        ),
        "failed_stops": st.column_config.NumberColumn("Failed", format="%d", width=65),
        "load_capacity": st.column_config.TextColumn("Load / capacity", width=105),
        "capacity_utilization": st.column_config.ProgressColumn(
            "Load factor",
            help="Assigned load units ÷ route capacity units.",
            min_value=0.0,
            max_value=1.0,
            format="percent",
            width=115,
            color=ui.ACCENT,
        ),
        "distance_km": st.column_config.NumberColumn(
            "Distance (km)", format="%.1f", width=95
        ),
        "shift_overrun_minutes": st.column_config.NumberColumn(
            "Overrun (min)", format="%.0f", width=90
        ),
    }
    ui.table(
        view,
        columns=[
            "route_id",
            "vehicle_id",
            "vehicle_type",
            "stop_count",
            "delivered_stops",
            "failed_stops",
            "load_capacity",
            "capacity_utilization",
            "distance_km",
            "shift_overrun_minutes",
        ],
        config=config,
        height=460,
        empty_title="No routes on this date",
    )
    st.caption(
        "Load factor is assigned load units ÷ route capacity. Overrun is actual "
        "end time beyond the planned shift window."
    )


def _render_routing(
    tables: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    run_id: str,
) -> None:
    ui.section_header(
        "02",
        "Network & capacity",
        "Depot-to-stop geography, route workload, vehicle mix and shift reliability "
        "for a selected service date.",
    )
    summary = route_summary_frame(tables)
    dates = _filter_dates(summary)
    if not dates:
        ui.status_banner(
            "No planned routes",
            "This snapshot does not contain route planning output.",
            "info",
        )
        return

    date_key = f"route_date::{run_id}"
    if st.session_state.get(date_key) not in dates:
        st.session_state[date_key] = dates[-1]
    selected = st.selectbox(
        "Service date",
        dates,
        key=date_key,
        help=(
            "Choose the service date used by the map, workload table and "
            "shift-overrun review."
        ),
    )
    day_routes = summary[summary["service_date"].map(_date_iso) == selected].copy()

    stops = _sum(day_routes, "stop_count")
    distance = _sum(day_routes, "distance_km")
    load_factor = _ratio(_sum(day_routes, "load_units"), _sum(day_routes, "capacity_units"))
    overrun_count = int((_numeric(day_routes, "shift_overrun_minutes").fillna(0) > 0).sum())
    fleet_rate = _metric_value(metrics, "fleet", "fleet_utilization")

    ui.metric_grid(
        [
            {"label": "Routes", "value": _num(len(day_routes))},
            {"label": "Stops", "value": _num(stops)},
            {"label": "Distance", "value": f"{_num(distance, 1)} km"},
            {
                "label": "Load factor",
                "value": _pct(load_factor),
                "help": "Selected day: assigned load units ÷ route capacity units.",
            },
            {
                "label": "Fleet use (run)",
                "value": _pct(fleet_rate),
                "help": _metric_help(
                    metrics,
                    "fleet",
                    "fleet_utilization",
                    "Vehicles used ÷ active vehicles across the run",
                ),
            },
            {
                "label": "Shift overruns",
                "value": _num(overrun_count),
                "help": "Routes whose actual end passed the planned shift end on this date.",
            },
        ]
    )

    palette = _route_palette(day_routes.get("route_id", []))
    figure = _route_map(tables, selected, palette)
    map_column, key_column = st.columns((3.35, 1.0), gap="medium")
    with map_column:
        with ui.panel("Operating geography", f"Depot-to-stop sequences for {selected}"):
            if figure is None:
                ui.empty_state(
                    "No mapped stops for this date",
                    "The selected service date has no route stops with coordinates.",
                )
            else:
                ui.plot_map(figure, MAP_HEIGHT)
                st.caption(
                    "Route lines use the colours in the map key. Markers show stop "
                    "outcomes; the star marks the depot. Use the modebar to zoom."
                )

    with key_column:
        with ui.panel("Map key", "Route colours · markers"):
            if figure is None:
                ui.empty_state("No routes to key", "No mapped routes on this date.")
            else:
                route_items: list[tuple[str, str, str]] = []
                for row in day_routes.sort_values("route_id").itertuples(index=False):
                    route_id = str(getattr(row, "route_id", ""))
                    note_parts: list[str] = []
                    stop_count = getattr(row, "stop_count", None)
                    distance_km = getattr(row, "distance_km", None)
                    if not _is_missing(stop_count):
                        note_parts.append(f"{float(stop_count):,.0f} stops")
                    if not _is_missing(distance_km):
                        note_parts.append(f"{float(distance_km):,.0f} km")
                    route_items.append(
                        (
                            palette.get(route_id, ui.NEUTRAL),
                            route_id or "Unassigned route",
                            " · ".join(note_parts) or "No planned workload",
                        )
                    )
                ui.route_legend(route_items)
                ui.marker_legend(
                    [
                        (ui.SUCCESS, "●", "Delivered"),
                        (ui.DANGER, "×", "Failed"),
                        (ui.WARNING, "◆", "Unserved"),
                        (ui.INFO, "○", "Assigned"),
                        (ui.WARNING, "★", "Depot"),
                    ]
                )
                st.caption(
                    "Load factor in the workload table uses assigned load units ÷ "
                    "route capacity."
                )

    with ui.panel(
        "Route workload",
        "Selected service date; read load factor against each route's capacity.",
    ):
        _render_route_workload(day_routes)

    capacity = _dimension_bar(
        metrics,
        "fleet_type",
        "capacity_utilization_units",
        axis_title="Load factor",
        orientation="h",
        value_kind="rate",
        color=ui.ACCENT,
    )
    distance_chart = _dimension_bar(
        metrics,
        "fleet_type",
        "distance_km",
        axis_title="Distance (km)",
        orientation="h",
        value_kind="count",
        color=ui.SUCCESS,
    )
    histogram = _route_load_histogram(summary)

    left, middle, right = st.columns(3, gap="medium")
    with left:
        with ui.panel(
            "Load factor by vehicle type",
            "Run-level capacity utilisation",
        ):
            if capacity is None:
                ui.empty_state("No vehicle-type capacity data", "Fleet mix metrics are unavailable.")
            else:
                ui.plot(capacity, CHART_HEIGHT)
    with middle:
        with ui.panel("Distance by vehicle type", "Run-level distance travelled"):
            if distance_chart is None:
                ui.empty_state("No vehicle-type distance data", "Fleet mix metrics are unavailable.")
            else:
                ui.plot(distance_chart, CHART_HEIGHT)
    with right:
        with ui.panel(
            "Route load factor distribution",
            "All planned routes, 100% capacity reference",
        ):
            if histogram is None:
                ui.empty_state(
                    "No route load data",
                    "Route capacity utilisation is not available for this snapshot.",
                )
            else:
                ui.plot(histogram, CHART_HEIGHT)

    with ui.panel("Shift-overrun review", "Selected service date"):
        overruns = day_routes.copy()
        overruns["_overrun"] = _numeric(overruns, "shift_overrun_minutes").fillna(0)
        overruns = overruns[overruns["_overrun"] > 0]
        if overruns.empty:
            ui.status_banner(
                "No shift overruns on this service date",
                "Every route ended within its planned shift window.",
                "success",
            )
        else:
            view = overruns.copy()
            view["planned_end"] = pd.to_datetime(view.get("planned_end"), errors="coerce")
            view["actual_end"] = pd.to_datetime(view.get("actual_end"), errors="coerce")
            ui.table(
                view,
                columns=[
                    "route_id",
                    "vehicle_id",
                    "driver_id",
                    "planned_end",
                    "actual_end",
                    "_overrun",
                ],
                config={
                    "route_id": st.column_config.TextColumn("Route", width=110),
                    "vehicle_id": st.column_config.TextColumn("Vehicle", width=95),
                    "driver_id": st.column_config.TextColumn("Driver", width=95),
                    "planned_end": st.column_config.DatetimeColumn(
                        "Planned end", format="YYYY-MM-DD HH:mm", width=150
                    ),
                    "actual_end": st.column_config.DatetimeColumn(
                        "Actual end", format="YYYY-MM-DD HH:mm", width=150
                    ),
                    "_overrun": st.column_config.NumberColumn(
                        "Overrun (min)", format="%.0f", width=105
                    ),
                },
                height=ui.table_height(len(view), max_height=320),
                empty_title="No shift overruns on this date",
            )


def _route_load_histogram(summary: pd.DataFrame) -> go.Figure | None:
    values = pd.to_numeric(summary.get("capacity_utilization"), errors="coerce")
    values = values.dropna() * 100
    if values.empty:
        return None
    bins = max(6, min(18, len(values) // 12 or 6))
    upper = max(105.0, float(values.max()) * 1.04)
    figure = go.Figure(
        go.Histogram(
            x=values,
            nbinsx=bins,
            marker_color=ui.ACCENT,
            marker_line_width=0,
            hovertemplate="Load factor %{x:.0f}%<br>Routes %{y}<extra></extra>",
        )
    )
    figure.add_vline(x=100, line_dash="dot", line_color=ui.WARNING, line_width=1)
    figure.add_annotation(
        x=100,
        y=1,
        yref="paper",
        text="100% capacity",
        showarrow=False,
        font={"color": ui.WARNING, "size": 10},
        xanchor="left",
        yanchor="bottom",
    )
    figure.update_xaxes(title_text="Load factor (%)", range=[0, upper])
    figure.update_yaxes(title_text="Routes")
    return figure


def _render_execution(
    tables: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
) -> None:
    ui.section_header(
        "03",
        "Execution quality",
        "Planned versus actual delivery outcomes, failure reasons and on-time "
        "performance across the run.",
    )
    outcomes = tables.get("delivery_outcomes", pd.DataFrame())
    routes = tables.get("routes", pd.DataFrame())
    if outcomes.empty:
        ui.status_banner(
            "No execution outcomes",
            "This snapshot does not contain delivery outcome records.",
            "info",
        )
        return

    status_series = outcomes["status"].astype(str).str.upper()
    counts = status_series.value_counts().to_dict()
    delivered = int(counts.get("DELIVERED", 0))
    failed = int(counts.get("FAILED", 0))
    unserved = int(counts.get("UNSERVED", 0))
    total = len(outcomes)

    on_time_count = 0
    if "on_time" in outcomes:
        delivered_mask = status_series == "DELIVERED"
        on_time_count = int(
            outcomes.loc[delivered_mask, "on_time"].fillna(False).map(bool).sum()
        )

    assigned = _assigned_count(tables) or total
    on_time_rate = _metric_value(metrics, "service", "on_time_rate")
    if on_time_rate is None:
        on_time_rate = _ratio(on_time_count, delivered)
    failure_rate = _metric_value(metrics, "service", "failure_rate")
    if failure_rate is None:
        failure_rate = _ratio(failed, assigned)
    completion_rate = _metric_value(metrics, "service", "execution_completion_rate")
    if completion_rate is None:
        completion_rate = _ratio(delivered, assigned)

    if failed == 0 and unserved == 0:
        ui.status_banner(
            "All requested deliveries completed",
            f"{delivered:,} delivered · {on_time_count:,} on time · "
            f"{_pct(completion_rate)} completion.",
            "success",
        )
    elif unserved > 0:
        ui.status_banner(
            f"{unserved:,} deliveries were not served",
            f"{delivered:,} delivered · {failed:,} failed · "
            f"{_pct(completion_rate)} completion.",
            "warning",
        )
    else:
        ui.status_banner(
            f"{failed:,} deliveries failed",
            f"{delivered:,} delivered · {unserved:,} unserved · "
            f"{on_time_count:,} on time. Failure rate {_pct(failure_rate)}.",
            "warning",
        )

    ui.metric_grid(
        [
            {
                "label": "Delivered",
                "value": _num(delivered),
                "help": f"{delivered:,} of {total:,} requested deliveries.",
            },
            {
                "label": "Failed",
                "value": _num(failed),
                "help": f"{failed:,} of {assigned:,} assigned deliveries.",
            },
            {
                "label": "Unserved",
                "value": _num(unserved),
                "help": f"{unserved:,} of {total:,} requested deliveries.",
            },
            {
                "label": "On-time deliveries",
                "value": _num(on_time_count),
                "help": f"{on_time_count:,} of {delivered:,} delivered outcomes.",
            },
            {
                "label": "On-time rate",
                "value": _pct(on_time_rate),
                "help": _metric_help(
                    metrics,
                    "service",
                    "on_time_rate",
                    "On-time delivered outcomes ÷ delivered outcomes",
                ),
            },
            {
                "label": "Failure rate",
                "value": _pct(failure_rate),
                "help": _metric_help(
                    metrics,
                    "service",
                    "failure_rate",
                    "Failed outcomes ÷ assigned deliveries",
                ),
            },
        ]
    )

    figure = _outcome_figure({str(key): int(value) for key, value in counts.items()})
    reasons = _failure_reason_figure(outcomes)
    left, right = st.columns((1.0, 1.45), gap="medium")
    with left:
        with ui.panel("Outcome mix", "Delivered, failed and unserved outcomes"):
            if figure is None:
                ui.empty_state("No outcome mix", "Outcome statuses are unavailable.")
            else:
                ui.plot(figure, CHART_HEIGHT)
    with right:
        with ui.panel("Failure reasons", "Reason codes for failed deliveries"):
            if reasons is None:
                ui.status_banner(
                    "No failed deliveries",
                    "Every assigned delivery in this run was completed.",
                    "success",
                )
            else:
                ui.plot(reasons, CHART_HEIGHT)

    planned = _planned_actual_figure(routes)
    priority = _dimension_bar(
        metrics,
        "service_priority",
        "on_time_rate",
        axis_title="On-time rate",
        orientation="v",
        value_kind="rate",
        color=ui.SUCCESS,
        entity_order=PRIORITY_ORDER,
    )
    left, right = st.columns((1.35, 1.0), gap="medium")
    with left:
        with ui.panel(
            "Planned vs actual time",
            "Service and wait hours from the plan versus executed events",
        ):
            if planned is None:
                ui.empty_state(
                    "No planned/actual timing data",
                    "Route timing columns are not available for this snapshot.",
                )
            else:
                ui.plot(planned, CHART_HEIGHT)
    with right:
        with ui.panel("On-time rate by priority", "Delivered outcomes marked on time"):
            if priority is None:
                ui.empty_state(
                    "No priority on-time breakdown",
                    "Priority on-time metrics are not available for this snapshot.",
                )
            else:
                ui.plot(priority, CHART_HEIGHT)

    with ui.panel(
        "Route execution",
        "Routes with failed stops are listed first; actual end is compared with the plan.",
    ):
        if routes.empty:
            ui.empty_state(
                "No route execution records",
                "This snapshot does not contain route-level execution data.",
            )
        else:
            view = routes.copy()
            view["status"] = view["status"].fillna("UNKNOWN").map(ui.humanize)
            view["planned_end"] = pd.to_datetime(view.get("planned_end"), errors="coerce")
            view["actual_end"] = pd.to_datetime(view.get("actual_end"), errors="coerce")
            view["_failed"] = pd.to_numeric(view.get("failed_stops"), errors="coerce").fillna(0)
            view = view.sort_values(["_failed", "route_id"], ascending=[False, True])
            ui.table(
                view,
                columns=[
                    "route_id",
                    "service_date",
                    "status",
                    "stop_count",
                    "delivered_stops",
                    "failed_stops",
                    "planned_end",
                    "actual_end",
                    "shift_overrun_minutes",
                ],
                config={
                    "route_id": st.column_config.TextColumn("Route", width=110),
                    "service_date": st.column_config.DateColumn(
                        "Service date", format="YYYY-MM-DD", width=100
                    ),
                    "status": st.column_config.TextColumn("Status", width=85),
                    "stop_count": st.column_config.NumberColumn("Stops", format="%d", width=60),
                    "delivered_stops": st.column_config.NumberColumn(
                        "Delivered", format="%d", width=75
                    ),
                    "failed_stops": st.column_config.NumberColumn(
                        "Failed", format="%d", width=65
                    ),
                    "planned_end": st.column_config.DatetimeColumn(
                        "Planned end", format="YYYY-MM-DD HH:mm", width=150
                    ),
                    "actual_end": st.column_config.DatetimeColumn(
                        "Actual end", format="YYYY-MM-DD HH:mm", width=150
                    ),
                    "shift_overrun_minutes": st.column_config.NumberColumn(
                        "Overrun (min)", format="%.0f", width=95
                    ),
                },
                height=460,
                empty_title="No route execution records",
            )


def _filter_dates(routes: pd.DataFrame) -> list[str]:
    if routes.empty or "service_date" not in routes:
        return []
    return sorted({_date_iso(value) for value in routes["service_date"].dropna()} - {""})


def _render_quality(
    tables: dict[str, pd.DataFrame],
    run_id: str,
) -> None:
    ui.section_header(
        "04",
        "Validation & quarantine",
        "Findings from the clean snapshot and the intentionally corrupted anomaly "
        "snapshot, plus quarantine handling and cross-table reconciliation.",
    )
    issues = tables.get("data_quality_issues", pd.DataFrame())
    quarantine = tables.get("quarantine_records", pd.DataFrame())
    reconciliation = tables.get("reconciliation_checks", pd.DataFrame())
    if issues.empty and quarantine.empty and reconciliation.empty:
        ui.status_banner(
            "No data-quality artifacts",
            "This snapshot does not contain validation findings, quarantine records "
            "or reconciliation checks.",
            "info",
        )
        return

    issues_view = issues.copy()
    if issues_view.empty:
        issues_view["snapshot"] = pd.Series(dtype="object")
        issues_view["severity"] = pd.Series(dtype="object")
    else:
        issues_view["snapshot"] = (
            issues_view["issue_id"].astype(str).str.startswith("DQD-").map(
                {True: "dirty", False: "clean"}
            )
        )
        issues_view["severity"] = (
            issues_view["severity"].fillna("UNKNOWN").astype(str).str.upper()
        )

    clean_issues = issues_view[issues_view["snapshot"] == "clean"]
    dirty_issues = issues_view[issues_view["snapshot"] == "dirty"]
    clean_blocking = int(
        clean_issues["severity"].isin({"ERROR", "CRITICAL"}).sum()
    ) if not clean_issues.empty else 0
    dirty_blocking = int(
        dirty_issues["severity"].isin({"ERROR", "CRITICAL"}).sum()
    ) if not dirty_issues.empty else 0
    clean_warnings = int((clean_issues["severity"] == "WARNING").sum()) if not clean_issues.empty else 0
    dirty_warnings = int((dirty_issues["severity"] == "WARNING").sum()) if not dirty_issues.empty else 0

    passed = int(reconciliation["passed"].fillna(False).map(_truthy).sum()) if not reconciliation.empty else 0
    reconciliation_label = (
        f"{passed}/{len(reconciliation)}" if not reconciliation.empty else "n/a"
    )

    ui.metric_grid(
        [
            {
                "label": "Clean issues",
                "value": _num(len(clean_issues)),
                "help": f"{clean_warnings:,} advisory warnings · {clean_blocking:,} blocking issues.",
            },
            {
                "label": "Clean blocking",
                "value": _num(clean_blocking),
                "help": "ERROR or CRITICAL findings in the clean snapshot.",
            },
            {
                "label": "Anomaly issues",
                "value": _num(len(dirty_issues)),
                "help": f"{dirty_warnings:,} warnings · {dirty_blocking:,} blocking findings.",
            },
            {
                "label": "Anomaly blocking",
                "value": _num(dirty_blocking),
                "help": "ERROR or CRITICAL findings in the intentionally corrupted snapshot.",
            },
            {
                "label": "Quarantined",
                "value": _num(len(quarantine)),
                "help": "Dirty records isolated during validation.",
            },
            {
                "label": "Reconciliation",
                "value": reconciliation_label,
                "help": "Cross-table arithmetic and coverage checks that passed.",
            },
        ]
    )

    left, right = st.columns(2, gap="medium")
    with left:
        if clean_blocking == 0:
            ui.status_banner(
                "Clean snapshot passed",
                f"0 blocking issues; {clean_warnings:,} advisory warnings. "
                "Volume-capacity warnings do not fail clean validation.",
                "success",
            )
        else:
            ui.status_banner(
                "Clean snapshot has blocking issues",
                f"{clean_blocking:,} ERROR/CRITICAL findings require review.",
                "danger",
            )
    with right:
        if dirty_issues.empty:
            ui.status_banner(
                "No anomaly-snapshot findings",
                "The intentionally corrupted snapshot produced no validation issues.",
                "info",
            )
        else:
            ui.status_banner(
                "Anomaly snapshot issues detected",
                f"{dirty_blocking:,} blocking · {dirty_warnings:,} warnings · "
                f"{len(quarantine):,} quarantined records. This snapshot is corrupted "
                "on purpose to exercise validation.",
                "warning",
            )

    severity_figure = _severity_comparison_figure(clean_issues, dirty_issues)
    issue_type_figure = _issue_type_figure(issues_view)
    left, right = st.columns((1.0, 1.45), gap="medium")
    with left:
        with ui.panel(
            "Clean vs anomaly snapshot",
            "Issue counts by severity in each validation pass",
        ):
            if severity_figure is None:
                ui.empty_state("No severity findings", "No issues were recorded in either snapshot.")
            else:
                ui.plot(severity_figure, CHART_HEIGHT)
    with right:
        with ui.panel(
            "Issues by type and severity",
            "Both snapshots combined; use the inventory filters below to isolate",
        ):
            if issue_type_figure is None:
                ui.empty_state("No issue-type findings", "No issues were recorded.")
            else:
                ui.plot(issue_type_figure, TALL_CHART_HEIGHT)

    with ui.panel(
        "Reconciliation",
        "Cross-table arithmetic and coverage checks",
    ):
        if reconciliation.empty:
            ui.empty_state(
                "No reconciliation checks",
                "This snapshot does not contain reconciliation results.",
            )
        else:
            failed_checks = reconciliation[
                ~reconciliation["passed"].fillna(False).map(_truthy)
            ] if "passed" in reconciliation else reconciliation
            if failed_checks.empty:
                ui.status_banner(
                    f"All {len(reconciliation):,} reconciliation checks passed",
                    "Coverage, partition and arithmetic totals agree across tables.",
                    "success",
                )
            else:
                ui.status_banner(
                    f"{len(failed_checks):,} reconciliation checks failed",
                    ", ".join(
                        ui.humanize(value)
                        for value in failed_checks["check_name"].astype(str).tolist()
                    ),
                    "danger",
                )
            view = reconciliation.copy()
            view["check"] = view["check_name"].map(ui.humanize)
            view["status"] = view["passed"].fillna(False).map(
                lambda value: "PASS" if _truthy(value) else "FAIL"
            )
            view["expected"] = pd.to_numeric(view.get("expected"), errors="coerce")
            view["actual"] = pd.to_numeric(view.get("actual"), errors="coerce")
            view["difference"] = pd.to_numeric(view.get("difference"), errors="coerce")
            ui.table(
                view,
                columns=["check", "expected", "actual", "difference", "status"],
                config={
                    "check": st.column_config.TextColumn("Check", width=260),
                    "expected": st.column_config.NumberColumn(
                        "Expected", format="%.2f", width=115
                    ),
                    "actual": st.column_config.NumberColumn(
                        "Actual", format="%.2f", width=115
                    ),
                    "difference": st.column_config.NumberColumn(
                        "Difference", format="%.4f", width=115
                    ),
                    "status": st.column_config.TextColumn("Status", width=80),
                },
                height=ui.table_height(len(view), max_height=560),
                empty_title="No reconciliation checks",
            )
            if "details" in reconciliation:
                with st.expander("Check definitions"):
                    for row in reconciliation.sort_values("check_name").itertuples():
                        detail = getattr(row, "details", None)
                        detail_text = (
                            str(detail) if not _is_missing(detail) and str(detail) else "No definition recorded."
                        )
                        st.markdown(
                            f"**{ui.humanize(row.check_name)}** — {detail_text}"
                        )

    with ui.panel(
        "Validation issue inventory",
        "Every clean and anomaly-snapshot finding; filters never hide the recorded total.",
    ):
        issue_types = sorted(issues_view["issue_type"].dropna().astype(str).unique().tolist()) if not issues_view.empty else []
        type_labels = {ui.humanize(value): value for value in issue_types}
        severity_labels = {
            severity.title(): severity
            for severity in SEVERITY_ORDER
            if severity in set(issues_view["severity"].dropna().astype(str).unique())
        } if not issues_view.empty else {}

        filter_columns = st.columns(3, gap="small")
        snapshot_choice = filter_columns[0].selectbox(
            "Snapshot",
            ["All snapshots", "Clean snapshot", "Anomaly snapshot"],
            key=f"quality_snapshot::{run_id}",
        )
        severity_choice = filter_columns[1].selectbox(
            "Severity",
            ["All severities", *severity_labels.keys()],
            key=f"quality_severity::{run_id}",
        )
        type_choice = filter_columns[2].selectbox(
            "Issue type",
            ["All types", *type_labels.keys()],
            key=f"quality_type::{run_id}",
        )

        filtered = issues_view.copy()
        if snapshot_choice == "Clean snapshot":
            filtered = filtered[filtered["snapshot"] == "clean"]
        elif snapshot_choice == "Anomaly snapshot":
            filtered = filtered[filtered["snapshot"] == "dirty"]
        if severity_choice != "All severities":
            filtered = filtered[filtered["severity"] == severity_labels[severity_choice]]
        if type_choice != "All types":
            filtered = filtered[filtered["issue_type"] == type_labels[type_choice]]

        st.caption(
            f"Showing {len(filtered):,} of {len(issues_view):,} recorded issues. "
            "CRITICAL and ERROR are blocking; WARNING is advisory."
        )
        if filtered.empty:
            ui.empty_state(
                "No issues match these filters",
                "Clear a filter to return to the full validation inventory.",
            )
        else:
            view = filtered.copy()
            view["entity"] = view["entity_id"].where(
                ~view["entity_id"].map(_is_missing), "—"
            )
            view["field"] = view["field_name"].where(
                ~view["field_name"].map(_is_missing), "—"
            )
            view["finding"] = [
                _issue_finding(issue_type, message)
                for issue_type, message in zip(
                    view["issue_type"], view["message"], strict=True
                )
            ]
            view["quarantined"] = view["quarantined"].fillna(False).map(
                lambda value: "Yes" if _truthy(value) else "No"
            )
            view["issue_type"] = view["issue_type"].map(ui.humanize)
            view["severity"] = view["severity"].map(lambda value: str(value).title())
            view["snapshot"] = view["snapshot"].map(
                {"clean": "Clean", "dirty": "Anomaly"}
            ).fillna("—")
            view["_severity_rank"] = filtered["severity"].map(SEVERITY_RANK).fillna(9)
            view = view.sort_values(
                ["_severity_rank", "issue_type", "issue_id"],
                ascending=[True, True, True],
            )
            ui.table(
                view,
                columns=[
                    "issue_id",
                    "snapshot",
                    "severity",
                    "finding",
                    "entity",
                    "field",
                    "quarantined",
                ],
                config={
                    "issue_id": st.column_config.TextColumn("Issue", width=88),
                    "snapshot": st.column_config.TextColumn("Snapshot", width=82),
                    "severity": st.column_config.TextColumn("Severity", width=80),
                    "finding": st.column_config.TextColumn("Finding", width=255),
                    "entity": st.column_config.TextColumn("Entity", width=160),
                    "field": st.column_config.TextColumn("Field", width=125),
                    "quarantined": st.column_config.TextColumn("Quarantined", width=88),
                },
                height=440,
                empty_title="No issues match these filters",
            )

            with st.expander("Inspect issue detail"):
                detail_ids = view["issue_id"].astype(str).tolist()
                detail_key = f"quality_issue::{run_id}"
                if st.session_state.get(detail_key) not in detail_ids:
                    st.session_state[detail_key] = detail_ids[0]
                detail_labels = {
                    str(row.issue_id): (
                        f"{row.issue_id} · {row.severity} · {row.issue_type}"
                    )
                    for row in view.itertuples(index=False)
                }
                selected_issue = st.selectbox(
                    "Issue record",
                    detail_ids,
                    key=detail_key,
                    format_func=lambda value: detail_labels.get(str(value), str(value)),
                )
                selected_row = view.loc[
                    view["issue_id"].astype(str) == str(selected_issue)
                ].iloc[0]
                entity = selected_row.get("entity")
                field = selected_row.get("field")
                entity_field = (
                    f"{entity}"
                    if _is_missing(field) or str(field) in {"", "—"}
                    else f"{entity} · {field}"
                )
                meta_column, message_column = st.columns((1.0, 1.5), gap="medium")
                with meta_column:
                    ui.stat_grid(
                        [
                            (
                                "Snapshot",
                                str(selected_row.get("snapshot") or "—"),
                                None,
                            ),
                            (
                                "Severity",
                                str(selected_row.get("severity") or "—"),
                                None,
                            ),
                            ("Entity · field", str(entity_field), None),
                            (
                                "Source table",
                                str(selected_row.get("source_table") or "—"),
                                None,
                            ),
                            (
                                "Quarantined",
                                str(selected_row.get("quarantined") or "—"),
                                None,
                            ),
                            (
                                "Detected",
                                ui.format_timestamp(selected_row.get("detected_at")),
                                None,
                            ),
                        ]
                    )
                with message_column:
                    st.markdown("**Raw validation message**")
                    st.code(
                        _clean_issue_message(selected_row.get("message")) or "No message recorded.",
                        language=None,
                        wrap_lines=True,
                    )
                    st.caption(
                        "The inventory shows a concise finding; this is the unmodified "
                        "validator output for the selected issue."
                    )
                details_value = selected_row.get("details")
                if not _is_missing(details_value) and str(details_value).strip() not in {
                    "",
                    "{}",
                    "null",
                    "None",
                }:
                    st.markdown("**Validator details**")
                    st.code(str(details_value), language="json", wrap_lines=True)

    with ui.panel(
        "Quarantine register",
        "Isolated dirty records and the validation reason that captured them",
    ):
        if quarantine.empty:
            ui.empty_state(
                "No quarantined records",
                "No dirty records were isolated during this run.",
            )
        else:
            view = quarantine.copy()
            view["reason"] = view["reason"].fillna("UNKNOWN").map(ui.humanize)
            view["quarantined_at"] = pd.to_datetime(
                view.get("quarantined_at"), errors="coerce"
            )
            ui.table(
                view,
                columns=[
                    "quarantine_id",
                    "issue_id",
                    "source_table",
                    "entity_id",
                    "reason",
                    "quarantined_at",
                ],
                config={
                    "quarantine_id": st.column_config.TextColumn(
                        "Quarantine", width=115
                    ),
                    "issue_id": st.column_config.TextColumn("Issue", width=95),
                    "source_table": st.column_config.TextColumn(
                        "Source table", width=135
                    ),
                    "entity_id": st.column_config.TextColumn("Entity", width=150),
                    "reason": st.column_config.TextColumn("Reason", width=165),
                    "quarantined_at": st.column_config.DatetimeColumn(
                        "Quarantined at", format="YYYY-MM-DD HH:mm", width=150
                    ),
                },
                height=ui.table_height(len(view), max_height=420),
                empty_title="No quarantined records",
            )
            with st.expander("Inspect quarantine payloads"):
                quarantine_ids = view["quarantine_id"].astype(str).tolist()
                quarantine_key = f"quarantine_record::{run_id}"
                if st.session_state.get(quarantine_key) not in quarantine_ids:
                    st.session_state[quarantine_key] = quarantine_ids[0]
                selected_quarantine = st.selectbox(
                    "Quarantine record",
                    quarantine_ids,
                    key=quarantine_key,
                )
                selected_row = quarantine.loc[
                    quarantine["quarantine_id"].astype(str) == selected_quarantine
                ].iloc[0]
                st.caption(
                    f"{ui.humanize(selected_row.get('reason'))} · "
                    f"{selected_row.get('source_table')} · "
                    f"{selected_row.get('entity_id')}"
                )
                payload = _safe_json(selected_row.get("record_json"))
                if payload is None:
                    st.caption("No JSON payload was recorded for this quarantine row.")
                else:
                    st.json(payload)


def _render_anomalies(
    tables: dict[str, pd.DataFrame],
    run_id: str,
) -> None:
    ui.section_header(
        "05",
        "Detection evaluation",
        "Ground-truth injection versus detector output, including confusion counts "
        "and per-type precision, recall and F1.",
    )
    truth = tables.get("ground_truth_anomalies", pd.DataFrame())
    detections = tables.get("anomaly_detections", pd.DataFrame())
    metrics = anomaly_metrics_frame(tables.get("anomaly_detection_metrics", pd.DataFrame()))
    if truth.empty and detections.empty:
        ui.status_banner(
            "No anomaly evaluation in this run",
            "Re-run the pipeline in FULL mode to populate ground truth, detections "
            "and detector measurements.",
            "info",
        )
        return

    truth_keys = (
        set(
            zip(
                truth["issue_type"].astype(str),
                truth["entity_id"].astype(str),
            )
        )
        if not truth.empty and {"issue_type", "entity_id"}.issubset(truth.columns)
        else set()
    )
    detection_keys = (
        set(
            zip(
                detections["issue_type"].astype(str),
                detections["entity_id"].astype(str),
            )
        )
        if not detections.empty
        and {"issue_type", "entity_id"}.issubset(detections.columns)
        else set()
    )
    matched = len(truth_keys & detection_keys)
    missed = len(truth_keys - detection_keys)
    unexpected = len(detection_keys - truth_keys)

    if not metrics.empty:
        tp = int(metrics["tp"].sum())
        fp = int(metrics["fp"].sum())
        fn = int(metrics["fn"].sum())
    else:
        tp, fp, fn = matched, unexpected, missed
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = _f1(precision, recall)
    type_count = metrics["issue_type"].nunique() if not metrics.empty else len(
        set(truth["issue_type"].astype(str)) | set(detections["issue_type"].astype(str))
    )

    if tp == len(truth) and fp == 0 and fn == 0 and not truth.empty:
        ui.status_banner(
            f"All {len(truth):,} injected anomalies detected",
            f"{matched:,} matched · {missed:,} missed · {unexpected:,} unexpected "
            f"across {type_count:,} anomaly type{'s' if type_count != 1 else ''}.",
            "success",
        )
    elif not detections.empty:
        ui.status_banner(
            "Detection quality needs review",
            f"{matched:,} matched · {missed:,} missed · {unexpected:,} unexpected.",
            "warning",
        )
    else:
        ui.status_banner(
            "No detections recorded",
            f"{len(truth):,} injected anomalies have no matching detector output.",
            "danger",
        )

    ui.metric_grid(
        [
            {"label": "Injected anomalies", "value": _num(len(truth))},
            {"label": "Detections", "value": _num(len(detections))},
            {
                "label": "Precision",
                "value": _pct(precision),
                "help": f"TP ÷ (TP + FP) = {tp:,} ÷ {tp + fp:,}.",
            },
            {
                "label": "Recall",
                "value": _pct(recall),
                "help": f"TP ÷ (TP + FN) = {tp:,} ÷ {tp + fn:,}.",
            },
            {
                "label": "F1 score",
                "value": _pct(f1) if f1 is not None else "n/a",
                "help": "Harmonic mean of precision and recall.",
            },
            {
                "label": "False positives",
                "value": _num(fp),
                "help": "Detected entities with no matching ground-truth injection.",
            },
        ]
    )

    confusion = _confusion_figure(metrics)
    comparison = _truth_detection_figure(truth, detections)
    left, right = st.columns((1.0, 1.2), gap="medium")
    with left:
        with ui.panel(
            "Confusion by anomaly type",
            "True positives, false positives and false negatives",
        ):
            if confusion is None:
                ui.empty_state(
                    "No per-type confusion metrics",
                    "Detector measurements are not available for this snapshot.",
                )
            else:
                ui.plot_heatmap(confusion, CHART_HEIGHT)
    with right:
        with ui.panel(
            "Ground truth vs detected",
            "Injected anomalies and detector output by type",
        ):
            if comparison is None:
                ui.empty_state(
                    "No ground-truth comparison",
                    "Ground truth and detections are not available.",
                )
            else:
                ui.plot(comparison, TALL_CHART_HEIGHT)

    quality = _anomaly_quality_figure(metrics)
    with ui.panel(
        "Detection quality",
        "Per-type precision, recall and F1; hover for exact scores",
    ):
        if quality is None:
            ui.empty_state(
                "No detection-quality scores",
                "Per-type precision, recall and F1 are not available.",
            )
        else:
            ui.plot(quality, CHART_HEIGHT)

    with ui.panel(
        "Detection quality by anomaly type",
        "Confusion counts, scores and evaluated universe for each detector",
    ):
        if metrics.empty:
            ui.empty_state(
                "No detector metrics",
                "Anomaly detection metrics are not available for this snapshot.",
            )
        else:
            view = metrics.copy()
            view["issue_type"] = view["issue_type"].map(ui.humanize)
            view = view.sort_values("issue_type")
            ui.table(
                view,
                columns=[
                    "issue_type",
                    "tp",
                    "fp",
                    "fn",
                    "tn",
                    "precision",
                    "recall",
                    "f1",
                    "evaluated_universe",
                ],
                config={
                    "issue_type": st.column_config.TextColumn(
                        "Anomaly type", width=205
                    ),
                    "tp": st.column_config.NumberColumn("TP", format="%,d", width=55),
                    "fp": st.column_config.NumberColumn("FP", format="%,d", width=55),
                    "fn": st.column_config.NumberColumn("FN", format="%,d", width=55),
                    "tn": st.column_config.NumberColumn("TN", format="%,d", width=65),
                    "precision": st.column_config.ProgressColumn(
                        "Precision",
                        min_value=0.0,
                        max_value=1.0,
                        format="percent",
                        width=120,
                        color="auto",
                    ),
                    "recall": st.column_config.ProgressColumn(
                        "Recall",
                        min_value=0.0,
                        max_value=1.0,
                        format="percent",
                        width=110,
                        color="auto",
                    ),
                    "f1": st.column_config.ProgressColumn(
                        "F1",
                        min_value=0.0,
                        max_value=1.0,
                        format="percent",
                        width=100,
                        color="auto",
                    ),
                    "evaluated_universe": st.column_config.NumberColumn(
                        "Universe", format="%,d", width=95
                    ),
                },
                height=ui.table_height(len(view), max_height=320),
                empty_title="No detector metrics",
            )
            if "notes" in metrics:
                with st.expander("Evaluation method notes"):
                    for row in metrics.sort_values("issue_type").itertuples():
                        note = getattr(row, "notes", None)
                        if _is_missing(note) or not str(note):
                            continue
                        st.markdown(
                            f"**{ui.humanize(row.issue_type)}** — {note}"
                        )

    with st.expander("Ground truth injections"):
        if truth.empty:
            ui.empty_state(
                "No ground-truth injections",
                "This run did not record injected anomalies.",
            )
        else:
            view = truth.copy()
            view["issue_type"] = view["issue_type"].map(ui.humanize)
            view["injected"] = [
                _injected_summary(issue_type, injected)
                for issue_type, injected in zip(
                    truth["issue_type"], truth["injected_value"], strict=True
                )
            ]
            ui.table(
                view,
                columns=[
                    "anomaly_id",
                    "issue_type",
                    "entity_id",
                    "injected",
                ],
                config={
                    "anomaly_id": st.column_config.TextColumn(
                        "Anomaly", width=112
                    ),
                    "issue_type": st.column_config.TextColumn(
                        "Anomaly type", width=195
                    ),
                    "entity_id": st.column_config.TextColumn("Entity", width=160),
                    "injected": st.column_config.TextColumn(
                        "Injected change", width=330
                    ),
                },
                height=ui.table_height(len(view), max_height=460),
                empty_title="No ground-truth injections",
            )
            st.markdown("**Inspect ground-truth injection**")
            injection_ids = view["anomaly_id"].astype(str).tolist()
            injection_key = f"ground_truth::{run_id}"
            if st.session_state.get(injection_key) not in injection_ids:
                st.session_state[injection_key] = injection_ids[0]
            injection_labels = {
                str(record.anomaly_id): f"{record.anomaly_id} · {record.issue_type}"
                for record in view.itertuples(index=False)
            }
            selected_injection = st.selectbox(
                "Injection record",
                injection_ids,
                key=injection_key,
                format_func=lambda value: injection_labels.get(str(value), str(value)),
            )
            selected_injection_row = view.loc[
                view["anomaly_id"].astype(str) == str(selected_injection)
            ].iloc[0]
            meta_column, value_column = st.columns((1.0, 1.4), gap="medium")
            with meta_column:
                ui.stat_grid(
                    [
                        (
                            "Anomaly type",
                            str(selected_injection_row.get("issue_type")),
                            None,
                        ),
                        (
                            "Entity",
                            str(selected_injection_row.get("entity_id")),
                            None,
                        ),
                    ]
                )
            with value_column:
                st.markdown("**Full injected value**")
                st.code(
                    _clean_issue_message(selected_injection_row.get("injected_value"))
                    or "No injected value recorded.",
                    language=None,
                    wrap_lines=True,
                )
                st.caption(
                    "Ground truth is injected by the pipeline; detections are matched "
                    "by anomaly type and entity."
                )

    with st.expander("Detection details"):
        if detections.empty:
            ui.empty_state(
                "No detections",
                "The detector did not record any findings for this run.",
            )
        else:
            view = detections.copy()
            view["issue_type"] = view["issue_type"].map(ui.humanize)
            view["detection_rule"] = view["detection_rule"].map(ui.humanize)
            view["observed"] = [
                _observed_summary(issue_type, observed)
                for issue_type, observed in zip(
                    detections["issue_type"], detections["observed_value"], strict=True
                )
            ]
            view["detected_at"] = pd.to_datetime(
                view.get("detected_at"), errors="coerce"
            )
            ui.table(
                view,
                columns=[
                    "detection_id",
                    "issue_type",
                    "entity_id",
                    "detection_rule",
                    "observed",
                ],
                config={
                    "detection_id": st.column_config.TextColumn(
                        "Detection", width=106
                    ),
                    "issue_type": st.column_config.TextColumn(
                        "Anomaly type", width=215
                    ),
                    "entity_id": st.column_config.TextColumn("Entity", width=150),
                    "detection_rule": st.column_config.TextColumn(
                        "Rule", width=190
                    ),
                    "observed": st.column_config.TextColumn(
                        "Observed", width=230
                    ),
                },
                height=ui.table_height(len(view), max_height=460),
                empty_title="No detections",
            )
            st.markdown("**Inspect detection record**")
            detection_ids = view["detection_id"].astype(str).tolist()
            detail_key = f"anomaly_detection::{run_id}"
            if st.session_state.get(detail_key) not in detection_ids:
                st.session_state[detail_key] = detection_ids[0]
            detail_labels = {
                str(row.detection_id): f"{row.detection_id} · {row.issue_type}"
                for row in view.itertuples(index=False)
            }
            selected_detection = st.selectbox(
                "Detection record",
                detection_ids,
                key=detail_key,
                format_func=lambda value: detail_labels.get(str(value), str(value)),
            )
            selected_row = view.loc[
                view["detection_id"].astype(str) == str(selected_detection)
            ].iloc[0]
            meta_column, value_column = st.columns((1.0, 1.4), gap="medium")
            with meta_column:
                ui.stat_grid(
                    [
                        ("Anomaly type", str(selected_row.get("issue_type")), None),
                        ("Entity", str(selected_row.get("entity_id")), None),
                        ("Rule", str(selected_row.get("detection_rule")), None),
                        (
                            "Detected",
                            ui.format_timestamp(selected_row.get("detected_at")),
                            None,
                        ),
                    ]
                )
            with value_column:
                st.markdown("**Observed value**")
                st.code(
                    _clean_issue_message(selected_row.get("observed_value"))
                    or "No observed value recorded.",
                    language=None,
                    wrap_lines=True,
                )
                st.caption(
                    "The table shows a concise interpretation; this is the detector's "
                    "full recorded evidence."
                )


def _render_run_details(
    tables: dict[str, pd.DataFrame],
    run_id: str,
) -> None:
    metadata = st.session_state.get("_metadata", {})
    started = pd.to_datetime(metadata.get("started_at"), errors="coerce")
    completed = pd.to_datetime(metadata.get("completed_at"), errors="coerce")
    elapsed = (
        (completed - started).total_seconds() / 60
        if not pd.isna(started) and not pd.isna(completed)
        else None
    )
    metadata_frame = pd.DataFrame(
        [
            ("Run ID", str(metadata.get("run_id") or run_id)),
            ("Status", str(metadata.get("status") or "n/a")),
            ("Mode", str(metadata.get("mode") or "n/a")),
            (
                "Seed",
                str(metadata.get("seed") if metadata.get("seed") is not None else "n/a"),
            ),
            ("Dataset version", str(metadata.get("dataset_version") or "n/a")),
            ("Pipeline version", str(metadata.get("pipeline_version") or "n/a")),
            ("Started", ui.format_timestamp(metadata.get("started_at"))),
            ("Completed", ui.format_timestamp(metadata.get("completed_at"))),
            ("Elapsed", _duration_text(elapsed)),
        ],
        columns=["field", "value"],
    )

    stages = metadata.get("stage_durations") or {}
    if isinstance(stages, dict) and stages:
        stage_frame = pd.DataFrame(
            {
                "stage": [ui.humanize(stage) for stage in stages.keys()],
                "seconds": list(stages.values()),
            }
        ).sort_values("seconds", ascending=False)
    else:
        stage_frame = pd.DataFrame(columns=["stage", "seconds"])

    left, right = st.columns(2, gap="medium")
    with left:
        st.markdown("**Snapshot metadata**")
        ui.table(
            metadata_frame,
            columns=["field", "value"],
            config={
                "field": st.column_config.TextColumn("Field", width=150),
                "value": st.column_config.TextColumn("Value", width=280),
            },
            height=ui.table_height(len(metadata_frame), row_height=28),
            row_height=28,
            empty_title="No metadata",
        )
    with right:
        st.markdown("**Pipeline stage timings**")
        if stage_frame.empty:
            ui.empty_state("No stage timings", "Stage duration data is not available.")
        else:
            ui.table(
                stage_frame,
                columns=["stage", "seconds"],
                config={
                    "stage": st.column_config.TextColumn("Stage", width=190),
                    "seconds": st.column_config.NumberColumn(
                        "Duration (s)", format="%.2f", width=105
                    ),
                },
                height=ui.table_height(len(stage_frame), row_height=28),
                row_height=28,
                empty_title="No stage timings",
            )

    audit = tables.get("audit_records", pd.DataFrame())
    st.markdown("**Audit trail**")
    if audit.empty:
        ui.empty_state("No audit records", "This run did not persist an audit trail.")
        return
    view = audit.copy().sort_values("timestamp")
    view["action"] = view["action"].map(ui.humanize)
    view["timestamp"] = pd.to_datetime(view.get("timestamp"), errors="coerce")
    view["details"] = view.get("details_json", pd.Series([""] * len(view))).map(
        lambda value: _truncate(value, 140)
    )
    ui.table(
        view,
        columns=["timestamp", "action", "actor", "details"],
        config={
            "timestamp": st.column_config.DatetimeColumn(
                "Timestamp", format="YYYY-MM-DD HH:mm:ss", width=155
            ),
            "action": st.column_config.TextColumn("Action", width=220),
            "actor": st.column_config.TextColumn("Actor", width=150),
            "details": st.column_config.TextColumn("Details", width=520),
        },
        height=ui.table_height(len(view), max_height=380),
        empty_title="No audit records",
    )
    st.caption(
        "Details are truncated here; full payloads remain in the warehouse's "
        "audit_records table."
    )


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="FleetMind Control Tower",
        page_icon="FM",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    ui.inject_styles()
    st.title("FleetMind Control Tower")
    st.caption(
        "Logistics planning, execution, data quality and anomaly observability"
    )

    default_db = Path(os.environ.get("FLEETMIND_DB", str(DEFAULT_DB)))
    with st.sidebar:
        ui.sidebar_brand()
        st.header("Run selector")
        db_text = st.text_input("DuckDB warehouse", value=str(default_db))
        db_path = Path(db_text).expanduser()
        if not db_path.is_file():
            st.error(
                "Warehouse not found. Run "
                "`fleetmind run --config configs/starter.yaml` first."
            )
            st.stop()
        try:
            fingerprint = db_path.stat().st_mtime_ns
            runs = _runs_cached(str(db_path), fingerprint)
        except Exception as exc:
            st.error(f"Unable to read the warehouse: {exc}")
            st.stop()
        if runs.empty:
            st.error("No completed runs could be read from this warehouse.")
            st.stop()

        runs = runs.copy()
        runs["label"] = runs.apply(
            lambda row: (
                f"{row['run_id']} | {row['status']} | "
                f"{_date_only(row.get('started_at'))}"
            ),
            axis=1,
        )
        labels = runs["label"].tolist()
        if st.session_state.get("run_selector") not in labels:
            st.session_state["run_selector"] = labels[0]
        selected_label = st.selectbox(
            "Run",
            labels,
            key="run_selector",
            help=(
                "Select a persisted pipeline run. The dashboard is read-only and "
                "never writes to the warehouse."
            ),
        )
        selected_run = str(
            runs.loc[runs["label"] == selected_label, "run_id"].iloc[0]
        )
        st.caption(
            f"{len(runs):,} run snapshot{'s' if len(runs) != 1 else ''} "
            "available in this warehouse."
        )
        st.divider()
        ui.sidebar_note()

    try:
        data = _run_cached(str(db_path), selected_run, fingerprint)
    except Exception as exc:
        st.error(f"Unable to load run {selected_run}: {exc}")
        st.stop()

    metadata = data.get("_metadata", {})
    if not metadata:
        ui.status_banner(
            "Run metadata unavailable",
            "The selected run has no run_metadata row.",
            "danger",
        )
        st.stop()

    tables = {
        name: frame for name, frame in data.items() if not name.startswith("_")
    }
    metrics = tables.get("report_metrics", pd.DataFrame())
    st.session_state["_metadata"] = metadata

    ui.run_header(selected_run, metadata)

    tabs = st.tabs(
        ["Overview", "Routing & fleet", "Execution", "Data quality", "Anomalies"]
    )
    with tabs[0]:
        _render_overview(tables, metrics, selected_run)
    with tabs[1]:
        _render_routing(tables, metrics, selected_run)
    with tabs[2]:
        _render_execution(tables, metrics)
    with tabs[3]:
        _render_quality(tables, selected_run)
    with tabs[4]:
        _render_anomalies(tables, selected_run)

    st.divider()
    version = metadata.get("pipeline_version") or "n/a"
    st.caption(
        f"FleetMind v{version} · Run {selected_run} · Read-only DuckDB warehouse · "
        "Bound to loopback only"
    )


if __name__ == "__main__":
    main()
