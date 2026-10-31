# Anomaly methodology

FleetMind treats anomaly injection, detection, and evaluation as three separate concerns. Injection writes a
ground-truth ledger. Detection reads only operational tables. Evaluation is the only component allowed to compare the
two.

## Design guarantees

1. **The clean snapshot is never mutated.** `AnomalyInjector.inject` deep-copies the operational tables and returns a
   new mapping plus a ground-truth frame.
2. **Injection is deterministic.** Candidate selection uses a SHA-256-derived generator seeded from the run seed and the
   literal label `anomaly-injection`. A fixed configuration produces the same entities and values.
3. **Each requested instance is recorded once.** The injector asserts exactly
   `anomaly.instances_per_type` records for every class and raises `AnomalyInjectionError` otherwise.
4. **Detectors do not receive ground truth.** `detect_anomalies(tables, config)` has no ground-truth parameter.
   Ground truth is passed only to `evaluate_detections`.
5. **Routes are kept disjoint where practical.** Capacity, omitted-stop, duplicated-stop, arrival-order, and
   shift-overrun injections avoid reusing a route, so one corruption does not accidentally create a second required
   class. Cascading data-quality findings are still possible and are recorded separately.
6. **The duplicate stop is not executed.** Its actual timestamps are cleared, so it cannot accidentally trigger the
   arrival-order detector.

## Injected classes

### 1. Route capacity exceeded

**Mechanism.** Pick a route with a known vehicle capacity and at least one stop. On the route's first stop, set
`demand_units` to at least `capacity_units + 25` (and above the original demand). Route aggregates are recomputed from
stops, but the detector recomputes the load independently.

**Ground truth.** Entity is the route (`entity_type=route`, `entity_id=route_id`), source table `route_stops`.

**Why it is realistic.** The route still references a valid vehicle and shift; only the demand record is corrupted.
This models a load-planning or order-entry error rather than a fleet-assignment error.

### 2. Stop omitted

**Mechanism.** Choose an assigned delivery that has exactly one stop on a route with more than one stop, avoiding
routes already used by capacity injections and avoiding the inflated stop. Remove the stop row. Leave the
`delivery_assignments` row as `ASSIGNED`.

**Ground truth.** Entity is the delivery (`entity_type=delivery`, `entity_id=delivery_id`).

**Why it is realistic.** A stop can disappear during route export or dispatch while the assignment system still
believes the delivery is planned.

### 3. Stop duplicated

**Mechanism.** Choose a different assigned delivery whose route has capacity headroom for the extra units and weight.
Append a copy of its stop with a new stop ID, sequence `max + 1`, and cleared actual execution timestamps. The route
aggregates are recomputed.

**Ground truth.** Entity is the delivery (`entity_type=delivery`, `entity_id=delivery_id`).

**Why it is realistic.** A retry or integration replay can create a second stop record without cancelling the first.

### 4. Arrival before previous departure

**Mechanism.** On a route not already used, choose a stop at sequence ≥ 2 that has an actual arrival and whose
predecessor has an actual departure. Set the arrival to exactly ten minutes before the predecessor's departure. The
service-start and departure timestamps are left untouched, so the record is chronologically impossible without
looking like a malformed timestamp.

**Ground truth.** Entity is the stop (`entity_type=route_stop`, `entity_id=stop_id`).

**Why it is realistic.** Telemetry can arrive out of order or be merged from devices with clock skew.

### 5. Driver shift exceeded

**Mechanism.** Choose a completed route not used by another injection. Extend its `actual_end` beyond the shift's
`planned_end` by 30–90 deterministic minutes, and set `shift_overrun_minutes` accordingly. No other route field is
changed.

**Ground truth.** Entity is the route (`entity_type=route`, `entity_id=route_id`).

**Why it is realistic.** Traffic or service delays can push a route past the driver's legal or scheduled shift.

### 6. Coordinate outside region

**Mechanism.** Choose customer sites deterministically and set both latitude and longitude to
`region.max_* + anomaly.coordinate_margin_degrees` (default margin 0.35°).

**Ground truth.** Entity is the customer site (`entity_type=customer_site`, `entity_id=site_id`).

**Why it is realistic.** Geocoding failures often produce coordinates in the right country but outside the service
envelope.

## Detection rules and universes

| Class | Rule name | Detection logic | Evaluated universe |
|---|---|---|---|
| `ROUTE_CAPACITY_EXCEEDED` | `ROUTE_LOAD_EXCEEDS_CAPACITY` | Sum stop demand per route; join route vehicle to capacity; flag `load > capacity`. | Routes with a known capacity row. |
| `STOP_OMITTED` | `ASSIGNED_DELIVERY_WITHOUT_STOP` | Flag `ASSIGNED` deliveries with no `route_stops` row. | Assigned deliveries. |
| `STOP_DUPLICATED` | `DELIVERY_WITH_MULTIPLE_STOPS` | Count stops per delivery; flag counts > 1. | Assigned deliveries plus all stop-referenced deliveries. |
| `ARRIVAL_BEFORE_PREVIOUS_DEPARTURE` | `ARRIVAL_PRECEDES_PREVIOUS_DEPARTURE` | Sort by route/sequence; flag an actual arrival earlier than the preceding actual departure. | Stops with an actual arrival and a preceding actual departure. |
| `DRIVER_SHIFT_EXCEEDED` | `ROUTE_END_AFTER_SHIFT_END` | Join route to shift; flag `actual_end > planned_end`. | Routes with an actual end and a resolvable shift end. |
| `COORDINATE_OUT_OF_REGION` | `SITE_COORDINATE_OUTSIDE_REGION` | Compare numeric site coordinates with configured bounds. | Sites with numeric coordinates. |

The detector uses no knowledge of which rows were injected and no ground-truth columns. Rule names are stable and are
persisted in `anomaly_detections.detection_rule`.

## Evaluation

`evaluate_detections` matches each class by exact `(issue_type, entity_id)`:

- `TP` = truth ∩ predicted
- `FP` = predicted − truth
- `FN` = truth − predicted
- `TN` = universe − truth − predicted
- `precision = TP / (TP + FP)`, zero when the denominator is zero
- `recall = TP / (TP + FN)`, zero when the denominator is zero
- `F1 = 2PR / (P + R)`, zero when `P + R` is zero

`evaluated_universe` is persisted alongside the metrics so a reviewer can see the denominator behind `TN`. The
universe is the detector's own candidate set, augmented with truth and predictions for safety. This makes the metric
interpretable: an empty detector on an empty universe is not rewarded with a misleading `TN`.

## Cascading validation findings

Dirty validation is intentionally not limited to the six classes. Removing a stop can orphan event or assignment
references; duplicating a stop can create duplicate natural keys; moving a coordinate can make a site inconsistent
with other records. The dirty issue frame therefore often contains `INVALID_FOREIGN_KEY`, `STOP_DUPLICATED`, and
`COORDINATE_OUT_OF_REGION` counts larger than the injected instance count. These cascades are evidence that
record-level validation is working, and they are kept out of the detection metrics by matching only exact injected
entities in the anomaly evaluation.

## Measured behavior

The 30-day baseline and larger benchmarks both reported:

- 3 injected instances per type on baseline (18 total) and larger (18 total).
- 100% precision and recall on all six classes.
- Mean F1 1.000.
- Zero clean validation issues.

The starter run (`data/sample/starter-run`) uses 2 instances per type and also reports F1 1.000 with 12 detections.
These results demonstrate correctness on the injected distribution; they do **not** establish performance on
unseen, naturally occurring anomalies. The detectors are rule-based and deliberately limited to the six specified
classes.

## Limitations

- Rules assume the schema and semantics described in the data dictionary. A renamed column or changed unit would
  invalidate the detector.
- Capacity detection trusts `route_stops.demand_units` as the operational truth; it does not re-read the order.
- Shift detection compares against the shift's planned end, not the driver's legal maximum.
- Coordinate detection is a bounding-box check, not a polygon or road-network reachability check.
- Injection requires enough suitable candidates. Very small configurations can raise rather than silently inject
  fewer instances; tests use a configuration large enough for all six classes.
