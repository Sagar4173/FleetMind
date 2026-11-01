# 04 — Routing and simulation

> **Supplementary normalized summary.** This file groups the work by theme for readability. It is not a verbatim transcript. The user-authored prompt text is preserved in [`verbatim/opencode-prompts.json`](verbatim/opencode-prompts.json) and the Markdown renderings under [`verbatim/opencode/`](verbatim/opencode/). Figures quoted here describe the milestone at the time it was written; the final evidence is recorded in [`../docs/verification.md`](../docs/verification.md).

## Context

The dataset and travel model existed. The next requirement was hard-constrained route planning followed by a
deterministic execution simulation with a complete event ledger.

## Prompt

> Implement per-day OR-Tools planning that never silently violates capacity, time windows, shift limits, site access,
> equipment, certification, or license rules. Record unserved deliveries with reasons. Then simulate execution with
> realistic variance and emit route/delivery events and outcomes. Keep both stages deterministic.

## Decisions

### Planning

- One OR-Tools model per service date, because shifts and windows are date-specific.
- Candidate shifts require: `SCHEDULED` status, active vehicle, active driver, capacity record, positive effective
  window. Effective end reserves `break_minutes` and `execution_buffer_minutes`, and is capped by
  `max_route_duration_min`.
- Delivery eligibility checks, in order:
  1. vehicle equipment contains the delivery's required equipment;
  2. driver license class equals the vehicle's required license class;
  3. site access class permits the vehicle type;
  4. cold-chain deliveries require a `COLD_CHAIN` certification;
  5. bulk deliveries without a dock require both lift-gate equipment and certification;
  6. individual unit and weight demand fit at least one eligible vehicle;
  7. a depot-out → service → depot-return timeline intersects the delivery window.
- If eligibility fails, the delivery is marked `UNSERVED` with a reason: `NO_ELIGIBLE_VEHICLE`,
  `CAPACITY_EXCEEDS_FLEET`, `OUTSIDE_SHIFT`, or `TIME_WINDOW_INFEASIBLE`. Otherwise OR-Tools may still drop it as an
  `OPTIMIZER_TRADEOFF`.
- Constraints: unit capacity and weight capacity dimensions, stop-count dimension (`max_stops_per_route`), time
  dimension with per-vehicle shift start/end and span cap, hard node time windows, and per-delivery allowed-vehicle
  domains.
- Objective: distance cost, fixed vehicle cost (`80000`), and priority-weighted unserved penalties (`LOW=0.7`,
  `STANDARD=1.0`, `HIGH=1.6`, `URGENT=2.5`).
- Search: `PATH_CHEAPEST_ARC` first solution, `GUIDED_LOCAL_SEARCH` metaheuristic, per-day wall-clock limit.
- Post-solve defensive checks re-verify unit and weight capacity and raise `RoutingError` if violated.
- Every requested delivery gets exactly one `delivery_assignments` row. Unserved rows carry a reason code and an
  explanation.
- `solver_runs` records one row per service date, including status, objective, seconds, assigned/unserved counts, and
  vehicle count.

### Simulation

- Execute every planned route in stop-sequence order.
- Per-route dispatch delay uniform in `[0, dispatch_delay_max_minutes]`.
- Per-leg travel multiplier in `1 ± travel_variance_pct`; distance unchanged.
- Per-stop service multiplier in `1 ± service_variance_pct`.
- Service cannot start before planned service start; early arrival becomes waiting time.
- Customer-unavailable failure on a deterministic draw below `customer_unavailable_base_rate`.
- `LATE_ARRIVAL` when service starts after window end plus `simulation.late_grace_minutes`.
- On-time compares service start to window end plus `reporting.on_time_grace_minutes`.
- Emit `DeliveryOutcome` per requested delivery, `RouteEvent` and `DeliveryEvent` ledgers with deterministic event IDs
  and sequences, and executed route/stop/leg tables.
- Update route status, delivered/failed counts, actual start/end, and shift overrun.

### Determinism

- Simulation randomness is derived from stable labels, not from a shared global RNG.
- Fractional minutes are converted to whole seconds to avoid timestamp drift.
- Route and event IDs are built from deterministic sequences, not UUIDs.

## Verification

- Starter clean pipeline: 25 routes, 118 assigned, 2 unserved, 116 delivered, 2 failed, 0 clean validation issues.
- Baseline: 296 routes, 2,000 assigned, 0 unserved, 1,991 delivered, 9 failed.
- All planned stops passed hard capacity, window, and shift validation.
- Simulation produced route and delivery events with monotonic sequences and consistent outcomes.
- Integration tests cover plane assignments, unserved reasons, event ordering, and deterministic reruns.

## Follow-ups

- The OR-Tools wheel does not accept lists in `SetAllowedVehiclesForIndex`; the vehicle-variable domain workaround is
  documented in the optimizer.
- Solver time limits dominate runtime at baseline scale; measured evidence is in `docs/benchmark_methodology.md`.
