"""Rebuild ``data/sample/starter-run`` from a real starter pipeline execution.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe scripts/build_sample.py

The script executes the configured pipeline, verifies every clean-table checksum
against the Parquet artifacts, copies the complete run directory into the sample
location, and normalizes the summary paths for portability.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from fleetmind.config import load_config
from fleetmind.generation.factory import logical_checksum
from fleetmind.persistence.artifacts import write_json
from fleetmind.pipeline import execute_pipeline

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "starter.yaml"
DEFAULT_DESTINATION = ROOT / "data" / "sample" / "starter-run"

SAMPLE_README = r"""# FleetMind starter-run sample

This directory is a complete copy of one deterministic `fleetmind run --config starter`
execution (`RUN-C16424724715`). Regenerate it with `scripts/build_sample.py`.

## Synthetic data

Every customer name, contact, street address, phone number, email address, coordinate,
and identifier in this sample is synthetic. The dataset is produced by seeded generators
and Faker; it does not describe real people, companies, or locations.

## Contents

- `master/` contains the pre-execution master snapshot. `deliveries.status` is `PENDING`
  and `orders.status` is `CONFIRMED`.
- `plan/` contains the generated routes, stops, legs, assignments, and solver runs.
- `simulation/` contains the post-execution versions of routes, stops, legs, deliveries,
  orders, outcomes, and event streams. Assignments remain in `plan/` because execution does
  not rewrite them. Checksums in `checksums.json` describe the final version of each table:
  the `simulation/` copy where execution supersedes planning or master data, otherwise the
  `plan/` or `master/` copy. `quarantine_dirty/` holds the quarantined copies of dirty rows
  and is not part of the 21 clean-table checksums.
- `anomalies/`, `validation/`, `quarantine_dirty/`, and `reports/` contain the anomaly
  ground truth, detections, dirty snapshot issues, quarantine evidence, and reconciliation
  output.
- `manifest.json` lists every artifact with its row count, columns, logical checksum, and
  a path relative to this directory.
- `checksums.json` contains the 21 clean-table logical checksums.

The DuckDB warehouse is intentionally **not** included: it is a runtime artifact and is
much larger than the CSV/Parquet evidence. `summary.json` records its table counts for
provenance with `warehouse.included` set to `false`.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_sample_artifacts.py -q
```

CSV files are provided for reviewer convenience. Parquet is the supported format for
checksum reproduction because CSV readers can infer different column types.
"""


def _verify_checksums(run_directory: Path) -> dict[str, str]:
    checksums = json.loads(
        (run_directory / "checksums.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (run_directory / "manifest.json").read_text(encoding="utf-8")
    )["table_groups"]

    artifacts: dict[str, str] = {}
    for tables in manifest.values():
        for table, entry in tables.items():
            frame = pd.read_parquet(run_directory / entry["parquet"])
            actual = logical_checksum(frame)
            if actual != entry["checksum"]:
                raise RuntimeError(
                    f"manifest checksum mismatch for {table} at {entry['parquet']}"
                )
            artifacts.setdefault(table, set()).add(actual)

    missing = sorted(set(checksums) - set(artifacts))
    if missing:
        raise RuntimeError(f"checksummed tables without artifacts: {missing}")
    for table, expected in checksums.items():
        if expected not in artifacts[table]:
            raise RuntimeError(
                f"no artifact reproduces checksum for {table}: {expected}"
            )
    return checksums


def build_sample(
    *,
    config_path: Path = DEFAULT_CONFIG,
    destination: Path = DEFAULT_DESTINATION,
) -> Path:
    config = load_config(config_path)
    with tempfile.TemporaryDirectory(prefix="fleetmind_sample_") as temporary:
        temporary_path = Path(temporary)
        run, paths = execute_pipeline(
            config,
            temporary_path / "artifacts",
            warehouse_path=temporary_path / "fleetmind.duckdb",
        )
        checksums = _verify_checksums(paths.run_directory)

        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(paths.run_directory, destination)

    summary_path = destination / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["artifacts"] = {
        "run_directory": ".",
        "checksums": "checksums.json",
        "groups": summary["artifacts"]["groups"],
    }
    summary["warehouse"] = {
        "path": "fleetmind.duckdb",
        "included": False,
        "table_counts": summary["warehouse"]["table_counts"],
    }
    write_json(summary_path, summary)

    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["warehouse_path"] = "fleetmind.duckdb"
    write_json(manifest_path, manifest)

    (destination / "README.md").write_text(SAMPLE_README, encoding="utf-8")

    print(
        f"Wrote {len(checksums)} checksummed tables to {destination} "
        f"from {run.run_id} ({run.status})"
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    build_sample(config_path=args.config, destination=args.destination)


if __name__ == "__main__":
    main()
