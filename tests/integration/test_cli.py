from __future__ import annotations

from pathlib import Path

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
