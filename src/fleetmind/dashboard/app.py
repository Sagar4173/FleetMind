"""FleetMind Streamlit dashboard.

Launch from the repository root with::

    streamlit run dashboard/app.py -- --database outputs/fleetmind.duckdb

The dashboard is strictly read-only and queries the DuckDB warehouse directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from fleetmind.dashboard.data import (
    anomaly_metrics_frame,
    daily_frame,
    issue_summary,
    list_runs,
    load_run_data,
    metric_value,
    parse_dimensions,
    route_map_data,
    route_summary_frame,
    scalar_metrics,
    with_dimensions,
)

DEFAULT_DB = Path("outputs") / "fleetmind.duckdb"


def _pct(value: float) -> str:
    return f"{float(value):.1%}"


def _num(value: float, digits: int = 0) -> str:
    return f"{float(value):,.{digits}f}"


def _status_badge(status: str) -> None:
    label = str(status).upper()
    if label == "SUCCEEDED":
        st.success(f"Run status: {label}")
    elif label in {"PARTIAL", "RUNNING"}:
        st.warning(f"Run status: {label}")
    else:
        st.error(f"Run status: {label}")


def _filter_dates(routes: pd.DataFrame) -> list[str]:
    if routes.empty or "service_date" not in routes:
        return []
    values = {
        pd.Timestamp(value).date().isoformat()
        for value in routes["service_date"].dropna().tolist()
    }
    return sorted(values)


def _route_map(tables: dict[str, pd.DataFrame], service_date: str) -> go.Figure:
    map_data = route_map_data(tables, service_date)
    routes = map_data["routes"]
    stops = map_data["stops"]
    depot = map_data["depot"]
    figure = go.Figure()
    if stops.empty:
        figure.update_layout(height=420, title="No route stops for this date")
        return figure
    depot_lat = float(depot["latitude"].iloc[0]) if not depot.empty else 32.7767
    depot_lon = float(depot["longitude"].iloc[0]) if not depot.empty else -96.797
    colors = px.colors.qualitative.Dark24 + px.colors.qualitative.Light24
    for index, (route_id, group) in enumerate(stops.groupby("route_id", sort=True)):
        group = group.sort_values("stop_sequence")
        group = group.dropna(subset=["latitude", "longitude"])
        if group.empty:
            continue
        lats = [depot_lat, *group["latitude"].astype(float).tolist(), depot_lat]
        lons = [depot_lon, *group["longitude"].astype(float).tolist(), depot_lon]
        labels = ["DEPOT", *group["site_id"].astype(str).tolist(), "DEPOT"]
        statuses = ["DEPOT", *group.get("status", pd.Series(["ASSIGNED"] * len(group))).astype(str).tolist(), "DEPOT"]
        hover = [
            f"{label}<br>Stop status: {status}"
            for label, status in zip(labels, statuses, strict=True)
        ]
        color = colors[index % len(colors)]
        figure.add_trace(
            go.Scattergeo(
                lat=lats,
                lon=lons,
                mode="lines+markers",
                line={"width": 1.7, "color": color},
                marker={"size": 5, "color": color},
                text=hover,
                hoverinfo="text",
                name=str(route_id),
            )
        )
    figure.update_geos(
        scope="usa",
        fitbounds="locations",
        showland=True,
        landcolor="#f1f5f9",
        showlakes=False,
        showcountries=False,
        showframe=False,
        bgcolor="rgba(0,0,0,0)",
    )
    figure.update_layout(
        height=520,
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.05},
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return figure


def _render_overview(metrics: pd.DataFrame) -> None:
    service = metric_value(metrics, "service", "delivery_service_rate")
    on_time = metric_value(metrics, "service", "on_time_rate")
    capacity = metric_value(metrics, "routing", "capacity_utilization_units")
    fleet = metric_value(metrics, "fleet", "fleet_utilization")
    unserved = metric_value(metrics, "service", "unserved_rate")
    failure = metric_value(metrics, "service", "failure_rate")
    columns = st.columns(6)
    columns[0].metric("Service rate", _pct(service), help="Delivered / requested deliveries")
    columns[1].metric("On-time rate", _pct(on_time), help="On-time delivered / delivered")
    columns[2].metric("Capacity use", _pct(capacity), help="Load units / route capacity")
    columns[3].metric("Fleet use", _pct(fleet), help="Routed vehicles / active vehicles")
    columns[4].metric("Unserved", _pct(unserved), help="Unserved / requested deliveries")
    columns[5].metric("Failure rate", _pct(failure), help="Failed / assigned deliveries")

    daily = daily_frame(metrics)
    left, right = st.columns((1.6, 1))
    with left:
        if not daily.empty:
            melted = daily.melt(
                id_vars="service_date",
                value_vars=[column for column in ("delivered", "failed") if column in daily],
                var_name="outcome",
                value_name="deliveries",
            )
            figure = px.bar(
                melted,
                x="service_date",
                y="deliveries",
                color="outcome",
                barmode="group",
                title="Executed deliveries by service date",
                labels={"service_date": "Service date", "deliveries": "Deliveries"},
            )
            figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0}, legend_title_text="")
            st.plotly_chart(figure, width="stretch")
        else:
            st.info("Daily delivery metrics are not available.")
    with right:
        priority = with_dimensions(metrics, "service_priority", "delivery_service_rate")
        if not priority.empty:
            priority["priority"] = priority["entity"]
            figure = px.bar(
                priority,
                x="priority",
                y="value",
                text=priority["value"].map(_pct),
                title="Service rate by priority",
                labels={"value": "Service rate", "priority": "Priority"},
                color="priority",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            figure.update_yaxes(tickformat=".0%", range=[0, 1.05])
            figure.update_layout(showlegend=False, margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")
        if not daily.empty and "on_time_rate" in daily:
            figure = px.line(
                daily,
                x="service_date",
                y="on_time_rate",
                markers=True,
                title="On-time rate by service date",
                labels={"service_date": "Service date", "on_time_rate": "On-time rate"},
            )
            figure.update_yaxes(tickformat=".0%", range=[0, 1.05])
            figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")

    left, right = st.columns(2)
    with left:
        handling = with_dimensions(metrics, "service_handling", "delivery_service_rate")
        if not handling.empty:
            handling["handling_class"] = handling["entity"]
            figure = px.bar(
                handling,
                x="handling_class",
                y="value",
                title="Service rate by handling class",
                labels={"value": "Service rate", "handling_class": "Handling class"},
                color="handling_class",
                color_discrete_sequence=px.colors.qualitative.Pastel,
            )
            figure.update_yaxes(tickformat=".0%", range=[0, 1.05])
            figure.update_layout(showlegend=False, margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")
    with right:
        city = with_dimensions(metrics, "site_city", "delivered_deliveries")
        if not city.empty:
            city = city.sort_values("value", ascending=False).head(10)
            city["city"] = city["entity"]
            figure = px.bar(
                city,
                x="value",
                y="city",
                orientation="h",
                title="Delivered volume by city",
                labels={"value": "Deliveries", "city": "City"},
                color="city",
                color_discrete_sequence=px.colors.qualitative.T10,
            )
            figure.update_layout(showlegend=False, margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")

    with st.expander("Run metadata and audit trail"):
        metadata = st.session_state.get("_metadata", {})
        st.write(
            {
                "run_id": metadata.get("run_id"),
                "mode": metadata.get("mode"),
                "seed": metadata.get("seed"),
                "dataset_version": metadata.get("dataset_version"),
                "pipeline_version": metadata.get("pipeline_version"),
                "started_at": str(metadata.get("started_at")),
                "completed_at": str(metadata.get("completed_at")),
                "stage_durations_seconds": metadata.get("stage_durations", {}),
            }
        )
        audit = st.session_state.get("_audit", pd.DataFrame())
        if not audit.empty:
            audit = audit.copy()
            if "details_json" in audit:
                audit["details_json"] = audit["details_json"].astype(str).map(
                    lambda value: value[:160] + ("..." if len(value) > 160 else "")
                )
            st.dataframe(audit, width="stretch", hide_index=True)


def _render_routing(tables: dict[str, pd.DataFrame], metrics: pd.DataFrame) -> None:
    summary = route_summary_frame(tables)
    dates = _filter_dates(summary)
    if not dates:
        st.info("No routes were planned for this run.")
        return
    selected = st.selectbox("Service date", dates, index=len(dates) - 1, key="route_date")
    figure = _route_map(tables, selected)
    st.plotly_chart(figure, width="stretch")

    day_routes = summary[
        summary["service_date"].map(lambda value: pd.Timestamp(value).date().isoformat())
        == selected
    ]
    columns = st.columns(4)
    columns[0].metric("Routes", _num(len(day_routes)))
    columns[1].metric("Stops", _num(day_routes["stop_count"].sum() if "stop_count" in day_routes else 0))
    columns[2].metric("Distance", f"{_num(day_routes['distance_km'].sum() if 'distance_km' in day_routes else 0, 1)} km")
    columns[3].metric("Shift overruns", _num((day_routes.get("shift_overrun_minutes", 0) > 0).sum()))

    left, right = st.columns((1.4, 1))
    with left:
        st.subheader("Route workload")
        display_columns = [
            column
            for column in (
                "route_id",
                "vehicle_id",
                "vehicle_type",
                "stop_count",
                "delivered_stops",
                "failed_stops",
                "load_units",
                "capacity_units",
                "capacity_utilization",
                "distance_km",
                "travel_minutes",
                "wait_minutes",
                "shift_overrun_minutes",
            )
            if column in day_routes
        ]
        view = day_routes[display_columns].copy()
        if "capacity_utilization" in view:
            view["capacity_utilization"] = view["capacity_utilization"].map(
                lambda value: _pct(value) if pd.notna(value) else ""
            )
        st.dataframe(view, width="stretch", hide_index=True)
    with right:
        if "capacity_utilization" in summary:
            utilization = summary.dropna(subset=["capacity_utilization"]).copy()
            if not utilization.empty:
                utilization["utilization_pct"] = utilization["capacity_utilization"] * 100
                figure = px.histogram(
                    utilization,
                    x="utilization_pct",
                    nbins=min(20, max(5, len(utilization) // 2)),
                    title="Route capacity utilization",
                    labels={"utilization_pct": "Capacity utilization (%)"},
                    color_discrete_sequence=["#2563eb"],
                )
                figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
                st.plotly_chart(figure, width="stretch")
        vehicle_type = with_dimensions(metrics, "fleet_type", "distance_km")
        if not vehicle_type.empty:
            vehicle_type["vehicle_type"] = vehicle_type["entity"]
            figure = px.bar(
                vehicle_type,
                x="vehicle_type",
                y="value",
                title="Distance by vehicle type",
                labels={"value": "Distance (km)", "vehicle_type": "Vehicle type"},
                color="vehicle_type",
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            figure.update_layout(showlegend=False, margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")


def _render_execution(tables: dict[str, pd.DataFrame], metrics: pd.DataFrame) -> None:
    outcomes = tables.get("delivery_outcomes", pd.DataFrame())
    routes = tables.get("routes", pd.DataFrame())
    if outcomes.empty:
        st.info("No execution outcomes are available.")
        return
    status_counts = (
        outcomes["status"].astype(str).value_counts().rename_axis("status").reset_index(name="count")
    )
    columns = st.columns(4)
    delivered_count = int((outcomes["status"].astype(str) == "DELIVERED").sum())
    failed_count = int((outcomes["status"].astype(str) == "FAILED").sum())
    unserved_count = int((outcomes["status"].astype(str) == "UNSERVED").sum())
    on_time_count = int(
        outcomes.loc[outcomes["status"].astype(str) == "DELIVERED", "on_time"]
        .fillna(False)
        .map(bool)
        .sum()
    ) if "on_time" in outcomes else 0
    columns[0].metric("Delivered", _num(delivered_count))
    columns[1].metric("Failed", _num(failed_count))
    columns[2].metric("Unserved", _num(unserved_count))
    columns[3].metric("On-time", _num(on_time_count))

    left, right = st.columns(2)
    with left:
        figure = px.pie(
            status_counts,
            names="status",
            values="count",
            title="Delivery outcomes",
            color="status",
            color_discrete_map={"DELIVERED": "#16a34a", "FAILED": "#dc2626", "UNSERVED": "#d97706"},
            hole=0.45,
        )
        figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
        st.plotly_chart(figure, width="stretch")
    with right:
        failed = outcomes[outcomes["status"].astype(str) == "FAILED"]
        if not failed.empty and "reason_code" in failed:
            reasons = (
                failed["reason_code"].fillna("UNKNOWN").astype(str).value_counts()
                .rename_axis("reason").reset_index(name="count")
            )
            figure = px.bar(
                reasons,
                x="count",
                y="reason",
                orientation="h",
                title="Failure reasons",
                labels={"count": "Deliveries", "reason": "Reason"},
                color_discrete_sequence=["#dc2626"],
            )
            figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")
        else:
            st.success("No failed deliveries in this run.")

    priority = with_dimensions(metrics, "service_priority", "on_time_rate")
    if not priority.empty:
        priority["priority"] = priority["entity"]
        figure = px.bar(
            priority,
            x="priority",
            y="value",
            text=priority["value"].map(_pct),
            title="On-time rate by priority",
            labels={"value": "On-time rate", "priority": "Priority"},
            color="priority",
            color_discrete_sequence=px.colors.qualitative.Bold,
        )
        figure.update_yaxes(tickformat=".0%", range=[0, 1.05])
        figure.update_layout(showlegend=False, margin={"l": 0, "r": 0, "t": 50, "b": 0})
        st.plotly_chart(figure, width="stretch")

    if not routes.empty:
        overruns = routes[
            pd.to_numeric(routes.get("shift_overrun_minutes", 0), errors="coerce").fillna(0) > 0
        ]
        st.subheader("Shift overruns")
        if overruns.empty:
            st.success("No shift overruns were recorded.")
        else:
            st.dataframe(
                overruns[
                    [
                        column
                        for column in (
                            "route_id",
                            "service_date",
                            "driver_id",
                            "planned_end",
                            "actual_end",
                            "shift_overrun_minutes",
                        )
                        if column in overruns
                    ]
                ],
                width="stretch",
                hide_index=True,
            )


def _render_quality(
    tables: dict[str, pd.DataFrame], metadata: dict[str, Any]
) -> None:
    issues = tables.get("data_quality_issues", pd.DataFrame())
    quarantine = tables.get("quarantine_records", pd.DataFrame())
    reconciliation = tables.get("reconciliation_checks", pd.DataFrame())
    clean_issues = issues[issues["issue_id"].astype(str).str.startswith("DQ-")] if not issues.empty else issues
    dirty_issues = issues[issues["issue_id"].astype(str).str.startswith("DQD-")] if not issues.empty else issues
    columns = st.columns(4)
    columns[0].metric("Clean issues", _num(len(clean_issues)))
    columns[1].metric("Dirty-snapshot issues", _num(len(dirty_issues)))
    columns[2].metric("Quarantine records", _num(len(quarantine)))
    passed = int(pd.Series(reconciliation["passed"]).fillna(False).map(bool).sum()) if not reconciliation.empty else 0
    columns[3].metric(
        "Reconciliation", f"{passed}/{len(reconciliation)}",
        help="Cross-table arithmetic checks that passed",
    )
    validation = metadata.get("validation", {})
    if isinstance(validation, str):
        try:
            validation = json.loads(validation)
        except json.JSONDecodeError:
            validation = {}
    if validation:
        st.caption(
            f"Clean validation: {'PASS' if validation.get('clean_passed') else 'FAIL'} | "
            f"anomaly-snapshot validation: "
            f"{'expected errors present' if not validation.get('dirty_passed') else 'PASS'}"
        )

    left, right = st.columns((1, 1.3))
    with left:
        summary = issue_summary(issues)
        if not summary.empty:
            figure = px.bar(
                summary,
                x="count",
                y="issue_type",
                color="severity",
                orientation="h",
                title="Issues by type and severity",
                labels={"count": "Issues", "issue_type": "Issue type"},
                color_discrete_sequence=px.colors.qualitative.Safe,
            )
            figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
            st.plotly_chart(figure, width="stretch")
        else:
            st.success("No data-quality issues were recorded.")
    with right:
        st.subheader("Reconciliation checks")
        if reconciliation.empty:
            st.info("No reconciliation checks were recorded.")
        else:
            view = reconciliation.copy()
            if "passed" in view:
                view["passed"] = view["passed"].map(lambda value: "PASS" if bool(value) else "FAIL")
            st.dataframe(
                view[
                    [
                        column
                        for column in ("check_name", "expected", "actual", "difference", "passed")
                        if column in view
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

    st.subheader("Validation issue inventory")
    if issues.empty:
        st.info("No issues were detected in either snapshot.")
    else:
        display = issues.copy()
        display["snapshot"] = display["issue_id"].astype(str).map(
            lambda value: "anomaly" if value.startswith("DQD-") else "clean"
        )
        st.dataframe(
            display[
                [
                    column
                    for column in (
                        "issue_id",
                        "snapshot",
                        "issue_type",
                        "severity",
                        "entity_type",
                        "entity_id",
                        "field_name",
                        "message",
                        "source_table",
                        "quarantined",
                    )
                    if column in display
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    with st.expander("Quarantine records"):
        if quarantine.empty:
            st.info("No records were quarantined.")
        else:
            st.dataframe(quarantine, width="stretch", hide_index=True)


def _render_anomalies(tables: dict[str, pd.DataFrame]) -> None:
    truth = tables.get("ground_truth_anomalies", pd.DataFrame())
    detections = tables.get("anomaly_detections", pd.DataFrame())
    metrics = anomaly_metrics_frame(tables.get("anomaly_detection_metrics", pd.DataFrame()))
    if truth.empty and detections.empty:
        st.info(
            "This run did not include anomaly injection. Re-run the pipeline with the FULL "
            "mode to populate ground truth and detection measurements."
        )
        return
    columns = st.columns(4)
    columns[0].metric("Injected anomalies", _num(len(truth)))
    columns[1].metric("Detections", _num(len(detections)))
    columns[2].metric("Anomaly types", _num(metrics["issue_type"].nunique() if not metrics.empty else 0))
    columns[3].metric(
        "Mean F1", f"{metrics['f1'].mean():.3f}" if not metrics.empty else "n/a"
    )
    if not metrics.empty:
        left, right = st.columns(2)
        with left:
            melted = metrics.melt(
                id_vars="issue_type",
                value_vars=["tp", "fp", "fn"],
                var_name="outcome",
                value_name="count",
            )
            figure = px.bar(
                melted,
                x="issue_type",
                y="count",
                color="outcome",
                barmode="group",
                title="Confusion matrix by anomaly type",
                labels={"issue_type": "Anomaly type", "count": "Entities"},
                color_discrete_map={"tp": "#16a34a", "fp": "#dc2626", "fn": "#d97706"},
            )
            figure.update_xaxes(tickangle=-25)
            figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0}, legend_title_text="")
            st.plotly_chart(figure, width="stretch")
        with right:
            melted = metrics.melt(
                id_vars="issue_type",
                value_vars=["precision", "recall", "f1"],
                var_name="metric",
                value_name="score",
            )
            figure = px.bar(
                melted,
                x="issue_type",
                y="score",
                color="metric",
                barmode="group",
                title="Detection quality",
                labels={"issue_type": "Anomaly type", "score": "Score"},
                color_discrete_sequence=px.colors.qualitative.Set1,
            )
            figure.update_yaxes(range=[0, 1.05], tickformat=".0%")
            figure.update_xaxes(tickangle=-25)
            figure.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0}, legend_title_text="")
            st.plotly_chart(figure, width="stretch")
        st.dataframe(
            metrics[
                [
                    column
                    for column in (
                        "issue_type",
                        "tp",
                        "fp",
                        "fn",
                        "tn",
                        "precision",
                        "recall",
                        "f1",
                        "evaluated_universe",
                        "notes",
                    )
                    if column in metrics
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    with st.expander("Ground truth"):
        st.dataframe(truth, width="stretch", hide_index=True)
    with st.expander("Detections"):
        st.dataframe(detections, width="stretch", hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="FleetMind Control Tower",
        page_icon="FM",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title("FleetMind Control Tower")
    st.caption("Deterministic logistics data, routing, execution and anomaly observability")

    default_db = Path(os.environ.get("FLEETMIND_DB", DEFAULT_DB))
    with st.sidebar:
        st.header("Run selector")
        db_text = st.text_input("DuckDB warehouse", value=str(default_db))
        db_path = Path(db_text)
        if not db_path.exists():
            st.error("Warehouse not found. Run `fleetmind run --config configs/starter.yaml` first.")
            st.stop()
        runs = list_runs(db_path)
        if runs.empty:
            st.error("No completed runs exist in this warehouse.")
            st.stop()
        runs = runs.copy()
        runs["label"] = runs.apply(
            lambda row: (
                f"{row['run_id']} | {row['status']} | "
                f"{pd.Timestamp(row['started_at']).date().isoformat()}"
            ),
            axis=1,
        )
        selected_label = st.selectbox("Run", runs["label"].tolist())
        selected_run = str(runs.loc[runs["label"] == selected_label, "run_id"].iloc[0])
        st.divider()
        st.caption(
            "All views are read-only. Switch runs by selecting a different snapshot or warehouse."
        )

    data = load_run_data(db_path, selected_run)
    metadata = data.get("_metadata", {})
    tables = {name: frame for name, frame in data.items() if not name.startswith("_")}
    st.session_state["_metadata"] = metadata
    st.session_state["_audit"] = tables.get("audit_records", pd.DataFrame())

    header = st.columns((2, 1, 1, 1))
    header[0].subheader(selected_run)
    header[1].metric("Mode", str(metadata.get("mode", "n/a")))
    header[2].metric("Seed", str(metadata.get("seed", "n/a")))
    header[3].metric(
        "Completed",
        pd.Timestamp(metadata["completed_at"]).strftime("%Y-%m-%d %H:%M")
        if metadata.get("completed_at") is not None
        else "n/a",
    )
    _status_badge(str(metadata.get("status", "UNKNOWN")))

    metrics = tables.get("report_metrics", pd.DataFrame())
    tabs = st.tabs(
        ["Overview", "Routing & fleet", "Execution", "Data quality", "Anomalies"]
    )
    with tabs[0]:
        _render_overview(metrics)
    with tabs[1]:
        _render_routing(tables, metrics)
    with tabs[2]:
        _render_execution(tables, metrics)
    with tabs[3]:
        _render_quality(tables, metadata)
    with tabs[4]:
        _render_anomalies(tables)

    st.divider()
    st.caption(
        "FleetMind v1.0.0 | Deterministic baseline seed 20261022 | "
        "DuckDB warehouse, OR-Tools routing, independent anomaly detectors"
    )


if __name__ == "__main__":
    main()
