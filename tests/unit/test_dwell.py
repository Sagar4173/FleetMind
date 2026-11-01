"""Regression tests for planned versus actual dwell semantics.

``wait_minutes`` and ``service_minutes`` on routes and stops are planned
values produced by the routing model.  Execution must preserve those values
and publish separately named actual durations derived from the simulated
timestamps.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from fleetmind.config import AppConfig, HorizonConfig, SimulationConfig
from fleetmind.generation.factory import Dataset
from fleetmind.models.entities import RouteStop
from fleetmind.models.enums import AssignmentStatus, DeliveryStatus, RouteStatus
from fleetmind.simulation.engine import simulate_execution

DAY = date(2026, 11, 2)


def _moment(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 2, hour, minute)


def _config(**simulation: object) -> AppConfig:
    defaults = {
        "dispatch_delay_max_minutes": 0,
        "travel_variance_pct": 0.0,
        "service_variance_pct": 0.0,
        "customer_unavailable_base_rate": 0.0,
    }
    defaults.update(simulation)
    return AppConfig(
        seed=7,
        horizon=HorizonConfig(start_date=DAY, days=1),
        simulation=SimulationConfig(**defaults),
    )


def _dataset(config: AppConfig, delivery_ids: list[str]) -> Dataset:
    tables = {
        "deliveries": pd.DataFrame(
            [
                {
                    "delivery_id": delivery_id,
                    "order_id": f"ORD-2026-{index:06d}",
                    "service_date": DAY,
                    "status": DeliveryStatus.ASSIGNED,
                }
                for index, delivery_id in enumerate(delivery_ids, start=1)
            ]
        ),
        "orders": pd.DataFrame(
            [
                {"order_id": f"ORD-2026-{index:06d}", "status": "CONFIRMED"}
                for index in range(1, len(delivery_ids) + 1)
            ]
        ),
        "time_windows": pd.DataFrame(
            [
                {
                    "time_window_id": f"TW-2026-{index:06d}",
                    "window_end": _moment(18, 0),
                }
                for index in range(1, len(delivery_ids) + 1)
            ]
        ),
        "shifts": pd.DataFrame(
            [
                {
                    "shift_id": "SHT-2026-000001",
                    "planned_end": _moment(17, 0),
                }
            ]
        ),
    }
    return Dataset(config=config, run_id=config.run_id, tables=tables)


def _stop(
    sequence: int,
    *,
    planned_arrival: datetime,
    planned_service_start: datetime,
    planned_departure: datetime,
    wait_minutes: float,
    service_minutes: float,
    delivery_id: str | None = None,
) -> dict[str, object]:
    return {
        "stop_id": f"STOP-RTE-2026-000001-{sequence:03d}",
        "route_id": "RTE-2026-000001",
        "stop_sequence": sequence,
        "delivery_id": delivery_id or f"DEL-2026-{sequence:06d}",
        "site_id": f"CUS-{sequence:06d}",
        "time_window_id": f"TW-2026-{sequence:06d}",
        "planned_arrival": planned_arrival,
        "planned_service_start": planned_service_start,
        "planned_departure": planned_departure,
        "wait_minutes": wait_minutes,
        "service_minutes": service_minutes,
        "demand_units": 10,
        "weight_kg": 100.0,
    }


def _leg(
    sequence: int,
    *,
    planned_departure: datetime,
    planned_arrival: datetime,
    travel_minutes: float,
) -> dict[str, object]:
    return {
        "leg_id": f"LEG-2026-000001-{sequence:04d}",
        "route_id": "RTE-2026-000001",
        "leg_sequence": sequence,
        "origin_type": "DEPOT",
        "origin_id": "DEP-001",
        "destination_type": "SITE",
        "destination_id": f"CUS-{sequence:06d}",
        "planned_departure": planned_departure,
        "planned_arrival": planned_arrival,
        "planned_distance_km": 5.0,
        "planned_travel_minutes": travel_minutes,
        "scenario_id": "SCN-2026-0001",
        "traffic_multiplier": 1.0,
    }


def _plan(stops: list[dict[str, object]], legs: list[dict[str, object]]) -> dict[str, pd.DataFrame]:
    return {
        "routes": pd.DataFrame(
            [
                {
                    "route_id": "RTE-2026-000001",
                    "scenario_id": "SCN-2026-0001",
                    "service_date": DAY,
                    "depot_id": "DEP-001",
                    "vehicle_id": "VEH-000001",
                    "driver_id": "DRV-000001",
                    "shift_id": "SHT-2026-000001",
                    "status": RouteStatus.PLANNED,
                    "planned_start": _moment(8, 0),
                    "planned_end": _moment(9, 35),
                    "stop_count": len(stops),
                    "capacity_units": 100,
                    "capacity_weight_kg": 10_000.0,
                    "service_minutes": sum(float(stop["service_minutes"]) for stop in stops),
                    "wait_minutes": sum(float(stop["wait_minutes"]) for stop in stops),
                    "solver_status": "FEASIBLE",
                    "created_at": _moment(7, 0),
                }
            ]
        ),
        "route_stops": pd.DataFrame(stops),
        "route_legs": pd.DataFrame(legs),
        "delivery_assignments": pd.DataFrame(
            [
                {
                    "delivery_id": stop["delivery_id"],
                    "status": AssignmentStatus.ASSIGNED,
                }
                for stop in stops
            ]
        ),
    }


def test_actual_dwell_is_derived_and_planned_values_are_preserved() -> None:
    config = _config()
    stops = [
        _stop(
            1,
            planned_arrival=_moment(8, 10),
            planned_service_start=_moment(8, 10),
            planned_departure=_moment(8, 20),
            wait_minutes=0.0,
            service_minutes=10.0,
        ),
        _stop(
            2,
            planned_arrival=_moment(8, 30),
            planned_service_start=_moment(9, 0),
            planned_departure=_moment(9, 15),
            wait_minutes=30.0,
            service_minutes=15.0,
        ),
    ]
    legs = [
        _leg(
            1,
            planned_departure=_moment(8, 0),
            planned_arrival=_moment(8, 10),
            travel_minutes=10.0,
        ),
        _leg(
            2,
            planned_departure=_moment(8, 20),
            planned_arrival=_moment(8, 30),
            travel_minutes=10.0,
        ),
        _leg(
            3,
            planned_departure=_moment(9, 15),
            planned_arrival=_moment(9, 35),
            travel_minutes=20.0,
        ),
    ]

    result = simulate_execution(
        _dataset(config, ["DEL-2026-000001", "DEL-2026-000002"]),
        _plan(stops, legs),
        config,
    )

    simulated = result.tables["route_stops"].set_index("stop_sequence")
    zero_wait = simulated.loc[1]
    positive_wait = simulated.loc[2]

    # Planned values remain the routing model's values.
    assert zero_wait["wait_minutes"] == pytest.approx(0.0)
    assert zero_wait["service_minutes"] == pytest.approx(10.0)
    assert positive_wait["wait_minutes"] == pytest.approx(30.0)
    assert positive_wait["service_minutes"] == pytest.approx(15.0)

    # Actual values are separately named and derived from execution timestamps.
    assert zero_wait["actual_wait_minutes"] == pytest.approx(0.0)
    assert zero_wait["actual_service_minutes"] == pytest.approx(10.0)
    assert positive_wait["actual_wait_minutes"] == pytest.approx(30.0)
    assert positive_wait["actual_service_minutes"] == pytest.approx(15.0)
    assert positive_wait["actual_service_start"] - positive_wait["actual_arrival"] == pd.Timedelta(
        minutes=30
    )

    route = result.tables["routes"].iloc[0]
    assert route["wait_minutes"] == pytest.approx(30.0)
    assert route["service_minutes"] == pytest.approx(25.0)
    assert route["actual_wait_minutes"] == pytest.approx(30.0)
    assert route["actual_service_minutes"] == pytest.approx(25.0)
    assert route["status"] == RouteStatus.COMPLETED


def test_failed_attempt_records_actual_dwell_distinct_from_planned() -> None:
    # DEL-2026-000004 has a deterministic customer-unavailable draw below 0.2.
    config = _config(customer_unavailable_base_rate=0.2)
    stops = [
        _stop(
            1,
            planned_arrival=_moment(8, 10),
            planned_service_start=_moment(8, 10),
            planned_departure=_moment(8, 30),
            wait_minutes=0.0,
            service_minutes=20.0,
            delivery_id="DEL-2026-000004",
        )
    ]
    legs = [
        _leg(
            1,
            planned_departure=_moment(8, 0),
            planned_arrival=_moment(8, 10),
            travel_minutes=10.0,
        ),
        _leg(
            2,
            planned_departure=_moment(8, 30),
            planned_arrival=_moment(8, 50),
            travel_minutes=20.0,
        ),
    ]

    result = simulate_execution(
        _dataset(config, ["DEL-2026-000004"]), _plan(stops, legs), config
    )

    stop = result.tables["route_stops"].iloc[0]
    assert stop["status"] == DeliveryStatus.FAILED
    assert stop["service_minutes"] == pytest.approx(20.0)
    assert stop["actual_wait_minutes"] == pytest.approx(0.0)
    assert stop["actual_service_minutes"] == pytest.approx(
        float(config.simulation.failed_attempt_service_minutes)
    )

    route = result.tables["routes"].iloc[0]
    assert route["status"] == RouteStatus.PARTIAL
    assert route["actual_service_minutes"] == pytest.approx(
        float(config.simulation.failed_attempt_service_minutes)
    )


def test_stop_without_execution_timestamps_has_null_actual_dwell() -> None:
    stop = RouteStop(
        run_id="RUN-ABC123",
        stop_id="STOP-RTE-2026-000001-001",
        route_id="RTE-2026-000001",
        stop_sequence=1,
        delivery_id="DEL-2026-000001",
        site_id="CUS-000001",
        time_window_id="TW-2026-000001",
        planned_arrival=_moment(8, 10),
        planned_service_start=_moment(8, 10),
        planned_departure=_moment(8, 20),
        wait_minutes=0.0,
        service_minutes=10.0,
        demand_units=10,
        weight_kg=100.0,
    )

    assert stop.actual_arrival is None
    assert stop.actual_wait_minutes is None
    assert stop.actual_service_minutes is None


def test_actual_dwell_must_reconcile_with_timestamps() -> None:
    with pytest.raises(ValidationError, match="actual dwell"):
        RouteStop(
            run_id="RUN-ABC123",
            stop_id="STOP-RTE-2026-000001-001",
            route_id="RTE-2026-000001",
            stop_sequence=1,
            delivery_id="DEL-2026-000001",
            site_id="CUS-000001",
            time_window_id="TW-2026-000001",
            planned_arrival=_moment(8, 10),
            planned_service_start=_moment(8, 10),
            planned_departure=_moment(8, 20),
            actual_arrival=_moment(8, 10),
            actual_service_start=_moment(8, 10),
            actual_departure=_moment(8, 20),
            wait_minutes=0.0,
            service_minutes=10.0,
            actual_wait_minutes=5.0,
            actual_service_minutes=10.0,
            demand_units=10,
            weight_kg=100.0,
        )
