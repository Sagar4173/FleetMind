"""End-to-end orchestration: generate, plan, simulate, validate, detect, report."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fleetmind.anomalies import (
    AnomalyEvaluationResult,
    AnomalyInjectionResult,
    detect_and_evaluate,
    inject_anomalies,
)
from fleetmind.config import AppConfig, PIPELINE_VERSION
from fleetmind.generation.factory import Dataset, generate_dataset, table_checksums
from fleetmind.models.entities import AuditRecord, QuarantineRecord, RunMetadata
from fleetmind.persistence.artifacts import (
    artifact_manifest,
    write_frame_set,
    write_json,
    write_table_checksums,
)
from fleetmind.persistence.schema import DIRTY_BASE_NAMES
from fleetmind.persistence.warehouse import Warehouse
from fleetmind.reporting import compute_reconciliation, compute_report_metrics
from fleetmind.routing.optimizer import PlanResult, build_plan
from fleetmind.simulation.engine import SimulationResult, simulate_execution
from fleetmind.validation.engine import PRIMARY_KEYS, ValidationResult, validate_full


class PipelineError(RuntimeError):
    """Base class for a failed orchestration run."""


class PipelineValidationError(PipelineError):
    """The clean plan/simulation snapshot failed hard validation."""


class PipelineIncompleteError(PipelineError):
    """The pipeline produced artifacts but one or more optional stages failed."""


@dataclass
class PipelineRun:
    config: AppConfig
    dataset: Dataset
    plan: PlanResult
    simulation: SimulationResult
    tables: dict[str, pd.DataFrame]
    validation: ValidationResult
    report_metrics: pd.DataFrame
    reconciliation: pd.DataFrame
    report_summary: dict[str, Any]
    status: str
    started_at: datetime
    completed_at: datetime
    stage_durations: dict[str, float]
    audit_records: pd.DataFrame
    run_metadata: pd.DataFrame
    anomaly_injection: AnomalyInjectionResult | None = None
    anomaly_evaluation: AnomalyEvaluationResult | None = None
    dirty_validation: ValidationResult | None = None
    quarantine: dict[str, pd.DataFrame] = field(default_factory=dict)
    dirty_quarantine: dict[str, pd.DataFrame] = field(default_factory=dict)
    quarantine_records: pd.DataFrame = field(default_factory=pd.DataFrame)
    dirty_quarantine_records: pd.DataFrame = field(default_factory=pd.DataFrame)
    error_message: str = ""
    include_anomalies: bool = True

    @property
    def run_id(self) -> str:
        return self.config.run_id

    @property
    def mode(self) -> str:
        return "FULL" if self.include_anomalies else "CLEAN"

    def counts(self) -> dict[str, int]:
        counts = {f"master_{name}": int(len(frame)) for name, frame in self.dataset.tables.items()}
        counts.update(
            {f"plan_{name}": int(len(frame)) for name, frame in self.plan.tables.items()}
        )
        counts.update(
            {
                f"simulation_{name}": int(len(frame))
                for name, frame in self.simulation.tables.items()
                if name
                in {
                    "delivery_outcomes",
                    "route_events",
                    "delivery_events",
                }
            }
        )
        counts["clean_issues"] = int(len(self.validation.issues))
        counts["clean_quarantine_records"] = int(len(self.quarantine_records))
        if self.dirty_validation is not None:
            counts["dirty_issues"] = int(len(self.dirty_validation.issues))
            counts["dirty_quarantine_records"] = int(len(self.dirty_quarantine_records))
        if self.anomaly_injection is not None:
            counts["ground_truth_anomalies"] = int(len(self.anomaly_injection.ground_truth))
        if self.anomaly_evaluation is not None:
            counts["anomaly_detections"] = int(len(self.anomaly_evaluation.detections))
        return counts

    def anomaly_summary(self) -> dict[str, Any]:
        if self.anomaly_injection is None or self.anomaly_evaluation is None:
            return {"enabled": False, "injected_by_type": {}, "detected_by_type": {}, "metrics": []}
        metrics = self.anomaly_evaluation.metrics
        return {
            "enabled": True,
            "injected": int(len(self.anomaly_injection.ground_truth)),
            "detected": int(len(self.anomaly_evaluation.detections)),
            "injected_by_type": dict(sorted(self.anomaly_injection.counts_by_type.items())),
            "detected_by_type": dict(sorted(self.anomaly_evaluation.counts_by_type.items())),
            "metrics": metrics.to_dict("records") if not metrics.empty else [],
        }

    def summary(self) -> dict[str, Any]:
        clean_counts = self.validation.counts_by_type()
        dirty_counts = (
            self.dirty_validation.counts_by_type() if self.dirty_validation else {}
        )
        reconciliation_passed = (
            int(self.reconciliation["passed"].sum()) if not self.reconciliation.empty else 0
        )
        reconciliation_total = int(len(self.reconciliation))
        return {
            "run_id": self.run_id,
            "status": self.status,
            "mode": self.mode,
            "seed": self.config.seed,
            "dataset_version": self.config.dataset_version,
            "pipeline_version": PIPELINE_VERSION,
            "config_hash": self.config.config_hash(),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "counts": self.counts(),
            "kpis": self.report_summary,
            "validation": {
                "clean_passed": self.validation.passed,
                "clean_errors": self.validation.error_count,
                "clean_critical": self.validation.critical_count,
                "clean_by_type": clean_counts,
                "dirty_passed": (
                    self.dirty_validation.passed if self.dirty_validation is not None else None
                ),
                "dirty_errors": (
                    self.dirty_validation.error_count
                    if self.dirty_validation is not None
                    else None
                ),
                "dirty_by_type": dirty_counts,
            },
            "anomalies": self.anomaly_summary(),
            "reconciliation": {
                "passed": reconciliation_passed,
                "total": reconciliation_total,
                "all_passed": reconciliation_passed == reconciliation_total,
                "checks": self.reconciliation.to_dict("records")
                if not self.reconciliation.empty
                else [],
            },
            "stage_durations_seconds": self.stage_durations,
            "checksums": {
                "clean": table_checksums(self.tables),
                "dirty": table_checksums(self.anomaly_injection.tables)
                if self.anomaly_injection is not None
                else {},
            },
            "error_message": self.error_message,
        }


@dataclass
class PipelinePaths:
    run_directory: Path
    warehouse_path: Path
    manifest_path: Path
    summary_path: Path


@dataclass
class _AuditTrail:
    run_id: str
    year: int
    execution_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, action: str, details: dict[str, Any], actor: str = "fleetmind-pipeline") -> None:
        self.rows.append(
            {
                "run_id": self.run_id,
                "audit_id": f"AUD-{self.year}-{len(self.rows) + 1:06d}",
                "execution_id": self.execution_id,
                "timestamp": datetime.now().replace(microsecond=0),
                "actor": actor,
                "action": action,
                "details_json": json.dumps(details, sort_keys=True, default=str),
            }
        )

    def frame(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame(columns=list(AuditRecord.model_fields))
        return pd.DataFrame([AuditRecord(**row).model_dump(mode="python") for row in self.rows])


def _build_quarantine_records(
    validation: ValidationResult | None,
    config: AppConfig,
    *,
    phase: str,
) -> pd.DataFrame:
    columns = list(QuarantineRecord.model_fields)
    if validation is None or not validation.quarantined:
        return pd.DataFrame(columns=columns)
    prefix = "QTN" if phase == "clean" else "QTD"
    year = config.id_year
    rows: list[dict[str, Any]] = []
    issues = validation.issues
    for table, frame in sorted(validation.quarantined.items()):
        keys = PRIMARY_KEYS.get(table, [])
        key = keys[0] if keys else None
        for _, record in frame.iterrows():
            entity_id = str(record[key]) if key else ""
            matching = issues[
                (issues["source_table"] == table)
                & (issues["entity_id"].astype(str) == entity_id)
            ] if not issues.empty else issues
            issue_id = str(matching["issue_id"].iloc[0]) if len(matching) else ""
            reason = str(matching["issue_type"].iloc[0]) if len(matching) else "QUARANTINED_RECORD"
            quarantined_at = (
                matching["detected_at"].iloc[0] if len(matching) else datetime.now().replace(microsecond=0)
            )
            rows.append(
                {
                    "run_id": config.run_id,
                    "quarantine_id": f"{prefix}-{year}-{len(rows) + 1:06d}",
                    "issue_id": issue_id,
                    "source_table": table,
                    "entity_id": entity_id,
                    "record_json": pd.Series(record).to_json(
                        orient="index", date_format="iso"
                    ),
                    "reason": reason,
                    "quarantined_at": quarantined_at,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _counts_by_severity(issues: pd.DataFrame) -> dict[str, int]:
    if issues.empty or "severity" not in issues:
        return {}
    return {
        str(key): int(value)
        for key, value in issues["severity"].value_counts().sort_index().items()
    }


def build_pipeline_run(
    config: AppConfig,
    *,
    include_anomalies: bool = True,
) -> PipelineRun:
    """Execute all in-memory stages. No files are written by this function."""
    started_at = datetime.now().replace(microsecond=0)
    stage_durations: dict[str, float] = {}
    audit = _AuditTrail(config.run_id, config.id_year, f"EXE-{config.run_id[4:]}")
    audit.add(
        "PIPELINE_STARTED",
        {
            "config_hash": config.config_hash(),
            "seed": config.seed,
            "days": config.horizon.days,
            "include_anomalies": include_anomalies,
        },
    )

    started = time.perf_counter()
    dataset = generate_dataset(config)
    stage_durations["generate"] = round(time.perf_counter() - started, 4)
    audit.add(
        "GENERATION_COMPLETED",
        {"duration_seconds": stage_durations["generate"], "tables": dataset.counts()},
    )

    started = time.perf_counter()
    plan = build_plan(dataset, config)
    stage_durations["plan"] = round(time.perf_counter() - started, 4)
    audit.add(
        "PLANNING_COMPLETED",
        {
            "duration_seconds": stage_durations["plan"],
            "routes": len(plan.tables["routes"]),
            "assigned_deliveries": int(plan.summary.get("assigned_deliveries", 0)),
            "unserved_deliveries": int(plan.summary.get("unserved_deliveries", 0)),
        },
    )

    started = time.perf_counter()
    simulation = simulate_execution(dataset, plan.tables, config)
    stage_durations["simulate"] = round(time.perf_counter() - started, 4)
    audit.add(
        "SIMULATION_COMPLETED",
        {
            "duration_seconds": stage_durations["simulate"],
            "counts": {name: int(len(frame)) for name, frame in simulation.tables.items()},
        },
    )

    tables = dataset.copy_tables()
    tables.update(plan.tables)
    tables.update(simulation.tables)

    started = time.perf_counter()
    validation = validate_full(tables, config, prefix="DQ")
    stage_durations["validate_clean"] = round(time.perf_counter() - started, 4)
    quarantine_records = _build_quarantine_records(validation, config, phase="clean")
    audit.add(
        "VALIDATION_CLEAN_COMPLETED",
        {
            "duration_seconds": stage_durations["validate_clean"],
            "passed": validation.passed,
            "errors": validation.error_count,
            "issues_by_type": validation.counts_by_type(),
            "severity": _counts_by_severity(validation.issues),
        },
    )

    started = time.perf_counter()
    report_metrics, report_summary = compute_report_metrics(tables, config)
    reconciliation = compute_reconciliation(tables, config)
    stage_durations["reporting"] = round(time.perf_counter() - started, 4)
    audit.add(
        "REPORTING_COMPLETED",
        {
            "metrics": len(report_metrics),
            "reconciliation_checks": len(reconciliation),
            "reconciliation_passed": int(reconciliation["passed"].sum()) if len(reconciliation) else 0,
        },
    )

    status = "SUCCEEDED"
    error_message = ""
    anomaly_injection: AnomalyInjectionResult | None = None
    anomaly_evaluation: AnomalyEvaluationResult | None = None
    dirty_validation: ValidationResult | None = None
    dirty_quarantine: dict[str, pd.DataFrame] = {}
    dirty_quarantine_records = pd.DataFrame(columns=list(QuarantineRecord.model_fields))

    if not validation.passed:
        status = "FAILED"
        error_message = (
            f"clean snapshot failed validation with {validation.error_count} error(s) "
            f"and {validation.critical_count} critical issue(s)"
        )
        audit.add("PIPELINE_FAILED", {"reason": error_message})
    elif include_anomalies:
        try:
            started = time.perf_counter()
            anomaly_injection = inject_anomalies(tables, config)
            stage_durations["inject_anomalies"] = round(time.perf_counter() - started, 4)
            audit.add(
                "ANOMALY_INJECTION_COMPLETED",
                {
                    "duration_seconds": stage_durations["inject_anomalies"],
                    "counts": anomaly_injection.counts_by_type,
                },
            )
            started = time.perf_counter()
            anomaly_evaluation = detect_and_evaluate(
                anomaly_injection.tables,
                anomaly_injection.ground_truth,
                config,
            )
            stage_durations["detect_anomalies"] = round(time.perf_counter() - started, 4)
            audit.add(
                "DETECTION_COMPLETED",
                {
                    "duration_seconds": stage_durations["detect_anomalies"],
                    "detections": len(anomaly_evaluation.detections),
                    "counts": anomaly_evaluation.counts_by_type,
                },
            )
            started = time.perf_counter()
            dirty_validation = validate_full(
                anomaly_injection.tables, config, prefix="DQD"
            )
            stage_durations["validate_dirty"] = round(time.perf_counter() - started, 4)
            dirty_quarantine = dirty_validation.quarantined
            dirty_quarantine_records = _build_quarantine_records(
                dirty_validation, config, phase="dirty"
            )
            audit.add(
                "VALIDATION_DIRTY_COMPLETED",
                {
                    "duration_seconds": stage_durations["validate_dirty"],
                    "passed": dirty_validation.passed,
                    "errors": dirty_validation.error_count,
                    "issues_by_type": dirty_validation.counts_by_type(),
                },
            )
        except Exception as exc:
            status = "PARTIAL"
            error_message = f"anomaly stage failed: {exc}"
            audit.add("PIPELINE_PARTIAL", {"reason": error_message})
    else:
        audit.add("ANOMALY_STAGE_SKIPPED", {"reason": "include_anomalies=false"})

    completed_at = datetime.now().replace(microsecond=0)
    if status == "SUCCEEDED":
        audit.add(
            "PIPELINE_SUCCEEDED",
            {
                "duration_seconds": round((completed_at - started_at).total_seconds(), 4),
                "clean_issues": len(validation.issues),
                "anomalies_detected": (
                    len(anomaly_evaluation.detections)
                    if anomaly_evaluation is not None
                    else 0
                ),
            },
        )
    audit_frame = audit.frame()
    run_metadata = _metadata_frame(
        config,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        stage_durations=stage_durations,
        counts=_pipeline_counts(dataset, plan, simulation, validation, anomaly_injection, anomaly_evaluation),
        checksums={
            "clean": table_checksums(tables),
            "dirty": table_checksums(anomaly_injection.tables)
            if anomaly_injection is not None
            else {},
        },
        error_message=error_message,
        mode="FULL" if include_anomalies else "CLEAN",
    )
    return PipelineRun(
        config=config,
        dataset=dataset,
        plan=plan,
        simulation=simulation,
        tables=tables,
        validation=validation,
        report_metrics=report_metrics,
        reconciliation=reconciliation,
        report_summary=report_summary,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        stage_durations=stage_durations,
        audit_records=audit_frame,
        run_metadata=run_metadata,
        anomaly_injection=anomaly_injection,
        anomaly_evaluation=anomaly_evaluation,
        dirty_validation=dirty_validation,
        quarantine=validation.quarantined,
        dirty_quarantine=dirty_quarantine,
        quarantine_records=quarantine_records,
        dirty_quarantine_records=dirty_quarantine_records,
        error_message=error_message,
        include_anomalies=include_anomalies,
    )


def _pipeline_counts(
    dataset: Dataset,
    plan: PlanResult,
    simulation: SimulationResult,
    validation: ValidationResult,
    injection: AnomalyInjectionResult | None,
    evaluation: AnomalyEvaluationResult | None,
) -> dict[str, int]:
    counts = {f"master_{name}": int(len(frame)) for name, frame in dataset.tables.items()}
    counts.update({f"plan_{name}": int(len(frame)) for name, frame in plan.tables.items()})
    counts.update(
        {
            f"simulation_{name}": int(len(frame))
            for name, frame in simulation.tables.items()
            if name in {"delivery_outcomes", "route_events", "delivery_events"}
        }
    )
    counts["clean_issues"] = int(len(validation.issues))
    if injection is not None:
        counts["ground_truth_anomalies"] = int(len(injection.ground_truth))
    if evaluation is not None:
        counts["anomaly_detections"] = int(len(evaluation.detections))
    return counts


def _metadata_frame(
    config: AppConfig,
    *,
    status: str,
    started_at: datetime,
    completed_at: datetime,
    stage_durations: dict[str, float],
    counts: dict[str, int],
    checksums: dict[str, dict[str, str]],
    error_message: str,
    mode: str = "FULL",
) -> pd.DataFrame:
    metadata = RunMetadata(
        run_id=config.run_id,
        mode=mode,
        seed=config.seed,
        dataset_version=config.dataset_version,
        pipeline_version=PIPELINE_VERSION,
        config_hash=config.config_hash(),
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        stage_durations_json=json.dumps(stage_durations, sort_keys=True),
        counts_json=json.dumps(counts, sort_keys=True),
        checksums_json=json.dumps(checksums, sort_keys=True),
        error_message=error_message,
    )
    return pd.DataFrame([metadata.model_dump(mode="python")])


def _combine_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if not frame.empty]
    if non_empty:
        return pd.concat(non_empty, ignore_index=True)
    for frame in frames:
        if list(frame.columns):
            return frame.copy()
    return pd.DataFrame()


def warehouse_tables(run: PipelineRun) -> dict[str, pd.DataFrame]:
    """Assemble the complete run-scoped warehouse snapshot."""
    tables: dict[str, pd.DataFrame] = {
        name: frame.copy() for name, frame in run.dataset.tables.items()
    }
    for name in ("routes", "route_stops", "route_legs", "delivery_assignments", "solver_runs"):
        if name in run.plan.tables:
            tables[name] = run.plan.tables[name].copy()
    for name in (
        "routes",
        "route_stops",
        "route_legs",
        "delivery_assignments",
        "delivery_outcomes",
        "route_events",
        "delivery_events",
        "deliveries",
        "orders",
    ):
        if name in run.simulation.tables:
            tables[name] = run.simulation.tables[name].copy()
    if run.anomaly_injection is not None:
        for name in DIRTY_BASE_NAMES:
            tables[f"dirty_{name}"] = run.anomaly_injection.tables[name].copy()

    issue_frames = [run.validation.issues]
    if run.dirty_validation is not None:
        issue_frames.append(run.dirty_validation.issues)
    issues = _combine_frames(issue_frames)
    tables["data_quality_issues"] = issues
    quarantine_frames = [run.quarantine_records]
    if not run.dirty_quarantine_records.empty:
        quarantine_frames.append(run.dirty_quarantine_records)
    tables["quarantine_records"] = _combine_frames(quarantine_frames)
    if run.anomaly_injection is not None:
        tables["ground_truth_anomalies"] = run.anomaly_injection.ground_truth
    if run.anomaly_evaluation is not None:
        tables["anomaly_detections"] = run.anomaly_evaluation.detections
        tables["anomaly_detection_metrics"] = run.anomaly_evaluation.metrics
    tables["report_metrics"] = run.report_metrics
    tables["reconciliation_checks"] = run.reconciliation
    tables["run_metadata"] = run.run_metadata
    tables["audit_records"] = run.audit_records
    return tables


def persist_pipeline_run(
    run: PipelineRun,
    output_directory: Path | str,
    *,
    warehouse_path: Path | str | None = None,
    artifact_formats: tuple[str, ...] = ("parquet", "csv"),
) -> PipelinePaths:
    """Write warehouse snapshots and human-readable artifacts atomically."""
    output_root = Path(output_directory)
    run_directory = output_root / run.run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    db_path = Path(warehouse_path) if warehouse_path else output_root / "fleetmind.duckdb"
    started = time.perf_counter()

    config_path = run_directory / "config.yaml"
    run.config.to_yaml(config_path)

    plan_names = ("routes", "route_stops", "route_legs", "delivery_assignments", "solver_runs")
    simulation_names = (
        "routes",
        "route_stops",
        "route_legs",
        "delivery_assignments",
        "delivery_outcomes",
        "route_events",
        "delivery_events",
        "deliveries",
        "orders",
    )
    artifact_groups: dict[str, dict[str, pd.DataFrame]] = {
        "master": dict(run.dataset.tables),
        "plan": {name: run.plan.tables[name] for name in plan_names if name in run.plan.tables},
        "simulation": {
            name: run.simulation.tables[name]
            for name in simulation_names
            if name in run.simulation.tables
        },
        "reports": {
            "report_metrics": run.report_metrics,
            "reconciliation_checks": run.reconciliation,
        },
    }
    anomaly_artifacts: dict[str, pd.DataFrame] = {}
    if run.anomaly_injection is not None:
        anomaly_artifacts["ground_truth_anomalies"] = run.anomaly_injection.ground_truth
        for name in DIRTY_BASE_NAMES:
            anomaly_artifacts[f"dirty_{name}"] = run.anomaly_injection.tables[name]
    if run.anomaly_evaluation is not None:
        anomaly_artifacts["anomaly_detections"] = run.anomaly_evaluation.detections
        anomaly_artifacts["anomaly_detection_metrics"] = run.anomaly_evaluation.metrics
    if anomaly_artifacts:
        artifact_groups["anomalies"] = anomaly_artifacts
    artifact_groups["validation"] = {
        "clean_issues": run.validation.issues,
        "quarantine_records": run.quarantine_records,
    }
    if run.dirty_validation is not None:
        artifact_groups["validation"]["dirty_issues"] = run.dirty_validation.issues
        artifact_groups["validation"]["dirty_quarantine_records"] = run.dirty_quarantine_records
    if run.quarantine:
        artifact_groups["quarantine_clean"] = run.quarantine
    if run.dirty_quarantine:
        artifact_groups["quarantine_dirty"] = run.dirty_quarantine

    written: dict[str, dict[str, dict[str, str]]] = {}
    for group, frames in artifact_groups.items():
        written[group] = write_frame_set(
            run_directory / group, frames, formats=artifact_formats
        )
    write_table_checksums(run_directory, run.tables)

    table_frames = warehouse_tables(run)
    with Warehouse(db_path) as warehouse:
        warehouse.write_snapshot(run.run_id, table_frames)
        warehouse_counts = warehouse.counts(run.run_id)

    persist_seconds = round(time.perf_counter() - started, 4)
    run.stage_durations["persist"] = persist_seconds
    completed_at = datetime.now().replace(microsecond=0)
    run.completed_at = completed_at
    run.run_metadata = _metadata_frame(
        run.config,
        status=run.status,
        started_at=run.started_at,
        completed_at=completed_at,
        stage_durations=run.stage_durations,
        counts=run.counts(),
        checksums={
            "clean": table_checksums(run.tables),
            "dirty": table_checksums(run.anomaly_injection.tables)
            if run.anomaly_injection is not None
            else {},
        },
        error_message=run.error_message,
        mode=run.mode,
    )
    with Warehouse(db_path) as warehouse:
        warehouse.upsert_table(run.run_id, "run_metadata", run.run_metadata)

    summary = run.summary()
    summary["warehouse"] = {
        "path": str(db_path),
        "table_counts": warehouse_counts,
    }
    summary["artifacts"] = {
        "run_directory": ".",
        "checksums": "checksums.json",
        "groups": sorted(written),
    }
    summary_path = write_json(run_directory / "summary.json", summary)
    manifest = {
        "run_id": run.run_id,
        "status": run.status,
        "mode": run.mode,
        "config_hash": run.config.config_hash(),
        "seed": run.config.seed,
        "dataset_version": run.config.dataset_version,
        "pipeline_version": PIPELINE_VERSION,
        "created_at": datetime.now().replace(microsecond=0),
        "warehouse_path": str(db_path),
        "warehouse_table_counts": warehouse_counts,
        "table_groups": artifact_manifest(
            run_directory, artifact_groups, formats=artifact_formats
        ),
        "tested_stage_durations_seconds": run.stage_durations,
        "anomalies": run.anomaly_summary(),
    }
    manifest_path = write_json(run_directory / "manifest.json", manifest)
    return PipelinePaths(
        run_directory=run_directory,
        warehouse_path=db_path,
        manifest_path=manifest_path,
        summary_path=summary_path,
    )


def execute_pipeline(
    config: AppConfig,
    output_directory: Path | str,
    *,
    include_anomalies: bool = True,
    warehouse_path: Path | str | None = None,
    artifact_formats: tuple[str, ...] = ("parquet", "csv"),
) -> tuple[PipelineRun, PipelinePaths]:
    """Build and persist a run, raising after evidence is written if needed."""
    run = build_pipeline_run(config, include_anomalies=include_anomalies)
    paths = persist_pipeline_run(
        run,
        output_directory,
        warehouse_path=warehouse_path,
        artifact_formats=artifact_formats,
    )
    if run.status == "FAILED":
        raise PipelineValidationError(f"{run.error_message}; artifacts: {paths.run_directory}")
    if run.status == "PARTIAL":
        raise PipelineIncompleteError(f"{run.error_message}; artifacts: {paths.run_directory}")
    return run, paths
