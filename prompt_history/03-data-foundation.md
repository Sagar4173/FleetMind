# 03 — Data foundation

## Context

The package scaffold and configuration existed, but no domain schema or generated data. The dataset had to be
relational, deterministic, realistic enough for routing, and valid before planning.

## Prompt

> Define the complete FleetMind data model and deterministic generator. Include the required entities plus route,
> delivery, run, quality, audit, ground-truth, detection, and scenario tables. Use the specified ID formats. Make
> generation reproducible from the seed and config, and generate a travel model that planning and simulation can
> share.

## Decisions

### Schema

- 30 Pydantic models in `models/entities.py`:
  - Master: `Depot`, `CustomerSite`, `TimeWindow`, `Order`, `Delivery`, `Vehicle`, `VehicleCapacity`,
    `VehicleEquipment`, `Driver`, `DriverCertification`, `Shift`, `ScenarioConfiguration`, `TravelTimeMatrix`.
  - Plan: `Route`, `RouteStop`, `RouteLeg`, `DeliveryAssignment`, `SolverRun`.
  - Simulation: `DeliveryOutcome`, `RouteEvent`, `DeliveryEvent`.
  - Quality/governance: `DataQualityIssue`, `QuarantineRecord`, `GroundTruthAnomaly`,
    `AnomalyDetectionResult`, `AnomalyDetectionMetric`, `ReportMetric`, `ReconciliationCheck`, `RunMetadata`,
    `AuditRecord`.
- Enums in `models/enums.py` for priorities, handling, equipment, vehicle/driver status, licenses, certifications,
  site access, order/delivery/outcome status, solver status, route status, events, weather, strictness, DQ types, and
  anomaly types.
- `ids.py` centralizes every identifier pattern. Examples: `ORD-2026-000001`, `DEL-2026-000001`, `VEH-000001`,
  `DRV-000001`, `RTE-2026-000001`, `STOP-RTE-2026-000001-001`, `EVT-RTE-2026-000001-0001`.

### Generation

- Parent-before-child ordering: depot → sites → time windows → orders → deliveries → vehicles/capacities/equipment →
  drivers/certifications → shifts → scenarios/matrix.
- A SHA-256-derived generator per `(seed, component label)` so adding a component does not shift another component's
  random stream.
- Faker supplies names, addresses, phone numbers, emails, and plates; NumPy supplies numeric draws, mixes, geometry,
  and schedules.
- Baseline scale: 500 sites (97% active), 2,000 orders/deliveries, 100 vehicles, 100 drivers, 30 days.
- Demand mixes: priority 15/55/23/7, handling 62/14/16/8, fleet 45/35/12/8. Mixes are validated to sum to 1.0.
- Shift availability is 92% on weekdays and 62% on weekends. The resulting baseline has 2,143 shifts over 30 days.
- One time window per order, on its requested service date.
- Equipment and certification rules: refrigerated demand requires cold-chain capability and certification; bulk at a
  site without a dock requires lift gate; site access classes restrict vehicle types.

### Geography and travel

- Seven DFW clusters with 11 km spread, sampled around configured centers and clipped to the regional envelope.
- `travel_time_matrix` is all-pairs over depot + sites, baseline `501 × 501 = 251,001` rows.
- Haversine distance × road factor, then speed-based base minutes.
- One `ScenarioConfiguration` per service day with traffic index, weather factor, peak multipliers, network delay,
  incident zone, and seed.
- Planning uses the day-level symmetric multiplier; simulation uses leg-level variance and records the exact
  multiplier on every route leg.

### Reproducibility

- `logical_checksum` hashes canonical CSV content by table, not Parquet bytes.
- A config hash drives the run ID; identical configs map to the same run.
- Generated tables are stored in a `Dataset` snapshot with a `copy_tables()` method so later stages cannot mutate the
  source.

## Verification

- Baseline generation produced 500 sites, 2,000 orders/deliveries, 100 vehicles, 100 drivers, 2,143 shifts, 30
  scenarios, and 251,001 travel-matrix rows.
- Generation was run twice with the same config; logical checksums matched.
- Pydantic validation succeeded for every generated record.
- Starter generation produced 40 sites, 120 orders/deliveries, 12 vehicles, 12 drivers, 7 scenarios.

## Follow-ups

- Volume is stored but not used as a hard routing constraint; this is documented in `docs/assumptions.md`.
- The quadratic travel matrix is a deliberate trade-off for reproducible lookups; scaling limits are documented.
