# Benchmark methodology

FleetMind's benchmark suite measures the actual pipeline, not a synthetic estimate. The checked-in report at
`outputs/benchmarks/benchmark_report.json` / `.md` is the source of truth for the numbers quoted in the README.
The pre-remediation report from 2026-10-31, the intermediate PyArrow 21 report from 2026-11-01, and the pre-dwell
report from 2026-11-01 are preserved under `outputs/benchmarks/history/` so earlier measurements remain auditable.

## Command

```powershell
python -m fleetmind benchmark --configs baseline,larger --output outputs/benchmarks
```

Each named configuration is loaded through the same `load_config` path as a normal run, then executed once with
`build_pipeline_run` and persisted with `persist_pipeline_run`.

## What is measured

| Measurement | Source | Includes |
|---|---|---|
| `generate` | Pipeline stage timer | Master data, travel matrix, scenarios. |
| `plan` | Pipeline stage timer | Deterministic pre-checks, per-day model construction, OR-Tools search, result assembly. |
| `simulate` | Pipeline stage timer | Every planned route, outcome, and event ledger. |
| `reporting` | Pipeline stage timer | KPI reporting and reconciliation checks. |
| `validate_clean` | Pipeline stage timer | Full master + plan + simulation validation and quarantine. |
| `inject_anomalies` | Pipeline stage timer | Six injectors and ground-truth construction. |
| `detect_anomalies` | Pipeline stage timer | Six detectors and evaluation. |
| `validate_dirty` | Pipeline stage timer | Full validation of the corrupted snapshot. |
| `persist` | Pipeline stage timer | Parquet + CSV writes, table checksums, DuckDB snapshot, warehouse counts. |
| `total` | Wall clock around build + persist | End-to-end benchmarked command work. |
| `measured_sum` | Sum of the nine stage timers | Timed stage work. |
| `planning_seconds` | `run.stage_durations["plan"]` | Full planning stage, identical to the `plan` stage above. |
| `solver_seconds` | Sum of persisted `solver_runs.solve_seconds` | Per-day solver timers written by the planner, not the full planning stage. |
| `solver_seconds_per_day` | Solver seconds ÷ service days | Normalizes solver work across horizons. |
| `finalization_seconds` | Persist wall time − `persist` stage timer | Run metadata, warehouse metadata upsert, repeated checksum computation, summary writing, and manifest construction. |
| `unattributed_seconds` | `total` − `measured_sum` − `finalization_seconds` | Remaining harness overhead, including report writing. |
| `deliveries_per_second` | Deliveries ÷ `total` | End-to-end throughput. |
| `diagnostics_rows_per_second` | Sum of selected table row counts ÷ `total` | Artifact/event volume throughput. |
| `peak_rss_mb` | `psutil` background sampler at 50 ms | Resident memory high-water mark for the process. |
| `python_tracemalloc_peak_mb` | `tracemalloc` | Python allocation high-water mark. |

`reporting` is a real pipeline stage that runs between clean validation and anomaly injection. The timing breakdown
deliberately keeps finalization and harness overhead visible instead of folding them into an unrelated stage.

## Measurement protocol

1. One measured run per configuration. No warm-up run and no cache priming.
2. Configurations run sequentially in the same Python process, baseline first, larger second. The larger run benefits
   from an already-warm process and OS/library caches, but not from cached pipeline results.
3. The solver time limit is read from YAML: 10 s/day for baseline and 15 s/day for larger. The limit bounds the
   OR-Tools search, not the entire persisted `solve_seconds` timer.
4. `peak_rss_mb` is sampled by a daemon thread while the pipeline runs; the final sample is taken at stop.
5. `tracemalloc` starts immediately before the pipeline build and stops after persistence.
6. A failed configuration is recorded as `FAILED` with the exception type and message. The suite continues and still
   writes the report.
7. Both Parquet and CSV are written, so `persist` includes both artifact formats and the DuckDB snapshot.

## Environment

Measured 2026-11-01:

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
| Pipeline version | 1.0.0 |

## Results

### Stage timings

| Config | Days | Sites | Orders | Vehicles | Generate (s) | Plan (s) | Simulate (s) | Reporting (s) | Validate clean (s) | Inject (s) | Detect (s) | Validate dirty (s) | Persist (s) | Total (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 30 | 500 | 2,000 | 100 | 16.02 | 311.35 | 5.68 | 0.21 | 27.83 | 6.01 | 0.99 | 27.89 | 28.87 | 513.40 |
| larger | 30 | 750 | 4,000 | 150 | 35.79 | 479.88 | 11.38 | 0.27 | 59.93 | 12.20 | 1.19 | 58.96 | 52.59 | 875.52 |

### Timing breakdown

| Config | Planning (s) | Solver (s) | Solver s/day | Total (s) | Measured stages (s) | Finalization (s) | Unattributed (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 311.35 | 303.32 | 10.111 | 513.40 | 424.85 | 67.03 | 21.53 |
| larger | 479.88 | 457.52 | 15.251 | 875.52 | 712.19 | 118.85 | 44.47 |

### Memory and throughput

| Config | Peak RSS (MB) | Python peak (MB) | Start RSS (MB) | Deliveries/s | Diagnostic rows/s |
|---|---:|---:|---:|---:|---:|
| baseline | 454.98 | 170.95 | 114.50 | 3.896 | 543.198 |
| larger | 856.05 | 371.03 | 213.29 | 4.569 | 705.891 |

### Quality

| Config | Routes | Stops | Assigned | Delivered | Failed | Unserved | Service rate | On-time | Clean warnings | Mean F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 296 | 2,000 | 2,000 | 1,991 | 9 | 0 | 99.55% | 100.00% | 43 | 1.000 |
| larger | 522 | 4,000 | 4,000 | 3,984 | 16 | 0 | 99.60% | 100.00% | 116 | 1.000 |

Both runs succeeded, passed all 17 reconciliation checks, and detected every injected anomaly with zero false
positives and zero false negatives. Clean validation had zero hard errors in both runs; the 43 and 116 clean issues are
advisory `VOLUME_CAPACITY_EXCEEDED` warnings. The service-rate difference from 100% is due to simulated
customer-unavailable or late failures, not unplanned demand: both runs assigned every requested delivery.

## Interpretation

- Planning dominates wall time: 311.35 s of 513.40 s (60.6%) for baseline and 479.88 s of 875.52 s (54.8%) for larger.
  This is expected because both use a fixed per-day solver budget and more vehicles/sites increase model size.
- Persisted solver seconds per day slightly exceed the YAML search limit (10.111 vs. 10 s, 15.251 vs. 15 s) because the
  per-day timer also covers model construction, extraction, and OR-Tools overhead around the search itself.
- Persistence plus finalization is the second-largest cost: 28.87 s + 67.03 s for baseline and 52.59 s + 118.85 s for
  larger. It includes Parquet and CSV writes, DuckDB snapshot insertion, checksum computation, run metadata, summary,
  and manifest generation.
- Peak RSS stays below 1 GB at 4,000 deliveries and 564,001 travel-matrix rows, but the larger run uses roughly twice
  the baseline memory (455.0 MB to 856.1 MB). Treat these as measured points, not a guarantee for larger site counts.
- Single measured runs are sensitive to machine load, antivirus scanning, and solver search behavior. Re-run the
  command on the target machine before making performance decisions.

## Reproducibility

The benchmark report includes clean table checksums, artifact paths, and library versions. Generation is deterministic
for a fixed configuration and seed, so generated master tables should reproduce. Planning and simulation tables depend
on OR-Tools' time-limited search; a machine under different load can end a solve at a different point and produce a
different feasible route set. The logical checksum excludes `solve_seconds`, so solver-timing jitter alone does not
change a checksum, but a genuinely different route solution does. Parquet artifacts preserve column types and can be
used to recompute every checksum; reading the CSV files without the schema may infer different types. The suite
intentionally does not average multiple runs or discard outliers; `repeats` is recorded as `1`.

### What is reproducible, and what is not

| Layer | Reproducible? | Notes |
|---|---|---|
| Starter generation and checked-in sample | Yes, on the verified environment | The starter config's 21 clean table checksums reproduce from the checked-in Parquet artifacts; the sample integrity test recomputes them. |
| Benchmark aggregate KPIs | Directionally stable | Delivery counts, service rate, on-time rate, unserved count, and anomaly F1 are the appropriate comparison points. They are measured outcomes, not fixed constants. |
| Benchmark route-level output | No, not bit-for-bit | Route IDs, stop assignments, route-dependent checksums, and some route counts can change between runs because OR-Tools search is time-limited. The 2026-11-01 pre-dwell report recorded 295 baseline and 521 larger routes; the post-remediation rerun recorded 296 and 522 with the same configurations and environment. |
| Wall-clock timings | No | CPU load, antivirus scanning, and solver termination timing change measured seconds. |

Do not claim that benchmark route outputs are bit-reproducible. Treat the checked-in report as one measured sample on
the recorded hardware, and compare reruns by aggregate quality and throughput rather than by identical route tables.
