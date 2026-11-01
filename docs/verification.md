# Verification record

This document records the final verification evidence for FleetMind. All commands were run on 2026-11-01 from the
project root with the project virtual environment unless a fresh-clone step is stated. Nothing in this document is
estimated: the numbers come from pytest output, `summary.json`, the DuckDB warehouse, netstat, or
`outputs/benchmarks/benchmark_report.json`.

## Environment

| Property | Value |
|---|---|
| Platform | Windows 11 (`Windows-11-10.0.26300-SP0`) |
| Python | 3.13.9 |
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

OR-Tools is installed for Python 3.13 because the system Python 3.14 had no compatible wheel. On this machine the
`py` launcher lists only Python 3.14, so the virtual environment was created with the 3.13 interpreter path directly;
`py -3.13` is documented only for machines whose launcher registers it. The package is installed in editable mode, so
`.\\.venv\\Scripts\\python.exe -m fleetmind` and the `fleetmind` entry point are equivalent.

## Dependency installation and lock

A fresh clone of the repository at commit `73343ba` was created outside the working tree and installed from scratch:

```powershell
git clone <local-repository> fleetmind-phase8-clone
cd fleetmind-phase8-clone

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m pip check
```

Result:

```text
No broken requirements found.
```

The lock contains 61 pinned packages. Its non-comment pin set matches `pip freeze --exclude-editable` exactly in the
verified environment. Installing the lock first and the project second with `--no-deps` keeps the verified versions
unchanged.

An OSV.dev `querybatch` scan of all 61 pinned package versions on 2026-11-01 returned zero advisory matches. The scan is
a point-in-time query against one public advisory database, not a guarantee that no vulnerability exists. It does not
cover transitive native libraries, platform packages, or license obligations.

## Test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Final result in the working tree:

```text
114 passed, 3 warnings in 54.50s
```

The same command in the fresh clone produced `114 passed, 3 warnings in 51.22s` after the clone was fast-forwarded to
commit `73343ba`. The three warnings are OR-Tools SWIG deprecation notices (`SwigPyPacked`, `SwigPyObject`,
`swigvarlink`). There were no failures, errors, or skips.

The suite is split into:

| Area | Files | What it covers |
|---|---|---|
| Unit | `test_generation.py`, `test_geography_travel.py`, `test_config.py`, `test_validation.py`, `test_anomalies.py`, `test_reporting.py`, `test_dashboard_data.py`, `test_sample_artifacts.py`, `test_dwell.py` | Determinism, identifiers, travel model, config merging and driver/vehicle validation, every validation rule family, all six injectors/detectors/evaluation, KPI formulas, dashboard shaping, checked-in sample integrity, and planned-versus-actual dwell semantics. |
| Integration | `test_pipeline_persistence.py`, `test_routing_simulation.py`, `test_cli.py`, `test_dashboard_app.py`, `test_benchmarks.py` | Tiny end-to-end pipeline, OR-Tools constraints, simulation events, high-variance shift-overrun regression coverage, warehouse idempotency/rollback and additive schema migration, CLI exit codes and loopback dashboard launch, Streamlit `AppTest`, measured benchmark writer and corrected timing metrics. |

The integration tests use small configurations (8-12 sites) and run the real OR-Tools solver; the pipeline stages are
not mocked. The dwell regression file covers timestamp-derived actual dwell, planned/actual coexistence, validation
consistency, and the sampler. The volume regression tests cover warning severity, non-failure of clean validation,
and the two documented starter examples.

## Starter run

Run from the fresh clone:

```powershell
.\.venv\Scripts\python.exe -m fleetmind run --config starter `
  --output outputs/verify-artifacts `
  --database outputs/verify.duckdb
```

Result: `RUN-C16424724715`, status `SUCCEEDED`, mode `FULL`, seed `20261022`.

| KPI | Value |
|---|---:|
| Customer sites | 40 |
| Requested deliveries | 120 |
| Vehicles / drivers / shifts | 12 / 12 / 64 |
| Service days | 7 |
| Routes | 25 |
| Planned stops | 118 |
| Assigned deliveries | 120 |
| Delivered | 116 |
| Failed | 2 |
| Unserved | 2 |
| Service rate | 96.67% |
| On-time rate | 100.00% |
| Total distance | 781.80 km |
| Capacity utilization | 58.10% |
| Fleet utilization | 91.67% |
| Actual service hours | 46.829444 |
| Actual wait hours | 67.443889 |
| Clean validation warnings | 2 |
| Clean validation errors | 0 |
| Dirty validation issues | 44 (42 errors + 2 volume warnings) |
| Dirty quarantine records | 24 |
| Ground-truth anomalies | 12 |
| Detections | 12 |
| Mean anomaly F1 | 1.000 |
| Reconciliation checks | 17 / 17 passed |

Stage timings from `summary.json` for the second run:

| Stage | Seconds |
|---|---:|
| generate | 0.2994 |
| plan | 35.0582 |
| simulate | 0.0568 |
| validate_clean | 0.3577 |
| reporting | 0.0406 |
| inject_anomalies | 0.1429 |
| detect_anomalies | 0.0291 |
| validate_dirty | 0.3542 |
| persist | 0.9920 |

The starter run used 2 instances per anomaly type. All six classes were detected with zero false positives and zero
false negatives. Dirty validation independently flagged the injected classes plus expected cascading foreign-key and
arithmetic issues; those cascades are excluded from the exact-match detection metrics.

The two clean warnings are advisory `VOLUME_CAPACITY_EXCEEDED` findings, not hard-constraint failures:

| Route | Route volume | Vehicle volume limit | Severity |
|---|---:|---:|---|
| `RTE-2026-000008` | 45.051 m3 | 45.0 m3 | `WARNING` |
| `RTE-2026-000017` | 57.129 m3 | 45.0 m3 | `WARNING` |

Both messages state that volume is advisory and not enforced by the router. Clean validation still passes with
`clean_errors = 0` and `clean_passed = true`.

## Reproducibility and idempotency

The starter command was executed twice against the same output directory and warehouse. The second run replaced the
first run's snapshot rather than appending to it.

Warehouse contents after the rerun:

| Table | Rows |
|---|---:|
| `run_metadata` | 1 |
| `routes` | 25 |
| `route_stops` | 118 |
| `deliveries` | 120 |
| `ground_truth_anomalies` | 12 |
| `anomaly_detections` | 12 |
| `quarantine_records` | 24 |
| `reconciliation_checks` | 17 |

Checksum comparison:

- All 21 clean table checksums in `checksums.json` were identical across the two runs.
- All 21 checksums were also identical to the checked-in `data/sample/starter-run/checksums.json`.
- All 21 checksums were recomputed successfully from the corresponding Parquet artifacts.
- The KPI object in `summary.json` was identical across the two runs.

### Warehouse schema and dwell reconciliation

The warehouse created by the fresh-clone run contains 35 tables and records schema version `1.1.0`. Both `routes` and
`route_stops` expose the planned and actual dwell columns:

| Check | Result |
|---|---|
| `routes` rows with non-null `actual_wait_minutes` / `actual_service_minutes` | 25 / 25 |
| `route_stops` rows with non-null `actual_wait_minutes` / `actual_service_minutes` | 118 / 118 |
| Maximum stop wait difference from stored timestamps | 3.3e-07 minutes |
| Maximum stop service difference from stored timestamps | 3.3e-07 minutes |
| Route total minus stop sum, actual wait | -4.5e-13 minutes |
| Route total minus stop sum, actual service | -2.0e-06 minutes |
| Stops where planned service differs from actual service | 118 of 118 |

`wait_minutes` and `service_minutes` remain the planner's values. The actual columns are derived from
`actual_arrival`, `actual_service_start`, and `actual_departure`, so they reconcile with execution times rather than
reusing the plan. The small residuals come from rounding durations to six decimals; the reconciliation tolerance for
aggregate actual-dwell totals is 0.01 minutes.

### Checked-in sample verification

`data/sample/starter-run/` is a complete copy of one real starter run (`RUN-C16424724715`), not a hand-assembled
subset. It has 91 files and 1,342,403 bytes:

- `master/` holds the pre-execution snapshot, including all 13 master tables. `deliveries.status` is `PENDING` and
  `orders.status` is `CONFIRMED`.
- `plan/` holds the five planning tables, including `solver_runs`.
- `simulation/` holds the post-execution versions of routes, stops, legs, deliveries, orders, outcomes, and event
  streams. Assignments remain in `plan/` because execution does not rewrite them. Routes and stops carry both planned
  dwell and actual dwell. `checksums.json` describes the final versions for tables that execution supersedes.
- `anomalies/`, `validation/`, `quarantine_dirty/`, and `reports/` hold the anomaly ground truth (12), detections
  (12), clean issues (2), dirty issues (44), quarantine evidence (24 records), and 17 reconciliation checks.
- `manifest.json` lists every table in every group with row count, columns, logical checksum, and paths relative to
  the sample directory.
- `config.yaml`, `summary.json`, and `checksums.json` are included. The DuckDB warehouse is not; `summary.json` marks
  `warehouse.included` as `false` and keeps its table counts for provenance.

The exact verification is automated in `tests/unit/test_sample_artifacts.py`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_sample_artifacts.py -q
```

The test checks that every manifest path exists and is relative, reads each Parquet/CSV file, recomputes every
manifest checksum, resolves every entry in `checksums.json` to a Parquet artifact, asserts that the master
`deliveries`/`orders` snapshots are pre-execution while the simulation snapshots are final, and confirms the
dirty-quarantine evidence totals match `summary.json`.

Regenerate the sample from a fresh real run with:

```powershell
.\.venv\Scripts\python.exe scripts/build_sample.py
```

Parquet is required for exact checksum reproduction. A CSV reader without an explicit schema can infer different types
(for example a numeric-looking postal code), so CSV files are reviewer convenience only. This limitation is documented
in `README.md`, `docs/architecture.md`, and `docs/benchmark_methodology.md`.

### Checksum defect found and fixed

The first cross-run comparison showed `routes` and `solver_runs` checksums changing even though the data was the same.
The only differing column was `solve_seconds`, which is a measured wall-clock duration. Logical checksums now exclude
that column. A unit test asserts that changing `solve_seconds` does not change the checksum and that changing a real
data field does.

## Benchmark evidence

Command:

```powershell
.\.venv\Scripts\python.exe -m fleetmind benchmark --configs baseline,larger --output outputs/benchmarks
```

Report: `outputs/benchmarks/benchmark_report.json` and `outputs/benchmarks/benchmark_report.md`, measured
`2026-11-01T17:59:43`. The pre-remediation report from `2026-10-31T17:21:29`, the intermediate PyArrow 21 report from
`2026-11-01T00:33:17`, and the pre-dwell report from `2026-11-01T17:48:22` are preserved under
`outputs/benchmarks/history/`.

| Config | Run ID | Days | Sites | Orders | Vehicles | Total (s) | Planning (s) | Solver (s) | Solver s/day | Routes | Assigned | Delivered | Unserved | Service | Peak RSS (MB) | Mean F1 | Clean warnings |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | `RUN-A89FE8126581` | 30 | 500 | 2,000 | 100 | 513.40 | 311.35 | 303.32 | 10.111 | 296 | 2,000 | 1,991 | 0 | 99.55% | 454.98 | 1.000 | 43 |
| larger | `RUN-2155D9A8756A` | 30 | 750 | 4,000 | 150 | 875.52 | 479.88 | 457.52 | 15.251 | 522 | 4,000 | 3,984 | 0 | 99.60% | 856.05 | 1.000 | 116 |

Additional measured details:

| Config | Generate (s) | Simulate (s) | Reporting (s) | Validate clean (s) | Inject (s) | Detect (s) | Validate dirty (s) | Persist (s) | Finalization (s) | Deliveries/s | Python peak (MB) | Travel-matrix rows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 16.018 | 5.6828 | 0.209 | 27.8318 | 6.0078 | 0.9935 | 27.8897 | 28.8693 | 67.0264 | 3.896 | 170.95 | 251,001 |
| larger | 35.7934 | 11.3776 | 0.2664 | 59.9304 | 12.201 | 1.1894 | 58.9591 | 52.5906 | 118.85 | 4.569 | 371.03 | 564,001 |

Both configurations:

- passed clean validation with zero hard errors; the 43 and 116 clean issues are advisory `VOLUME_CAPACITY_EXCEEDED`
  warnings;
- assigned every requested delivery (0 unserved);
- passed all 17 reconciliation checks;
- detected every injected anomaly with precision, recall, and F1 of 1.000;
- ran one measurement per configuration with `repeats=1` and no warm-up;
- reproduced all 21 clean table checksums from the persisted Parquet artifacts when recomputed from the report.

`planning_seconds` is the full planning stage. `solver_seconds` is the sum of the per-day `solver_runs.solve_seconds`
values persisted by the planner, and `solver_seconds_per_day` divides that by the 30-day horizon. The totals include
Parquet/CSV writes, DuckDB persistence, both validation passes, injection, detection, and reporting. They are not
solver-only timings. `benchmark_seconds` (516.0326 s baseline, 879.5182 s larger) includes harness overhead outside the
measured stages.

The benchmark report was measured before a dtype-only change to the duplicated-stop concatenation in
`anomalies/injection.py`. That change removes a pandas `FutureWarning` and does not alter injected values, counts, or
validation results; the full test suite and starter run were re-executed after the change.

### Benchmark reproducibility limits

The benchmark is a measured run, not a bit-reproducible oracle. OR-Tools search is time-limited, so route IDs, stop
assignments, route-dependent checksums, and even route counts can change between runs. The pre-dwell report recorded
295 baseline and 521 larger routes; this rerun recorded 296 and 522 with the same configurations and environment.
Aggregate quality was unchanged (same delivered counts, service rates, and anomaly F1). The checked-in report records
one measured run and must not be cited as a promise of bit-identical route output.

## CLI and dashboard checks

```powershell
.\.venv\Scripts\python.exe -m fleetmind info --config starter
.\.venv\Scripts\python.exe -m fleetmind runs --database <run-warehouse>
.\.venv\Scripts\python.exe -m fleetmind schema --show
```

Observed:

- `info` resolved `RUN-C16424724715` with configuration hash
  `c16424724715e796adbacc00f7480d36e6faa59964766d0bf9835d5ce19a74aa`.
- `runs` listed the persisted run with status `SUCCEEDED` and mode `FULL`.
- `schema --show` printed the generated DDL header with `schema_version: 1.1.0`.

The dashboard launch path was verified end to end. The launcher printed:

```text
Starting dashboard: ... -m streamlit run ...\src\fleetmind\dashboard\app.py
  --server.address 127.0.0.1 --server.port 8599
```

The server log recorded `Uvicorn server started on 127.0.0.1:8599`, and netstat showed only a loopback listener:

```text
TCP    127.0.0.1:8599    0.0.0.0:0    LISTENING    10668
```

`http://127.0.0.1:8599/_stcore/health` returned `ok`, and `http://127.0.0.1:8599/` returned HTTP 200. A Streamlit
`AppTest` run against the persisted baseline benchmark warehouse completed with no exceptions, rendered the
`FleetMind Control Tower` title, five tabs, 25 metrics, and 14 Plotly charts. The repository
`.streamlit/config.toml` sets the same address for the `streamlit run` path, and regression tests assert both the CLI
arguments and the config value.

The dashboard is a read-only, unauthenticated local tool. It is not hardened for a shared or public interface, and
none of the shipped launch paths bind to one.

## What was verified

- Deterministic generation and stable logical checksums for identical starter configurations.
- Fresh-clone installation from the lockfile, editable project install, and a clean `pip check`.
- Planned dwell preserved in `wait_minutes`/`service_minutes`; actual dwell derived from stored execution timestamps
  and reconciled at stop and route level.
- Advisory volume handling: warnings are recorded, the documented examples are reported, and clean validation still
  passes with zero hard errors.
- Hard capacity, time-window, shift, eligibility, and service constraints at the planner and validator levels.
- Exactly one assignment and one outcome per requested delivery.
- All six anomaly classes injected, detected independently, and evaluated with exact entity matching.
- Clean validation failure semantics, quarantine generation, and 17 reconciliation invariants.
- Transactional warehouse snapshots, rerun idempotency, rollback, run isolation, clean/dirty coexistence, and additive
  schema migration for older warehouses.
- CLI commands, exit codes, bare config resolution, schema export, and benchmark report rendering.
- Dashboard loopback binding, HTTP health, warehouse loading, read-only behavior, and empty-state handling.
- Measured baseline and larger performance at the required scale.

## Known limitations

- Benchmarks are single measured runs on one Windows machine, not a statistical distribution. Re-run on the target
  hardware before making capacity decisions.
- Travel is a static Haversine model with road and scenario multipliers. It is not live traffic or road-network
  routing.
- Planning solves each service day independently. Multi-depot operation, multi-day route continuity, and overnight
  routes are out of scope.
- Volume is stored and reported as advisory only; the router does not enforce it.
- Anomaly detection is rule-based and limited to the six specified classes; it is not a general anomaly classifier.
- The dashboard is a read-only consumer of this schema, not a general BI tool, and it has no authentication.
- The checked-in sample contains Parquet and CSV artifacts but not the DuckDB warehouse, so it cannot be opened
  directly by the dashboard.
- Credential and effective-date expiry fields are stored and internally ordered, but planning does not filter
  candidates by expiry. The generator keeps credentials valid across the provided 7- and 30-day horizons;
  expiry-aware eligibility is out of scope.
- All names, addresses, contacts, coordinates, and identifiers are synthetic. The sample and benchmark data do not
  describe real people, companies, or locations.
- The preserved Git history contains earlier local-path and assessment wording, and commit metadata contains the
  author email. History was not rewritten to remove it.
- The remediation commits in this record are local and have not been pushed to GitHub; review is pending.
