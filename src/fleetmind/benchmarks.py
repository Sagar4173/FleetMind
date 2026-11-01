"""Measured, reproducible benchmark suite for FleetMind pipeline stages."""

from __future__ import annotations

import platform
import shutil
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pandas as pd
import psutil

from fleetmind.config import AppConfig, PIPELINE_VERSION, load_config
from fleetmind.generation.factory import table_checksums
from fleetmind.pipeline import PipelineRun, build_pipeline_run, persist_pipeline_run
from fleetmind.persistence.artifacts import write_json


class _RSSMonitor:
    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval = interval_seconds
        self.process = psutil.Process()
        self.peak_bytes = 0
        self.start_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.peak_bytes = max(self.peak_bytes, self.process.memory_info().rss)
            except psutil.Error:
                break

    def start(self) -> None:
        self.start_bytes = self.process.memory_info().rss
        self.peak_bytes = self.start_bytes
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self.peak_bytes = max(self.peak_bytes, self.process.memory_info().rss)
        except psutil.Error:
            pass

    @property
    def peak_mb(self) -> float:
        return round(self.peak_bytes / (1024 * 1024), 2)

    @property
    def start_mb(self) -> float:
        return round(self.start_bytes / (1024 * 1024), 2)


def _environment() -> dict[str, Any]:
    import duckdb
    import numpy
    import ortools
    import pandas
    import pyarrow

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": psutil.cpu_count(logical=True),
        "memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "pandas": pandas.__version__,
        "numpy": numpy.__version__,
        "duckdb": duckdb.__version__,
        "pyarrow": pyarrow.__version__,
        "ortools": ortools.__version__,
        "pipeline_version": PIPELINE_VERSION,
    }


def _quality(run: PipelineRun) -> dict[str, Any]:
    metrics = run.anomaly_evaluation.metrics if run.anomaly_evaluation is not None else pd.DataFrame()
    return {
        "clean_validation_passed": run.validation.passed,
        "clean_issue_count": int(len(run.validation.issues)),
        "assigned_deliveries": int(run.plan.summary.get("assigned_deliveries", 0)),
        "unserved_deliveries": int(run.plan.summary.get("unserved_deliveries", 0)),
        "delivered_deliveries": int(run.report_summary.get("delivered_deliveries", 0)),
        "service_rate": float(run.report_summary.get("delivery_service_rate", 0.0)),
        "on_time_rate": float(run.report_summary.get("on_time_rate", 0.0)),
        "route_count": int(len(run.plan.tables.get("routes", pd.DataFrame()))),
        "anomaly_detection_mean_f1": (
            float(metrics["f1"].mean()) if not metrics.empty else None
        ),
        "anomaly_detection_min_f1": (
            float(metrics["f1"].min()) if not metrics.empty else None
        ),
    }


def _summarise_stages(run: PipelineRun, orchestration_seconds: float) -> dict[str, float]:
    stages = {name: round(float(value), 4) for name, value in run.stage_durations.items()}
    measured = sum(
        stages.get(name, 0.0)
        for name in (
            "generate",
            "plan",
            "simulate",
            "reporting",
            "validate_clean",
            "inject_anomalies",
            "detect_anomalies",
            "validate_dirty",
            "persist",
        )
    )
    stages["measured_sum"] = round(measured, 4)
    stages["total"] = round(orchestration_seconds, 4)
    return stages


def _benchmark_config(
    config: AppConfig,
    *,
    output_directory: Path,
    artifact_formats: tuple[str, ...],
    warehouse_path: Path | str | None = None,
) -> dict[str, Any]:
    run_output = output_directory / f"bench-{config.run_id}"
    db_path = Path(warehouse_path) if warehouse_path else run_output / "benchmark.duckdb"
    if run_output.exists():
        shutil.rmtree(run_output)
    monitor = _RSSMonitor()
    tracemalloc.start()
    started = time.perf_counter()
    monitor.start()
    run = build_pipeline_run(config, include_anomalies=True)
    persist_started = time.perf_counter()
    paths = persist_pipeline_run(
        run,
        run_output,
        warehouse_path=db_path,
        artifact_formats=artifact_formats,
    )
    persist_wall_seconds = time.perf_counter() - persist_started
    orchestration_seconds = time.perf_counter() - started
    current_bytes, python_peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    monitor.stop()
    stages = _summarise_stages(run, orchestration_seconds)
    counts = {
        "customer_sites": len(run.dataset.tables["customer_sites"]),
        "orders": len(run.dataset.tables["orders"]),
        "deliveries": len(run.dataset.tables["deliveries"]),
        "vehicles": len(run.dataset.tables["vehicles"]),
        "drivers": len(run.dataset.tables["drivers"]),
        "shifts": len(run.dataset.tables["shifts"]),
        "travel_matrix_rows": len(run.dataset.tables["travel_time_matrix"]),
        "routes": len(run.plan.tables["routes"]),
        "route_stops": len(run.plan.tables["route_stops"]),
        "route_events": len(run.simulation.tables["route_events"]),
        "delivery_events": len(run.simulation.tables["delivery_events"]),
    }
    throughput = {
        "deliveries_per_second": round(
            counts["deliveries"] / stages["total"], 3
        )
        if stages["total"]
        else 0.0,
        "diagnostics_rows_per_second": round(
            sum(counts.values()) / stages["total"], 3
        )
        if stages["total"]
        else 0.0,
    }
    solver_runs = run.plan.tables.get("solver_runs", pd.DataFrame())
    if len(solver_runs):
        solver_seconds = float(
            pd.to_numeric(solver_runs["solve_seconds"], errors="coerce")
            .fillna(0.0)
            .sum()
        )
    else:
        solver_seconds = 0.0
    planning_seconds = float(run.stage_durations.get("plan", 0.0))
    finalization_seconds = max(
        0.0, persist_wall_seconds - float(stages.get("persist", 0.0))
    )
    timing = {
        "planning_seconds": round(planning_seconds, 4),
        "solver_seconds": round(solver_seconds, 4),
        "solver_seconds_per_day": round(
            solver_seconds / max(1, config.horizon.days), 4
        ),
        "solver_run_count": int(len(solver_runs)),
        "persist_total_seconds": round(persist_wall_seconds, 4),
        "persist_stage_seconds": round(float(stages.get("persist", 0.0)), 4),
        "finalization_seconds": round(finalization_seconds, 4),
        "total_seconds": stages["total"],
        "measured_stage_seconds": stages["measured_sum"],
        "unattributed_seconds": round(
            max(
                0.0,
                stages["total"]
                - float(stages["measured_sum"])
                - finalization_seconds,
            ),
            4,
        ),
    }
    return {
        "config": config.run_id,
        "run_id": config.run_id,
        "config_hash": config.config_hash(),
        "status": run.status,
        "seed": config.seed,
        "days": config.horizon.days,
        "solver_time_limit_seconds": config.routing.time_limit_seconds,
        "stages": stages,
        "timing": timing,
        "throughput": throughput,
        "memory": {
            "start_rss_mb": monitor.start_mb,
            "peak_rss_mb": monitor.peak_mb,
            "python_tracemalloc_peak_mb": round(
                python_peak_bytes / (1024 * 1024), 2
            ),
            "python_tracemalloc_current_mb": round(
                current_bytes / (1024 * 1024), 2
            ),
        },
        "counts": counts,
        "quality": _quality(run),
        "artifacts": {
            "run_directory": str(paths.run_directory),
            "warehouse": str(paths.warehouse_path),
            "manifest": str(paths.manifest_path),
        },
        "checksums": {
            "clean": table_checksums(run.tables),
        },
        "error": run.error_message,
    }


def _resolve_config(name: str) -> AppConfig:
    path = Path(name)
    if not path.exists() and not path.suffix:
        path = Path(f"{name}.yaml")
    return load_config(path)


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# FleetMind benchmark report",
        "",
        f"Measured: {report['measured_at']}",
        "",
        "## Environment",
        "",
    ]
    for key, value in report["environment"].items():
        lines.append(f"- **{key}**: {value}")
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Config | Days | Sites | Orders | Vehicles | Generate (s) | Plan (s) | Simulate (s) | Validate (s) | Persist (s) | Total (s) | Peak RSS (MB) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["results"]:
        if item.get("status") == "FAILED":
            lines.append(
                f"| {item.get('config')} | - | - | - | - | - | - | - | - | - | - | - |"
            )
            continue
        stages = item["stages"]
        counts = item["counts"]
        lines.append(
            "| {config} | {days} | {sites} | {orders} | {vehicles} | {generate:.2f} | "
            "{plan:.2f} | {simulate:.2f} | {validate:.2f} | {persist:.2f} | {total:.2f} | {rss:.1f} |".format(
                config=item["config"],
                days=item["days"],
                sites=counts["customer_sites"],
                orders=counts["orders"],
                vehicles=counts["vehicles"],
                generate=stages.get("generate", 0.0),
                plan=stages.get("plan", 0.0),
                simulate=stages.get("simulate", 0.0),
                validate=stages.get("validate_clean", 0.0),
                persist=stages.get("persist", 0.0),
                total=stages.get("total", 0.0),
                rss=item["memory"]["peak_rss_mb"],
            )
        )
    lines.extend(
        [
            "",
            "## Timing",
            "",
            "| Config | Planning (s) | Solver (s) | Solver s/day | Total (s) | Measured stages (s) | Finalization (s) | Unattributed (s) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["results"]:
        if item.get("status") == "FAILED":
            continue
        timing = item["timing"]
        lines.append(
            "| {config} | {planning:.2f} | {solver:.2f} | {solver_day:.3f} | "
            "{total:.2f} | {measured:.2f} | {finalize:.2f} | {unattributed:.2f} |".format(
                config=item["config"],
                planning=timing["planning_seconds"],
                solver=timing["solver_seconds"],
                solver_day=timing["solver_seconds_per_day"],
                total=timing["total_seconds"],
                measured=timing["measured_stage_seconds"],
                finalize=timing["finalization_seconds"],
                unattributed=timing["unattributed_seconds"],
            )
        )
    lines.extend(
        [
            "",
            "## Quality and throughput",
            "",
            "| Config | Routes | Delivered | Service | On-time | Solver s/day | Deliveries/s | Mean anomaly F1 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["results"]:
        if item.get("status") == "FAILED":
            continue
        quality = item["quality"]
        throughput = item["throughput"]
        timing = item["timing"]
        f1 = quality.get("anomaly_detection_mean_f1")
        lines.append(
            "| {config} | {routes} | {delivered} | {service:.1%} | {on_time:.1%} | "
            "{solver_day:.3f} | {dps:.2f} | {f1} |".format(
                config=item["config"],
                routes=quality["route_count"],
                delivered=quality["delivered_deliveries"],
                service=quality["service_rate"],
                on_time=quality["on_time_rate"],
                solver_day=timing["solver_seconds_per_day"],
                dps=throughput["deliveries_per_second"],
                f1="n/a" if f1 is None else f"{f1:.3f}",
            )
        )
    failures = [item for item in report["results"] if item.get("status") == "FAILED"]
    if failures:
        lines.extend(["", "## Failures", ""])
        for item in failures:
            lines.append(f"- **{item.get('config')}**: {item.get('error')}")
    lines.extend(
        [
            "",
            "## Method",
            "",
            "- One measured run per configuration on the same machine; no warm-up run. Configurations run sequentially in one Python process, so later runs may benefit from warm OS and library caches.",
            "- `planning_seconds` is the full planning stage: deterministic pre-checks, model construction, solver search, and result assembly across all service days.",
            "- `solver_seconds` is the sum of the per-day `solver_runs.solve_seconds` values actually persisted by the planner, not the full planning stage.",
            "- `finalization_seconds` covers checksum regeneration, metadata/warehouse upserts, summary writing, and manifest construction after the timed persist stage.",
            "- `unattributed_seconds` is total wall time minus measured stages and finalization; it covers benchmark harness work such as report writing.",
            "- Peak RSS is sampled every 50 ms by a background thread; Python allocation peak uses tracemalloc.",
            "- Solver wall time is subject to OR-Tools search limits configured per day in YAML.",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark_suite(
    config_names: list[str],
    *,
    output_directory: Path | str = Path("outputs/benchmarks"),
    warehouse_path: Path | str | None = None,
    artifact_formats: tuple[str, ...] = ("parquet", "csv"),
) -> dict[str, Any]:
    """Run each named config once and write JSON + Markdown reports."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    shared_warehouse = Path(warehouse_path) if warehouse_path else None
    for name in config_names:
        started = time.perf_counter()
        try:
            config = _resolve_config(name)
            item = _benchmark_config(
                config,
                output_directory=output,
                artifact_formats=artifact_formats,
                warehouse_path=shared_warehouse,
            )
            item["config"] = name
            item["config_name"] = name
            item["benchmark_seconds"] = round(time.perf_counter() - started, 4)
        except Exception as exc:
            item = {
                "config": name,
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "benchmark_seconds": round(time.perf_counter() - started, 4),
            }
        results.append(item)
    report = {
        "measured_at": pd.Timestamp.now().isoformat(),
        "environment": _environment(),
        "configurations": config_names,
        "repeats": 1,
        "results": results,
    }
    write_json(output / "benchmark_report.json", report)
    (output / "benchmark_report.md").write_text(_markdown(report), encoding="utf-8")
    return report
