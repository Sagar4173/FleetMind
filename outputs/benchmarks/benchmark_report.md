# FleetMind benchmark report

Measured: 2026-11-01T17:59:43.010702

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
| baseline | 30 | 500 | 2000 | 100 | 16.02 | 311.35 | 5.68 | 27.83 | 28.87 | 513.40 | 455.0 |
| larger | 30 | 750 | 4000 | 150 | 35.79 | 479.88 | 11.38 | 59.93 | 52.59 | 875.52 | 856.0 |

## Timing

| Config | Planning (s) | Solver (s) | Solver s/day | Total (s) | Measured stages (s) | Finalization (s) | Unattributed (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 311.35 | 303.32 | 10.111 | 513.40 | 424.85 | 67.03 | 21.53 |
| larger | 479.88 | 457.52 | 15.251 | 875.52 | 712.19 | 118.85 | 44.47 |

## Quality and throughput

| Config | Routes | Delivered | Service | On-time | Solver s/day | Deliveries/s | Mean anomaly F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 296 | 1991 | 99.6% | 100.0% | 10.111 | 3.90 | 1.000 |
| larger | 522 | 3984 | 99.6% | 100.0% | 15.251 | 4.57 | 1.000 |

## Method

- One measured run per configuration on the same machine; no warm-up run. Configurations run sequentially in one Python process, so later runs may benefit from warm OS and library caches.
- `planning_seconds` is the full planning stage: deterministic pre-checks, model construction, solver search, and result assembly across all service days.
- `solver_seconds` is the sum of the per-day `solver_runs.solve_seconds` values actually persisted by the planner, not the full planning stage.
- `finalization_seconds` covers checksum regeneration, metadata/warehouse upserts, summary writing, and manifest construction after the timed persist stage.
- `unattributed_seconds` is total wall time minus measured stages and finalization; it covers benchmark harness work such as report writing.
- Peak RSS is sampled every 50 ms by a background thread; Python allocation peak uses tracemalloc.
- Solver wall time is subject to OR-Tools search limits configured per day in YAML.
