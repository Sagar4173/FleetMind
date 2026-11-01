# 01 — Scope and architecture

## Context

Empty repository in the project workspace, no implementation, no Git history. The goal is a production-quality
deliverable, not a notebook demo.

## Prompt

> Build FleetMind, a synthetic logistics data engineering and delivery-planning platform. It must generate a
> realistic relational dataset, plan routes with OR-Tools under hard capacity, time-window, shift, eligibility, and
> service constraints, simulate execution, inject and independently detect six anomaly classes, validate data,
> persist to DuckDB, report KPIs, expose a dashboard, provide measured benchmarks and tests, and document every
> decision. Include a prompt history. Implement, run, test, and verify incrementally; do not stop at planning.
> Commit milestones but do not push.

Required baseline: 500 sites, 2,000 deliveries, 100 vehicles, 30 days, seed `20261022`. Required stack: Python
3.11–3.13, OR-Tools, Pandas, NumPy, Faker, DuckDB, Parquet/CSV, Pydantic, pytest, PyYAML, Streamlit, Plotly.

## Decisions

- Layered package under `src/fleetmind`: config, models, generation, routing, simulation, validation, anomalies,
  reporting, persistence, dashboard, benchmarks, CLI, pipeline.
- `pipeline.py` owns in-memory orchestration; persistence is a separate function so failed runs can still write
  evidence and so benchmarks can time both phases.
- Domain-first design: 30 Pydantic table models, enums, and deterministic ID builders before any generation code.
- Warehouse tables are run-scoped snapshots keyed by `(run_id, entity_id)`, allowing multiple runs in one DuckDB file.
- Clean and dirty snapshots coexist through canonical tables plus `dirty_*` mirrors.
- Starter, baseline, and larger YAML configs share an `extends` chain so scale changes do not duplicate settings.
- Acceptance criteria: clean baseline validation, all six anomaly types detected, idempotent reruns, reproducible
  checksums, measured benchmark evidence, and a passing test suite.

## Verification

- Repository scaffold, configuration files, package entry point, and initial Git commit created.
- Baseline configuration resolved to seed `20261022`, 500 sites, 2,000 orders, 100 vehicles, 30 days.
- Two milestone commits planned: core pipeline, then persistence/CLI/dashboard/benchmarks.

## Follow-ups

- The implementation was built stage by stage rather than as one large drop, with tiny-config integration tests to
  keep the full pipeline runnable in seconds.
