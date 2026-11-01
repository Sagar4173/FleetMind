# FleetMind benchmark report

Measured: 2026-11-01T01:18:22.646424

## Environment

- **python**: 3.13.9
- **platform**: Windows-11-10.0.26300-SP0
- **processor**: AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD
- **cpu_count**: 12
- **memory_gb**: 7.28
- **pandas**: 2.3.3
- **numpy**: 2.3.5
- **duckdb**: 2.0.0
- **pyarrow**: 23.0.1
- **ortools**: 9.15.6755
- **pipeline_version**: 1.0.0

## Results

| Config | Days | Sites | Orders | Vehicles | Generate (s) | Plan (s) | Simulate (s) | Validate (s) | Persist (s) | Total (s) | Peak RSS (MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 30 | 500 | 2000 | 100 | 15.68 | 310.63 | 4.81 | 28.56 | 26.74 | 503.27 | 449.4 |
| larger | 30 | 750 | 4000 | 150 | 34.18 | 481.11 | 9.22 | 57.60 | 59.05 | 886.07 | 865.5 |

## Timing

| Config | Planning (s) | Solver (s) | Solver s/day | Total (s) | Measured stages (s) | Finalization (s) | Unattributed (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 310.63 | 302.79 | 10.093 | 503.27 | 424.35 | 57.21 | 21.72 |
| larger | 481.11 | 457.65 | 15.255 | 886.07 | 716.84 | 124.01 | 45.21 |

## Quality and throughput

| Config | Routes | Delivered | Service | On-time | Solver s/day | Deliveries/s | Mean anomaly F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 295 | 1991 | 99.6% | 100.0% | 10.093 | 3.97 | 1.000 |
| larger | 521 | 3984 | 99.6% | 100.0% | 15.255 | 4.51 | 1.000 |

## Method

- One measured run per configuration on the same machine; no warm-up run. Configurations run sequentially in one Python process, so later runs may benefit from warm OS and library caches.
- `planning_seconds` is the full planning stage: deterministic pre-checks, model construction, solver search, and result assembly across all service days.
- `solver_seconds` is the sum of the per-day `solver_runs.solve_seconds` values actually persisted by the planner, not the full planning stage.
- `finalization_seconds` covers checksum regeneration, metadata/warehouse upserts, summary writing, and manifest construction after the timed persist stage.
- `unattributed_seconds` is total wall time minus measured stages and finalization; it covers benchmark harness work such as report writing.
- Peak RSS is sampled every 50 ms by a background thread; Python allocation peak uses tracemalloc.
- Solver wall time is subject to OR-Tools search limits configured per day in YAML.
