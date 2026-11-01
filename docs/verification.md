# Verification record

This document records the final verification evidence for FleetMind. All commands were run from the project root on
2026-11-01 with the project virtual environment. Nothing in this document is estimated:
the numbers come from pytest output, `summary.json`, the DuckDB warehouse, or
`outputs/benchmarks/benchmark_report.json`.

## Environment

| Property | Value |
|---|---|
| Platform | Windows 11 (`Windows-11-10.0.26300-SP0`) |
| Python | 3.13.9 (project `.venv`) |
| CPU | AMD64 Family 25 Model 80, 12 logical CPUs |
| RAM | 7.28 GB |
| OR-Tools | 9.15.6755 |
| DuckDB | 2.0.0 |
| Pandas | 2.3.3 |
| NumPy | 2.3.5 |
| PyArrow | 23.0.1 |
| Streamlit | 1.66.1 |
| Plotly | 6.9.0 |
| Typer | 0.27.3 |
| Faker | 40.44.0 |
| pytest | 9.0.3 |

OR-Tools is installed for Python 3.13 because the system Python 3.14 had no compatible wheel. The `.venv` is installed
in editable mode, so `.\\.venv\\Scripts\\python.exe -m fleetmind` and the `fleetmind` entry point are equivalent.

### Dependency lock and vulnerability scan

`requirements.lock` pins the exact versions from the verified environment. `pip check` reports
`No broken requirements found.` An OSV.dev `querybatch` scan on 2026-11-01 covered all 61 pinned package versions. The
initial scan found two advisory matches: `pyarrow 21.0.0` (`CVE-2026-25087`, fixed in 23.0.1) and `pytest 8.4.2`
(`CVE-2025-71176`, fixed in 9.0.3). Both packages were upgraded to the fixed versions, the environment was reinstalled,
and the full scan was rerun with zero known-vulnerability matches.

The scan is a point-in-time query against one public advisory database, not a guarantee that no vulnerability exists.
It does not cover transitive native libraries, platform packages, or license obligations. `docs/operations.md`
documents how to recreate the environment from the lock file.

## Test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result:

```text
104 passed, 3 warnings in 48.76s
```

The three warnings are OR-Tools SWIG deprecation notices (`SwigPyPacked`, `SwigPyObject`, `swigvarlink`). There were
no failures, errors, or skips.

The suite is split into:

| Area | Files | What it covers |
|---|---|---|
| Unit | `test_generation.py`, `test_geography_travel.py`, `test_config.py`, `test_validation.py`, `test_anomalies.py`, `test_reporting.py`, `test_dashboard_data.py`, `test_sample_artifacts.py` | Determinism, identifiers, travel model, config merging and driver/vehicle validation, every validation rule family, all six injectors/detectors/evaluation, KPI formulas, dashboard shaping, and checked-in sample integrity. |
| Integration | `test_pipeline_persistence.py`, `test_routing_simulation.py`, `test_cli.py`, `test_dashboard_app.py`, `test_benchmarks.py` | Tiny end-to-end pipeline, OR-Tools constraints, simulation events, high-variance shift-overrun regression coverage, warehouse idempotency/rollback, CLI exit codes and report rendering, Streamlit `AppTest`, measured benchmark writer and corrected timing metrics. |

The integration tests use small tiny configurations (8-12 sites) and run the real OR-Tools solver; the pipeline stages
are not mocked.

## Starter run

Command:

```powershell
.\.venv\Scripts\python.exe -m fleetmind run --config starter `
  --output outputs/verification-artifacts `
  --database outputs/verification.duckdb
```

Result: `RUN-C16424724715`, status `SUCCEEDED`, mode `FULL`, seed `20261022`.

| KPI | Value |
|---|---:|
| Customer sites | 40 |
| Requested deliveries | 120 |
| Vehicles / drivers / shifts | 12 / 12 / 64 |
| Service days | 7 |
| Routes | 25 |
| Assigned deliveries | 118 |
| Delivered | 116 |
| Failed | 2 |
| Unserved | 2 |
| Service rate | 96.67% |
| On-time rate | 100.00% |
| Total distance | 781.80 km |
| Capacity utilization | 58.10% |
| Fleet utilization | 91.67% |
| Clean validation issues | 0 |
| Dirty validation issues | 40 |
| Dirty quarantine records | 24 |
| Ground-truth anomalies | 12 |
| Detections | 12 |
| Mean anomaly F1 | 1.000 |
| Reconciliation checks | 15 / 15 passed |

Stage timings from `summary.json`:

| Stage | Seconds |
|---|---:|
| generate | 0.28 |
| plan | 35.06 |
| simulate | 0.05 |
| validate_clean | 0.33 |
| reporting | 0.04 |
| inject_anomalies | 0.12 |
| detect_anomalies | 0.03 |
| validate_dirty | 0.33 |
| persist | 1.01 |

The starter run used 2 instances per anomaly type. All six classes were detected with zero false positives and zero
false negatives. Dirty validation independently flagged the injected classes plus expected cascading foreign-key
issues; those cascades are excluded from the exact-match detection metrics.

## Reproducibility and idempotency

The starter command was executed twice against the same output directory and warehouse. The second run replaced the
first run's snapshot rather than appending to it.

Warehouse contents after the rerun:

| Table | Rows |
|---|---:|
| `run_metadata` | 1 |
| `routes` | 25 |
| `deliveries` | 120 |
| `ground_truth_anomalies` | 12 |
| `anomaly_detections` | 12 |
| `dirty_routes` | 25 |
| `dirty_route_stops` | 118 |

Checksum comparison:

- All 21 clean table checksums in `checksums.json` were identical across the two runs.
- All 21 checksums were recomputed successfully from the corresponding Parquet artifacts.
- The checked-in `data/sample/starter-run/` reproduces all 21 checksums from its Parquet files.

### Checked-in sample verification

`data/sample/starter-run/` is a complete copy of one real starter run (`RUN-C16424724715`), not a
hand-assembled subset. It has 91 files and roughly 1.3 MB:

- `master/` holds the pre-execution snapshot, including all 13 master tables. `deliveries.status`
  is `PENDING` and `orders.status` is `CONFIRMED`.
- `plan/` holds the five planning tables, including `solver_runs`.
- `simulation/` holds the post-execution versions of routes, stops, legs, deliveries, orders,
  outcomes, and event streams. Assignments remain in `plan/` because execution does not rewrite
  them. `checksums.json` describes the final versions for tables that execution supersedes.
- `anomalies/`, `validation/`, `quarantine_dirty/`, and `reports/` hold the anomaly ground truth
  (12), detections (12), dirty issues (40), quarantine evidence (24 records), and 15 reconciliation
  checks.
- `manifest.json` lists every table in every group with row count, columns, logical checksum, and
  paths relative to the sample directory.
- `config.yaml`, `summary.json`, and `checksums.json` are included. The DuckDB warehouse is not;
  `summary.json` marks `warehouse.included` as `false` and keeps its table counts for provenance.

The exact verification is automated in `tests/unit/test_sample_artifacts.py`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_sample_artifacts.py -q
```

The test checks that every manifest path exists and is relative, reads each Parquet/CSV file,
recomputes every manifest checksum, resolves every entry in `checksums.json` to a Parquet artifact,
and asserts that the master `deliveries`/`orders` snapshots are pre-execution while the simulation
snapshots are final. It also confirms the dirty-quarantine evidence totals match `summary.json`.

Regenerate the sample from a fresh real run with:

```powershell
.\.venv\Scripts\python.exe scripts/build_sample.py
```

Parquet is required for exact checksum reproduction. A CSV reader without an explicit schema can
infer different types (for example a numeric-looking postal code), so CSV files are reviewer
convenience only. This limitation is documented in `README.md`, `docs/architecture.md`, and
`docs/benchmark_methodology.md`.

### Checksum defect found and fixed

The first cross-run comparison showed `routes` and `solver_runs` checksums changing even though the data was the
same. The only differing column was `solve_seconds`, which is a measured wall-clock duration. Logical checksums now
exclude that column. A unit test asserts that changing `solve_seconds` does not change the checksum and that changing
a real data field does.

The benchmark report's checksum fields were recomputed from the preserved Parquet artifacts using the corrected
definition. Measured timings, row counts, and quality results were not changed.

## Benchmark evidence

Command:

```powershell
.\.venv\Scripts\python.exe -m fleetmind benchmark --configs baseline,larger --output outputs/benchmarks
```

Report: `outputs/benchmarks/benchmark_report.json` and `outputs/benchmarks/benchmark_report.md`, measured
`2026-11-01T01:18:22`. The pre-remediation report from `2026-10-31T17:21:29` and the intermediate PyArrow 21 report
from `2026-11-01T00:33:17` are preserved under `outputs/benchmarks/history/`.

| Config | Run ID | Days | Sites | Orders | Vehicles | Total (s) | Planning (s) | Solver (s) | Solver s/day | Routes | Assigned | Delivered | Unserved | Service | Peak RSS (MB) | Mean F1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | `RUN-A89FE8126581` | 30 | 500 | 2,000 | 100 | 503.27 | 310.63 | 302.79 | 10.093 | 295 | 2,000 | 1,991 | 0 | 99.55% | 449.42 | 1.000 |
| larger | `RUN-2155D9A8756A` | 30 | 750 | 4,000 | 150 | 886.07 | 481.11 | 457.65 | 15.255 | 521 | 4,000 | 3,984 | 0 | 99.60% | 865.45 | 1.000 |

Additional measured details:

| Config | Generate (s) | Simulate (s) | Reporting (s) | Validate clean (s) | Inject (s) | Detect (s) | Validate dirty (s) | Persist (s) | Finalization (s) | Deliveries/s | Python peak (MB) | Travel-matrix rows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 15.68 | 4.81 | 0.22 | 28.56 | 5.75 | 0.69 | 31.27 | 26.74 | 57.21 | 3.974 | 170.96 | 251,001 |
| larger | 34.18 | 9.22 | 0.27 | 57.60 | 12.80 | 1.27 | 61.35 | 59.05 | 124.01 | 4.514 | 371.01 | 564,001 |

Both configurations:

- passed clean validation with zero issues;
- assigned every requested delivery (0 unserved);
- passed all 15 reconciliation checks;
- detected every injected anomaly with precision, recall, and F1 of 1.000;
- ran one measurement per configuration with `repeats=1` and no warm-up.

`planning_seconds` is the full planning stage. `solver_seconds` is the sum of the per-day `solver_runs.solve_seconds`
values persisted by the planner, and `solver_seconds_per_day` divides that by the 30-day horizon. The totals include
Parquet/CSV writes, DuckDB persistence, both validation passes, injection, detection, and reporting. They are not
solver-only timings. `benchmark_seconds` (505.31 s baseline, 890.14 s larger) includes harness overhead outside the
measured stages.

## CLI and dashboard checks

```powershell
.\.venv\Scripts\python.exe -m fleetmind info --config starter
.\.venv\Scripts\python.exe -m fleetmind runs --database outputs/verification.duckdb
.\.venv\Scripts\python.exe -m fleetmind schema --show
```

Observed:

- `info` resolved `RUN-C16424724715` with configuration hash
  `c16424724715e796adbacc00f7480d36e6faa59964766d0bf9835d5ce19a74aa`.
- `runs` listed the persisted run with status `SUCCEEDED` and mode `FULL`.
- `schema --show` printed the generated DDL, including `CREATE TABLE IF NOT EXISTS run_metadata` and primary keys.
- The Streamlit `AppTest` integration test exercised the five dashboard tabs without starting a network server.
- The dashboard data helpers are tested for run selection, KPI filtering, route tables, quality frames, and missing
  database behavior.

## What was verified

- Deterministic generation and stable logical checksums for identical configurations.
- Hard capacity, time-window, shift, eligibility, and service constraints at the planner and validator levels.
- Exactly one assignment and one outcome per requested delivery.
- All six anomaly classes injected, detected independently, and evaluated with exact entity matching.
- Clean validation failure semantics, quarantine generation, and reconciliation invariants.
- Transactional warehouse snapshots, rerun idempotency, rollback, run isolation, and clean/dirty coexistence.
- CLI commands, exit codes, bare config resolution, schema export, and benchmark report rendering.
- Dashboard read-only behavior and empty-state handling.
- Measured baseline and larger performance at the required scale.

## Known limitations

- Benchmarks are single measured runs on one Windows machine, not a statistical distribution. Re-run on the target
  hardware before making capacity decisions.
- Travel is a static Haversine model with road and scenario multipliers. It is not live traffic or road-network
  routing.
- Planning solves each service day independently. Multi-depot operation, multi-day route continuity, and overnight
  routes are out of scope.
- Volume is stored but is not a hard routing constraint.
- Anomaly detection is rule-based and limited to the six specified classes; it is not a general anomaly classifier.
- The dashboard is a read-only consumer of this schema, not a general BI tool.
- The checked-in sample contains Parquet and CSV artifacts but not the DuckDB warehouse, so it cannot be opened
  directly by the dashboard.
- Credential and effective-date expiry fields are stored and internally ordered, but planning does not filter
  candidates by expiry. The generator keeps credentials valid across the provided 7- and 30-day horizons;
  expiry-aware eligibility is out of scope.
- All names, addresses, contacts, coordinates, and identifiers are synthetic. The sample and benchmark data do not
  describe real people, companies, or locations.
- No commit was pushed. Milestone commits remain local.
