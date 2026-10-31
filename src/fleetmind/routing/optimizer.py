"""OR-Tools vehicle-routing planner with hard capacity, shift and window rules."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from fleetmind.config import AppConfig
from fleetmind.generation import ids
from fleetmind.generation.factory import Dataset
from fleetmind.generation.travel import TravelTimeModel
from fleetmind.models.entities import (
    DeliveryAssignment,
    Route,
    RouteLeg,
    RouteStop,
    ScenarioConfiguration,
    SolverRun,
)
from fleetmind.models.enums import (
    AssignmentStatus,
    DeliveryStatus,
    LocationType,
    RouteStatus,
    SiteAccessClass,
    SolverStatus,
    UnservedReason,
    VehicleStatus,
)


class RoutingError(RuntimeError):
    """Raised when planning inputs cannot produce a traceable plan."""


# OR-Tools exposes the routing status enum only through the C++ binding, which
# this wheel does not export as Python constants. Values follow the public
# operations_research::RoutingModel::Status enum.
_ROUTING_SUCCESS = 1
_ROUTING_PARTIAL_SUCCESS = 2
_ROUTING_FAIL = 3
_ROUTING_FAIL_TIMEOUT = 4
_ROUTING_INVALID = 5
_ROUTING_INFEASIBLE = 6
_ROUTING_OPTIMAL = 7


@dataclass
class ShiftCandidate:
    index: int
    shift_id: str
    driver_id: str
    vehicle_id: str
    start: datetime
    end: datetime
    effective_end: datetime
    break_minutes: int
    capacity_units: int
    max_weight_kg: float
    vehicle_type: str
    required_equipment: set[str]
    certifications: set[str]
    license_class: str
    required_license_class: str
    max_route_duration_min: int


@dataclass
class PlanResult:
    tables: dict[str, pd.DataFrame]
    summary: dict[str, Any] = field(default_factory=dict)
    unserved_reasons: dict[str, str] = field(default_factory=dict)
    solve_seconds: float = 0.0


def _s(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return pd.Timestamp(value).date()


def _empty_frame(model: Any) -> pd.DataFrame:
    return pd.DataFrame(columns=list(model.model_fields.keys()))


def _scenario_from_row(row: dict[str, Any]) -> ScenarioConfiguration:
    cleaned = dict(row)
    cleaned["scenario_date"] = _date(row["scenario_date"])
    return ScenarioConfiguration.model_validate(cleaned)


class RoutePlanner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.year = config.id_year

    def plan(self, dataset: Dataset) -> PlanResult:
        started = time.perf_counter()
        tables = dataset.tables
        scenarios = {
            _date(row["scenario_date"]): _scenario_from_row(row)
            for row in tables["travel_scenarios"].to_dict("records")
        }
        depot = tables["depots"].iloc[0].to_dict()
        sites = {
            row["site_id"]: row for row in tables["customer_sites"].to_dict("records")
        }
        vehicles = {
            row["vehicle_id"]: row for row in tables["vehicles"].to_dict("records")
        }
        capacities = {
            row["vehicle_id"]: row
            for row in tables["vehicle_capacities"].to_dict("records")
        }
        equipment: dict[str, set[str]] = {}
        for row in tables["vehicle_equipment"].to_dict("records"):
            equipment.setdefault(str(row["vehicle_id"]), set()).add(_s(row["equipment"]))
        drivers = {
            row["driver_id"]: row for row in tables["drivers"].to_dict("records")
        }
        certifications: dict[str, set[str]] = {}
        for row in tables["driver_certifications"].to_dict("records"):
            certifications.setdefault(str(row["driver_id"]), set()).add(
                _s(row["certification"])
            )
        time_windows = {
            row["time_window_id"]: row
            for row in tables["time_windows"].to_dict("records")
        }
        delivery_rows = tables["deliveries"].to_dict("records")
        deliveries_by_date: dict[date, list[dict[str, Any]]] = {}
        for row in delivery_rows:
            deliveries_by_date.setdefault(_date(row["service_date"]), []).append(row)
        shifts = tables["shifts"].to_dict("records")
        shifts_by_date: dict[date, list[dict[str, Any]]] = {}
        for row in shifts:
            shifts_by_date.setdefault(_date(row["service_date"]), []).append(row)

        travel_model = TravelTimeModel(
            locations={
                str(depot["depot_id"]): (float(depot["latitude"]), float(depot["longitude"]))
            }
            | {
                str(row["site_id"]): (float(row["latitude"]), float(row["longitude"]))
                for row in sites.values()
            },
            depot_id=str(depot["depot_id"]),
        )
        depot_id = str(depot["depot_id"])
        routes: list[Route] = []
        stops: list[RouteStop] = []
        legs: list[RouteLeg] = []
        solver_runs: list[SolverRun] = []
        assigned_stop_by_delivery: dict[str, dict[str, Any]] = {}
        unserved_reason: dict[str, str] = {}
        unserved_explanation: dict[str, str] = {}
        route_seq = 0
        solve_seq = 0
        used_vehicles: set[str] = set()
        solver_status_counts: dict[str, int] = {}
        planning_time_by_day: dict[str, float] = {}

        for day_index, service_date in enumerate(self.config.service_dates):
            day_deliveries = sorted(
                deliveries_by_date.get(service_date, []),
                key=lambda row: str(row["delivery_id"]),
            )
            if not day_deliveries:
                continue
            scenario = scenarios.get(service_date)
            if scenario is None:
                raise RoutingError(f"no travel scenario for service date {service_date}")
            candidates = self._build_candidates(
                service_date,
                shifts_by_date.get(service_date, []),
                vehicles,
                drivers,
                capacities,
                equipment,
                certifications,
            )
            candidate_by_shift = {candidate.shift_id: candidate for candidate in candidates}
            serviceable: list[dict[str, Any]] = []
            allowed_by_delivery: dict[str, list[int]] = {}
            for delivery in day_deliveries:
                delivery_id = str(delivery["delivery_id"])
                site = sites[str(delivery["site_id"])]
                window = time_windows[str(delivery["time_window_id"])]
                allowed, reason = self._eligibility(
                    delivery=delivery,
                    site=site,
                    window=window,
                    candidates=candidates,
                    travel_model=travel_model,
                    scenario=scenario,
                    depot_id=depot_id,
                )
                if allowed is None:
                    unserved_reason[delivery_id] = reason.value
                    unserved_explanation[delivery_id] = self._reason_text(reason, candidates)
                else:
                    serviceable.append(delivery)
                    allowed_by_delivery[delivery_id] = [candidate.index for candidate in allowed]

            solve_started = time.perf_counter()
            day_routes: list[dict[str, Any]] = []
            solver_status = SolverStatus.UNKNOWN
            objective_value: float | None = None
            solver_message = ""
            if serviceable and candidates:
                day_routes, solver_status, objective_value, solver_message = self._solve_day(
                    service_date=service_date,
                    scenario=scenario,
                    deliveries=serviceable,
                    allowed_by_delivery=allowed_by_delivery,
                    candidates=candidates,
                    candidate_by_shift=candidate_by_shift,
                    sites=sites,
                    time_windows=time_windows,
                    travel_model=travel_model,
                    depot_id=depot_id,
                )
            elif serviceable:
                solver_status = SolverStatus.INFEASIBLE
                solver_message = "no active shift candidates available"
            else:
                solver_status = SolverStatus.UNKNOWN
                solver_message = "all deliveries rejected by deterministic pre-checks"
            solve_seconds = time.perf_counter() - solve_started
            planning_time_by_day[service_date.isoformat()] = round(solve_seconds, 4)
            solver_status_counts[solver_status.value] = (
                solver_status_counts.get(solver_status.value, 0) + 1
            )

            assigned_today = {
                delivery_id
                for route in day_routes
                for delivery_id in route["delivery_ids"]
            }
            dropped_reason: UnservedReason | None = None
            if solver_status == SolverStatus.TIMEOUT:
                dropped_reason = UnservedReason.SOLVER_TIMEOUT
            elif solver_status == SolverStatus.INFEASIBLE:
                dropped_reason = UnservedReason.SOLVER_INFEASIBLE
            elif solver_status in {SolverStatus.UNKNOWN}:
                dropped_reason = UnservedReason.SOLVER_UNKNOWN
            for delivery in serviceable:
                delivery_id = str(delivery["delivery_id"])
                if delivery_id in assigned_today:
                    continue
                reason = dropped_reason or UnservedReason.OPTIMIZER_TRADEOFF
                unserved_reason[delivery_id] = reason.value
                unserved_explanation[delivery_id] = (
                    f"eligible fleet existed but solver status {solver_status.value}; "
                    f"delivery was not assigned. Solver message: {solver_message or 'none'}"
                )

            for route_payload in day_routes:
                route_seq += 1
                route_key = ids.route_id(self.year, route_seq)
                draft_stops = route_payload["stops"]
                route_load_units = sum(int(stop["demand_units"]) for stop in draft_stops)
                route_load_weight = sum(float(stop["weight_kg"]) for stop in draft_stops)
                candidate: ShiftCandidate = route_payload["candidate"]
                if route_load_units > candidate.capacity_units + 1e-9:
                    raise RoutingError(
                        f"route {route_key} violates unit capacity: {route_load_units} > {candidate.capacity_units}"
                    )
                if route_load_weight > candidate.max_weight_kg + 1e-9:
                    raise RoutingError(
                        f"route {route_key} violates weight capacity: {route_load_weight} > {candidate.max_weight_kg}"
                    )
                if route_payload["planned_end"] > candidate.effective_end:
                    raise RoutingError(
                        f"route {route_key} ends after effective shift end"
                    )
                stops_models: list[RouteStop] = []
                for stop_sequence, stop_payload in enumerate(draft_stops, start=1):
                    stop_key = ids.stop_id(self.year, route_seq, stop_sequence)
                    stop_model = RouteStop(
                        run_id=dataset.run_id,
                        stop_id=stop_key,
                        route_id=route_key,
                        stop_sequence=stop_sequence,
                        delivery_id=str(stop_payload["delivery_id"]),
                        site_id=str(stop_payload["site_id"]),
                        time_window_id=str(stop_payload["time_window_id"]),
                        planned_arrival=stop_payload["planned_arrival"],
                        planned_service_start=stop_payload["planned_service_start"],
                        planned_departure=stop_payload["planned_departure"],
                        wait_minutes=float(stop_payload["wait_minutes"]),
                        service_minutes=float(stop_payload["service_minutes"]),
                        demand_units=int(stop_payload["demand_units"]),
                        weight_kg=float(stop_payload["weight_kg"]),
                        status=DeliveryStatus.ASSIGNED,
                    )
                    stops_models.append(stop_model)
                    assigned_stop_by_delivery[stop_model.delivery_id] = {
                        "route_id": route_key,
                        "stop_id": stop_key,
                        "stop_sequence": stop_sequence,
                        "planned_arrival": stop_model.planned_arrival,
                        "planned_service_start": stop_model.planned_service_start,
                        "planned_departure": stop_model.planned_departure,
                        "vehicle_id": candidate.vehicle_id,
                        "driver_id": candidate.driver_id,
                        "shift_id": candidate.shift_id,
                    }
                used_vehicles.add(candidate.vehicle_id)
                route_model = Route(
                    run_id=dataset.run_id,
                    route_id=route_key,
                    scenario_id=scenario.scenario_id,
                    service_date=service_date,
                    depot_id=depot_id,
                    vehicle_id=candidate.vehicle_id,
                    driver_id=candidate.driver_id,
                    shift_id=candidate.shift_id,
                    status=RouteStatus.PLANNED,
                    planned_start=route_payload["planned_start"],
                    planned_end=route_payload["planned_end"],
                    stop_count=len(stops_models),
                    delivered_stops=0,
                    failed_stops=0,
                    load_units=route_load_units,
                    capacity_units=candidate.capacity_units,
                    load_weight_kg=round(route_load_weight, 3),
                    capacity_weight_kg=candidate.max_weight_kg,
                    distance_km=round(route_payload["distance_km"], 3),
                    travel_minutes=round(route_payload["travel_minutes"], 2),
                    service_minutes=round(route_payload["service_minutes"], 2),
                    wait_minutes=round(route_payload["wait_minutes"], 2),
                    solver_status=solver_status,
                    objective_value=objective_value,
                    solve_seconds=round(solve_seconds, 4),
                    shift_overrun_minutes=0,
                    created_at=datetime.combine(service_date, dtime(0, 0)),
                )
                routes.append(route_model)
                stops.extend(stops_models)
                for leg_payload in route_payload["legs"]:
                    legs.append(
                        RouteLeg(
                            run_id=dataset.run_id,
                            leg_id=ids.leg_id(self.year, route_seq, int(leg_payload["leg_sequence"])),
                            route_id=route_key,
                            leg_sequence=int(leg_payload["leg_sequence"]),
                            origin_type=leg_payload["origin_type"],
                            origin_id=str(leg_payload["origin_id"]),
                            destination_type=leg_payload["destination_type"],
                            destination_id=str(leg_payload["destination_id"]),
                            planned_departure=leg_payload["planned_departure"],
                            planned_arrival=leg_payload["planned_arrival"],
                            planned_distance_km=round(float(leg_payload["planned_distance_km"]), 3),
                            planned_travel_minutes=round(
                                float(leg_payload["planned_travel_minutes"]), 2
                            ),
                            scenario_id=scenario.scenario_id,
                            traffic_multiplier=round(
                                travel_model.planning_multiplier(scenario), 4
                            ),
                        )
                    )

            solve_seq += 1
            solver_runs.append(
                SolverRun(
                    run_id=dataset.run_id,
                    solve_id=ids.solve_id(self.year, day_index + 1),
                    service_date=service_date,
                    status=solver_status,
                    objective_value=objective_value,
                    solve_seconds=round(solve_seconds, 4),
                    assigned_count=sum(len(route["delivery_ids"]) for route in day_routes),
                    unserved_count=len(day_deliveries)
                    - sum(len(route["delivery_ids"]) for route in day_routes),
                    vehicle_count=len(day_routes),
                    message=solver_message,
                )
            )

        assignments: list[DeliveryAssignment] = []
        for delivery in sorted(delivery_rows, key=lambda row: str(row["delivery_id"])):
            delivery_id = str(delivery["delivery_id"])
            sequence = int(delivery_id.split("-")[-1])
            service_date = _date(delivery["service_date"])
            timestamp = datetime.combine(service_date, dtime(0, 0))
            match = assigned_stop_by_delivery.get(delivery_id)
            if match:
                assignments.append(
                    DeliveryAssignment(
                        run_id=dataset.run_id,
                        assignment_id=ids.assignment_id(self.year, sequence),
                        delivery_id=delivery_id,
                        route_id=str(match["route_id"]),
                        stop_id=str(match["stop_id"]),
                        service_date=service_date,
                        status=AssignmentStatus.ASSIGNED,
                        reason_code=None,
                        explanation=(
                            f"Assigned to vehicle {match['vehicle_id']} (driver {match['driver_id']}, "
                            f"shift {match['shift_id']}) as stop {match['stop_sequence']}; "
                            f"planned service start {match['planned_service_start'].isoformat()}."
                        ),
                        created_at=timestamp,
                    )
                )
            else:
                reason_value = unserved_reason.get(delivery_id, UnservedReason.OPTIMIZER_TRADEOFF.value)
                assignments.append(
                    DeliveryAssignment(
                        run_id=dataset.run_id,
                        assignment_id=ids.assignment_id(self.year, sequence),
                        delivery_id=delivery_id,
                        route_id=None,
                        stop_id=None,
                        service_date=service_date,
                        status=AssignmentStatus.UNSERVED,
                        reason_code=reason_value,
                        explanation=unserved_explanation.get(
                            delivery_id,
                            "Delivery was not assigned by the routing model.",
                        ),
                        created_at=timestamp,
                    )
                )

        plan_tables = {
            "solver_runs": self._frame(solver_runs, SolverRun),
            "routes": self._frame(routes, Route),
            "route_stops": self._frame(stops, RouteStop),
            "route_legs": self._frame(legs, RouteLeg),
            "delivery_assignments": self._frame(assignments, DeliveryAssignment),
        }
        assigned_count = len(stops)
        unserved_count = len(delivery_rows) - assigned_count
        reason_counts: dict[str, int] = {}
        for value in unserved_reason.values():
            reason_counts[value] = reason_counts.get(value, 0) + 1
        # Deliveries pre-rejected but not in unserved_reason are not possible; still
        # reconcile defensively.
        if unserved_count != sum(reason_counts.values()):
            raise RoutingError(
                "unserved delivery reconciliation failed between routes and reason codes"
            )
        summary = {
            "service_days": len(planning_time_by_day),
            "routes": len(routes),
            "deliveries": len(delivery_rows),
            "assigned_deliveries": assigned_count,
            "unserved_deliveries": unserved_count,
            "used_vehicles": len(used_vehicles),
            "solver_status_counts": solver_status_counts,
            "unserved_reason_counts": reason_counts,
            "optimization_seconds": round(time.perf_counter() - started, 4),
            "planning_seconds_by_day": planning_time_by_day,
        }
        return PlanResult(
            tables=plan_tables,
            summary=summary,
            unserved_reasons=unserved_reason,
            solve_seconds=summary["optimization_seconds"],
        )

    @staticmethod
    def _frame(rows: list[Any], model: Any) -> pd.DataFrame:
        if not rows:
            return _empty_frame(model)
        return pd.DataFrame([row.model_dump(mode="python") for row in rows])

    def _build_candidates(
        self,
        service_date: date,
        day_shifts: list[dict[str, Any]],
        vehicles: dict[str, dict[str, Any]],
        drivers: dict[str, dict[str, Any]],
        capacities: dict[str, dict[str, Any]],
        equipment: dict[str, set[str]],
        certifications: dict[str, set[str]],
    ) -> list[ShiftCandidate]:
        candidates: list[ShiftCandidate] = []
        buffer_minutes = self.config.routing.execution_buffer_minutes
        seen_vehicles: set[str] = set()
        for shift in sorted(day_shifts, key=lambda row: str(row["shift_id"])):
            if _s(shift.get("status")) != "SCHEDULED":
                continue
            vehicle_id = str(shift["vehicle_id"])
            driver_id = str(shift["driver_id"])
            vehicle = vehicles.get(vehicle_id)
            driver = drivers.get(driver_id)
            capacity = capacities.get(vehicle_id)
            if not vehicle or not driver or not capacity:
                continue
            if _s(vehicle.get("status")) != VehicleStatus.ACTIVE.value:
                continue
            if _s(driver.get("status")) != "ACTIVE":
                continue
            if vehicle_id in seen_vehicles:
                raise RoutingError(
                    f"duplicate shift for vehicle {vehicle_id} on {service_date}"
                )
            start = pd.Timestamp(shift["planned_start"]).to_pydatetime()
            end = pd.Timestamp(shift["planned_end"]).to_pydatetime()
            effective_end = end - timedelta(
                minutes=int(shift.get("break_minutes", 0)) + buffer_minutes
            )
            if effective_end <= start:
                continue
            max_duration = int(vehicle.get("max_route_duration_min", 540))
            effective_end = min(effective_end, start + timedelta(minutes=max_duration))
            seen_vehicles.add(vehicle_id)
            candidates.append(
                ShiftCandidate(
                    index=len(candidates),
                    shift_id=str(shift["shift_id"]),
                    driver_id=driver_id,
                    vehicle_id=vehicle_id,
                    start=start,
                    end=end,
                    effective_end=effective_end,
                    break_minutes=int(shift.get("break_minutes", 0)),
                    capacity_units=int(capacity["capacity_units"]),
                    max_weight_kg=float(capacity["max_weight_kg"]),
                    vehicle_type=_s(vehicle["vehicle_type"]),
                    required_equipment=set(equipment.get(vehicle_id, set())),
                    certifications=set(certifications.get(driver_id, set())),
                    license_class=_s(driver["license_class"]),
                    required_license_class=_s(vehicle["required_license_class"]),
                    max_route_duration_min=max_duration,
                )
            )
        return candidates

    def _eligibility(
        self,
        delivery: dict[str, Any],
        site: dict[str, Any],
        window: dict[str, Any],
        candidates: list[ShiftCandidate],
        travel_model: TravelTimeModel,
        scenario: ScenarioConfiguration,
        depot_id: str,
    ) -> tuple[list[ShiftCandidate] | None, UnservedReason]:
        if not candidates:
            return None, UnservedReason.NO_ELIGIBLE_VEHICLE
        access = _s(site["access_class"])
        required_equipment = _s(delivery["required_equipment"])
        requires_cold_chain = required_equipment == "REFRIGERATED"
        requires_lift_gate = (
            required_equipment == "BULK" and not bool(site.get("dock_available", False))
        )
        unit_demand = int(delivery["demand_units"])
        weight = float(delivery["weight_kg"])
        viable: list[ShiftCandidate] = []
        for candidate in candidates:
            if required_equipment not in candidate.required_equipment:
                continue
            if candidate.license_class != candidate.required_license_class:
                continue
            if access == SiteAccessClass.VAN_ONLY.value and candidate.vehicle_type != "CARGO_VAN":
                continue
            if access == SiteAccessClass.MEDIUM.value and candidate.vehicle_type == "FLATBED":
                continue
            if requires_cold_chain and "COLD_CHAIN" not in candidate.certifications:
                continue
            if requires_lift_gate and (
                "LIFT_GATE" not in candidate.certifications
                or "LIFT_GATE" not in candidate.required_equipment
            ):
                continue
            viable.append(candidate)
        if not viable:
            return None, UnservedReason.NO_ELIGIBLE_VEHICLE
        capacity_viable = [
            candidate
            for candidate in viable
            if candidate.capacity_units >= unit_demand
            and candidate.max_weight_kg >= weight
        ]
        if not capacity_viable:
            return None, UnservedReason.CAPACITY_EXCEEDS_FLEET
        window_start = pd.Timestamp(window["window_start"]).to_pydatetime()
        window_end = pd.Timestamp(window["window_end"]).to_pydatetime()
        service_minutes = int(delivery["service_duration_min"])
        time_viable: list[ShiftCandidate] = []
        outside_shift = True
        for candidate in capacity_viable:
            if window_end >= candidate.start and window_start <= candidate.effective_end:
                outside_shift = False
            travel_out = travel_model.planning_minutes(depot_id, str(site["site_id"]), scenario)
            travel_back = travel_model.planning_minutes(str(site["site_id"]), depot_id, scenario)
            earliest_service = candidate.start + timedelta(minutes=math.ceil(travel_out))
            latest_return = candidate.effective_end - timedelta(
                minutes=math.ceil(travel_back) + service_minutes
            )
            if earliest_service <= window_end and latest_return >= window_start:
                time_viable.append(candidate)
        if not time_viable:
            if outside_shift:
                return None, UnservedReason.OUTSIDE_SHIFT
            return None, UnservedReason.TIME_WINDOW_INFEASIBLE
        return time_viable, UnservedReason.OPTIMIZER_TRADEOFF

    @staticmethod
    def _reason_text(reason: UnservedReason, candidates: list[ShiftCandidate]) -> str:
        if reason == UnservedReason.NO_ELIGIBLE_VEHICLE:
            return (
                "No active shift satisfied vehicle equipment, site access, driver "
                "qualification and certification rules for this delivery."
            )
        if reason == UnservedReason.CAPACITY_EXCEEDS_FLEET:
            return (
                "No eligible vehicle could carry the delivery's unit or weight demand "
                "(individual delivery exceeds every eligible capacity)."
            )
        if reason == UnservedReason.OUTSIDE_SHIFT:
            return "The delivery time window lies outside every eligible driver's shift."
        if reason == UnservedReason.TIME_WINDOW_INFEASIBLE:
            return (
                "The delivery time window cannot be reached within every eligible shift "
                "after depot travel, service and return travel are considered."
            )
        return f"Delivery was not assigned (eligible shift count {len(candidates)})."

    def _solve_day(
        self,
        service_date: date,
        scenario: ScenarioConfiguration,
        deliveries: list[dict[str, Any]],
        allowed_by_delivery: dict[str, list[int]],
        candidates: list[ShiftCandidate],
        candidate_by_shift: dict[str, ShiftCandidate],
        sites: dict[str, dict[str, Any]],
        time_windows: dict[str, dict[str, Any]],
        travel_model: TravelTimeModel,
        depot_id: str,
    ) -> tuple[list[dict[str, Any]], SolverStatus, float | None, str]:
        location_ids = [depot_id] + sorted(
            {str(delivery["site_id"]) for delivery in deliveries}
        )
        location_index = {location_id: index for index, location_id in enumerate(location_ids)}
        _, minutes_matrix, distance_matrix = travel_model.matrix_for_day(location_ids, scenario)
        node_deliveries: list[dict[str, Any] | None] = [None]
        node_locations: list[str] = [depot_id]
        for delivery in deliveries:
            node_deliveries.append(delivery)
            node_locations.append(str(delivery["site_id"]))
        node_location_index = [location_index[location_id] for location_id in node_locations]
        service_seconds = [0] + [
            int(delivery["service_duration_min"]) * 60 for delivery in deliveries
        ]
        unit_demands = [0] + [int(delivery["demand_units"]) for delivery in deliveries]
        weight_demands = [0] + [
            int(math.ceil(float(delivery["weight_kg"]))) for delivery in deliveries
        ]
        n_nodes = len(node_deliveries)
        n_vehicles = len(candidates)
        manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
        routing = pywrapcp.RoutingModel(manager)

        def transit_callback(from_index: int, to_index: int) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            from_location = node_location_index[from_node]
            to_location = node_location_index[to_node]
            return (
                minutes_matrix[from_location][to_location] * 60
                + service_seconds[from_node]
            )

        transit_index = routing.RegisterTransitCallback(transit_callback)

        def distance_callback(from_index: int, to_index: int) -> int:
            from_location = node_location_index[manager.IndexToNode(from_index)]
            to_location = node_location_index[manager.IndexToNode(to_index)]
            return int(
                round(
                    distance_matrix[from_location][to_location]
                    * 1000.0
                    * self.config.routing.distance_weight
                )
            )

        distance_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(distance_index)
        routing.SetFixedCostOfAllVehicles(self.config.routing.fixed_vehicle_cost)

        demand_callback = routing.RegisterUnaryTransitCallback(
            lambda index: unit_demands[manager.IndexToNode(index)]
        )
        routing.AddDimensionWithVehicleCapacity(
            demand_callback,
            0,
            [candidate.capacity_units for candidate in candidates],
            True,
            "CapacityUnits",
        )
        weight_callback = routing.RegisterUnaryTransitCallback(
            lambda index: weight_demands[manager.IndexToNode(index)]
        )
        routing.AddDimensionWithVehicleCapacity(
            weight_callback,
            0,
            [int(candidate.max_weight_kg) for candidate in candidates],
            True,
            "CapacityWeight",
        )
        count_callback = routing.RegisterUnaryTransitCallback(
            lambda index: 1 if manager.IndexToNode(index) > 0 else 0
        )
        routing.AddDimension(
            count_callback,
            0,
            self.config.routing.max_stops_per_route,
            True,
            "StopCount",
        )
        routing.AddDimension(
            transit_index,
            24 * 3600,
            2 * 24 * 3600,
            False,
            "Time",
        )
        time_dimension = routing.GetDimensionOrDie("Time")
        for candidate in candidates:
            start_index = routing.Start(candidate.index)
            end_index = routing.End(candidate.index)
            start_seconds = candidate.start.hour * 3600 + candidate.start.minute * 60
            effective_seconds = (
                candidate.effective_end.hour * 3600
                + candidate.effective_end.minute * 60
                + candidate.effective_end.second
            )
            time_dimension.CumulVar(start_index).SetRange(start_seconds, start_seconds + 1800)
            time_dimension.CumulVar(end_index).SetRange(start_seconds, effective_seconds)
            time_dimension.SetSpanUpperBoundForVehicle(
                int(candidate.max_route_duration_min * 60), candidate.index
            )
            routing.AddVariableMinimizedByFinalizer(time_dimension.CumulVar(end_index))
        for node in range(1, n_nodes):
            delivery = node_deliveries[node]
            assert delivery is not None
            window = time_windows[str(delivery["time_window_id"])]
            start = pd.Timestamp(window["window_start"]).to_pydatetime()
            end = pd.Timestamp(window["window_end"]).to_pydatetime()
            start_seconds = start.hour * 3600 + start.minute * 60
            end_seconds = end.hour * 3600 + end.minute * 60
            index = manager.NodeToIndex(node)
            time_dimension.CumulVar(index).SetRange(start_seconds, end_seconds)
            allowed_vehicles = allowed_by_delivery[str(delivery["delivery_id"])]
            if self.config.routing.allow_dropping:
                # -1 is OR-Tools' sentinel for an unperformed disjunction node.
                allowed_vehicles = [-1, *allowed_vehicles]
            # OR-Tools 9.15's Python SetAllowedVehiclesForIndex binding expects
            # an absl::Span which the wheel does not construct from a Python
            # list. Setting the underlying vehicle variable domain is
            # equivalent and keeps dropping through the disjunction penalty.
            routing.VehicleVar(index).SetValues(allowed_vehicles)
            if self.config.routing.allow_dropping:
                priority = _s(delivery["priority"])
                multiplier = self.config.routing.priority_multipliers[
                    next(
                        item
                        for item in self.config.routing.priority_multipliers
                        if item.value == priority
                    )
                ]
                penalty = int(
                    round(self.config.routing.unserved_penalty * multiplier)
                )
                routing.AddDisjunction([index], penalty)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        params.time_limit.FromSeconds(self.config.routing.time_limit_seconds)
        params.log_search = False
        assignment = routing.SolveWithParameters(params)
        solve_status = routing.status()
        if assignment is None:
            if solve_status == _ROUTING_INFEASIBLE:
                status = SolverStatus.INFEASIBLE
            elif solve_status == _ROUTING_FAIL_TIMEOUT:
                status = SolverStatus.TIMEOUT
            else:
                status = SolverStatus.UNKNOWN
            return [], status, None, f"OR-Tools status {solve_status}"

        if solve_status == _ROUTING_OPTIMAL:
            status = SolverStatus.OPTIMAL
        elif solve_status in {_ROUTING_SUCCESS, _ROUTING_PARTIAL_SUCCESS}:
            status = SolverStatus.FEASIBLE
        else:
            status = SolverStatus.FEASIBLE
        objective = float(assignment.ObjectiveValue())
        route_payloads: list[dict[str, Any]] = []
        visited_nodes: set[int] = set()
        for candidate in candidates:
            index = routing.Start(candidate.index)
            sequence: list[int] = []
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                if node > 0:
                    sequence.append(node)
                    visited_nodes.add(node)
                index = assignment.Value(routing.NextVar(index))
            if not sequence:
                continue
            route_start_seconds = assignment.Min(
                time_dimension.CumulVar(routing.Start(candidate.index))
            )
            start_dt = datetime.combine(service_date, dtime()) + timedelta(
                seconds=route_start_seconds
            )
            previous_departure = start_dt
            previous_location = depot_id
            stop_payloads: list[dict[str, Any]] = []
            leg_payloads: list[dict[str, Any]] = []
            total_distance = 0.0
            total_travel = 0.0
            total_service = 0.0
            total_wait = 0.0
            for stop_sequence, node in enumerate(sequence, start=1):
                delivery = node_deliveries[node]
                assert delivery is not None
                site_id = str(delivery["site_id"])
                from_node = location_index[previous_location]
                to_node = location_index[site_id]
                travel_minutes = float(minutes_matrix[from_node][to_node])
                distance_km = float(distance_matrix[from_node][to_node])
                arrival = previous_departure + timedelta(minutes=travel_minutes)
                service_start_seconds = assignment.Min(
                    time_dimension.CumulVar(manager.NodeToIndex(node))
                )
                service_start = datetime.combine(service_date, dtime()) + timedelta(
                    seconds=service_start_seconds
                )
                if service_start < arrival:
                    raise RoutingError(
                        f"stop {delivery['delivery_id']} solver service start precedes arrival"
                    )
                service_minutes = float(delivery["service_duration_min"])
                departure = service_start + timedelta(minutes=service_minutes)
                wait_minutes = max(0.0, (service_start - arrival).total_seconds() / 60.0)
                text_leg = {
                    "leg_sequence": len(leg_payloads) + 1,
                    "origin_type": LocationType.DEPOT if previous_location == depot_id else LocationType.SITE,
                    "origin_id": previous_location,
                    "destination_type": LocationType.SITE,
                    "destination_id": site_id,
                    "planned_departure": previous_departure,
                    "planned_arrival": arrival,
                    "planned_distance_km": distance_km,
                    "planned_travel_minutes": travel_minutes,
                }
                leg_payloads.append(text_leg)
                stop_payloads.append(
                    {
                        "delivery_id": str(delivery["delivery_id"]),
                        "site_id": site_id,
                        "time_window_id": str(delivery["time_window_id"]),
                        "planned_arrival": arrival,
                        "planned_service_start": service_start,
                        "planned_departure": departure,
                        "wait_minutes": wait_minutes,
                        "service_minutes": service_minutes,
                        "demand_units": int(delivery["demand_units"]),
                        "weight_kg": float(delivery["weight_kg"]),
                    }
                )
                total_distance += distance_km
                total_travel += travel_minutes
                total_service += service_minutes
                total_wait += wait_minutes
                previous_departure = departure
                previous_location = site_id
            from_node = location_index[previous_location]
            to_node = location_index[depot_id]
            return_minutes = float(minutes_matrix[from_node][to_node])
            return_distance = float(distance_matrix[from_node][to_node])
            return_arrival = previous_departure + timedelta(minutes=return_minutes)
            leg_payloads.append(
                {
                    "leg_sequence": len(leg_payloads) + 1,
                    "origin_type": LocationType.SITE,
                    "origin_id": previous_location,
                    "destination_type": LocationType.DEPOT,
                    "destination_id": depot_id,
                    "planned_departure": previous_departure,
                    "planned_arrival": return_arrival,
                    "planned_distance_km": return_distance,
                    "planned_travel_minutes": return_minutes,
                }
            )
            total_distance += return_distance
            total_travel += return_minutes
            if return_arrival > candidate.effective_end:
                raise RoutingError(
                    f"solved route for {candidate.vehicle_id} returns after effective shift end"
                )
            route_payloads.append(
                {
                    "candidate": candidate,
                    "planned_start": start_dt,
                    "planned_end": return_arrival,
                    "stops": stop_payloads,
                    "legs": leg_payloads,
                    "distance_km": total_distance,
                    "travel_minutes": total_travel,
                    "service_minutes": total_service,
                    "wait_minutes": total_wait,
                    "delivery_ids": [stop["delivery_id"] for stop in stop_payloads],
                }
            )
        drop_count = len(deliveries) - len(visited_nodes)
        message = (
            f"OR-Tools status {solve_status}; vehicles used {len(route_payloads)}; "
            f"dropped nodes {drop_count}"
        )
        return route_payloads, status, objective, message


def build_plan(dataset: Dataset, config: AppConfig) -> PlanResult:
    return RoutePlanner(config).plan(dataset)
