# Data dictionary

The canonical schema is defined once in `src/fleetmind/models/entities.py` and generated into
`src/fleetmind/persistence/schema.sql`. This document describes the meaning and relationships of those tables. When
this document and the generated DDL disagree, the generated DDL is authoritative and the discrepancy is a bug.

## Conventions

- **Snapshot key.** Every table starts with `run_id` and is keyed by `(run_id, <natural key>)`. The same entity key
  may exist in multiple runs.
- **Types.** Dates are `DATE`, timestamps are `TIMESTAMP` (naive local time in the synthetic region), counts and
  units are `BIGINT`, measurements and money are `DOUBLE`, enums are `VARCHAR` containing the enum value, and
  optional values are nullable.
- **Time.** Minute-of-day fields are integers in `[0, 1440]`. Generated service dates begin at the configured
  `horizon.start_date`. Datetimes are local to the synthetic DFW region.
- **Units.** `demand_units` is a normalized handling unit; `weight_kg` is kilograms; `distance_km` is kilometres;
  durations are minutes unless a field name ends in `_seconds`.
- **Enums.** Enum values are uppercase strings listed in `src/fleetmind/models/enums.py`. Important sets include
  `Priority`, `HandlingClass`, `Equipment`, `VehicleType`, `VehicleStatus`, `LicenseClass`, `Certification`,
  `DriverStatus`, `ShiftStatus`, `SiteType`, `SiteAccessClass`, `OrderStatus`, `DeliveryStatus`, `OutcomeStatus`,
  `AssignmentStatus`, `SolverStatus`, `RouteStatus`, `Weather`, `DQSeverity`, `DQIssueType`, and `AnomalyType`.
- **Booleans.** DuckDB stores them natively; CSV writes `True`/`False`.

## Identifier formats

| Entity | Pattern | Example |
|---|---|---|
| Depot | `DEP-###` | `DEP-001` |
| Customer site | `CUS-######` | `CUS-000001` |
| Time window | `TW-YYYY-######` | `TW-2026-000001` |
| Order | `ORD-YYYY-######` | `ORD-2026-000001` |
| Delivery | `DEL-YYYY-######` | `DEL-2026-000001` |
| Vehicle | `VEH-######` | `VEH-000001` |
| Capacity | `CAP-######` | `CAP-000001` |
| Driver | `DRV-######` | `DRV-000001` |
| Shift | `SHT-YYYY-######` | `SHT-2026-000001` |
| Travel scenario | `SCN-YYYY-####` | `SCN-2026-0001` |
| Route | `RTE-YYYY-######` | `RTE-2026-000001` |
| Route stop | `STOP-RTE-YYYY-######-###` | `STOP-RTE-2026-000001-001` |
| Route leg | `LEG-YYYY-######-####` | `LEG-2026-000001-0001` |
| Assignment | `ASN-YYYY-######` | `ASN-2026-000001` |
| Solver run | `SOL-YYYY-######` | `SOL-2026-000001` |
| Delivery outcome | `OUT-YYYY-######` | `OUT-2026-000001` |
| Route event | `EVT-RTE-YYYY-######-####` | `EVT-RTE-2026-000001-0001` |
| Delivery event | `EVT-DEL-YYYY-######-####` | `EVT-DEL-2026-000001-0001` |
| Ground truth | `GT-YYYY-######` | `GT-2026-000001` |
| Detection | `DET-YYYY-######` | `DET-2026-000001` |
| Data-quality issue | `<PREFIX>-######` | `DQ-000001` / `DQD-000001` |

## Table catalog

| Group | Tables |
|---|---|
| Master | `depots`, `customer_sites`, `time_windows`, `orders`, `deliveries`, `vehicles`, `vehicle_capacities`, `vehicle_equipment`, `drivers`, `driver_certifications`, `shifts`, `travel_scenarios`, `travel_time_matrix` |
| Plan | `routes`, `route_stops`, `route_legs`, `delivery_assignments`, `solver_runs` |
| Simulation | `delivery_outcomes`, `route_events`, `delivery_events` |
| Quality | `data_quality_issues`, `quarantine_records` |
| Anomaly | `ground_truth_anomalies`, `anomaly_detections`, `anomaly_detection_metrics` |
| Reporting | `report_metrics`, `reconciliation_checks` |
| Governance | `run_metadata`, `audit_records` |
| Corrupted mirrors | `dirty_routes`, `dirty_route_stops`, `dirty_delivery_assignments`, `dirty_customer_sites` |

## Master data

### `depots`

The origin and return point for every route.

| Column | Meaning |
|---|---|
| `run_id` | Run snapshot key. |
| `depot_id` | `DEP-###`, one depot in the baseline region. |
| `name`, `address_line`, `city`, `state`, `postal_code` | Postal identity. |
| `latitude`, `longitude` | Depot coordinates inside the region envelope. |
| `open_minute`, `close_minute` | Operating window in minutes from midnight. |
| `active` | Whether the depot can dispatch. |

### `customer_sites`

Delivery locations with receiving rules and access characteristics.

| Column | Meaning |
|---|---|
| `site_id` | `CUS-######`. |
| `customer_name`, `site_name` | Customer and location labels. |
| `address_line`, `city`, `state`, `postal_code` | Postal address. |
| `latitude`, `longitude` | Site coordinates, normally inside the region. |
| `site_type` | `RETAIL`, `WAREHOUSE`, `CLINIC`, `RESTAURANT`, `OFFICE`, `RESIDENTIAL`. |
| `access_class` | `VAN_ONLY`, `MEDIUM`, `ALL`; constrains eligible vehicle types. |
| `receiving_start_minute`, `receiving_end_minute` | Receiving hours. |
| `dock_available` | Whether a dock is available. |
| `contact_name`, `contact_phone` | Synthetic site contact. |
| `risk_score` | Synthetic `[0, 1]` risk indicator. |
| `active` | Whether the site can receive deliveries. |
| `effective_from`, `effective_to` | Site validity interval. |
| `created_at` | Record creation timestamp. |

### `time_windows`

Customer delivery windows, one per order/delivery owner.

| Column | Meaning |
|---|---|
| `time_window_id` | `TW-YYYY-######`. |
| `owner_type` | `ORDER` or `DELIVERY`. |
| `owner_id` | The owning `order_id` or `delivery_id`. |
| `window_start`, `window_end` | Hard delivery window. |
| `strictness` | `HARD` (default) or `SOFT`. Baseline planning uses hard windows. |
| `grace_minutes` | Operational grace retained on the record. |

### `orders`

Customer demand before it becomes a planned delivery. Each order has exactly one delivery.

| Column | Meaning |
|---|---|
| `order_id` | `ORD-YYYY-######`. |
| `site_id` | Destination site. |
| `order_date`, `requested_delivery_date` | Order date and requested service date. |
| `priority` | `LOW`, `STANDARD`, `HIGH`, `URGENT`. |
| `handling_class` | `AMBIENT`, `REFRIGERATED`, `FRAGILE`, `BULK`. |
| `required_equipment` | Equipment the vehicle must provide. |
| `demand_units`, `weight_kg`, `volume_m3` | Demand size. Weight and unit count are hard planning constraints; volume is carried as an advisory dimension. |
| `service_duration_min` | Planned service time. |
| `time_window_id` | Hard receiving window. |
| `status` | `CONFIRMED` at generation; later `ASSIGNED`, `DELIVERED`, `FAILED`, `UNSERVED`, or `CANCELLED`. |
| `source_system` | Synthetic source label. |
| `created_at` | Record creation timestamp. |

### `deliveries`

The execution-facing form of an order. IDs are deterministic and share the order's site, window, and demand.

| Column | Meaning |
|---|---|
| `delivery_id` | `DEL-YYYY-######`. |
| `order_id`, `site_id` | Parent order and destination. |
| `service_date` | Day the delivery is requested. |
| `priority`, `handling_class`, `required_equipment` | Planning attributes copied from the order. |
| `demand_units`, `weight_kg`, `volume_m3`, `service_duration_min` | Demand and service time. Weight and unit count are hard constraints; volume is advisory. |
| `time_window_id` | Hard window. |
| `status` | `PENDING` → `ASSIGNED` → `DELIVERED` / `FAILED` / `UNSERVED`. |
| `created_at` | Record creation timestamp. |

### `vehicles`, `vehicle_capacities`, `vehicle_equipment`

Fleet identity, physical limits, and equipment capabilities.

`vehicles`: `vehicle_id`, `vehicle_type`, `make`, `model`, `model_year`, `license_plate`, `status`, `home_depot_id`,
`required_license_class`, `max_route_duration_min`, `odometer_km`, `acquisition_date`, `effective_from`,
`effective_to`.

`vehicle_capacities`: `capacity_id`, `vehicle_id`, `capacity_units`, `max_weight_kg`, `max_volume_m3`,
`dimension_note`. One capacity row per vehicle. `capacity_units` are synthetic dock handling units. Unit capacity and
weight are hard routing constraints; `max_volume_m3` is advisory and is not enforced by OR-Tools.

`vehicle_equipment`: `vehicle_id`, `equipment`; composite key `(vehicle_id, equipment)`. Equipment values include
`AMBIENT`, `REFRIGERATED`, `FRAGILE`, `BULK`, `LIFT_GATE`.

Vehicle types in the generator:

| Type | Capacity units | Max weight | Equipment | License |
|---|---:|---:|---|---|
| `CARGO_VAN` | 120 | 1,500 kg | ambient, fragile | C |
| `BOX_TRUCK` | 420 | 7,000 kg | ambient, fragile, bulk, lift gate | B |
| `REFRIGERATED_TRUCK` | 300 | 6,500 kg | ambient, refrigerated, lift gate | A |
| `FLATBED` | 600 | 9,000 kg | bulk, lift gate | A |

### `drivers`, `driver_certifications`

Driver identity, licensing, cost, and certifications.

`drivers`: `driver_id`, `first_name`, `last_name`, `phone`, `email`, `license_number`, `license_class`,
`license_expiry`, `hire_date`, `home_depot_id`, `status`, `max_shift_hours`, `hourly_cost`, `effective_from`,
`effective_to`.

`driver_certifications`: `driver_id`, `certification`, `issued_date`, `expiry_date`; key
`(driver_id, certification)`. Certifications include `COLD_CHAIN`, `HAZMAT`, `LIFT_GATE`.

### `shifts`

A driver/vehicle pairing available on a service date. Routes consume shifts.

| Column | Meaning |
|---|---|
| `shift_id` | `SHT-YYYY-######`. |
| `driver_id`, `vehicle_id` | Assigned resources. |
| `service_date` | Shift date. |
| `planned_start`, `planned_end` | Paid shift interval. |
| `break_minutes` | Break reserved inside the shift. |
| `status` | `SCHEDULED`, `ACTIVE`, `COMPLETED`, `CANCELLED`, `MISSED`. |
| `created_at` | Record creation timestamp. |

Uniqueness rules: one shift per driver per date and one shift per vehicle per date.

### `travel_scenarios`

One row per service date describing travel conditions.

| Column | Meaning |
|---|---|
| `scenario_id` | `SCN-YYYY-####`. |
| `scenario_date`, `day_name` | Calendar identity. |
| `traffic_index` | `[0.5, 3.0]` aggregate congestion index. |
| `weather`, `weather_factor` | Weather class and multiplier. |
| `am_peak_multiplier`, `pm_peak_multiplier` | Peak-period multipliers. |
| `network_delay_minutes` | Added delay. |
| `incident_zone` | Synthetic incident area label. |
| `seed` | Scenario generator seed. |
| `notes` | Human-readable explanation. |

### `travel_time_matrix`

Static all-pairs travel model. Baseline generates `501 × 501 = 251,001` rows (depot + 500 sites).

| Column | Meaning |
|---|---|
| `origin_id`, `destination_id` | `DEP-001` or `CUS-######`. |
| `distance_km` | Haversine distance × road factor. |
| `base_travel_minutes` | Distance divided by an assumed road speed. |
| `model_version` | Travel model version. |

Planning applies the day's scenario multiplier symmetrically. Simulation applies leg-level variance and records the
exact multiplier used on each leg.

## Plan data

### `routes`

One planned route per vehicle/shift/day that serves at least one stop.

| Column | Meaning |
|---|---|
| `route_id` | `RTE-YYYY-######`. |
| `scenario_id`, `service_date` | Travel context and day. |
| `depot_id`, `vehicle_id`, `driver_id`, `shift_id` | Resources. |
| `status` | `PLANNED` after planning; `COMPLETED` / `PARTIAL` / `CANCELLED` after execution. |
| `planned_start`, `planned_end` | Planned depot departure and return. |
| `actual_start`, `actual_end` | Simulated execution interval. |
| `stop_count`, `delivered_stops`, `failed_stops` | Stop outcome counts. |
| `load_units`, `capacity_units` | Unit load and vehicle unit capacity. |
| `load_weight_kg`, `capacity_weight_kg` | Weight load and vehicle weight capacity. |
| `distance_km`, `travel_minutes` | Route distance and travel aggregates. |
| `service_minutes`, `wait_minutes` | **Planned** route service and waiting minutes from the routing model. |
| `actual_service_minutes`, `actual_wait_minutes` | **Executed** route service and waiting minutes, summed from stop actual dwell. Null before execution. |
| `solver_status` | `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `TIMEOUT`, `UNKNOWN`. |
| `objective_value`, `solve_seconds` | Solver result and wall time. |
| `shift_overrun_minutes` | Actual end beyond shift end, zero when within shift. |
| `created_at` | Record creation timestamp. |

### `route_stops`

Ordered stops within a route.

| Column | Meaning |
|---|---|
| `stop_id` | `STOP-RTE-YYYY-######-###`. |
| `route_id`, `stop_sequence` | Route and 1-based order. |
| `delivery_id`, `site_id`, `time_window_id` | Delivery, destination, and window. |
| `planned_arrival`, `planned_service_start`, `planned_departure` | Planned timeline. |
| `actual_arrival`, `actual_service_start`, `actual_departure` | Simulated timeline. |
| `wait_minutes`, `service_minutes` | **Planned** waiting and service time from the routing model. Preserved unchanged by execution. |
| `actual_wait_minutes`, `actual_service_minutes` | **Executed** dwell derived from `actual_arrival`/`actual_service_start`/`actual_departure`. Null when the stop never executed. |
| `demand_units`, `weight_kg` | Stop load contribution. |
| `status` | `ASSIGNED`, `DELIVERED`, `FAILED`, or `UNSERVED`. |

The distinction matters: planned dwell answers "what did the plan expect?" and actual dwell answers "what happened?".
Execution does not overwrite `wait_minutes` or `service_minutes`. A failed stop keeps its planned service minutes but
records only the configured failed-attempt duration as actual service time.

### `route_legs`

Travel segments between depot and stops.

| Column | Meaning |
|---|---|
| `leg_id` | `LEG-YYYY-######-####`. |
| `route_id`, `leg_sequence` | Parent route and order. |
| `origin_type`, `origin_id`, `destination_type`, `destination_id` | Endpoints, `DEPOT` or `SITE`. |
| `planned_departure`, `planned_arrival` | Planned segment timeline. |
| `planned_distance_km`, `planned_travel_minutes` | Planned segment measures. |
| `actual_departure`, `actual_arrival` | Executed segment timeline. |
| `actual_distance_km`, `actual_travel_minutes` | Executed measures (distance unchanged). |
| `scenario_id`, `traffic_multiplier` | Scenario and exact applied multiplier. |

### `delivery_assignments`

The authoritative partition of requested deliveries into served and unserved.

| Column | Meaning |
|---|---|
| `assignment_id` | `ASN-YYYY-######`. |
| `delivery_id` | Exactly one row per requested delivery. |
| `route_id`, `stop_id` | Null for `UNSERVED` deliveries. |
| `service_date` | Requested date. |
| `status` | `ASSIGNED` or `UNSERVED`. |
| `reason_code` | Why a delivery was unserved, for example `NO_ELIGIBLE_VEHICLE`. |
| `explanation` | Human-readable assignment explanation. |
| `created_at` | Record creation timestamp. |

### `solver_runs`

One row per service date, recording solver behavior even when it returns no routes.

| Column | Meaning |
|---|---|
| `solve_id` | `SOL-YYYY-######`. |
| `service_date` | Solved date. |
| `status` | Solver status. |
| `objective_value` | Objective value when available. |
| `solve_seconds` | Measured wall time. |
| `assigned_count`, `unserved_count`, `vehicle_count` | Solution composition. |
| `message` | Solver or planner message. |

## Simulation data

### `delivery_outcomes`

Exactly one outcome per requested delivery after execution.

| Column | Meaning |
|---|---|
| `outcome_id` | `OUT-YYYY-######`. |
| `delivery_id`, `route_id`, `stop_id` | Delivery and optional execution references. |
| `status` | `DELIVERED`, `FAILED`, `UNSERVED`. |
| `reason_code` | Failure reason such as `CUSTOMER_UNAVAILABLE` or `LATE_ARRIVAL`. |
| `actual_arrival`, `service_start`, `service_end` | Execution times. |
| `on_time` | Whether delivery met the reporting grace threshold. |
| `proof_of_delivery` | Synthetic proof identifier when enabled. |
| `notes` | Additional context. |
| `recorded_at` | Record timestamp. |

### `route_events`, `delivery_events`

Append-only event ledgers with deterministic event IDs and sequences.

`route_events`: `event_id`, `route_id`, `stop_id`, `event_type`, `event_time`, `planned_time`, `sequence`, `details`.
Event types: `ROUTE_START`, `ARRIVE_STOP`, `WAIT_START`, `WAIT_END`, `SERVICE_START`, `SERVICE_END`,
`ROUTE_RETURN`, `ROUTE_END`.

`delivery_events`: `event_id`, `delivery_id`, `route_id`, `stop_id`, `event_type`, `event_time`, `planned_time`,
`sequence`, `details`. Event types: `ASSIGNED`, `DISPATCHED`, `ARRIVED`, `SERVICE_STARTED`, `DELIVERED`, `FAILED`,
`UNSERVED`.

## Quality and anomaly data

### `data_quality_issues`

| Column | Meaning |
|---|---|
| `issue_id` | `DQ-######` for the clean pass, `DQD-######` for the dirty pass. |
| `issue_type` | A `DQIssueType` value. |
| `severity` | `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `entity_type`, `entity_id` | Offending table/record. |
| `field_name` | Field or composite key involved. |
| `message` | Human-readable diagnosis. |
| `source_table` | Table that produced the issue. |
| `detected_at` | Validation timestamp. |
| `quarantined` | Whether the record was copied to quarantine. |
| `details` | Extra context such as duplicate counts. |

### `quarantine_records`

| Column | Meaning |
|---|---|
| `quarantine_id` | Deterministic record ID. |
| `issue_id` | Originating data-quality issue. |
| `source_table`, `entity_id` | Record identity. |
| `record_json` | Original record serialized as JSON. |
| `reason` | Quarantine reason. |
| `quarantined_at` | Timestamp. |

### `ground_truth_anomalies`

| Column | Meaning |
|---|---|
| `anomaly_id` | `GT-YYYY-######`. |
| `issue_type` | One of the six `AnomalyType` values. |
| `entity_type`, `entity_id` | Corrupted entity. |
| `source_table` | Table holding the entity. |
| `original_value`, `injected_value` | Before/after evidence. |
| `injected_at` | Injection timestamp. |
| `notes` | Injection explanation. |

### `anomaly_detections`

| Column | Meaning |
|---|---|
| `detection_id` | `DET-YYYY-######`. |
| `issue_type` | Detector class. |
| `entity_type`, `entity_id` | Detected entity. |
| `source_table` | Evidence table. |
| `detection_rule` | Stable detector rule name. |
| `observed_value` | Observed values that triggered the rule. |
| `detected_at` | Detection timestamp. |

### `anomaly_detection_metrics`

| Column | Meaning |
|---|---|
| `issue_type` | Evaluated anomaly class. |
| `tp`, `fp`, `tn`, `fn` | Confusion counts using exact entity matching. |
| `precision`, `recall`, `f1` | Derived scores, zero when the denominator is zero. |
| `evaluated_universe` | Number of entities the detector could meaningfully evaluate. |
| `notes` | Definition of the detector universe. |

## Reporting and governance

### `report_metrics`

A long-format metric table: `category`, `metric_name`, `value`, `unit`, `numerator`, `denominator`, `formula`, and
`dimensions`. Dimensions use a stable `key=value;key=value` string. Scalar metrics have an empty `dimensions` value;
the primary key is `(run_id, category, metric_name, dimensions)`.

Categories include `service`, `service_priority`, `service_handling`, `daily`, `site_city`, `fleet`, `fleet_type`,
`routing`, `reliability`, `top_route`, and `top_site`.

### `reconciliation_checks`

`check_name`, `expected`, `actual`, `difference`, `passed`, `details`. Checks independently re-derive counts and sums
from related tables (assignment coverage, outcome coverage, stop counts, route arithmetic, trip counts).

### `run_metadata`

One row per run: `mode` (`FULL` or `CLEAN`), `seed`, `dataset_version`, `pipeline_version`, `config_hash`, `status`
(`RUNNING`, `SUCCEEDED`, `FAILED`, `PARTIAL`), `started_at`, `completed_at`, JSON stage durations, counts, and
checksums, and `error_message`.

### `audit_records`

Append-only execution trail: `audit_id`, `execution_id`, `timestamp`, `actor`, `action`, and `details_json`. Actions
include pipeline start, generation, planning, simulation, validation, anomaly injection/detection, reporting,
success, failure, and partial completion.

### `dirty_*` mirrors

`dirty_routes`, `dirty_route_stops`, `dirty_delivery_assignments`, and `dirty_customer_sites` have exactly the same
columns as their clean counterparts. They preserve the corrupted snapshot used for detection and dirty validation.
They are not foreign-keyed to each other or to the clean tables; that separation is deliberate so the corrupted world
can be inspected without affecting the validated snapshot.
