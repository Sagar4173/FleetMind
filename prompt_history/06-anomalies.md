# 06 — Anomalies

## Context

The clean pipeline validated successfully. The assessment required six specific anomaly classes, separate ground
truth, independent detection, and quantitative evaluation.

## Prompt

> Implement deterministic injection and independent detection for capacity exceeded, stop omitted, stop duplicated,
> arrival before previous departure, driver shift exceeded, and coordinate outside region. Ground truth must be
> separate and unavailable to detectors. Evaluate per type with precision, recall, F1, and explicit universes. Keep
> injected routes disjoint where practical and avoid accidental cascades that create a second required anomaly.

## Decisions

### Injection

- Work on a deep copy of operational tables; never mutate the clean snapshot.
- Deterministic candidate selection using a SHA-256-derived generator with label `anomaly-injection`.
- Per type:
  - capacity: inflate the first stop's demand to at least capacity + 25;
  - omitted: remove a stop from a multi-stop route while the assignment stays `ASSIGNED`;
  - duplicated: append a copy of a stop on the same route with a new stop ID and cleared actual timestamps;
  - arrival order: move a later stop's actual arrival to 10 minutes before the predecessor's departure;
  - shift: extend an untouched route's actual end 30–90 minutes past the shift end;
  - coordinate: set latitude/longitude to `region.max + margin`.
- Track used routes to keep injections disjoint. Exclude capacity-inflated stops from omission candidates.
- Record every change in `GroundTruthAnomaly` with original/injected values, entity, source table, and notes.
- Assert exact counts per type; raise if there are not enough candidates.

### Detection

- Six separate detectors with stable rule names.
- Inputs are operational tables only: routes, stops, assignments, capacities, shifts, sites. No ground-truth table is
  accepted by the detector API.
- Each detector returns findings plus the universe of entities it could evaluate.
- Capacity recomputes load from stops rather than trusting route aggregates.
- Omission uses assignment status vs. stop presence.
- Duplication counts stops per delivery.
- Arrival order sorts by route/sequence and compares actual arrival to the previous actual departure.
- Shift compares route actual end to shift planned end.
- Coordinates compare to configured region bounds, not global lat/long validity.

### Evaluation

- Match by exact `(issue_type, entity_id)`.
- Compute TP/FP/FN/TN from truth, predictions, and the detector universe.
- Persist precision, recall, F1, `evaluated_universe`, and a plain-language universe note.
- Detection reads the dirty snapshot; evaluation receives ground truth separately.

## Verification

- Starter: 2 instances per type, 12 ground-truth records, 12 detections, precision/recall/F1 = 1.0 for all six.
- Baseline and larger: 3 instances per type, 18 ground truth and 18 detections, all F1 = 1.000.
- Unit tests verify each detector fires on its own injected class and does not fire on the clean snapshot.
- Dirty validation independently flags the injected classes, proving the validation layer and detector layer agree on
  observable evidence.

## Follow-ups

- Cascading `INVALID_FOREIGN_KEY` findings in dirty validation are expected and documented.
- Small configs with too few candidates fail injection loudly; tests use at least 12 sites/60 orders.
