"""Integrity checks for the checked-in starter-run sample."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from fleetmind.generation.factory import logical_checksum

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample" / "starter-run"
CLEAN_TABLE_COUNT = 21


def _read_json(name: str) -> dict:
    return json.loads((SAMPLE / name).read_text(encoding="utf-8"))


def _manifest_artifacts() -> dict[str, dict[str, dict]]:
    return _read_json("manifest.json")["table_groups"]


def test_sample_has_manifest_summary_and_checksums() -> None:
    for name in ("manifest.json", "summary.json", "checksums.json", "config.yaml"):
        assert (SAMPLE / name).is_file(), f"missing sample metadata: {name}"

    manifest = _read_json("manifest.json")
    summary = _read_json("summary.json")
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["status"] == "SUCCEEDED"
    assert summary["status"] == "SUCCEEDED"
    assert manifest["config_hash"] == summary["config_hash"]


def test_every_manifest_artifact_exists_and_matches_metadata() -> None:
    manifest = _read_json("manifest.json")
    artifacts = manifest["table_groups"]
    assert artifacts, "manifest has no table groups"

    for group, tables in artifacts.items():
        assert tables, f"manifest group {group} is empty"
        for table, entry in tables.items():
            for output_format in ("parquet", "csv"):
                assert output_format in entry, f"{group}/{table} missing {output_format} path"
                path = SAMPLE / entry[output_format]
                assert path.is_file(), f"manifest artifact missing: {path}"

            parquet = pd.read_parquet(SAMPLE / entry["parquet"])
            csv = pd.read_csv(SAMPLE / entry["csv"])
            assert len(parquet) == entry["rows"], f"{group}/{table} parquet row count"
            assert len(csv) == entry["rows"], f"{group}/{table} csv row count"
            assert list(parquet.columns) == entry["columns"], f"{group}/{table} columns"
            assert logical_checksum(parquet) == entry["checksum"], (
                f"{group}/{table} manifest checksum does not reproduce"
            )


def test_manifest_paths_are_relative_to_the_run_directory() -> None:
    artifacts = _manifest_artifacts()
    for tables in artifacts.values():
        for entry in tables.values():
            for output_format in ("parquet", "csv"):
                path = Path(entry[output_format])
                assert not path.is_absolute(), f"manifest path is absolute: {path}"
                assert ".." not in path.parts, f"manifest path escapes run directory: {path}"

    summary = _read_json("summary.json")
    assert summary["artifacts"]["run_directory"] == "."
    assert summary["artifacts"]["checksums"] == "checksums.json"

    manifest = _read_json("manifest.json")
    assert manifest["warehouse_path"] == "fleetmind.duckdb"
    assert not Path(manifest["warehouse_path"]).is_absolute()


def test_every_checksum_resolves_to_a_parquet_artifact() -> None:
    checksums = _read_json("checksums.json")
    assert len(checksums) == CLEAN_TABLE_COUNT

    artifacts = _manifest_artifacts()
    found: dict[str, dict[str, str]] = {}
    for group, tables in artifacts.items():
        for table, entry in tables.items():
            found.setdefault(table, {})[group] = entry["checksum"]

    missing = sorted(set(checksums) - set(found))
    assert not missing, f"checksummed tables have no artifact: {missing}"

    for table, expected in checksums.items():
        matches = [
            group for group, checksum in found[table].items() if checksum == expected
        ]
        assert matches, (
            f"no artifact reproduces checksum for {table}: expected {expected}, "
            f"available groups {found[table]}"
        )


def test_master_and_simulation_snapshots_are_not_mixed() -> None:
    summary = _read_json("summary.json")
    checksums = _read_json("checksums.json")
    counts = summary["counts"]

    master_deliveries = pd.read_parquet(SAMPLE / "master" / "deliveries.parquet")
    master_orders = pd.read_parquet(SAMPLE / "master" / "orders.parquet")
    simulation_deliveries = pd.read_parquet(SAMPLE / "simulation" / "deliveries.parquet")
    simulation_orders = pd.read_parquet(SAMPLE / "simulation" / "orders.parquet")

    assert len(master_deliveries) == counts["master_deliveries"]
    assert len(master_orders) == counts["master_orders"]
    assert len(simulation_deliveries) == counts["master_deliveries"]
    assert len(simulation_orders) == counts["master_orders"]

    assert set(master_deliveries["status"]) == {"PENDING"}
    assert set(master_orders["status"]) == {"CONFIRMED"}
    finished = {"DELIVERED", "FAILED", "UNSERVED"}
    assert set(simulation_deliveries["status"]) <= finished
    assert set(simulation_orders["status"]) <= finished
    assert set(simulation_deliveries["status"]) == {"DELIVERED", "FAILED", "UNSERVED"}

    # checksums.json describes the post-simulation snapshot, not the pre-execution one.
    assert logical_checksum(simulation_deliveries) == checksums["deliveries"]
    assert logical_checksum(simulation_orders) == checksums["orders"]
    assert logical_checksum(master_deliveries) != checksums["deliveries"]
    assert logical_checksum(master_orders) != checksums["orders"]


def test_summary_groups_and_counts_match_checked_in_artifacts() -> None:
    summary = _read_json("summary.json")
    counts = summary["counts"]

    group_directories = sorted(path.name for path in SAMPLE.iterdir() if path.is_dir())
    assert summary["artifacts"]["groups"] == group_directories
    assert "quarantine_dirty" in group_directories

    assert _read_json("checksums.json") == summary["checksums"]["clean"]
    assert len(summary["checksums"]["dirty"]) == CLEAN_TABLE_COUNT

    dirty_issues = pd.read_parquet(
        SAMPLE / "validation" / "dirty_issues.parquet"
    )
    assert len(dirty_issues) == counts["dirty_issues"]

    dirty_quarantine = pd.read_parquet(
        SAMPLE / "validation" / "dirty_quarantine_records.parquet"
    )
    assert len(dirty_quarantine) == counts["dirty_quarantine_records"]

    quarantined_rows = 0
    for path in sorted((SAMPLE / "quarantine_dirty").glob("*.parquet")):
        frame = pd.read_parquet(path)
        quarantined_rows += len(frame)
        assert not frame.empty, f"empty quarantine evidence: {path.name}"
    assert quarantined_rows == counts["dirty_quarantine_records"]


def test_sample_declares_that_the_warehouse_is_not_included() -> None:
    summary = _read_json("summary.json")
    warehouse = summary["warehouse"]
    assert warehouse["included"] is False
    assert warehouse["path"] == "fleetmind.duckdb"
    assert warehouse["table_counts"], "warehouse provenance counts were removed"

    sample_readme = SAMPLE / "README.md"
    assert sample_readme.is_file(), "sample must explain what is and is not included"
    text = sample_readme.read_text(encoding="utf-8").lower()
    assert "synthetic" in text
    assert "warehouse" in text
