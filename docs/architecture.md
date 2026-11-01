# Architecture

FleetMind is a batch pipeline with a clean separation between domain modelling, generation, planning, execution,
quality, anomaly evaluation, persistence, and presentation. Each stage returns explicit tables and summaries; no stage
silently reaches into another stage's private state.

## Component map

```text
src/fleetmind/
├── config.py                 Pydantic configuration, YAML extends, canonical hashing
├── pipeline.py               In-memory orchestration and artifact/warehouse persistence
├── models/
│   ├── entities.py           30 Pydantic table schemas
│   └── enums.py              Controlled vocabularies
├── generation/
│   ├── factory.py            Seeded master-data generation
│   ├── geography.py          Region-bounded site sampling
│   ├── travel.py             Haversine travel model and daily scenarios
│   └── ids.py                Deterministic identifier builders
├── routing/optimizer.py      OR-Tools per-day vehicle routing
├── simulation/engine.py      Deterministic execution and event ledger
├── validation/engine.py      Master, plan, execution, lifecycle, and quarantine rules
├── anomalies/
│   ├── injection.py          Six anomaly injectors + ground truth
│   └── detection.py          Six independent detectors + evaluation
├── reporting/metrics.py      KPIs, dimensions, reconciliation
├── persistence/
│   ├── schema.py             Generated DuckDB DDL from Pydantic models
│   ├── schema.sql            Checked-in generated DDL
│   ├── warehouse.py          Transactional run-scoped snapshots
│   └── artifacts.py          Parquet/CSV manifests and checksums
├── benchmarks.py             Cold-run benchmark harness
├── dashboard/                Read-only Streamlit application
└── cli.py                    Typer command surface
```

## Stage flow

1. **Configuration resolution.** `load_config` reads YAML, resolves an `extends` chain (with cycle detection), merges
   nested mappings, and validates the result through `AppConfig`. Unknown keys are rejected. The canonical JSON form is
   SHA-256 hashed; the first 12 uppercase hex characters become the run ID.
2. **Generation.** Parents are generated before children: depot → sites → time windows → orders → deliveries →
   vehicles/capacities/equipment → drivers/certifications → shifts → travel scenarios/matrix. Every table is built
   from typed models, so schema violations fail before planning.
3. **Planning.** For each service date, the planner builds a routing model over that day's deliveries and eligible
   shift/vehicle combinations. It applies hard constraints and a weighted objective. Outputs are routes, stops, legs,
   assignments, and solver runs.
4. **Simulation.** Every planned route is executed. Dispatch delay, travel variance, service variance, customer
   availability, and late-arrival rules are applied deterministically. The result includes executed route/stop/leg
   rows, delivery outcomes, and route/delivery event ledgers. Planned dwell (`wait_minutes`, `service_minutes`) is
   preserved unchanged; executed dwell is published separately as `actual_wait_minutes` and
   `actual_service_minutes`, derived from the stored execution timestamps.
5. **Clean validation.** `validate_full` checks the complete clean snapshot. If errors or critical issues exist, the
   run is marked `FAILED`; evidence is still persisted before the CLI raises.
6. **Reporting.** KPI metrics are computed with explicit formulas and dimensions. Reconciliation checks compare
   independent table-derived counts and sums.
7. **Anomaly injection.** A deep copy of the operational tables is corrupted with a fixed number of instances per
   type. Ground truth is written to a separate frame. Clean tables are never mutated.
8. **Detection and evaluation.** Six detectors read only operational tables. Evaluation joins predictions to ground
   truth by exact `(issue_type, entity_id)` and writes per-type metrics.
9. **Dirty validation.** The corrupted snapshot is validated to prove that quality controls catch the injected
   problems. Cascading issues (for example an invalid foreign key after a stop is removed) are expected and recorded.
10. **Persistence.** Artifacts are written first, then the warehouse snapshot is replaced transactionally, then
    summary and manifest are written. Failed or partial runs retain their evidence.

## Why planning is per day

Shifts, time windows, and travel scenarios are date-specific. Solving a single 30-day model would conflate vehicles
and drivers across dates and make the constraint semantics ambiguous. FleetMind instead builds one OR-Tools model per
service date, reusing only vehicles and drivers that have a shift on that date. This keeps the model auditable and
lets each day's solver status, objective, and wall time be recorded independently in `solver_runs`.

## Determinism model

Determinism is treated as a property of the data, not of the execution clock.

| Source | Control |
|---|---|
| Random values | SHA-256-derived generator per `(seed, component label)` |
| IDs | Sequential builders with fixed widths and year components |
| Configuration | Pydantic normalization + canonical JSON + SHA-256 |
| Table order | Sorted by table name before writes |
| Checksums | Canonical typed CSV text; Parquet bytes and solver wall time excluded |
| Runtime clock | Run/audit wall-clock fields live outside checksummed tables; entity timestamps derive from the calendar |

The run ID intentionally depends on configuration only, not on output paths. Re-running an identical configuration
therefore targets the same run-scoped warehouse snapshot, which makes reruns idempotent. Changing any configuration
value produces a different run ID and a separate snapshot.

## Warehouse model

The warehouse is a set of run-scoped snapshot tables. Every table's primary key begins with `run_id`, followed by the
entity key (for example `("run_id", "route_id")`). Two runs can coexist in one database without collisions.

- `write_snapshot(run_id, tables)` deletes every row for that `run_id` in one transaction, inserts the new frames,
  and commits. Any failure rolls back, leaving the previous snapshot intact.
- `upsert_table` is used for `run_metadata` after persistence, because the metadata row needs final stage durations
  and warehouse counts.
- The four corrupted operational tables are stored as `dirty_*` mirrors. The clean tables remain the validated
  snapshot.
- `schema.sql` is generated from the Pydantic models by `fleetmind schema`; a test keeps it synchronized.

## Error handling

| Failure | Behaviour |
|---|---|
| Invalid configuration | CLI exits with code 2 before generation. |
| Clean validation failure | Run status `FAILED`; artifacts, warehouse snapshot, metadata, and audit trail are written; CLI exits 1. |
| Anomaly stage failure | Run status `PARTIAL`; clean evidence is preserved; CLI exits 1. |
| Warehouse write failure | Transaction rolls back; `WarehouseError` includes the run ID. |
| Missing required detector tables | `AnomalyDetectionError` names the missing tables. |
| Impossible injection (not enough candidates) | `AnomalyInjectionError` names the class and candidate shortage. |

The pipeline purposely writes evidence before raising for validation problems. A failed run is an observable result,
not an empty directory.

### Why a clean simulation can fail

A clean (non-injected) simulation can legitimately exceed a driver shift when execution variance is high enough:
dispatch delays, travel-time variance, and service-time variance all consume the slack that planning reserved
(`routing.execution_buffer_minutes` plus `break_minutes`). When a route's `actual_end` passes the shift's
`planned_end`, validation emits `DRIVER_SHIFT_EXCEEDED` with `CRITICAL` severity. The run becomes `FAILED` with an
explicit `clean snapshot failed validation ...` error; this is a real operational finding, not a validator defect.

Because anomaly injection requires a validated clean snapshot, the pipeline skips anomaly injection, detection, and
dirty validation for a `FAILED` run. Clean validation issues, artifacts, and the warehouse snapshot are still written
so the overrun can be investigated. With the default 30-minute buffer and moderate variance this is rare; zero buffer
and deliberately high variance make it reproducible (see `tests/integration/test_routing_simulation.py`).

## Dashboard boundary

The dashboard is a separate read-only consumer. It opens DuckDB with `read_only=True`, selects a run from
`run_metadata`, and reads the tables needed for the five tabs. It does not import pipeline execution code, does not
write, and degrades to an empty state when the database or run is absent. Pure data-shaping functions live in
`dashboard/data.py` so they can be tested without launching Streamlit.
