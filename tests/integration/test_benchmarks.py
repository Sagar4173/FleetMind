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
