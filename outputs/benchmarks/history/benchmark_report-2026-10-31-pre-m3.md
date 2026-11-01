# FleetMind benchmark report

Measured: 2026-10-31T17:21:29.764644

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
| baseline | 30 | 500 | 2000 | 100 | 17.16 | 311.50 | 5.18 | 28.91 | 30.82 | 518.34 | 445.9 |
| larger | 30 | 750 | 4000 | 150 | 37.77 | 481.75 | 9.85 | 74.46 | 62.19 | 953.85 | 859.7 |

## Quality and throughput

| Config | Routes | Delivered | Service | On-time | Solver s/day | Deliveries/s | Mean anomaly F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 296 | 1991 | 99.6% | 100.0% | 10.378 | 3.86 | 1.000 |
| larger | 521 | 3984 | 99.6% | 100.0% | 16.042 | 4.19 | 1.000 |

## Method

- One cold measurement per configuration on the same machine; no warm-up run.
- Stage durations are emitted by the pipeline itself and include full validation, anomaly injection/detection, and both parquet and CSV artifact writes.
- Peak RSS is sampled every 50 ms by a background thread; Python allocation peak uses tracemalloc.
- Solver wall time is subject to OR-Tools search limits configured per day in YAML.
