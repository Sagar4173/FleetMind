from __future__ import annotations

import pandas as pd

from fleetmind.dashboard.data import (
    anomaly_metrics_frame,
    daily_frame,
    issue_summary,
    list_runs,
    load_run_data,
    metric_value,
    parse_dimensions,
    parse_metadata,
    route_map_data,
    route_summary_frame,
    scalar_metrics,
    with_dimensions,
)


def test_dashboard_lists_and_loads_runs(tiny_persisted) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    runs = list_runs(paths.warehouse_path)
    assert len(runs) == 1
    assert runs.iloc[0]["run_id"] == tiny_persisted.run.run_id
    data = load_run_data(paths.warehouse_path, tiny_persisted.run.run_id)
    assert data["routes"].shape[0] == len(tiny_persisted.run.plan.tables["routes"])
    assert not data["report_metrics"].empty
    metadata = data["_metadata"]
    assert metadata["status"] == "SUCCEEDED"
    assert metadata["counts"]["clean_issues"] == 0
    assert metadata["stage_durations"]["persist"] >= 0


def test_dashboard_metric_helpers(tiny_persisted) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    data = load_run_data(paths.warehouse_path, tiny_persisted.run.run_id)
    metrics = data["report_metrics"]
    scalars = scalar_metrics(metrics)
    assert scalars["service.delivery_service_rate"] == metric_value(
        metrics, "service", "delivery_service_rate"
    )
    daily = daily_frame(metrics)
    assert not daily.empty
    assert set(daily.columns) == {"service_date", "delivered", "failed", "on_time_rate"}
    priority = with_dimensions(metrics, "service_priority", "delivery_service_rate")
    assert not priority.empty
    assert set(priority["entity"]) <= {"LOW", "STANDARD", "HIGH", "URGENT"}
    assert parse_dimensions("priority=HIGH;city=Dallas") == {
        "priority": "HIGH",
        "city": "Dallas",
    }


def test_dashboard_route_views(tiny_persisted) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    data = load_run_data(paths.warehouse_path, tiny_persisted.run.run_id)
    summary = route_summary_frame(data)
    assert not summary.empty
    assert {"route_id", "stop_count", "distance_km"} <= set(summary.columns)
    service_date = pd.Timestamp(summary["service_date"].iloc[0]).date().isoformat()
    map_data = route_map_data(data, service_date)
    assert not map_data["routes"].empty
    assert not map_data["stops"].empty
    assert {"latitude", "longitude", "site_id"} <= set(map_data["stops"].columns)


def test_dashboard_quality_and_anomaly_views(tiny_persisted) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    data = load_run_data(paths.warehouse_path, tiny_persisted.run.run_id)
    issues = data["data_quality_issues"]
    assert not issues.empty
    summary = issue_summary(issues)
    assert {"issue_type", "severity", "count"} <= set(summary.columns)
    metrics = anomaly_metrics_frame(data["anomaly_detection_metrics"])
    assert not metrics.empty
    assert metrics["f1"].between(0, 1).all()
    assert metrics["tp"].sum() == 6
    assert not data["ground_truth_anomalies"].empty


def test_metadata_parser_handles_invalid_json() -> None:
    frame = pd.DataFrame(
        {
            "run_id": ["RUN-X"],
            "stage_durations_json": ["not-json"],
            "counts_json": ["{}"],
            "checksums_json": [None],
        }
    )
    parsed = parse_metadata(frame)
    assert parsed["stage_durations"] == {}
    assert parsed["counts"] == {}
    assert parsed["checksums"] == {}
