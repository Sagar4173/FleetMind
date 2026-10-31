# Benchmark methodology

FleetMind's benchmark suite measures the actual pipeline, not a synthetic estimate. The checked-in report at
`outputs/benchmarks/benchmark_report.json` / `.md` is the source of truth for the numbers quoted in the README.

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
| `plan` | Pipeline stage timer | All per-day OR-Tools solves and plan table construction. |
| `simulate` | Pipeline stage timer | Every planned route, outcome, and event ledger. |
| `validate_clean` | Pipeline stage timer | Full master + plan + simulation validation and quarantine. |
| `inject_anomalies` | Pipeline stage timer | Six injectors and ground-truth construction. |
| `detect_anomalies` | Pipeline stage timer | Six detectors and evaluation. |
| `validate_dirty` | Pipeline stage timer | Full validation of the corrupted snapshot. |
| `persist` | Pipeline stage timer | Parquet + CSV writes, DuckDB snapshot, checksums. |
| `orchestration` | Wall clock around build + persist | End-to-end command work, including Python overhead. |
| `measured_sum` | Sum of the eight stage timers | Stage work; excludes reporting and orchestration gaps. |
| `solver_seconds` | `plan.solve_seconds` | Sum of OR-Tools wall time across days. |
| `solver_seconds_per_day` | Solver seconds ÷ service days | Normalizes planning across horizons. |
| `deliveries_per_second` | Deliveries ÷ orchestration seconds | End-to-end throughput. |
| `diagnostics_rows_per_second` | Sum of selected table row counts ÷ orchestration seconds | Artifact/event volume throughput. |
| `peak_rss_mb` | `psutil` background sampler at 50 ms | Resident memory high-water mark for the process. |
| `python_tracemalloc_peak_mb` | `tracemalloc` | Python allocation high-water mark. |

Reporting and reconciliation run between the timed clean validation and anomaly stages. They are part of the
orchestration wall time but are not a separate stage timer. The gap between `total` and `measured_sum` also includes
post-persistence metadata, repeated checksum generation, summary writing, and manifest construction. Those costs are
real and intentionally left visible instead of being folded into an unrelated stage.

## Measurement protocol

1. One cold run per configuration. No warm-up run and no cache priming.
2. Configurations run sequentially in the same process, baseline first, larger second. The larger run benefits from
   an already-warm Python process but not from cached pipeline results.
3. The solver time limit is read from YAML: 10 s/day for baseline and 15 s/day for larger.
4. `peak_rss_mb` is sampled by a daemon thread while the pipeline runs; the final sample is taken at stop.
5. `tracemalloc` starts immediately before the pipeline build and stops after persistence.
6. A failed configuration is recorded as `FAILED` with the exception type and message. The suite continues and still
   writes the report.
7. Both Parquet and CSV are written, so `persist` includes both artifact formats and the DuckDB snapshot.

## Environment

Measured 2026-10-31:

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
| PyArrow | 21.0.0 |
| Pipeline version | 1.0.0 |

## Results

### Stage timings

| Config | Days | Sites | Orders | Vehicles | Generate (s) | Plan (s) | Simulate (s) | Validate clean (s) | Inject (s) | Detect (s) | Validate dirty (s) | Persist (s) | Total (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 30 | 500 | 2,000 | 100 | 17.16 | 311.50 | 5.18 | 28.91 | 6.14 | 0.68 | 28.57 | 30.82 | 518.34 |
| larger | 30 | 750 | 4,000 | 150 | 37.77 | 481.75 | 9.85 | 74.46 | 13.17 | 1.80 | 71.27 | 62.19 | 953.85 |

### Memory and solver

| Config | Peak RSS (MB) | Python peak (MB) | Start RSS (MB) | Solver total (s) | Solver s/day | Deliveries/s |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 445.89 | 159.43 | 114.46 | 311.35 | 10.378 | 3.858 |
| larger | 859.69 | 345.19 | 147.31 | 481.27 | 16.042 | 4.194 |

### Quality

| Config | Routes | Stops | Assigned | Delivered | Failed | Unserved | Service rate | On-time | Clean issues | Mean F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 296 | 2,000 | 2,000 | 1,991 | 9 | 0 | 99.55% | 100.00% | 0 | 1.000 |
| larger | 521 | 4,000 | 4,000 | 3,984 | 16 | 0 | 99.60% | 100.00% | 0 | 1.000 |

Both runs succeeded, passed all 15 reconciliation checks, and detected every injected anomaly with zero false
positives and zero false negatives. The service-rate difference from 100% is due to simulated customer-unavailable
or late failures, not unplanned demand: both runs assigned every requested delivery.

## Interpretation

- Planning dominates wall time. The baseline spends about 60% of total time in `plan`; the larger run spends about
  50%. This is expected because both use a fixed per-day solver budget and more vehicles/sites increase model size.
- Solver seconds per day exceed the YAML time limit (10.38 vs. 10 s, 16.04 vs. 15 s) because the measured value
  includes model construction, extraction, and OR-Tools overhead in addition to the search limit.
- Persistence is the second-largest stage because both Parquet and CSV are written and a DuckDB snapshot is inserted.
- Peak RSS is below 1 GB even at 4,000 deliveries and 564,001 travel-matrix rows, but the larger run uses roughly
  twice the baseline memory. Treat these as measured points, not a guarantee for larger site counts.
- Single cold runs are sensitive to machine load, antivirus scanning, and solver search behavior. Re-run the command
  on the target machine before making performance decisions.

## Reproducibility

The benchmark report includes clean table checksums, artifact paths, and library versions. Given the same commit,
configuration, seed, dependency versions, and platform, the logical checksums should match. Timings and memory will
vary. `solve_seconds` is a measured wall-clock value and is excluded from the checksum, so a few milliseconds of
solver jitter do not change the checksum of `routes` or `solver_runs`. Parquet artifacts preserve column types and can
be used to recompute every checksum; reading the CSV files without the schema may infer different types. The suite
intentionally does not average multiple runs or discard outliers; `repeats` is recorded as `1`.
