from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from fleetmind.models.enums import AnomalyType
from fleetmind.pipeline import warehouse_tables
from fleetmind.persistence import (
    Warehouse,
    WarehouseError,
    render_schema,
    write_frame_set,
)


def test_tiny_pipeline_is_clean_and_fully_evaluated(tiny_pipeline) -> None:
    run = tiny_pipeline.run
    assert run.status == "SUCCEEDED", run.error_message
    assert run.validation.passed
    assert run.validation.issues.empty
    assert run.anomaly_injection is not None
    assert run.anomaly_evaluation is not None
    assert run.dirty_validation is not None
    assert not run.dirty_validation.passed
    expected = {item.value for item in AnomalyType}
    assert set(run.anomaly_evaluation.counts_by_type) == expected
    assert set(run.anomaly_evaluation.counts_by_type.values()) == {1}
    assert len(run.quarantine_records) + len(run.dirty_quarantine_records) > 0


def test_warehouse_schema_is_generated_consistently() -> None:
    path = Path(__file__).resolve().parents[2] / "src/fleetmind/persistence/schema.sql"
    assert path.read_text(encoding="utf-8") == render_schema()
    ddl = render_schema()
    assert "PRIMARY KEY (\"run_id\", \"route_id\")" in ddl
    assert "CREATE TABLE IF NOT EXISTS dirty_route_stops" in ddl
    assert "CREATE TABLE IF NOT EXISTS anomaly_detection_metrics" in ddl


def test_warehouse_round_trip_and_idempotency(tiny_persisted) -> None:
    run = tiny_persisted.run
    paths = tiny_persisted.paths
    assert paths is not None
    with Warehouse(paths.warehouse_path, read_only=True, initialize=False) as warehouse:
        first = warehouse.counts(run.run_id)
        metadata = warehouse.table("run_metadata", run.run_id)
        metrics = warehouse.table("report_metrics", run.run_id)
        truth = warehouse.table("ground_truth_anomalies", run.run_id)
        dirty_routes = warehouse.table("dirty_routes", run.run_id)
    assert first["routes"] == len(run.plan.tables["routes"])
    assert first["route_stops"] == len(run.plan.tables["route_stops"])
    assert first["report_metrics"] == len(run.report_metrics)
    assert first["ground_truth_anomalies"] == 6
    assert first["dirty_routes"] > 0
    assert metadata.iloc[0]["status"] == "SUCCEEDED"
    assert len(metrics) > 0
    assert len(truth) == 6
    assert len(dirty_routes) == len(run.anomaly_injection.tables["routes"])

    from fleetmind.pipeline import persist_pipeline_run

    persist_pipeline_run(run, paths.run_directory.parent)
    with Warehouse(paths.warehouse_path, read_only=True, initialize=False) as warehouse:
        second = warehouse.counts(run.run_id)
    assert first == second


def test_warehouse_rollback_preserves_previous_snapshot(tiny_persisted) -> None:
    run = tiny_persisted.run
    paths = tiny_persisted.paths
    assert paths is not None
    with Warehouse(paths.warehouse_path, read_only=True, initialize=False) as warehouse:
        before = warehouse.counts(run.run_id)
    tables = {name: frame.copy() for name, frame in warehouse_tables(run).items()}
    duplicate = tables["routes"].iloc[[0]].copy()
    tables["routes"] = pd.concat([tables["routes"], duplicate], ignore_index=True)
    with Warehouse(paths.warehouse_path) as warehouse:
        with pytest.raises(WarehouseError, match="rolled back"):
            warehouse.write_snapshot(run.run_id, tables)
        after = warehouse.counts(run.run_id)
    assert before == after


def test_warehouse_run_isolation_and_delete(tiny_persisted) -> None:
    run = tiny_persisted.run
    paths = tiny_persisted.paths
    assert paths is not None
    other_run = "RUN-OTHER000000"
    metrics = run.report_metrics.copy()
    metrics["run_id"] = other_run
    with Warehouse(paths.warehouse_path) as warehouse:
        before = warehouse.counts(run.run_id)
        warehouse.write_snapshot(other_run, {"report_metrics": metrics})
        assert len(warehouse.table("report_metrics", other_run)) == len(metrics)
        assert warehouse.counts(run.run_id) == before
        warehouse.delete_run(other_run)
        assert warehouse.table("report_metrics", other_run).empty
        assert warehouse.counts(run.run_id) == before


def test_read_only_warehouse_rejects_writes(tiny_persisted) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    with Warehouse(paths.warehouse_path, read_only=True, initialize=False) as warehouse:
        with pytest.raises(WarehouseError, match="read-only"):
            warehouse.write_snapshot("RUN-READONLY00", {})


def test_artifacts_are_written_in_both_formats(tiny_persisted) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    deliveries_parquet = paths.run_directory / "master" / "deliveries.parquet"
    deliveries_csv = paths.run_directory / "master" / "deliveries.csv"
    assert deliveries_parquet.exists()
    assert deliveries_csv.exists()
    parquet = pd.read_parquet(deliveries_parquet)
    csv = pd.read_csv(deliveries_csv)
    assert len(parquet) == len(tiny_persisted.run.dataset.tables["deliveries"])
    assert len(csv) == len(parquet)
    assert (paths.run_directory / "anomalies" / "ground_truth_anomalies.parquet").exists()
    assert (paths.run_directory / "validation" / "dirty_issues.parquet").exists()
    assert (paths.run_directory / "reports" / "reconciliation_checks.csv").exists()

    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(paths.summary_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == tiny_persisted.run.run_id
    assert manifest["status"] == "SUCCEEDED"
    assert summary["validation"]["clean_passed"] is True
    assert summary["anomalies"]["detected"] == 6
    assert "warehouse_table_counts" in manifest


def test_artifacts_are_byte_stable_for_business_tables(tiny_pipeline) -> None:
    run = tiny_pipeline.run
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fm_artifacts_") as first_dir:
        with tempfile.TemporaryDirectory(prefix="fm_artifacts_") as second_dir:
            first = write_frame_set(first_dir, {"deliveries": run.tables["deliveries"]})
            second = write_frame_set(second_dir, {"deliveries": run.tables["deliveries"]})
            first_bytes = Path(first["deliveries"]["parquet"]).read_bytes()
            second_bytes = Path(second["deliveries"]["parquet"]).read_bytes()
            assert first_bytes == second_bytes
            first_csv = Path(first["deliveries"]["csv"]).read_text(encoding="utf-8")
            second_csv = Path(second["deliveries"]["csv"]).read_text(encoding="utf-8")
            assert first_csv == second_csv
