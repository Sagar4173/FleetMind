"""Read-only data access and pure helpers for the Streamlit dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from fleetmind.persistence import Warehouse, WarehouseError

REQUIRED_TABLES = (
    "run_metadata",
    "report_metrics",
    "reconciliation_checks",
    "routes",
    "route_stops",
    "route_legs",
    "delivery_outcomes",
    "deliveries",
    "delivery_assignments",
    "customer_sites",
    "depots",
    "vehicles",
    "shifts",
    "data_quality_issues",
    "quarantine_records",
    "ground_truth_anomalies",
    "anomaly_detections",
    "anomaly_detection_metrics",
    "audit_records",
)


def database_available(db_path: Path | str) -> bool:
    return Path(db_path).exists()


def list_runs(db_path: Path | str) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    try:
        with Warehouse(path, read_only=True, initialize=False) as warehouse:
            return warehouse.runs()
    except WarehouseError:
        return pd.DataFrame()


def load_run_data(db_path: Path | str, run_id: str) -> dict[str, pd.DataFrame]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"warehouse does not exist: {path}")
    output: dict[str, pd.DataFrame] = {}
    with Warehouse(path, read_only=True, initialize=False) as warehouse:
        for table in REQUIRED_TABLES:
            try:
                output[table] = warehouse.table(table, run_id)
            except WarehouseError:
                output[table] = pd.DataFrame()
    metadata = output.get("run_metadata", pd.DataFrame())
    output["run_metadata"] = metadata
    output["_metadata"] = parse_metadata(metadata)
    return output


def parse_metadata(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    row = frame.iloc[0].to_dict()
    for field in ("stage_durations_json", "counts_json", "checksums_json"):
        raw = row.get(field)
        if isinstance(raw, str) and raw:
            try:
                row[field.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                row[field.replace("_json", "")] = {}
        else:
            row[field.replace("_json", "")] = {}
    return row


def metric_value(
    metrics: pd.DataFrame, category: str, name: str, dimensions: str = ""
) -> float:
    if metrics.empty:
        return 0.0
    found = metrics[
        (metrics["category"].astype(str) == category)
        & (metrics["metric_name"].astype(str) == name)
        & (metrics["dimensions"].fillna("").astype(str) == dimensions)
    ]
    return float(found["value"].iloc[0]) if len(found) else 0.0


def scalar_metrics(metrics: pd.DataFrame) -> dict[str, float]:
    if metrics.empty:
        return {}
    scalar = metrics[metrics["dimensions"].fillna("").astype(str) == ""]
    return {
        f"{row.category}.{row.metric_name}": float(row.value)
        for row in scalar.itertuples(index=False)
    }


def with_dimensions(metrics: pd.DataFrame, category: str, name: str) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame(columns=["dimensions", "value", "label", "entity"])
    subset = metrics[
        (metrics["category"].astype(str) == category)
        & (metrics["metric_name"].astype(str) == name)
    ].copy()
    if subset.empty:
        return pd.DataFrame(columns=["dimensions", "value", "label", "entity"])
    parsed = subset["dimensions"].fillna("").astype(str).map(parse_dimensions)
    subset["entity"] = parsed.map(lambda item: next(iter(item.values()), ""))
    subset["label"] = [
        " / ".join(f"{key}: {value}" for key, value in item.items())
        for item in parsed
    ]
    return subset.reset_index(drop=True)


def parse_dimensions(value: str) -> dict[str, str]:
    if not value:
        return {}
    output: dict[str, str] = {}
    for part in str(value).split(";"):
        if "=" in part:
            key, item = part.split("=", 1)
            output[key.strip()] = item.strip()
    return output


def daily_frame(metrics: pd.DataFrame) -> pd.DataFrame:
    delivered = with_dimensions(metrics, "daily", "delivered_deliveries")
    failed = with_dimensions(metrics, "daily", "failed_deliveries")
    on_time = with_dimensions(metrics, "daily", "on_time_rate")
    frames = []
    for name, frame in (
        ("delivered", delivered),
        ("failed", failed),
        ("on_time_rate", on_time),
    ):
        if frame.empty:
            continue
        frame = frame.rename(columns={"value": name})
        frame["service_date"] = frame["entity"].map(
            lambda item: parse_dimensions(item).get("service_date", item)
        )
        frames.append(frame[["service_date", name]])
    if not frames:
        return pd.DataFrame(columns=["service_date", "delivered", "failed", "on_time_rate"])
    output = frames[0]
    for frame in frames[1:]:
        output = output.merge(frame, on="service_date", how="outer")
    return output.sort_values("service_date").reset_index(drop=True)


def route_map_data(
    tables: dict[str, pd.DataFrame],
    service_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    routes = tables.get("routes", pd.DataFrame())
    stops = tables.get("route_stops", pd.DataFrame())
    sites = tables.get("customer_sites", pd.DataFrame())
    depots = tables.get("depots", pd.DataFrame())
    if routes.empty or stops.empty:
        return {"routes": pd.DataFrame(), "stops": pd.DataFrame(), "depot": pd.DataFrame()}
    routes = routes.copy()
    if service_date and "service_date" in routes:
        routes = routes[
            routes["service_date"].map(lambda value: pd.Timestamp(value).date().isoformat())
            == service_date
        ]
    route_ids = set(routes["route_id"].astype(str))
    stops = stops[stops["route_id"].astype(str).isin(route_ids)].copy()
    if not sites.empty:
        stops = stops.merge(
            sites[["site_id", "latitude", "longitude", "city", "site_name"]],
            on="site_id",
            how="left",
        )
    return {
        "routes": routes,
        "stops": stops,
        "depot": depots.head(1).copy(),
    }


def route_summary_frame(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    routes = tables.get("routes", pd.DataFrame())
    vehicles = tables.get("vehicles", pd.DataFrame())
    if routes.empty:
        return pd.DataFrame()
    output = routes.copy()
    if not vehicles.empty and "vehicle_type" in vehicles:
        output = output.merge(
            vehicles[["vehicle_id", "vehicle_type"]], on="vehicle_id", how="left"
        )
    columns = [
        column
        for column in (
            "route_id",
            "service_date",
            "vehicle_id",
            "vehicle_type",
            "driver_id",
            "stop_count",
            "delivered_stops",
            "failed_stops",
            "load_units",
            "capacity_units",
            "distance_km",
            "travel_minutes",
            "service_minutes",
            "wait_minutes",
            "shift_overrun_minutes",
            "status",
        )
        if column in output
    ]
    output = output[columns].copy()
    if {"load_units", "capacity_units"} <= set(output.columns):
        output["capacity_utilization"] = (
            pd.to_numeric(output["load_units"], errors="coerce")
            / pd.to_numeric(output["capacity_units"], errors="coerce").replace(0, pd.NA)
        )
    return output.sort_values(
        [column for column in ("service_date", "route_id") if column in output],
        kind="mergesort",
    )


def issue_summary(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty or "issue_type" not in issues:
        return pd.DataFrame(columns=["issue_type", "severity", "count"])
    return (
        issues.groupby(["issue_type", "severity"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["severity", "count"], ascending=[True, False])
    )


def anomaly_metrics_frame(metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame()
    output = metrics.copy()
    for column in ("tp", "fp", "fn", "tn", "evaluated_universe"):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce").fillna(0).astype(int)
    for column in ("precision", "recall", "f1"):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce").fillna(0.0)
    return output
