# Benchmark methodology

FleetMind's benchmark suite measures the actual pipeline, not a synthetic estimate. The checked-in report at
`outputs/benchmarks/benchmark_report.json` / `.md` is the source of truth for the numbers quoted in the README.
The pre-remediation report from 2026-10-31 and the intermediate PyArrow 21 report from 2026-11-01 are preserved under
`outputs/benchmarks/history/` so the earlier measurements remain auditable.

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
| baseline | 30 | 500 | 2,000 | 100 | 15.68 | 310.63 | 4.81 | 0.22 | 28.56 | 5.75 | 0.69 | 31.27 | 26.74 | 503.27 |
| larger | 30 | 750 | 4,000 | 150 | 34.18 | 481.11 | 9.22 | 0.27 | 57.60 | 12.80 | 1.27 | 61.35 | 59.05 | 886.07 |

### Timing breakdown

| Config | Planning (s) | Solver (s) | Solver s/day | Total (s) | Measured stages (s) | Finalization (s) | Unattributed (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 310.63 | 302.79 | 10.093 | 503.27 | 424.35 | 57.21 | 21.72 |
| larger | 481.11 | 457.65 | 15.255 | 886.07 | 716.84 | 124.01 | 45.21 |

### Memory and throughput

| Config | Peak RSS (MB) | Python peak (MB) | Start RSS (MB) | Deliveries/s | Diagnostic rows/s |
|---|---:|---:|---:|---:|---:|
| baseline | 449.42 | 170.96 | 114.53 | 3.974 | 554.151 |
| larger | 865.45 | 371.01 | 210.44 | 4.514 | 697.452 |

### Quality

| Config | Routes | Stops | Assigned | Delivered | Failed | Unserved | Service rate | On-time | Clean issues | Mean F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 295 | 2,000 | 2,000 | 1,991 | 9 | 0 | 99.55% | 100.00% | 0 | 1.000 |
| larger | 521 | 4,000 | 4,000 | 3,984 | 16 | 0 | 99.60% | 100.00% | 0 | 1.000 |

Both runs succeeded, passed all 15 reconciliation checks, and detected every injected anomaly with zero false
positives and zero false negatives. The service-rate difference from 100% is due to simulated customer-unavailable
or late failures, not unplanned demand: both runs assigned every requested delivery.

## Interpretation

- Planning dominates wall time: 310.63 s of 503.27 s (61.7%) for baseline and 481.11 s of 886.07 s (54.3%) for larger.
  This is expected because both use a fixed per-day solver budget and more vehicles/sites increase model size.
- Persisted solver seconds per day slightly exceed the YAML search limit (10.093 vs. 10 s, 15.255 vs. 15 s) because the
  per-day timer also covers model construction, extraction, and OR-Tools overhead around the search itself.
- Persistence plus finalization is the second-largest cost: 26.74 s + 57.21 s for baseline and 59.05 s + 124.01 s for
  larger. It includes Parquet and CSV writes, DuckDB snapshot insertion, checksum computation, run metadata, summary,
  and manifest generation.
- Peak RSS stays below 1 GB at 4,000 deliveries and 564,001 travel-matrix rows, but the larger run uses roughly twice
  the baseline memory (449.4 MB to 865.5 MB). Treat these as measured points, not a guarantee for larger site counts.
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
