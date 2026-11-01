from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fleetmind.cli import app

runner = CliRunner()


def test_cli_info_and_schema() -> None:
    info = runner.invoke(app, ["info", "--config", "configs/starter.yaml"])
    assert info.exit_code == 0, info.output
    assert "config_hash" in info.output
    schema = runner.invoke(app, ["schema", "--show"])
    assert schema.exit_code == 0
    assert "CREATE TABLE IF NOT EXISTS run_metadata" in schema.output
    assert "PRIMARY KEY" in schema.output


def test_cli_run_and_runs_listing(tiny_pipeline, tmp_path) -> None:
    config_path = tmp_path / "tiny.yaml"
    tiny_pipeline.config.to_yaml(config_path)
    output = tmp_path / "artifacts"
    database = tmp_path / "warehouse.duckdb"
    result = runner.invoke(
        app,
        [
            "run",
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--database",
            str(database),
            "--no-anomalies",
            "--formats",
            "parquet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Run summary" in result.output
    assert "SUCCEEDED" in result.output
    assert database.exists()
    assert (output / tiny_pipeline.config.run_id / "summary.json").exists()

    listing = runner.invoke(app, ["runs", "--database", str(database)])
    assert listing.exit_code == 0, listing.output
    assert tiny_pipeline.config.run_id in listing.output


@pytest.mark.integration
def test_cli_benchmark_renders_peak_memory(tiny_pipeline, tmp_path) -> None:
    config_path = tmp_path / "tiny.yaml"
    tiny_pipeline.config.to_yaml(config_path)
    output = tmp_path / "benchmarks"
    result = runner.invoke(
        app,
        [
            "benchmark",
            "--configs",
            str(config_path),
            "--output",
            str(output),
            "--database",
            str(tmp_path / "warehouse.duckdb"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Benchmark results" in result.output
    assert "Reports written under" in result.output
    report = (output / "benchmark_report.json").read_text(encoding="utf-8")
    assert '"peak_rss_mb"' in report


def test_cli_rejects_unknown_artifact_format(tiny_pipeline, tmp_path) -> None:
    config_path = tmp_path / "tiny.yaml"
    tiny_pipeline.config.to_yaml(config_path)
    result = runner.invoke(
        app,
        [
            "run",
            "--config",
            str(config_path),
            "--output",
            str(tmp_path / "out"),
            "--formats",
            "parquet,avro",
        ],
    )
    assert result.exit_code == 2
    assert "Unsupported formats" in result.output


def test_cli_missing_config_returns_usage_error(tmp_path) -> None:
    result = runner.invoke(
        app,
        ["run", "--config", str(tmp_path / "absent.yaml"), "--output", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_dashboard_launcher_binds_streamlit_to_loopback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CompletedProcess:
        returncode = 0

    def fake_run(command, check=False, env=None):
        captured["command"] = command
        captured["env"] = env
        return CompletedProcess()

    monkeypatch.setattr("fleetmind.cli.subprocess.run", fake_run)
    result = runner.invoke(app, ["dashboard", "--port", "8765"])
    assert result.exit_code == 0, result.output

    command = captured["command"]
    assert isinstance(command, list)
    address = command.index("--server.address")
    assert command[address + 1] == "127.0.0.1"
    port = command.index("--server.port")
    assert command[port + 1] == "8765"


def test_project_streamlit_config_binds_loopback() -> None:
    path = Path(__file__).resolve().parents[2] / ".streamlit" / "config.toml"
    server = tomllib.loads(path.read_text(encoding="utf-8"))["server"]
    assert server["address"] == "127.0.0.1"
