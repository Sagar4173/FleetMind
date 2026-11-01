# Operations guide

## Environment

FleetMind supports Python 3.11–3.13. OR-Tools wheels are not published for every newer Python release, and Python 3.14
is not supported by the pinned OR-Tools version. The verified environment for this repository is Python 3.13.9 on
Windows 11 with a local virtual environment.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Verify the installation:

```powershell
python -m fleetmind --help
python -c "import ortools, duckdb, streamlit; print('dependencies ok')"
```

The virtual environment is local and ignored by Git (`.venv/` is not checked in). After creating and activating it as
above, `.\.venv\Scripts\python.exe -m fleetmind` also works without activating the shell.

`requirements.lock` pins the exact dependency versions that passed verification on Python 3.13.9 / Windows 11. It is a
snapshot of the verified environment, including transitive and development packages, not a cross-platform resolver
lock. To recreate the verified environment:

```powershell
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m pip check
```

`pip check` verifies that installed packages have compatible requirements. A vulnerability-database audit is not run
by the test suite; see `docs/verification.md` for the dependency-audit evidence and its limits.

## Running the pipeline

```powershell
# Small, fast run (recommended for development)
python -m fleetmind run --config starter

# Clean-only run: no injection, detection, or dirty validation
python -m fleetmind run --config starter --no-anomalies

# Required baseline scale
python -m fleetmind run --config baseline

# Scale headroom
python -m fleetmind run --config larger
```

The default artifact root is `outputs`; the default warehouse is `outputs/fleetmind.duckdb`. Override either:

```powershell
python -m fleetmind run `
  --config baseline `
  --output outputs/experiments `
  --database outputs/experiments/fleetmind.duckdb
```

Choose formats with `--formats parquet,csv` (default) or a subset such as `--formats parquet`. Unsupported formats
exit with code 2 before any pipeline work.

### What success looks like

A successful command prints:

- the deterministic run ID and configuration summary;
- a KPI table with delivery service rate, on-time rate, route count, distance, utilization, and anomaly F1;
- stage timings;
- artifact, warehouse, summary, and manifest paths.

It does not print stack traces for expected validation failures. The exit code is the machine-readable signal.

### Exit codes

| Code | Meaning |
|---:|---|
| 0 | Run succeeded (or command completed). |
| 1 | Pipeline failed, partial, or warehouse operation failed. |
| 2 | Invalid command usage, missing configuration, or unsupported format. |

## Reruns and idempotency

The run ID is derived only from the canonical configuration hash. Rerunning the same configuration:

1. rewrites artifacts under the same `RUN-<HASH>` directory;
2. atomically deletes and reinserts every warehouse row for that `run_id`;
3. leaves other runs untouched.

This means artifacts are overwritten, not appended. If you need to preserve multiple executions of the same
configuration, copy the run directory or change a configuration value (which changes the run ID). The warehouse
guarantees the previous snapshot remains intact if the replacement transaction fails.

A `--no-anomalies` run uses the same configuration hash as a full run, so it replaces the full run's dirty evidence
with a clean-only snapshot and writes `mode=CLEAN`. Use a separate `--database` or a distinct configuration value if
both modes must coexist.

## Inspecting the warehouse

```powershell
python -m fleetmind runs --database outputs/fleetmind.duckdb
```

Ad-hoc SQL with Python:

```powershell
python -c "import duckdb; print(duckdb.connect('outputs/fleetmind.duckdb', read_only=True).sql(\"select run_id, status, mode, completed_at from run_metadata order by started_at desc\").df())"
```

Useful tables for review:

- `run_metadata`, `audit_records` — run-level status and execution trail.
- `report_metrics`, `reconciliation_checks` — KPI formulas and independent invariants.
- `data_quality_issues`, `quarantine_records` — validation findings.
- `ground_truth_anomalies`, `anomaly_detections`, `anomaly_detection_metrics` — anomaly evidence.
- `dirty_*` — corrupted snapshot mirrors.

DuckDB is single-writer. Close the dashboard or any other connection holding a write lock before running the
pipeline against the same file. The dashboard opens read-only, but Windows file locking can still prevent a writer
from opening the same database while Streamlit is active.

## Dashboard

```powershell
python -m fleetmind dashboard --database outputs/fleetmind.duckdb
```

The app defaults to port 8501. It selects the most recent run by `started_at`; the sidebar run selector lists all
persisted runs. To point the app at a different warehouse without the CLI:

```powershell
$env:FLEETMIND_DB = "F:\path\to\fleetmind.duckdb"
streamlit run dashboard\app.py
```

If the warehouse is missing or empty, the app renders an explanatory empty state instead of failing.

The dashboard is a localhost-only, read-only inspection tool. Streamlit binds to localhost by default, and the app has
no authentication or authorization layer. Do not expose the port publicly, and only open warehouses you trust.

## Benchmarks

```powershell
python -m fleetmind benchmark --configs baseline,larger --output outputs/benchmarks
```

The suite runs each configuration once and writes `benchmark_report.json` and `benchmark_report.md`. It records:

- environment (Python, OS, CPU count, RAM, library versions);
- per-stage durations (including reporting), measured stage sum, finalization time, unattributed harness time, and
  total wall time;
- `planning_seconds` for the full planning stage and `solver_seconds` / `solver_seconds_per_day` derived from the
  persisted per-day `solver_runs.solve_seconds` values;
- throughput in deliveries/second and diagnostic rows/second;
- peak RSS sampled every 50 ms and Python tracemalloc peak;
- quality outcomes (clean validation, service rate, on-time rate, anomaly F1);
- artifact and warehouse paths, plus clean table checksums.

A failed configuration is recorded with `status=FAILED` and an error string; the suite continues to the next
configuration and still writes the report. The CLI prints a compact table and exits non-zero only if the suite itself
cannot run.

The checked-in report under `outputs/benchmarks/` is the measured evidence used in the README. Re-running the
benchmark overwrites it; keep the historical file if you need to compare machines. The pre-remediation 2026-10-31
report and the intermediate PyArrow 21 run are preserved in `outputs/benchmarks/history/` for that reason.

## Artifact retention

`outputs/` is ignored by Git except for the benchmark report evidence. A baseline run produces tens of megabytes of
Parquet/CSV files and a DuckDB database; a larger run produces more. Prune old run directories when they are no longer
needed, but keep the warehouse if the dashboard must remain usable.

The checked-in `data/sample/starter-run/` is a complete small run for reviewers: every artifact group, Parquet and CSV
files, `manifest.json`, and `checksums.json`. The DuckDB warehouse is not included, so the directory cannot be loaded by
the dashboard. Regenerate it from a real run with `python scripts/build_sample.py`.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `No matching distribution found for ortools` | Unsupported Python version | Use Python 3.11–3.13 and recreate the venv. |
| `Configuration error: ... not found` | Bare name has no matching YAML | Use `starter`, `baseline`, or an explicit path. |
| `Validation failed` with artifacts written | Clean snapshot violated a hard rule | Inspect `validation/clean_issues.csv`, fix generation/planning, rerun. |
| Clean run fails with `DRIVER_SHIFT_EXCEEDED` and no anomaly output | Simulation variance pushed a route past its shift; the anomaly stage is skipped for failed clean snapshots | Inspect the route's `shift_overrun_minutes`, reduce variance, or increase the execution buffer. This is an intentional CRITICAL finding. |
| `Pipeline incomplete: anomaly stage failed` | Injection candidates insufficient or detector input missing | Inspect the error and `summary.json`; reduce `anomaly.instances_per_type` or enlarge the dataset. |
| `could not open warehouse ...` | File lock or permissions | Close other DuckDB/Streamlit processes, check path permissions. |
| Dashboard shows no runs | Wrong database path or empty warehouse | Run the pipeline first; pass `--database`; check `FLEETMIND_DB`. |
| Benchmark command exits 1 | A config path is invalid or the suite failed | Inspect `benchmark_report.json` failures; correct the config path. |

## Reproducing a run

Given the same repository commit, configuration, seed, and dependency versions, generation, planning, simulation,
validation, and anomaly evaluation are deterministic. Absolute timings and wall-clock audit timestamps are not.
Logical checksums in `checksums.json` should match across reruns on the same platform; Parquet file bytes may differ
because compression metadata and Arrow versions are not part of the logical contract.
