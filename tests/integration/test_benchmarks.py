from __future__ import annotations

import json

import pytest

from fleetmind.benchmarks import run_benchmark_suite


@pytest.mark.integration
def test_benchmark_suite_writes_measured_report(tiny_pipeline, tmp_path) -> None:
    config_path = tmp_path / "tiny.yaml"
    tiny_pipeline.config.to_yaml(config_path)
    output = tmp_path / "benchmarks"
    report = run_benchmark_suite(
        [str(config_path)],
        output_directory=output,
        artifact_formats=("parquet",),
    )
    assert report["repeats"] == 1
    assert len(report["results"]) == 1
    item = report["results"][0]
    assert item["status"] == "SUCCEEDED", item.get("error")
    assert item["stages"]["total"] > 0
    assert item["stages"]["generate"] >= 0
    assert item["stages"]["plan"] > 0
    assert item["stages"]["persist"] > 0
    assert item["stages"]["reporting"] >= 0
    assert "orchestration" not in item["stages"]

    timing = item["timing"]
    assert timing["planning_seconds"] == pytest.approx(item["stages"]["plan"])
    assert timing["solver_seconds"] > 0
    assert timing["solver_seconds"] <= timing["planning_seconds"]
    assert timing["solver_seconds_per_day"] == pytest.approx(
        timing["solver_seconds"] / item["days"]
    )
    assert timing["measured_stage_seconds"] == pytest.approx(
        sum(
            item["stages"].get(name, 0.0)
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
    )
    assert timing["finalization_seconds"] >= 0
    assert timing["persist_total_seconds"] >= timing["persist_stage_seconds"]
    assert timing["unattributed_seconds"] == pytest.approx(
        max(
            0.0,
            item["stages"]["total"]
            - timing["measured_stage_seconds"]
            - timing["finalization_seconds"],
        )
    )
    assert timing["total_seconds"] == item["stages"]["total"]
    assert item["config_hash"] == tiny_pipeline.config.config_hash()
    assert "config_file" not in item

    assert item["memory"]["peak_rss_mb"] > 0
    assert item["throughput"]["deliveries_per_second"] > 0
    assert item["quality"]["anomaly_detection_mean_f1"] == 1.0
    assert item["counts"]["deliveries"] == 60
    json_path = output / "benchmark_report.json"
    markdown_path = output / "benchmark_report.md"
    assert json_path.exists()
    assert markdown_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["environment"]["python"]
    assert "FleetMind benchmark report" in markdown_path.read_text(encoding="utf-8")


@pytest.mark.integration
def test_benchmark_records_failed_config(tmp_path) -> None:
    output = tmp_path / "benchmarks"
    report = run_benchmark_suite(
        [str(tmp_path / "missing.yaml")],
        output_directory=output,
        artifact_formats=("parquet",),
    )
    item = report["results"][0]
    assert item["status"] == "FAILED"
    assert "FileNotFoundError" in item["error"]
    assert (output / "benchmark_report.md").exists()
