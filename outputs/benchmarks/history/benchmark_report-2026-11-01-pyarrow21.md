# FleetMind benchmark report

Measured: 2026-11-01T00:33:17.391094

## Environment

- **python**: 3.13.9
- **platform**: Windows-11-10.0.26300-SP0
- **processor**: AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD
- **cpu_count**: 12
- **memory_gb**: 7.28
- **pandas**: 2.3.3
- **numpy**: 2.3.5
- **duckdb**: 2.0.0
- **pyarrow**: 21.0.0
- **ortools**: 9.15.6755
- **pipeline_version**: 1.0.0

## Results

| Config | Days | Sites | Orders | Vehicles | Generate (s) | Plan (s) | Simulate (s) | Validate (s) | Persist (s) | Total (s) | Peak RSS (MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 30 | 500 | 2000 | 100 | 15.70 | 309.69 | 4.50 | 25.73 | 25.17 | 487.94 | 451.9 |
| larger | 30 | 750 | 4000 | 150 | 31.95 | 478.27 | 8.87 | 56.01 | 55.75 | 863.68 | 889.3 |

## Timing

| Config | Planning (s) | Solver (s) | Solver s/day | Total (s) | Measured stages (s) | Finalization (s) | Unattributed (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 309.69 | 302.58 | 10.086 | 487.94 | 412.61 | 55.17 | 20.17 |
| larger | 478.27 | 456.89 | 15.230 | 863.68 | 700.08 | 117.74 | 45.86 |

## Quality and throughput

| Config | Routes | Delivered | Service | On-time | Solver s/day | Deliveries/s | Mean anomaly F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 294 | 1991 | 99.6% | 100.0% | 10.086 | 4.10 | 1.000 |
| larger | 521 | 3984 | 99.6% | 100.0% | 15.230 | 4.63 | 1.000 |

## Method

- One measured run per configuration on the same machine; no warm-up run. Configurations run sequentially in one Python process, so later runs may benefit from warm OS and library caches.
- `planning_seconds` is the full planning stage: deterministic pre-checks, model construction, solver search, and result assembly across all service days.
- `solver_seconds` is the sum of the per-day `solver_runs.solve_seconds` values actually persisted by the planner, not the full planning stage.
- `finalization_seconds` covers checksum regeneration, metadata/warehouse upserts, summary writing, and manifest construction after the timed persist stage.
- `unattributed_seconds` is total wall time minus measured stages and finalization; it covers benchmark harness work such as report writing.
- Peak RSS is sampled every 50 ms by a background thread; Python allocation peak uses tracemalloc.
- Solver wall time is subject to OR-Tools search limits configured per day in YAML.
