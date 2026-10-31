from __future__ import annotations

import pandas as pd
import pytest

from fleetmind.dashboard.data import metric_value
from fleetmind.models.enums import AnomalyType, OutcomeStatus
from fleetmind.pipeline import compute_reconciliation, compute_report_metrics


def test_report_metrics_match_outcome_inventory(tiny_pipeline) -> None:
    run = tiny_pipeline.run
    metrics = run.report_metrics
    outcomes = run.tables["delivery_outcomes"]
    deliveries = run.tables["deliveries"]
    delivered = int((outcomes["status"].astype(str) == OutcomeStatus.DELIVERED.value).sum())
    failed = int((outcomes["status"].astype(str) == OutcomeStatus.FAILED.value).sum())
    total = len(deliveries)
    assert metric_value(metrics, "service", "total_deliveries") == total
    assert metric_value(metrics, "service", "delivered_deliveries") == delivered
    assert metric_value(metrics, "service", "failed_deliveries") == failed
    assert metric_value(metrics, "service", "delivery_service_rate") == pytest.approx(
        delivered / total
    )
    assert metric_value(metrics, "service", "failure_rate") == pytest.approx(
        failed / (delivered + failed)
    )


def test_report_metric_primary_keys_are_unique(tiny_pipeline) -> None:
    metrics = tiny_pipeline.run.report_metrics
    assert not metrics.duplicated(["category", "metric_name", "dimensions"]).any()
    assert set(metrics["category"]) >= {
        "service",
        "routing",
        "fleet",
        "reliability",
        "daily",
        "service_priority",
        "site_city",
    }
    assert metrics["run_id"].nunique() == 1


def test_ratio_metrics_are_bounded(tiny_pipeline) -> None:
    metrics = tiny_pipeline.run.report_metrics
    ratios = metrics[metrics["unit"] == "ratio"]
    assert not ratios.empty
    assert ratios["value"].between(0, 1).all()


def test_reconciliation_passes_and_is_deterministic(tiny_pipeline) -> None:
    run = tiny_pipeline.run
    assert not run.reconciliation.empty
    assert run.reconciliation["passed"].all()
    again = compute_reconciliation(run.tables, run.config)
    pd.testing.assert_frame_equal(
        run.reconciliation.reset_index(drop=True), again.reset_index(drop=True)
    )


def test_reporting_is_deterministic(tiny_pipeline) -> None:
    run = tiny_pipeline.run
    metrics, summary = compute_report_metrics(run.tables, run.config)
    pd.testing.assert_frame_equal(
        run.report_metrics.reset_index(drop=True), metrics.reset_index(drop=True)
    )
    assert summary == run.report_summary


def test_anomaly_metrics_cover_all_six_types(tiny_pipeline) -> None:
    run = tiny_pipeline.run
    assert run.anomaly_evaluation is not None
    metrics = run.anomaly_evaluation.metrics
    assert set(metrics["issue_type"]) == {item.value for item in AnomalyType}
    assert metrics["f1"].eq(1.0).all()
    assert len(run.anomaly_injection.ground_truth) == 6
    assert len(run.anomaly_evaluation.detections) == 6
