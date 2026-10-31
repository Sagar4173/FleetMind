# Validation rules

FleetMind validates data at two boundaries:

1. **Clean snapshot** after generation, planning, and simulation, before persistence.
2. **Dirty snapshot** after anomaly injection, to demonstrate that the rules detect the injected faults and to
   populate quarantine evidence.

The implementation is in `src/fleetmind/validation/engine.py`. Rules are grouped by concern and emit typed
`DataQualityIssue` rows with a stable `issue_id`, source table, entity, field, severity, message, and quarantine flag.

## Outcomes

| Condition | Run status | Persistence | CLI exit |
|---|---|---|---|
| No `ERROR` or `CRITICAL` issues in the clean snapshot | `SUCCEEDED` | Full snapshot | 0 |
| Any clean `ERROR` / `CRITICAL` issue | `FAILED` | Evidence still written | 1 |
| Anomaly stage raises | `PARTIAL` | Clean evidence written | 1 |
| Only `INFO` / `WARNING` issues | Treated as passing | Full snapshot | 0 |

`ValidationResult.passed` is false when any issue has `ERROR` or `CRITICAL` severity. `error_count` counts both;
`critical_count` counts only `CRITICAL`.

Issue prefixes distinguish phases: `DQ-######` for clean validation and `DQD-######` for dirty validation. The prefix
keeps issue IDs deterministic and avoids collisions when both frames are inspected together.

## Rule catalog

### Structural rules

| Rule | Issue type | Severity | What it checks |
|---|---|---|---|
| Required table present | `MISSING_REQUIRED_FIELD` | `CRITICAL` | Every master/plan table exists in the snapshot. |
| Table is a DataFrame | `INVALID_DATA_TYPE` | `CRITICAL` | A stage returned a usable frame. |
| Required columns present | `MISSING_REQUIRED_FIELD` | `CRITICAL` | Every expected column exists. |
| Primary key not null | `MISSING_REQUIRED_FIELD` | `CRITICAL` | Key fields are populated. |
| Primary key unique | `DUPLICATE_PRIMARY_KEY` | `CRITICAL` | No duplicate entity keys. |
| Required values present | `MISSING_REQUIRED_FIELD` | `ERROR` | Non-key required fields are non-empty. |
| Numeric values parse | `INVALID_DATA_TYPE` | `ERROR` | Numeric columns contain numbers. |
| Timestamps parse | `INVALID_DATA_TYPE` | `ERROR` | Datetime columns contain valid timestamps. |
| Enum membership | `INVALID_ENUM` | `ERROR` | Controlled vocabularies are respected. |
| ID pattern | `INVALID_REFERENCE` | `ERROR` | Entity IDs match their documented pattern. |
| Foreign keys | `INVALID_FOREIGN_KEY` | `ERROR` | Child references resolve to parent keys. |

### Master-data rules

| Rule | Issue type | Severity | What it checks |
|---|---|---|---|
| Natural-key uniqueness | `DUPLICATE_PRIMARY_KEY` | `CRITICAL` | One delivery per order, one capacity row per vehicle, one time window per owner, one shift per driver/vehicle per date. |
| Region envelope | `COORDINATE_OUT_OF_REGION` | `ERROR` | Depot and site latitude/longitude lie inside the configured region. |
| Effective dates | `INVALID_DATE_ORDER` | `ERROR` | `effective_to >= effective_from`. |
| Order dates | `INVALID_DATE_ORDER` | `ERROR` | `order_date <= requested_delivery_date`. |
| Window order | `INVALID_DATE_ORDER` | `ERROR` | Window start precedes end. |
| Shift order | `INVALID_DATE_ORDER` | `ERROR` | Shift start precedes end. |
| Delivery/order consistency | `INVALID_REFERENCE` / `INVALID_DATE_ORDER` | `ERROR` | Delivery site and requested date align with its order. |
| Time-window ownership | `INVALID_REFERENCE` | `ERROR` | A delivery's window is owned by its order, and the window falls on the service date. |
| Shift license compatibility | `INVALID_REFERENCE` | `ERROR` | The assigned driver's license class can operate the assigned vehicle. |

### Plan rules

| Rule | Issue type | Severity | What it checks |
|---|---|---|---|
| Plan keys and required fields | `MISSING_REQUIRED_FIELD`, `DUPLICATE_PRIMARY_KEY` | `ERROR` / `CRITICAL` | Routes, stops, legs, assignments, and solver runs are complete and unique. |
| One route per delivery | `STOP_DUPLICATED` | `CRITICAL` | A delivery appears on at most one route stop. |
| Unique stop sequence | `STOP_DUPLICATED` | `CRITICAL` | `(route_id, stop_sequence)` is unique. |
| Unit capacity | `ROUTE_CAPACITY_EXCEEDED` | `CRITICAL` | Route stop demand sum does not exceed the vehicle's unit capacity. |
| Weight capacity | `WEIGHT_CAPACITY_EXCEEDED` | `CRITICAL` | Route stop weight sum does not exceed the vehicle's weight capacity. |
| Hard time windows | `TIME_WINDOW_VIOLATION` | `CRITICAL` | Planned service start lies inside the customer window. |
| Shift containment | `DRIVER_SHIFT_EXCEEDED` | `CRITICAL` | Planned start/end lie inside the shift after reserving break and execution buffer. |
| Stop chronology | `INVALID_DATE_ORDER` | `ERROR` | `planned_arrival <= planned_service_start <= planned_departure`. |
| Assignment coverage | `UNASSIGNED_DELIVERY` | `CRITICAL` | Every requested delivery has an assignment. |
| Assignment/stop coverage | `STOP_OMITTED` | `CRITICAL` | Every `ASSIGNED` delivery has a route stop; unserved deliveries do not. |
| Outcome coverage | `INVALID_LIFECYCLE_TRANSITION` | `ERROR` | Every delivery has exactly one outcome. |
| Route arithmetic | `ARITHMETIC_RECONCILIATION_ERROR` | `CRITICAL` | `stop_count`, loads, service, and wait totals equal stop sums. |
| Leg arithmetic | `ARITHMETIC_RECONCILIATION_ERROR` | `CRITICAL` | Route distance/travel equal leg sums. |
| Event duplication | `DUPLICATE_EVENT` | `ERROR` | No two events share entity, type, and timestamp. |
| Event ordering | `OUT_OF_ORDER_EVENT` | `ERROR` | Event sequences and timestamps are monotonic. |
| Lifecycle transitions | `INVALID_LIFECYCLE_TRANSITION` | `ERROR` | Delivery status matches outcome status and execution timestamps. |

### Execution-specific rules

| Rule | Issue type | Severity | What it checks |
|---|---|---|---|
| Actual stop chronology | `INVALID_DATE_ORDER` | `ERROR` | Actual arrival → service start → departure is ordered. |
| Arrival after previous departure | `ARRIVAL_BEFORE_PREVIOUS_DEPARTURE` | `CRITICAL` | Within a route, a stop's actual arrival is not before the previous stop's actual departure. |
| Actual route end after shift | `DRIVER_SHIFT_EXCEEDED` | `CRITICAL` | A completed route's actual end does not exceed the shift end (plus configured tolerance). |
| Completed route timestamps | `INVALID_DATE_ORDER` | `ERROR` | Route actual start/end are ordered. |
| Delivery status invariants | `INVALID_LIFECYCLE_TRANSITION` | `ERROR` | Planned status, assignment status, and outcome agree. |

## Quarantine

A record is quarantined when its issue type is one of:

- `DUPLICATE_PRIMARY_KEY`
- `MISSING_REQUIRED_FIELD`
- `INVALID_DATA_TYPE`
- `INVALID_FOREIGN_KEY`
- `INVALID_ENUM`
- `INVALID_REFERENCE`

Quarantine copies the original record from the source table into `quarantine_records.record_json` and marks the
issue's `quarantined` flag. The record is not deleted from the snapshot; the quarantine table is evidence for
operators and downstream cleansing. Plan/execution semantic anomalies (capacity, windows, shifts, chronology,
omissions, duplicates) are flagged but not removed: removing them would hide the very evidence the detectors and
reviewers need.

Quarantine is built independently for the clean and dirty snapshots. Dirty quarantine is written to
`quarantine_dirty/` and `dirty_quarantine_records`; clean quarantine is written to `quarantine_clean/` and
`quarantine_records`.

## Reconciliation

`compute_reconciliation` independently re-derives invariants from multiple tables. A failure here is a reporting
bug even if validation passes, so the checks are persisted alongside KPIs.

| Check | Invariant |
|---|---|
| `delivery_assignment_coverage` | One assignment row per requested delivery. |
| `assignment_partition` | Every assignment is `ASSIGNED` or `UNSERVED`. |
| `outcome_coverage` | One outcome row per requested delivery. |
| `outcome_partition` | Every outcome is `DELIVERED`, `FAILED`, or `UNSERVED`. |
| `route_stop_count` | Route `stop_count` equals its stop rows. |
| `assignment_stop_coverage` | Assigned deliveries equal route stop rows. |
| `route_load_units` | Route load equals stop demand sum. |
| `route_load_weight` | Route load weight equals stop weight sum. |
| `route_service_minutes` | Route service minutes equal stop service sum. |
| `route_wait_minutes` | Route wait minutes equal stop wait sum. |
| `route_distance` | Route distance equals planned leg distance sum. |
| `route_travel_minutes` | Route travel equals planned leg travel sum. |
| `route_delivered_stops` | Route delivered count equals `DELIVERED` stop rows. |
| `route_failed_stops` | Route failed count equals `FAILED` stop rows. |
| `planned_shifts` | Each planned route references a distinct shift. |

## Reading the output

```powershell
python -m fleetmind run --config starter
```

Then inspect:

- `validation/clean_issues.csv` — should be empty for a healthy run.
- `validation/dirty_issues.csv` — expected to contain the injected classes plus cascading issues.
- `validation/quarantine_records.csv` and `validation/dirty_quarantine_records.csv` — record-level evidence.
- `reports/reconciliation_checks.csv` — should show every check `passed=True`.
- `summary.json` → `validation` and `reconciliation` — machine-readable roll-up.

A clean run with non-empty `clean_issues.csv` is a defect in generation, planning, simulation, or the rule set.
A dirty run with empty `dirty_issues.csv` is a defect in anomaly injection or validation coverage.
