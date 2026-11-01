"""Deterministic execution simulator for planned FleetMind routes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from fleetmind.config import AppConfig
from fleetmind.generation import ids
from fleetmind.generation.factory import Dataset
from fleetmind.models.entities import (
    DeliveryEvent,
    DeliveryOutcome,
    Route,
    RouteEvent,
    RouteLeg,
    RouteStop,
)
from fleetmind.models.enums import (
    AssignmentStatus,
    DeliveryEventType,
    DeliveryStatus,
    FailureReason,
    LocationType,
    OutcomeStatus,
    RouteEventType,
    RouteStatus,
)


class SimulationError(RuntimeError):
    """Raised when planned data cannot be executed consistently."""


@dataclass
class SimulationResult:
    tables: dict[str, pd.DataFrame]
    summary: dict[str, Any] = field(default_factory=dict)
    execution_seconds: float = 0.0


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return pd.Timestamp(value).to_pydatetime()


def _as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return pd.Timestamp(value).date()


def _seconds(value: Any) -> datetime:
    return _as_datetime(value).replace(microsecond=0)


def _add_minutes(moment: datetime, minutes: float) -> datetime:
    """Add a fractional minute, rounded to whole seconds for stable output."""

    return moment + timedelta(seconds=int(round(minutes * 60.0)))


def _stable_rng(seed: int, *labels: str) -> np.random.Generator:
    payload = f"{seed}|{'|'.join(labels)}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _enum_value(value: Any) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _frame(rows: list[Any], model: Any) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(model.model_fields.keys()))
    return pd.DataFrame([row.model_dump(mode="python") for row in rows])


def _parse_sequence(identifier: str) -> int:
    return int(str(identifier).split("-")[-1])


class ExecutionSimulator:
    """Execute every planned route with deterministic operational variance.

    Documented execution rules
    --------------------------
    * Each route receives a dispatch delay drawn uniformly from
      ``[0, simulation.dispatch_delay_max_minutes]``.
    * Every travel leg varies by a factor drawn uniformly from
      ``1 +/- simulation.travel_variance_pct``; distance is unchanged.
    * Service duration varies by ``1 +/- simulation.service_variance_pct``.
    * Service cannot begin before the planned service start, preserving the
      planned customer window and absorbing early arrival as waiting time.
    * A stop fails with ``CUSTOMER_UNAVAILABLE`` when a deterministic draw is
      below ``simulation.customer_unavailable_base_rate``.
    * A stop fails with ``LATE_ARRIVAL`` when actual service starts after the
      hard window end plus ``simulation.late_grace_minutes``. Otherwise it is
      delivered and ``on_time`` compares service start to the window end plus
      ``reporting.on_time_grace_minutes``.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.year = config.id_year

    def simulate(self, dataset: Dataset, plan: dict[str, pd.DataFrame]) -> SimulationResult:
        started = datetime.now()
        tables = dataset.tables
        time_windows = {
            str(row["time_window_id"]): row
            for row in tables["time_windows"].to_dict("records")
        }
        shifts = {
            str(row["shift_id"]): row for row in tables["shifts"].to_dict("records")
        }
        deliveries = {
            str(row["delivery_id"]): row
            for row in tables["deliveries"].to_dict("records")
        }
        routes = {
            str(row["route_id"]): row for row in plan["routes"].to_dict("records")
        }
        stops_by_route: dict[str, list[dict[str, Any]]] = {}
        for row in plan["route_stops"].to_dict("records"):
            stops_by_route.setdefault(str(row["route_id"]), []).append(row)
        for rows in stops_by_route.values():
            rows.sort(key=lambda row: int(row["stop_sequence"]))
        legs_by_route: dict[str, list[dict[str, Any]]] = {}
        for row in plan["route_legs"].to_dict("records"):
            legs_by_route.setdefault(str(row["route_id"]), []).append(row)
        for rows in legs_by_route.values():
            rows.sort(key=lambda row: int(row["leg_sequence"]))
        assignments = {
            str(row["delivery_id"]): row
            for row in plan["delivery_assignments"].to_dict("records")
        }

        if set(stops_by_route) - set(routes):
            raise SimulationError("route stops reference unknown routes")
        if set(legs_by_route) - set(routes):
            raise SimulationError("route legs reference unknown routes")

        simulated_routes: list[dict[str, Any]] = []
        simulated_stops: list[RouteStop] = []
        simulated_legs: list[RouteLeg] = []
        route_events: list[RouteEvent] = []
        delivery_events: list[DeliveryEvent] = []
        outcomes: list[DeliveryOutcome] = []
        outcome_by_delivery: dict[str, DeliveryOutcome] = {}
        completed_routes = 0
        partial_routes = 0
        shift_overruns: list[dict[str, Any]] = []

        for route_id in sorted(routes):
            route_row = routes[route_id]
            route_rng = _stable_rng(self.config.seed, "simulation", route_id)
            route_seq = _parse_sequence(route_id)
            shift = shifts.get(str(route_row["shift_id"]))
            if shift is None:
                raise SimulationError(f"route {route_id} references an unknown shift")
            shift_end = _as_datetime(shift["planned_end"])
            stop_rows = stops_by_route.get(route_id, [])
            leg_rows = legs_by_route.get(route_id, [])
            if not stop_rows:
                raise SimulationError(f"planned route {route_id} has no stops")
            if len(leg_rows) != len(stop_rows) + 1:
                raise SimulationError(
                    f"route {route_id} has {len(leg_rows)} legs for {len(stop_rows)} stops"
                )

            planned_start = _as_datetime(route_row["planned_start"])
            dispatch_delay = int(
                route_rng.integers(0, self.config.simulation.dispatch_delay_max_minutes + 1)
            )
            actual_start = _seconds(_add_minutes(planned_start, dispatch_delay))
            actual_end = actual_start
            delivered_stops = 0
            failed_stops = 0
            actual_wait_total = 0.0
            actual_service_total = 0.0
            route_event_sequence = 1
            route_events.append(
                RouteEvent(
                    run_id=dataset.run_id,
                    event_id=ids.route_event_id(self.year, route_seq, route_event_sequence),
                    route_id=route_id,
                    event_type=RouteEventType.ROUTE_START,
                    event_time=actual_start,
                    planned_time=planned_start,
                    sequence=route_event_sequence,
                    details=f"Dispatch delay {dispatch_delay} minutes.",
                )
            )

            previous_departure = actual_start
            actual_legs: list[RouteLeg] = []
            for stop_index, stop_row in enumerate(stop_rows):
                stop_id = str(stop_row["stop_id"])
                delivery_id = str(stop_row["delivery_id"])
                delivery = deliveries.get(delivery_id)
                if delivery is None:
                    raise SimulationError(f"stop {stop_id} references unknown delivery")
                window = time_windows.get(str(stop_row["time_window_id"]))
                if window is None:
                    raise SimulationError(f"stop {stop_id} references unknown time window")
                leg_row = leg_rows[stop_index]
                planned_travel = float(leg_row["planned_travel_minutes"])
                travel_factor = float(
                    route_rng.uniform(
                        1.0 - self.config.simulation.travel_variance_pct,
                        1.0 + self.config.simulation.travel_variance_pct,
                    )
                )
                actual_travel = max(0.0, planned_travel * travel_factor)
                actual_arrival = _seconds(_add_minutes(previous_departure, actual_travel))
                planned_service_start = _as_datetime(stop_row["planned_service_start"])
                service_start = max(actual_arrival, planned_service_start)

                window_end = _as_datetime(window["window_end"])
                failure_rng = _stable_rng(
                    self.config.seed, "execution-outcome", delivery_id
                )
                unavailable = bool(
                    failure_rng.random()
                    < self.config.simulation.customer_unavailable_base_rate
                )
                late = service_start > _add_minutes(
                    window_end, self.config.simulation.late_grace_minutes
                )
                if unavailable:
                    stop_status = DeliveryStatus.FAILED
                    failure_reason: FailureReason | None = FailureReason.CUSTOMER_UNAVAILABLE
                    service_minutes = float(
                        self.config.simulation.failed_attempt_service_minutes
                    )
                    note = "Customer was unavailable at the service location."
                elif late:
                    stop_status = DeliveryStatus.FAILED
                    failure_reason = FailureReason.LATE_ARRIVAL
                    service_minutes = float(
                        max(1.0, float(stop_row["service_minutes"]))
                    )
                    note = (
                        "Service started after the hard window end plus the late grace "
                        f"period ({self.config.simulation.late_grace_minutes} minutes)."
                    )
                else:
                    stop_status = DeliveryStatus.DELIVERED
                    failure_reason = None
                    service_factor = float(
                        route_rng.uniform(
                            1.0 - self.config.simulation.service_variance_pct,
                            1.0 + self.config.simulation.service_variance_pct,
                        )
                    )
                    service_minutes = max(
                        1.0, float(stop_row["service_minutes"]) * service_factor
                    )
                    note = "Delivered to the customer."
                actual_departure = _seconds(_add_minutes(service_start, service_minutes))
                # Derive dwell from the timestamps that are actually stored so
                # the published durations reconcile exactly with execution
                # times after second-level rounding.
                actual_wait = max(
                    0.0, (service_start - actual_arrival).total_seconds() / 60.0
                )
                actual_service = (
                    actual_departure - service_start
                ).total_seconds() / 60.0
                actual_wait_total += actual_wait
                actual_service_total += actual_service

                simulated_stops.append(
                    RouteStop(
                        run_id=dataset.run_id,
                        stop_id=stop_id,
                        route_id=route_id,
                        stop_sequence=int(stop_row["stop_sequence"]),
                        delivery_id=delivery_id,
                        site_id=str(stop_row["site_id"]),
                        time_window_id=str(stop_row["time_window_id"]),
                        planned_arrival=_as_datetime(stop_row["planned_arrival"]),
                        planned_service_start=planned_service_start,
                        planned_departure=_as_datetime(stop_row["planned_departure"]),
                        actual_arrival=actual_arrival,
                        actual_service_start=service_start,
                        actual_departure=actual_departure,
                        wait_minutes=float(stop_row["wait_minutes"]),
                        service_minutes=float(stop_row["service_minutes"]),
                        actual_wait_minutes=round(actual_wait, 6),
                        actual_service_minutes=round(actual_service, 6),
                        demand_units=int(stop_row["demand_units"]),
                        weight_kg=float(stop_row["weight_kg"]),
                        status=stop_status,
                    )
                )
                actual_legs.append(
                    RouteLeg(
                        run_id=dataset.run_id,
                        leg_id=str(leg_row["leg_id"]),
                        route_id=route_id,
                        leg_sequence=int(leg_row["leg_sequence"]),
                        origin_type=LocationType(leg_row["origin_type"]),
                        origin_id=str(leg_row["origin_id"]),
                        destination_type=LocationType(leg_row["destination_type"]),
                        destination_id=str(leg_row["destination_id"]),
                        planned_departure=_as_datetime(leg_row["planned_departure"]),
                        planned_arrival=_as_datetime(leg_row["planned_arrival"]),
                        planned_distance_km=float(leg_row["planned_distance_km"]),
                        planned_travel_minutes=planned_travel,
                        actual_departure=previous_departure,
                        actual_arrival=actual_arrival,
                        actual_distance_km=float(leg_row["planned_distance_km"]),
                        actual_travel_minutes=actual_travel,
                        scenario_id=str(leg_row["scenario_id"]),
                        traffic_multiplier=float(leg_row["traffic_multiplier"]),
                    )
                )

                route_event_sequence += 1
                route_events.append(
                    RouteEvent(
                        run_id=dataset.run_id,
                        event_id=ids.route_event_id(
                            self.year, route_seq, route_event_sequence
                        ),
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=RouteEventType.ARRIVE_STOP,
                        event_time=actual_arrival,
                        planned_time=_as_datetime(stop_row["planned_arrival"]),
                        sequence=route_event_sequence,
                        details=f"Arrived at stop {stop_row['stop_sequence']}.",
                    )
                )
                if actual_wait > 0:
                    route_event_sequence += 1
                    route_events.append(
                        RouteEvent(
                            run_id=dataset.run_id,
                            event_id=ids.route_event_id(
                                self.year, route_seq, route_event_sequence
                            ),
                            route_id=route_id,
                            stop_id=stop_id,
                            event_type=RouteEventType.WAIT_START,
                            event_time=actual_arrival,
                            planned_time=_as_datetime(stop_row["planned_arrival"]),
                            sequence=route_event_sequence,
                            details=f"Waiting {actual_wait:.1f} minutes before service.",
                        )
                    )
                    route_event_sequence += 1
                    route_events.append(
                        RouteEvent(
                            run_id=dataset.run_id,
                            event_id=ids.route_event_id(
                                self.year, route_seq, route_event_sequence
                            ),
                            route_id=route_id,
                            stop_id=stop_id,
                            event_type=RouteEventType.WAIT_END,
                            event_time=service_start,
                            planned_time=planned_service_start,
                            sequence=route_event_sequence,
                            details="Waiting period complete.",
                        )
                    )
                route_event_sequence += 1
                route_events.append(
                    RouteEvent(
                        run_id=dataset.run_id,
                        event_id=ids.route_event_id(
                            self.year, route_seq, route_event_sequence
                        ),
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=RouteEventType.SERVICE_START,
                        event_time=service_start,
                        planned_time=planned_service_start,
                        sequence=route_event_sequence,
                        details=note,
                    )
                )
                route_event_sequence += 1
                route_events.append(
                    RouteEvent(
                        run_id=dataset.run_id,
                        event_id=ids.route_event_id(
                            self.year, route_seq, route_event_sequence
                        ),
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=RouteEventType.SERVICE_END,
                        event_time=actual_departure,
                        planned_time=_as_datetime(stop_row["planned_departure"]),
                        sequence=route_event_sequence,
                        details=f"Stop marked {stop_status.value}.",
                    )
                )

                delivery_sequence = _parse_sequence(delivery_id)
                delivery_event_sequence = 1
                delivery_events.append(
                    DeliveryEvent(
                        run_id=dataset.run_id,
                        event_id=ids.delivery_event_id(
                            self.year, delivery_sequence, delivery_event_sequence
                        ),
                        delivery_id=delivery_id,
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=DeliveryEventType.ASSIGNED,
                        event_time=actual_start,
                        planned_time=planned_start,
                        sequence=delivery_event_sequence,
                        details=f"Assigned to route {route_id}.",
                    )
                )
                delivery_event_sequence += 1
                delivery_events.append(
                    DeliveryEvent(
                        run_id=dataset.run_id,
                        event_id=ids.delivery_event_id(
                            self.year, delivery_sequence, delivery_event_sequence
                        ),
                        delivery_id=delivery_id,
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=DeliveryEventType.DISPATCHED,
                        event_time=actual_start,
                        planned_time=planned_start,
                        sequence=delivery_event_sequence,
                        details=f"Vehicle departed on route {route_id}.",
                    )
                )
                delivery_event_sequence += 1
                delivery_events.append(
                    DeliveryEvent(
                        run_id=dataset.run_id,
                        event_id=ids.delivery_event_id(
                            self.year, delivery_sequence, delivery_event_sequence
                        ),
                        delivery_id=delivery_id,
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=DeliveryEventType.ARRIVED,
                        event_time=actual_arrival,
                        planned_time=_as_datetime(stop_row["planned_arrival"]),
                        sequence=delivery_event_sequence,
                        details="Vehicle arrived at the customer site.",
                    )
                )
                delivery_event_sequence += 1
                delivery_events.append(
                    DeliveryEvent(
                        run_id=dataset.run_id,
                        event_id=ids.delivery_event_id(
                            self.year, delivery_sequence, delivery_event_sequence
                        ),
                        delivery_id=delivery_id,
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=DeliveryEventType.SERVICE_STARTED,
                        event_time=service_start,
                        planned_time=planned_service_start,
                        sequence=delivery_event_sequence,
                        details="Service began at the customer site.",
                    )
                )

                on_time: bool | None = None
                if stop_status == DeliveryStatus.DELIVERED:
                    on_time = service_start <= _add_minutes(
                        window_end, self.config.reporting.on_time_grace_minutes
                    )
                    delivered_stops += 1
                    delivery_event_type = DeliveryEventType.DELIVERED
                else:
                    failed_stops += 1
                    delivery_event_type = DeliveryEventType.FAILED
                delivery_event_sequence += 1
                delivery_events.append(
                    DeliveryEvent(
                        run_id=dataset.run_id,
                        event_id=ids.delivery_event_id(
                            self.year, delivery_sequence, delivery_event_sequence
                        ),
                        delivery_id=delivery_id,
                        route_id=route_id,
                        stop_id=stop_id,
                        event_type=delivery_event_type,
                        event_time=actual_departure,
                        planned_time=_as_datetime(stop_row["planned_departure"]),
                        sequence=delivery_event_sequence,
                        details=note,
                    )
                )

                outcome = DeliveryOutcome(
                    run_id=dataset.run_id,
                    outcome_id=ids.outcome_id(self.year, delivery_sequence),
                    delivery_id=delivery_id,
                    route_id=route_id,
                    stop_id=stop_id,
                    status=(
                        OutcomeStatus.DELIVERED
                        if stop_status == DeliveryStatus.DELIVERED
                        else OutcomeStatus.FAILED
                    ),
                    reason_code=(
                        failure_reason.value if failure_reason is not None else None
                    ),
                    actual_arrival=actual_arrival,
                    service_start=service_start,
                    service_end=actual_departure,
                    on_time=on_time,
                    proof_of_delivery=(
                        f"POD-{self.year}-{delivery_sequence:06d}"
                        if self.config.simulation.proof_of_delivery
                        and stop_status == DeliveryStatus.DELIVERED
                        else None
                    ),
                    notes=note,
                    recorded_at=actual_departure,
                )
                outcomes.append(outcome)
                outcome_by_delivery[delivery_id] = outcome

                # Advance the vehicle along the stop chain.
                previous_departure = actual_departure
                actual_end = actual_departure

            # Return leg to the depot.
            return_leg = leg_rows[-1]
            return_factor = float(
                route_rng.uniform(
                    1.0 - self.config.simulation.travel_variance_pct,
                    1.0 + self.config.simulation.travel_variance_pct,
                )
            )
            return_travel = max(0.0, float(return_leg["planned_travel_minutes"]) * return_factor)
            return_arrival = _seconds(_add_minutes(previous_departure, return_travel))
            actual_end = return_arrival
            actual_legs.append(
                RouteLeg(
                    run_id=dataset.run_id,
                    leg_id=str(return_leg["leg_id"]),
                    route_id=route_id,
                    leg_sequence=int(return_leg["leg_sequence"]),
                    origin_type=LocationType(return_leg["origin_type"]),
                    origin_id=str(return_leg["origin_id"]),
                    destination_type=LocationType(return_leg["destination_type"]),
                    destination_id=str(return_leg["destination_id"]),
                    planned_departure=_as_datetime(return_leg["planned_departure"]),
                    planned_arrival=_as_datetime(return_leg["planned_arrival"]),
                    planned_distance_km=float(return_leg["planned_distance_km"]),
                    planned_travel_minutes=float(return_leg["planned_travel_minutes"]),
                    actual_departure=previous_departure,
                    actual_arrival=return_arrival,
                    actual_distance_km=float(return_leg["planned_distance_km"]),
                    actual_travel_minutes=return_travel,
                    scenario_id=str(return_leg["scenario_id"]),
                    traffic_multiplier=float(return_leg["traffic_multiplier"]),
                )
            )
            simulated_legs.extend(actual_legs)

            route_event_sequence += 1
            route_events.append(
                RouteEvent(
                    run_id=dataset.run_id,
                    event_id=ids.route_event_id(self.year, route_seq, route_event_sequence),
                    route_id=route_id,
                    event_type=RouteEventType.ROUTE_RETURN,
                    event_time=return_arrival,
                    planned_time=_as_datetime(route_row["planned_end"]),
                    sequence=route_event_sequence,
                    details="Vehicle returned to the depot.",
                )
            )
            route_event_sequence += 1
            route_events.append(
                RouteEvent(
                    run_id=dataset.run_id,
                    event_id=ids.route_event_id(self.year, route_seq, route_event_sequence),
                    route_id=route_id,
                    event_type=RouteEventType.ROUTE_END,
                    event_time=return_arrival,
                    planned_time=_as_datetime(route_row["planned_end"]),
                    sequence=route_event_sequence,
                    details="Route execution closed.",
                )
            )

            overrun_minutes = max(
                0.0, (return_arrival - shift_end).total_seconds() / 60.0
            )
            if overrun_minutes > 0:
                shift_overruns.append(
                    {
                        "route_id": route_id,
                        "shift_id": str(route_row["shift_id"]),
                        "overrun_minutes": round(overrun_minutes, 2),
                    }
                )
            route_status = (
                RouteStatus.COMPLETED if failed_stops == 0 else RouteStatus.PARTIAL
            )
            if route_status == RouteStatus.COMPLETED:
                completed_routes += 1
            else:
                partial_routes += 1
            simulated_routes.append(
                {
                    **route_row,
                    "status": route_status,
                    "actual_start": actual_start,
                    "actual_end": return_arrival,
                    "delivered_stops": delivered_stops,
                    "failed_stops": failed_stops,
                    "actual_wait_minutes": round(actual_wait_total, 6),
                    "actual_service_minutes": round(actual_service_total, 6),
                    "shift_overrun_minutes": round(overrun_minutes, 2),
                }
            )

        # Unserved deliveries receive an explicit outcome and event.
        unserved_deliveries = 0
        for delivery_id in sorted(assignments):
            assignment = assignments[delivery_id]
            if _enum_value(assignment["status"]) != AssignmentStatus.UNSERVED.value:
                continue
            if delivery_id in outcome_by_delivery:
                continue
            delivery = deliveries[delivery_id]
            service_date = _as_date(delivery["service_date"])
            recorded_at = datetime.combine(service_date, dtime(18, 0))
            delivery_sequence = _parse_sequence(delivery_id)
            reason_code = str(assignment["reason_code"] or "OPTIMIZER_TRADEOFF")
            outcome = DeliveryOutcome(
                run_id=dataset.run_id,
                outcome_id=ids.outcome_id(self.year, delivery_sequence),
                delivery_id=delivery_id,
                route_id=None,
                stop_id=None,
                status=OutcomeStatus.UNSERVED,
                reason_code=reason_code,
                actual_arrival=None,
                service_start=None,
                service_end=None,
                on_time=None,
                proof_of_delivery=None,
                notes=str(assignment["explanation"]),
                recorded_at=recorded_at,
            )
            outcomes.append(outcome)
            outcome_by_delivery[delivery_id] = outcome
            delivery_events.append(
                DeliveryEvent(
                    run_id=dataset.run_id,
                    event_id=ids.delivery_event_id(self.year, delivery_sequence, 1),
                    delivery_id=delivery_id,
                    route_id=None,
                    stop_id=None,
                    event_type=DeliveryEventType.UNSERVED,
                    event_time=recorded_at,
                    planned_time=None,
                    sequence=1,
                    details=f"Delivery unserved: {reason_code}.",
                )
            )
            unserved_deliveries += 1

        missing = set(deliveries) - set(outcome_by_delivery)
        if missing:
            raise SimulationError(
                f"{len(missing)} deliveries have neither an assignment nor an outcome"
            )

        # Update delivery, order and route lifecycle statuses.
        status_by_delivery = {
            delivery_id: (
                DeliveryStatus.DELIVERED
                if outcome.status == OutcomeStatus.DELIVERED
                else DeliveryStatus.FAILED
                if outcome.status == OutcomeStatus.FAILED
                else DeliveryStatus.UNSERVED
            )
            for delivery_id, outcome in outcome_by_delivery.items()
        }
        updated_deliveries = tables["deliveries"].copy()
        updated_deliveries["status"] = updated_deliveries["delivery_id"].map(
            lambda value: status_by_delivery[str(value)]
        )
        if updated_deliveries["status"].isna().any():
            raise SimulationError("delivery lifecycle update produced a null status")

        updated_orders = tables["orders"].copy()
        if "order_id" in updated_orders.columns:
            order_status_by_id: dict[str, str] = {}
            order_id_by_delivery = {
                str(row["delivery_id"]): str(row["order_id"])
                for row in tables["deliveries"].to_dict("records")
            }
            for delivery_id, status in status_by_delivery.items():
                order_id = order_id_by_delivery.get(delivery_id)
                if order_id:
                    order_status_by_id[order_id] = status.value
            updated_orders["status"] = updated_orders["order_id"].map(
                lambda value: order_status_by_id.get(str(value), "CONFIRMED")
            )

        routed_deliveries = len(outcomes) - unserved_deliveries
        delivered_count = sum(
            1 for outcome in outcomes if outcome.status == OutcomeStatus.DELIVERED
        )
        failed_count = sum(
            1 for outcome in outcomes if outcome.status == OutcomeStatus.FAILED
        )
        on_time_count = sum(
            1
            for outcome in outcomes
            if outcome.status == OutcomeStatus.DELIVERED and outcome.on_time
        )
        summary = {
            "routes_executed": len(simulated_routes),
            "completed_routes": completed_routes,
            "partial_routes": partial_routes,
            "deliveries": len(outcomes),
            "routed_deliveries": routed_deliveries,
            "delivered": delivered_count,
            "failed": failed_count,
            "unserved": unserved_deliveries,
            "on_time_deliveries": on_time_count,
            "on_time_rate": round(
                on_time_count / delivered_count if delivered_count else 0.0, 6
            ),
            "failure_rate": round(
                failed_count / routed_deliveries if routed_deliveries else 0.0, 6
            ),
            "shift_overruns": shift_overruns,
            "shift_overrun_count": len(shift_overruns),
            "actual_distance_km": round(
                sum(leg.actual_distance_km or 0.0 for leg in simulated_legs), 3
            ),
            "actual_travel_minutes": round(
                sum(leg.actual_travel_minutes or 0.0 for leg in simulated_legs), 2
            ),
        }

        simulation_tables = {
            "routes": (
                pd.DataFrame(simulated_routes)
                if simulated_routes
                else pd.DataFrame(columns=list(Route.model_fields.keys()))
            ),
            "route_stops": _frame(simulated_stops, RouteStop),
            "route_legs": _frame(simulated_legs, RouteLeg),
            "delivery_outcomes": _frame(outcomes, DeliveryOutcome),
            "route_events": _frame(route_events, RouteEvent),
            "delivery_events": _frame(delivery_events, DeliveryEvent),
            "deliveries": updated_deliveries,
            "orders": updated_orders,
        }
        execution_seconds = (datetime.now() - started).total_seconds()
        summary["execution_seconds"] = round(execution_seconds, 4)
        return SimulationResult(
            tables=simulation_tables,
            summary=summary,
            execution_seconds=execution_seconds,
        )


def simulate_execution(
    dataset: Dataset, plan: dict[str, pd.DataFrame], config: AppConfig
) -> SimulationResult:
    return ExecutionSimulator(config).simulate(dataset, plan)
