# 07 — Persistence, CLI, dashboard

> **Supplementary normalized summary.** This file groups the work by theme for readability. It is not a verbatim transcript. The user-authored prompt text is preserved in [`verbatim/opencode-prompts.json`](verbatim/opencode-prompts.json) and the Markdown renderings under [`verbatim/opencode/`](verbatim/opencode/). Figures quoted here describe the milestone at the time it was written; the final evidence is recorded in [`../docs/verification.md`](../docs/verification.md).

## Context

The in-memory pipeline could generate data, plan routes, simulate execution, validate the snapshots, and evaluate
anomalies. It still needed durable storage, a usable command surface, and a way for a reviewer to inspect the results
without writing SQL by hand.

## Prompt

> Add a DuckDB warehouse and Parquet/CSV artifacts with transactional, idempotent, run-scoped persistence. Keep clean
> and corrupted snapshots separate. Add a Typer CLI for running, inspecting, exporting schema, launching the
> dashboard, and benchmarking. Build a read-only Streamlit control tower over the warehouse. Do not let persistence
> failures leave half a run behind.

## Decisions

### Warehouse

- The DDL is generated from the Pydantic entity models, written to `persistence/schema.sql`, and kept synchronized by
  a test. The checked-in SQL is a build artifact but is reviewable and diffable.
- Every table is a run-scoped snapshot with `run_id` as the first primary-key column, for example
  `(run_id, route_id)`. Multiple configurations coexist in one database.
- `Warehouse.write_snapshot` deletes all rows for the run and inserts the new frames in one transaction. A failure
  rolls back and leaves the previous snapshot intact. `upsert_table` updates `run_metadata` after the stage counts and
  warehouse inventory are known.
- The four corrupted operational tables are persisted as `dirty_routes`, `dirty_route_stops`,
  `dirty_delivery_assignments`, and `dirty_customer_sites`. Clean tables remain the validated snapshot.
- `run_metadata` records mode (`FULL`/`CLEAN`), status (`RUNNING`/`SUCCEEDED`/`FAILED`/`PARTIAL`), seed, versions,
  config hash, stage durations, counts, checksums, and the error message. `audit_records` is the append-only execution
  trail.

### Artifacts

- A run directory contains `config.yaml`, `manifest.json`, `summary.json`, `checksums.json`, and grouped
  `master/`, `plan/`, `simulation/`, `anomalies/`, `validation/`, `quarantine_dirty/`, and `reports/` folders.
- Every table is written as both Parquet and CSV by default; `--formats` can restrict the set.
- `manifest.json` records row counts, columns, logical checksums, and both format paths. `summary.json` rolls up KPIs,
  counts, validation, anomalies, reconciliation, stage timings, checksums, and warehouse table counts.
- `checksums.json` is a flat map of clean table checksums. The same values are embedded in `summary.json` and the
  manifest.

### CLI

- `fleetmind run` executes the pipeline and prints run identity, KPI summary, stage timings, and output paths.
- `fleetmind info` resolves and prints the deterministic configuration identity without running.
- `fleetmind runs` lists persisted runs with status, mode, seed, and timestamps.
- `fleetmind schema` prints or exports the generated DDL.
- `fleetmind dashboard` launches the Streamlit app and passes an optional `FLEETMIND_DB` environment override.
- `fleetmind benchmark` measures cold pipeline runs and writes JSON and Markdown reports.
- Exit codes are explicit: 0 success, 1 pipeline/warehouse failure, 2 invalid usage/configuration/format.
- Bare configuration names resolve to `configs/<name>.yaml`; explicit relative and absolute paths still work.

### Dashboard

- Five tabs: Overview, Routing & fleet, Execution, Data quality, and Anomalies.
- The app opens DuckDB with `read_only=True`, selects the latest run by `started_at`, and offers a run selector.
- Plotly provides the KPI trend, capacity, distance, outcome, and route-map views. The map uses site coordinates and
  route stop order.
- Pure data shaping lives in `dashboard/data.py`, so it can be unit-tested without Streamlit. The app degrades to an
  explanatory empty state when the database or run is missing.

## Verification

- `python -m fleetmind run --config starter` produced 25 routes, 118 assigned deliveries, 116 delivered, 2 failed,
  2 unserved, 0 clean validation issues, 12 ground-truth anomalies, 12 detections, and mean F1 1.000.
- The same command rerun produced one `run_metadata` row, 25 routes, 120 deliveries, 12 ground truths, and 12
  detections — no duplication.
- `python -m fleetmind info --config starter` resolved `RUN-C16424724715` from the canonical configuration hash.
- `python -m fleetmind runs` and `python -m fleetmind schema --show` returned the persisted run and generated DDL.
- Streamlit `AppTest` smoke tests passed for the dashboard tabs.
- Warehouse rollback, run isolation, rerun replacement, and dirty/clean coexistence are covered by integration tests.

## Follow-ups

- The first benchmark CLI renderer read `item["peak_rss_mb"]`, but the report stores memory under
  `item["memory"]["peak_rss_mb"]`. The renderer now handles successful and failed configurations, and a CLI regression
  test asserts that the report contains `peak_rss_mb`.
- Bare config names initially resolved to `configs/starter` without the `.yaml` suffix. The resolver now appends the
  suffix only when the bare path does not exist, preserving explicit filenames.
- Final verification found that `solve_seconds` made route and solver checksums change between otherwise identical
  runs. That defect and its fix are recorded in entry 08.
