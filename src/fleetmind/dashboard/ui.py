"""Presentation primitives for the FleetMind Control Tower dashboard.

This module owns visual language only: palette, typography, cards, status
treatments, empty states, table helpers and Plotly theming.  Warehouse access
stays in :mod:`fleetmind.dashboard.data`; section composition stays in
:mod:`fleetmind.dashboard.app`.
"""

from __future__ import annotations

import html
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
BACKGROUND = "#0b1220"
SURFACE = "#0f1828"
SURFACE_RAISED = "#111d30"
BORDER = "#22314a"
BORDER_SOFT = "#1b2a40"
TEXT = "#e6edf7"
MUTED = "#93a4bd"
SUBTLE = "#7f93ad"
ACCENT = "#4f8cff"
SUCCESS = "#34d399"
WARNING = "#fbbf24"
DANGER = "#f87171"
INFO = "#60a5fa"
NEUTRAL = "#94a3b8"
GRID = "rgba(148, 163, 184, 0.14)"
FONT = "'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif"

ROUTE_COLORS = (
    "#5b8ff9",
    "#5ad8a6",
    "#f6bd16",
    "#e8684a",
    "#6dc8ec",
    "#9270ca",
    "#ff9d4d",
    "#269a99",
    "#ff99c3",
    "#b6a2de",
    "#7ecf04",
    "#f2637b",
)

SEVERITY_COLORS = {
    "CRITICAL": DANGER,
    "ERROR": "#fb923c",
    "WARNING": WARNING,
    "INFO": INFO,
}

STATUS_COLORS = {
    "DELIVERED": SUCCESS,
    "FAILED": DANGER,
    "UNSERVED": WARNING,
    "ASSIGNED": INFO,
}

STATUS_LABELS = {
    "DELIVERED": "Delivered",
    "FAILED": "Failed",
    "UNSERVED": "Unserved",
    "ASSIGNED": "Assigned",
}

STATUS_SYMBOLS = {
    "DELIVERED": "circle",
    "FAILED": "x",
    "UNSERVED": "diamond",
    "ASSIGNED": "circle-open",
}

ISSUE_TYPE_LABELS = {
    "ARRIVAL_BEFORE_PREVIOUS_DEPARTURE": "Arrival before previous departure",
    "ARITHMETIC_RECONCILIATION_ERROR": "Arithmetic reconciliation error",
    "COORDINATE_OUT_OF_REGION": "Coordinate out of region",
    "DRIVER_SHIFT_EXCEEDED": "Driver shift exceeded",
    "INVALID_FOREIGN_KEY": "Invalid foreign key",
    "ROUTE_CAPACITY_EXCEEDED": "Route capacity exceeded",
    "STOP_DUPLICATED": "Stop duplicated",
    "STOP_OMITTED": "Stop omitted",
    "VOLUME_CAPACITY_EXCEEDED": "Volume capacity exceeded",
}

_ACRONYMS = {
    "api": "API",
    "f1": "F1",
    "fp": "FP",
    "fn": "FN",
    "id": "ID",
    "json": "JSON",
    "km": "km",
    "m3": "m³",
    "tn": "TN",
    "tp": "TP",
}


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
_CSS = """
:root {
  --fm-bg: #0b1220;
  --fm-surface: #0f1828;
  --fm-surface-raised: #111d30;
  --fm-border: #22314a;
  --fm-border-soft: #1b2a40;
  --fm-text: #e6edf7;
  --fm-muted: #93a4bd;
  --fm-subtle: #7f93ad;
  --fm-accent: #4f8cff;
  --fm-success: #34d399;
  --fm-warning: #fbbf24;
  --fm-danger: #f87171;
}

html,
body {
  font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
}

[data-testid="stAppViewContainer"] {
  background-color: var(--fm-bg);
}

[data-testid="stHeader"] {
  background: rgba(11, 18, 32, 0.92);
  border-bottom: 1px solid rgba(34, 49, 74, 0.45);
}

[data-testid="stAppDeployButton"] {
  display: none;
}

[data-testid="stMainBlockContainer"] {
  max-width: 1500px;
  padding: 1.25rem 2.2rem 3rem;
}

[data-testid="stSidebar"] {
  background-color: #0d1726;
  border-right: 1px solid var(--fm-border-soft);
}

[data-testid="stSidebarContent"] {
  padding: 1.2rem 1rem 1.5rem;
}

[data-testid="stSidebar"] h2 {
  color: var(--fm-text);
  font-size: 0.98rem;
  font-weight: 650;
  letter-spacing: -0.012em;
  margin: 0.15rem 0 0.55rem;
}

[data-testid="stHeading"] h1 {
  font-size: 1.75rem;
  font-weight: 650;
  letter-spacing: -0.028em;
  color: var(--fm-text);
  margin-bottom: 0.1rem;
}

[data-testid="stHeading"] h2,
[data-testid="stHeading"] h3 {
  color: var(--fm-text);
  letter-spacing: -0.018em;
}

[data-testid="stCaptionContainer"] p {
  color: var(--fm-muted);
  font-size: 0.82rem;
}

hr {
  border-color: var(--fm-border-soft);
}

/* Brand + sidebar */
.fm-brand {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.2rem 0 0.9rem;
  border-bottom: 1px solid var(--fm-border-soft);
  margin-bottom: 1rem;
}

.fm-brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #16253c;
  border: 1px solid #2a4264;
  color: #cfe0ff;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.fm-brand-name {
  color: var(--fm-text);
  font-size: 0.98rem;
  font-weight: 650;
  line-height: 1.15;
}

.fm-brand-sub {
  color: var(--fm-subtle);
  font-size: 0.7rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.fm-sidebar-label {
  color: var(--fm-subtle);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin: 0.2rem 0 0.45rem;
}

.fm-sidebar-note {
  color: var(--fm-subtle);
  font-size: 0.74rem;
  line-height: 1.5;
  border-top: 1px solid var(--fm-border-soft);
  padding-top: 0.8rem;
  margin-top: 0.35rem;
}

.fm-sidebar-note strong {
  color: var(--fm-muted);
  font-weight: 600;
}

[data-testid="stSidebar"] input {
  font-size: 0.78rem;
}

/* Run header */
.fm-run-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  flex-wrap: wrap;
  background: var(--fm-surface);
  border: 1px solid var(--fm-border);
  border-radius: 14px;
  padding: 1rem 1.15rem;
  margin: 0.35rem 0 1.05rem;
}

.fm-eyebrow {
  color: var(--fm-subtle);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  margin-bottom: 0.25rem;
}

.fm-run-id {
  color: var(--fm-text);
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 1.14rem;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.fm-run-context {
  color: var(--fm-muted);
  font-size: 0.76rem;
  margin-top: 0.3rem;
}

.fm-run-context span {
  color: #4d6079;
  padding: 0 0.2rem;
}

.fm-run-meta {
  display: flex;
  align-items: flex-start;
  gap: 1.7rem;
  flex-wrap: wrap;
}

.fm-meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 88px;
}

.fm-meta-label {
  color: var(--fm-subtle);
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.fm-meta-value {
  color: #dbe6f5;
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.fm-pill {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  padding: 0.18rem 0.55rem;
  border-radius: 999px;
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  border: 1px solid transparent;
}

.fm-pill-success {
  color: #a7f3d0;
  background: rgba(52, 211, 153, 0.12);
  border-color: rgba(52, 211, 153, 0.35);
}

.fm-pill-warning {
  color: #fde68a;
  background: rgba(251, 191, 36, 0.12);
  border-color: rgba(251, 191, 36, 0.35);
}

.fm-pill-danger {
  color: #fecaca;
  background: rgba(248, 113, 113, 0.12);
  border-color: rgba(248, 113, 113, 0.35);
}

.fm-pill-neutral {
  color: #dbe6f5;
  background: rgba(148, 163, 184, 0.12);
  border-color: rgba(148, 163, 184, 0.32);
}

/* Section headings */
.fm-section-head {
  margin: 0.1rem 0 0.95rem;
}

.fm-section-index {
  color: var(--fm-accent);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.fm-section-title {
  color: var(--fm-text);
  font-size: 1.18rem;
  font-weight: 620;
  letter-spacing: -0.018em;
  margin: 0.18rem 0 0;
}

.fm-section-desc {
  color: var(--fm-muted);
  font-size: 0.8rem;
  margin: 0.18rem 0 0;
  max-width: 72ch;
}

/* Panels and cards */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--fm-surface);
  border: 1px solid var(--fm-border) !important;
  border-radius: 14px !important;
}

.fm-card-title {
  color: #dbe6f5;
  font-size: 0.86rem;
  font-weight: 620;
  letter-spacing: -0.006em;
  margin: 0;
}

.fm-card-subtitle {
  color: var(--fm-subtle);
  font-size: 0.72rem;
  line-height: 1.45;
  margin: 0.18rem 0 0.3rem;
}

[data-testid="stMetric"] {
  background: var(--fm-surface);
  border: 1px solid var(--fm-border);
  border-radius: 12px;
  padding: 0.8rem 0.9rem 0.75rem;
  min-height: 112px;
  height: 100%;
}

[data-testid="stMetricLabel"] p {
  color: var(--fm-muted);
  font-size: 0.68rem;
  font-weight: 650;
  letter-spacing: 0.065em;
  text-transform: uppercase;
}

[data-testid="stMetricValue"] {
  color: var(--fm-text);
  font-size: 1.8rem;
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.15;
}

[data-testid="stTooltipIcon"] {
  color: #6f849f;
}

/* Status banners and empty states */
.fm-banner {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 0.72rem 0.85rem;
  margin: 0.25rem 0 0.7rem;
  font-size: 0.78rem;
  line-height: 1.45;
}

.fm-banner strong {
  display: block;
  font-weight: 600;
  margin-bottom: 0.08rem;
}

.fm-banner-detail {
  color: inherit;
  opacity: 0.82;
}

.fm-banner-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
  margin-top: 0.34rem;
}

.fm-banner-success {
  color: #b8f3d9;
  background: rgba(52, 211, 153, 0.07);
  border-color: rgba(52, 211, 153, 0.25);
}

.fm-banner-success .fm-banner-dot {
  background: var(--fm-success);
}

.fm-banner-warning {
  color: #fde7ae;
  background: rgba(251, 191, 36, 0.07);
  border-color: rgba(251, 191, 36, 0.25);
}

.fm-banner-warning .fm-banner-dot {
  background: var(--fm-warning);
}

.fm-banner-danger {
  color: #fecaca;
  background: rgba(248, 113, 113, 0.07);
  border-color: rgba(248, 113, 113, 0.25);
}

.fm-banner-danger .fm-banner-dot {
  background: var(--fm-danger);
}

.fm-banner-info {
  color: #cfe0ff;
  background: rgba(96, 165, 250, 0.07);
  border-color: rgba(96, 165, 250, 0.25);
}

.fm-banner-info .fm-banner-dot {
  background: var(--fm-info, #60a5fa);
}

.fm-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  min-height: 122px;
  padding: 1rem;
  border: 1px dashed #2a3b56;
  border-radius: 10px;
  background: rgba(15, 24, 40, 0.55);
  text-align: center;
}

.fm-empty strong {
  color: #cdd9e9;
  font-size: 0.82rem;
  font-weight: 600;
}

.fm-empty span {
  color: var(--fm-subtle);
  font-size: 0.74rem;
  max-width: 52ch;
  line-height: 1.45;
}

/* Route legend used beside the operating map */
.fm-legend {
  display: flex;
  flex-direction: column;
  gap: 0.42rem;
  max-height: 312px;
  overflow-y: auto;
  padding-right: 0.3rem;
}

.fm-legend::-webkit-scrollbar {
  width: 4px;
}

.fm-legend::-webkit-scrollbar-thumb {
  background: var(--fm-border);
  border-radius: 2px;
}

.fm-legend-row {
  display: flex;
  align-items: flex-start;
  gap: 0.55rem;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid var(--fm-border-soft);
}

.fm-legend-row:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.fm-legend-swatch {
  width: 9px;
  height: 9px;
  flex: 0 0 9px;
  border-radius: 2px;
  margin-top: 0.26rem;
}

.fm-legend-label {
  color: #dbe6f5;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 0.72rem;
  line-height: 1.25;
}

.fm-legend-note {
  color: var(--fm-subtle);
  font-size: 0.66rem;
  line-height: 1.3;
  margin-top: 0.1rem;
}

.fm-marker-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.75rem;
  margin-top: 0.7rem;
  padding-top: 0.65rem;
  border-top: 1px solid var(--fm-border-soft);
}

.fm-marker-item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  color: var(--fm-muted);
  font-size: 0.68rem;
}

.fm-marker-glyph {
  font-size: 0.84rem;
  line-height: 1;
}

/* Small stat grids used inside panels */
.fm-stat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
  margin-top: 0.15rem;
}

.fm-stat {
  background: #0c1624;
  border: 1px solid var(--fm-border-soft);
  border-radius: 10px;
  padding: 0.65rem 0.7rem;
}

.fm-stat span {
  display: block;
  color: var(--fm-subtle);
  font-size: 0.61rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.fm-stat strong {
  display: block;
  color: var(--fm-text);
  font-size: 1.18rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  margin-top: 0.24rem;
}

.fm-stat em {
  display: block;
  color: var(--fm-subtle);
  font-size: 0.66rem;
  font-style: normal;
  margin-top: 0.15rem;
}

.fm-note {
  color: var(--fm-subtle);
  font-size: 0.7rem;
  line-height: 1.5;
  margin: 0.55rem 0 0;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
  gap: 0.15rem;
  border-bottom: 1px solid var(--fm-border);
}

.stTabs [data-baseweb="tab"] {
  padding: 0.62rem 0.85rem;
  color: var(--fm-muted);
  font-size: 0.8rem;
  font-weight: 550;
}

.stTabs [aria-selected="true"] {
  color: var(--fm-text);
}

.stTabs [data-baseweb="tab-highlight"] {
  background-color: var(--fm-accent);
  height: 2px;
}

.stTabs [data-baseweb="tab-border"] {
  background-color: transparent;
}

/* Expanders, dataframes and inputs */
[data-testid="stExpander"] {
  background: var(--fm-surface);
  border: 1px solid var(--fm-border);
  border-radius: 11px;
}

[data-testid="stExpander"] summary {
  color: #cbd8e9;
  font-size: 0.8rem;
  font-weight: 550;
}

[data-testid="stExpander"] summary:hover {
  color: var(--fm-text);
}

[data-testid="stDataFrame"] {
  border: 1px solid var(--fm-border);
  border-radius: 10px;
  overflow: hidden;
}

[data-testid="stDataFrame"] [role="columnheader"] {
  font-weight: 600;
}

[data-testid="stAlertContainer"] {
  border-radius: 10px;
}

[data-baseweb="select"] > div,
[data-testid="stTextInput"] input {
  border-color: var(--fm-border) !important;
  background-color: var(--fm-surface) !important;
}

[data-testid="stTextInput"] input:focus {
  border-color: var(--fm-accent) !important;
  box-shadow: 0 0 0 1px rgba(79, 140, 255, 0.35);
}

/* Plotly chrome */
[data-testid="stPlotlyChart"] {
  border-radius: 10px;
  overflow: hidden;
}

.js-plotly-plot .plotly .modebar {
  opacity: 0.45;
}

.js-plotly-plot .plotly .modebar:hover {
  opacity: 1;
}

/* Accessibility */
button:focus-visible,
[data-baseweb="tab"]:focus-visible,
[data-testid="stExpander"] summary:focus-visible {
  outline: 2px solid var(--fm-accent);
  outline-offset: 2px;
}

/* Responsive refinements */
@media (max-width: 1400px) {
  [data-testid="stMetricLabel"] {
    height: 2.45rem;
    min-height: 2.45rem;
    align-items: flex-start;
  }

  [data-testid="stMetricLabel"] p {
    white-space: normal;
    line-height: 1.25;
  }
}

@media (max-width: 1200px) {
  [data-testid="stMainBlockContainer"] {
    padding-left: 1.2rem;
    padding-right: 1.2rem;
  }

  [data-testid="stMetricValue"] {
    font-size: 1.55rem;
  }

  .fm-run-header {
    padding: 0.9rem 1rem;
  }

  .fm-run-meta {
    gap: 1.2rem;
  }
}

@media (max-width: 900px) {
  .fm-run-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .fm-run-meta {
    width: 100%;
  }

  .fm-meta-item {
    min-width: 72px;
  }

  .fm-section-title {
    font-size: 1.08rem;
  }
}
"""


def inject_styles() -> None:
    """Attach the dashboard stylesheet once per script run."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Formatting and text helpers
# ---------------------------------------------------------------------------
def escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def humanize(value: Any) -> str:
    """Turn snake-case or upper-case identifiers into readable labels."""
    text = "" if value is None else str(value)
    if text in ISSUE_TYPE_LABELS:
        return ISSUE_TYPE_LABELS[text]
    words = text.replace("_", " ").strip().lower().split()
    if not words:
        return "Unknown"
    return " ".join(_ACRONYMS.get(word, word.capitalize()) for word in words)


def format_timestamp(value: Any) -> str:
    if value is None:
        return "Not recorded"
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return "Not recorded"
    if pd.isna(timestamp):
        return "Not recorded"
    return timestamp.strftime("%Y-%m-%d %H:%M")


def status_tone(status: Any) -> str:
    label = str(status).upper()
    if label == "SUCCEEDED":
        return "success"
    if label in {"PARTIAL", "RUNNING"}:
        return "warning"
    if label in {"FAILED", "ERROR"}:
        return "danger"
    return "neutral"


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------
def sidebar_brand() -> None:
    st.markdown(
        """
        <div class="fm-brand">
          <div class="fm-brand-mark">FM</div>
          <div>
            <div class="fm-brand-name">FleetMind</div>
            <div class="fm-brand-sub">Control Tower</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar_label(text: str) -> None:
    st.markdown(f'<div class="fm-sidebar-label">{escape(text)}</div>', unsafe_allow_html=True)


def sidebar_note() -> None:
    st.markdown(
        """
        <div class="fm-sidebar-note">
          <strong>Read-only snapshot.</strong><br>
          Local-only dashboard with no authentication. Keep it on loopback.
        </div>
        """,
        unsafe_allow_html=True,
    )


def run_header(run_id: str, metadata: dict[str, Any]) -> None:
    status = str(metadata.get("status") or "UNKNOWN").upper()
    tone = status_tone(status)
    mode = metadata.get("mode") or "n/a"
    seed = metadata.get("seed") if metadata.get("seed") is not None else "n/a"
    dataset = metadata.get("dataset_version") or "n/a"
    started = format_timestamp(metadata.get("started_at"))
    completed = format_timestamp(metadata.get("completed_at"))
    st.markdown(
        f"""
        <section class="fm-run-header" aria-label="Run summary">
          <div class="fm-run-identity">
            <div class="fm-eyebrow">Run snapshot</div>
            <div class="fm-run-id">{escape(run_id)}</div>
            <div class="fm-run-context">
              {escape(mode)} mode <span>·</span>
              Seed {escape(seed)} <span>·</span>
              Dataset {escape(dataset)}
            </div>
          </div>
          <div class="fm-run-meta">
            <div class="fm-meta-item">
              <span class="fm-meta-label">Status</span>
              <span class="fm-pill fm-pill-{tone}">{escape(status)}</span>
            </div>
            <div class="fm-meta-item">
              <span class="fm-meta-label">Started</span>
              <span class="fm-meta-value">{escape(started)}</span>
            </div>
            <div class="fm-meta-item">
              <span class="fm-meta-label">Completed</span>
              <span class="fm-meta-value">{escape(completed)}</span>
            </div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def section_header(index: str, title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="fm-section-head">
          <div class="fm-section-index">{escape(index)}</div>
          <div class="fm-section-title">{escape(title)}</div>
          <p class="fm-section-desc">{escape(description)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_banner(title: str, detail: str | None = None, tone: str = "info") -> None:
    tone = tone if tone in {"success", "warning", "danger", "info"} else "info"
    detail_html = (
        f'<span class="fm-banner-detail">{escape(detail)}</span>' if detail else ""
    )
    st.markdown(
        f"""
        <div class="fm-banner fm-banner-{tone}" role="status">
          <span class="fm-banner-dot"></span>
          <div><strong>{escape(title)}</strong>{detail_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_state(title: str, detail: str = "") -> None:
    detail_html = f"<span>{escape(detail)}</span>" if detail else ""
    st.markdown(
        f"""
        <div class="fm-empty" role="status">
          <strong>{escape(title)}</strong>
          {detail_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_grid(items: Sequence[dict[str, Any]], columns: int | None = None) -> None:
    """Render aligned native ``st.metric`` cards with optional tooltips."""
    if not items:
        return
    column_count = columns or len(items)
    holders = st.columns(column_count, gap="small")
    for holder, item in zip(holders, items):
        with holder:
            st.metric(
                str(item.get("label", "")),
                str(item.get("value", "")),
                help=item.get("help"),
            )


@contextmanager
def panel(title: str, subtitle: str | None = None) -> Iterator[None]:
    """A bordered surface with a consistent card heading."""
    with st.container(border=True, gap="small"):
        st.markdown(
            f'<p class="fm-card-title">{escape(title)}</p>',
            unsafe_allow_html=True,
        )
        if subtitle:
            st.markdown(
                f'<p class="fm-card-subtitle">{escape(subtitle)}</p>',
                unsafe_allow_html=True,
            )
        yield


def stat_grid(items: Sequence[tuple[str, str, str | None]]) -> None:
    cells: list[str] = []
    for label, value, note in items:
        note_html = f"<em>{escape(note)}</em>" if note else ""
        cells.append(
            f'<div class="fm-stat"><span>{escape(label)}</span>'
            f"<strong>{escape(value)}</strong>{note_html}</div>"
        )
    st.markdown(
        f'<div class="fm-stat-grid">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


def route_legend(items: Sequence[tuple[str, str, str]]) -> None:
    """Render a compact colour key for the operating map."""
    rows = "".join(
        f'<div class="fm-legend-row">'
        f'<span class="fm-legend-swatch" style="background:{escape(color)}"></span>'
        f'<div><div class="fm-legend-label">{escape(label)}</div>'
        f'<div class="fm-legend-note">{escape(note)}</div></div>'
        f'</div>'
        for color, label, note in items
    )
    st.markdown(f'<div class="fm-legend">{rows}</div>', unsafe_allow_html=True)


def marker_legend(items: Sequence[tuple[str, str, str]]) -> None:
    """Render stop-status and depot symbols for the operating map."""
    rows = "".join(
        f'<span class="fm-marker-item">'
        f'<span class="fm-marker-glyph" style="color:{escape(color)}">'
        f'{escape(symbol)}</span>{escape(label)}</span>'
        for color, symbol, label in items
    )
    st.markdown(
        f'<div class="fm-marker-legend">{rows}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def table_height(rows: int, max_height: int = 460, row_height: int = 32) -> int:
    return min(max_height, 42 + max(1, int(rows)) * row_height)


def table(
    frame: pd.DataFrame,
    *,
    columns: Sequence[str] | None = None,
    config: dict[str, Any] | None = None,
    height: int | None = None,
    empty_title: str = "No records",
    empty_detail: str = "",
    row_height: int = 32,
) -> None:
    if frame is None or frame.empty:
        empty_state(empty_title, empty_detail)
        return
    available = [column for column in columns if column in frame.columns] if columns else list(frame.columns)
    if not available:
        empty_state(empty_title, empty_detail)
        return
    view = frame[available]
    if config:
        config = {key: value for key, value in config.items() if key in available}
    st.dataframe(
        view,
        width="stretch",
        hide_index=True,
        height=height or table_height(len(view), row_height=row_height),
        column_config=config or {},
        row_height=row_height,
    )


# ---------------------------------------------------------------------------
# Plotly theming
# ---------------------------------------------------------------------------
def _base_layout(fig: go.Figure, height: int) -> None:
    fig.update_layout(
        template="plotly_dark",
        height=height,
        margin={"l": 8, "r": 8, "t": 28, "b": 24},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "color": TEXT, "size": 12},
        colorway=list(ROUTE_COLORS),
        hoverlabel={
            "bgcolor": SURFACE_RAISED,
            "bordercolor": BORDER,
            "font": {"family": FONT, "color": TEXT, "size": 12},
        },
        legend={
            "bgcolor": "rgba(0,0,0,0)",
            "font": {"family": FONT, "color": MUTED, "size": 11},
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.01,
            "xanchor": "left",
            "x": 0,
        },
    )


def style_figure(fig: go.Figure, height: int = 300) -> go.Figure:
    """Apply the shared Plotly theme to a cartesian figure."""
    _base_layout(fig, height)
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        linecolor=BORDER,
        tickfont={"color": MUTED, "size": 11},
        title_font={"color": MUTED, "size": 11},
        automargin=True,
    )
    fig.update_yaxes(
        gridcolor=GRID,
        zeroline=False,
        linecolor=BORDER,
        tickfont={"color": MUTED, "size": 11},
        title_font={"color": MUTED, "size": 11},
        automargin=True,
    )
    return fig


def plot(fig: go.Figure, height: int = 300) -> None:
    style_figure(fig, height=height)
    st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        config={"displaylogo": False},
    )


def plot_map(fig: go.Figure, height: int = 470) -> None:
    """Render a coordinate map with quiet geographic axes."""
    style_figure(fig, height=height)
    fig.update_xaxes(
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        nticks=6,
        tickformat=".2f",
        title_text="Longitude",
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        nticks=6,
        tickformat=".2f",
        title_text="Latitude",
    )
    st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        config={"displaylogo": False},
    )


def plot_heatmap(fig: go.Figure, height: int = 300) -> None:
    """Apply the shared theme to a matrix figure with quiet axes."""
    _base_layout(fig, height)
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        showline=False,
        tickfont={"color": MUTED, "size": 11},
        side="top",
    )
    fig.update_yaxes(
        showgrid=False,
        zeroline=False,
        showline=False,
        tickfont={"color": MUTED, "size": 11},
        autorange="reversed",
        automargin=True,
    )
    st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        config={"displaylogo": False},
    )
