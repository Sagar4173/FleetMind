# 08 — Benchmarks, verification, and documentation

## Context

The pipeline, warehouse, CLI, and dashboard were working. The remaining assessment requirement was evidence: measured
performance at the required scale, a clean final test run, reviewer-facing documentation, a checked-in sample run, and
an honest record of limitations.

## Prompt

> Measure the actual pipeline at baseline scale (500 sites, 2,000 deliveries, 100 vehicles, 30 days) and at a larger
> scale. Record stage timings, solver seconds per day, throughput, peak memory, and quality outcomes in JSON and
> Markdown. Do not hard-code performance claims. Fix regressions found during verification. Write the README, the
> architecture/data/validation/anomaly/operations/assumptions/methodology docs, prompt history, and a verification
> record. Check in a small representative run. Run the full test suite and commit milestones without pushing.

## Decisions

### Benchmark harness

- `benchmarks.py` calls the same `build_pipeline_run` and `persist_pipeline_run` used by the CLI. It does not use a
  reduced or mocked pipeline.
- Stage timers cover generation, planning, simulation, clean validation, injection, detection, dirty validation, and
  persistence. Orchestration wall time is measured around build + persist.
- Solver seconds come from `solver_runs.solve_seconds`; solver seconds per day normalizes across horizons.
- Throughput is deliveries divided by orchestration seconds. Diagnostic rows per second uses the selected table row
  counts.
- Peak RSS is sampled by a daemon thread every 50 ms with `psutil`; Python allocation peak is captured with
  `tracemalloc`.
- A failed configuration is recorded with `status=FAILED` and an error message; the suite continues and still writes
  the report.
- One cold run per configuration is recorded. The report states `repeats=1` and does not average or discard outliers.

### Documentation

- `README.md` is the entry point: quickstart, commands, pipeline diagram, determinism, constraint handling, outputs,
  configuration, anomaly summary, measured results, dashboard, tests, and limitations.
- `docs/architecture.md` explains component boundaries, stage flow, per-day planning, determinism, warehouse semantics,
  error handling, and the dashboard boundary.
- `docs/data_dictionary.md` documents every table group, identifier format, key, unit, and important enum.
- `docs/validation_rules.md` catalogs structural, master, plan, execution, quarantine, and reconciliation rules.
- `docs/anomaly_methodology.md` separates injection, detection, evaluation, and expected cascading findings.
- `docs/operations.md` covers environment setup, run modes, exit codes, reruns, warehouse inspection, dashboard,
  benchmarks, retention, and troubleshooting.
- `docs/assumptions.md` records modelling and performance assumptions.
- `docs/benchmark_methodology.md` defines every measurement and the measurement protocol.
- `docs/verification.md` records the final test, run, idempotency, checksum, and benchmark evidence.
- `prompt_history/` records normalized prompts and the decisions they produced.

### Checked-in evidence

- `data/sample/starter-run/` contains a 27-file CSV/JSON starter run: config, summary, checksums, master/plan/
  simulation tables, anomaly evidence, validation issues, and reports. The DuckDB file and Parquet files are not
  committed.
- `outputs/benchmarks/benchmark_report.json` and `.md` are force-included despite the normal `outputs/` ignore rule.
  The ignore rule was narrowed to ignore `outputs/*` but unignore the two report files.

### Verification fix

- Cross-run checksum comparison showed that `routes` and `solver_runs` checksums changed because `solve_seconds` is a
  measured wall-clock value. The checksum now excludes that column, with a unit test proving that solver timing does
  not affect the hash and that genuine data changes do.
- The benchmark report's checksum fields were recomputed from the preserved Parquet artifacts with the corrected
  definition. Measured timings, counts, and quality results were not altered.

## Verification

### Tests

```powershell
python -m pytest -q
```

Result: **86 passed, 3 warnings in 39.55s**. The three warnings are OR-Tools SWIG deprecation notices.

The suite covers generation counts, ID formats, referential integrity, geography/travel, configuration extends and
validation, OR-Tools constraints, unserved reasons, simulation outcomes and events, all six anomaly injectors and
detectors, strict evaluation, quarantine, warehouse idempotency and rollback, reporting reconciliation, CLI commands
and exit codes, dashboard data helpers, Streamlit `AppTest`, and the measured benchmark writer.

### Starter run

```powershell
python -m fleetmind run --config starter --output outputs/verification-artifacts --database outputs/verification.duckdb
```

Result: `RUN-C16424724715`, `SUCCEEDED`, `FULL`.

- 40 sites, 120 deliveries, 12 vehicles, 7 days, seed `20261022`.
- 25 routes, 118 assigned, 116 delivered, 2 failed, 2 unserved.
- Service rate 96.67%, on-time rate 100%, 0 clean validation issues.
- 15/15 reconciliation checks passed.
- 12 ground truths, 12 detections, mean anomaly F1 1.000.
- 40 dirty validation issues and 24 dirty quarantine records.
- Stage timings: generate 0.27 s, plan 35.06 s, simulate 0.06 s, validate clean 0.35 s, inject 0.13 s, detect 0.03 s,
  validate dirty 0.37 s, persist 0.99 s.

The same command was rerun against the same warehouse. `run_metadata` still had one row, along with 25 routes, 120
deliveries, 12 ground truths, and 12 detections. All 21 clean table checksums were identical across the two runs, and
all 21 checksums were recomputed successfully from the Parquet artifacts.

### Benchmarks

Report: `outputs/benchmarks/benchmark_report.json` and `.md`, measured 2026-10-31.

| Config | Total (s) | Plan (s) | Routes | Assigned | Delivered | Unserved | Service | Peak RSS (MB) | Solver s/day | Mean F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 518.34 | 311.50 | 296 | 2,000 | 1,991 | 0 | 99.55% | 445.89 | 10.378 | 1.000 |
| larger | 953.85 | 481.75 | 521 | 4,000 | 3,984 | 0 | 99.60% | 859.69 | 16.042 | 1.000 |

Both runs passed clean validation with zero issues, passed all reconciliation checks, and detected every injected
anomaly. Baseline used 251,001 travel-matrix rows; larger used 564,001. Totals include artifact writes, DuckDB
persistence, both validation passes, injection, detection, and reporting, so they are not solver-only timings.

## Follow-ups and limitations

- The benchmark is one cold run per configuration, not a distribution. Absolute timings depend on hardware, antivirus
  scanning, and solver search behavior.
- Travel is a static Haversine model with road and scenario multipliers, not live road-network routing.
- Planning is one model per service day; multi-day route continuity and multi-depot operation are out of scope.
- Anomaly detection is rule-based and limited to the six specified classes.
- Peak RSS is below 1 GB at the measured scales, but the larger run uses roughly twice the baseline memory. The
  quadratic travel matrix is the main scaling constraint.
- No changes were pushed. Milestone commits remain local to the repository.
