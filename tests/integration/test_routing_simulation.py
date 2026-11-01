from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fleetmind.config import (
    AppConfig,
    GenerationConfig,
    HorizonConfig,
    ReportingConfig,
    RoutingConfig,
    SimulationConfig,
)
from fleetmind.generation.factory import generate_dataset
from fleetmind.models.enums import (
    AssignmentStatus,
    DeliveryStatus,
    HandlingClass,
    OutcomeStatus,
    VehicleType,
)
from fleetmind.pipeline import build_pipeline_run
from fleetmind.routing.optimizer import build_plan
from fleetmind.simulation.engine import simulate_execution
from fleetmind.validation.engine import validate_full


def tiny_config(
    *, handling: HandlingClass = HandlingClass.AMBIENT,
    vehicle_type: VehicleType = VehicleType.CARGO_VAN,
    seed: int = 11,
) -> AppConfig:
    return AppConfig(
        seed=seed,
        horizon=HorizonConfig(start_date=date(2026, 11, 2), days=1),
        generation=GenerationConfig(
            customer_sites=6,
            orders=8,
            vehicles=3,
            drivers=3,
            site_active_ratio=1.0,
            shift_availability_weekday=1.0,
            shift_availability_weekend=1.0,
            handling_mix={handling: 1.0},
            fleet_mix={vehicle_type: 1.0},
        ),
        routing=RoutingConfig(time_limit_seconds=1),
        simulation=SimulationConfig(customer_unavailable_base_rate=0.0),
        reporting=ReportingConfig(),
    )


def variance_config(
    *,
    seed: int,
    orders: int,
    vehicles: int,
    sites: int,
    execution_buffer_minutes: int,
    simulation: dict,
) -> AppConfig:
    return AppConfig(
        seed=seed,
        horizon=HorizonConfig(start_date=date(2026, 11, 2), days=1),
        generation=GenerationConfig(
            customer_sites=sites,
            orders=orders,
            vehicles=vehicles,
            drivers=vehicles,
            site_active_ratio=1.0,
            shift_availability_weekday=1.0,
            shift_availability_weekend=1.0,
            handling_mix={HandlingClass.AMBIENT: 1.0},
            fleet_mix={VehicleType.BOX_TRUCK: 1.0},
        ),
        routing=RoutingConfig(
            time_limit_seconds=1,
            execution_buffer_minutes=execution_buffer_minutes,
            max_stops_per_route=20,
        ),
        simulation=SimulationConfig(
            customer_unavailable_base_rate=0.0, **simulation
        ),
        reporting=ReportingConfig(),
    )


def run_variance_scenario(
    *,
    seed: int,
    orders: int,
    vehicles: int,
    sites: int,
    execution_buffer_minutes: int = 0,
    simulation: dict,
):
    config = variance_config(
        seed=seed,
        orders=orders,
        vehicles=vehicles,
        sites=sites,
        execution_buffer_minutes=execution_buffer_minutes,
        simulation=simulation,
    )
    dataset = generate_dataset(config)
    plan = build_plan(dataset, config)
    simulation_result = simulate_execution(dataset, plan.tables, config)
    tables = dataset.copy_tables()
    tables.update(plan.tables)
    tables.update(simulation_result.tables)
    return config, dataset, plan, simulation_result, validate_full(tables, config)


@pytest.fixture(scope="module")
def pipeline():
    config = tiny_config()
    dataset = generate_dataset(config)
    plan = build_plan(dataset, config)
    simulation = simulate_execution(dataset, plan.tables, config)
    tables = dataset.copy_tables()
    tables.update(plan.tables)
    tables.update(simulation.tables)
    return config, dataset, plan, simulation, tables


def test_tiny_plan_assigns_all_deliveries(pipeline) -> None:
    _, dataset, plan, _, _ = pipeline
    assignments = plan.tables["delivery_assignments"]
    assert len(assignments) == len(dataset["deliveries"])
    assert (assignments["status"] == AssignmentStatus.ASSIGNED.value).all()
    assert plan.summary["assigned_deliveries"] == len(dataset["deliveries"])
    assert plan.summary["unserved_deliveries"] == 0
    assert len(plan.tables["routes"]) > 0


def test_plan_is_structurally_deterministic(pipeline) -> None:
    config, dataset, first, _, _ = pipeline
    second = build_plan(dataset, config)
    for name in ["routes", "route_stops", "route_legs", "delivery_assignments"]:
        left = first.tables[name].drop(
            columns=["solve_seconds"], errors="ignore"
        ).reset_index(drop=True)
        right = second.tables[name].drop(
            columns=["solve_seconds"], errors="ignore"
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)


def test_plan_identifiers_and_stop_sequences(pipeline) -> None:
    _, _, plan, _, _ = pipeline
    routes = plan.tables["routes"]
    stops = plan.tables["route_stops"]
    legs = plan.tables["route_legs"]
    assert routes["route_id"].str.fullmatch(r"RTE-2026-\d{6}").all()
    assert stops["stop_id"].str.fullmatch(r"STOP-RTE-2026-\d{6}-\d{3}").all()
    assert legs["leg_id"].str.fullmatch(r"LEG-2026-\d{6}-\d{4}").all()
    assert stops["stop_id"].is_unique
    assert legs["leg_id"].is_unique
    for route_id, group in stops.groupby("route_id"):
        assert list(group["stop_sequence"]) == list(range(1, len(group) + 1))
        leg_count = int((legs["route_id"] == route_id).sum())
        assert leg_count == len(group) + 1
    assert stops["delivery_id"].is_unique


def test_clean_pipeline_passes_full_validation(pipeline) -> None:
    config, _, _, _, tables = pipeline
    result = validate_full(tables, config)
    assert result.passed
    assert result.issues.empty


def test_simulation_is_deterministic(pipeline) -> None:
    config, dataset, plan, first, _ = pipeline
    second = simulate_execution(dataset, plan.tables, config)
    for name, frame in first.tables.items():
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), second.tables[name].reset_index(drop=True))


def test_simulation_chronology_and_event_consistency(pipeline) -> None:
    _, _, _, simulation, _ = pipeline
    stops = simulation.tables["route_stops"].sort_values(["route_id", "stop_sequence"])
    routes = simulation.tables["routes"]
    legs = simulation.tables["route_legs"].sort_values(["route_id", "leg_sequence"])
    outcomes = simulation.tables["delivery_outcomes"]
    assert outcomes["delivery_id"].is_unique
    assert set(outcomes["status"]) <= {
        OutcomeStatus.DELIVERED.value,
        OutcomeStatus.FAILED.value,
        OutcomeStatus.UNSERVED.value,
    }
    for route_id, group in stops.groupby("route_id"):
        previous_departure = None
        for _, stop in group.iterrows():
            assert stop["actual_arrival"] <= stop["actual_service_start"]
            assert stop["actual_service_start"] <= stop["actual_departure"]
            if previous_departure is not None:
                assert stop["actual_arrival"] >= previous_departure
            previous_departure = stop["actual_departure"]
        route_legs = legs[legs["route_id"] == route_id]
        assert len(route_legs) == len(group) + 1
        assert (route_legs["actual_arrival"] >= route_legs["actual_departure"]).all()
        route = routes[routes["route_id"] == route_id].iloc[0]
        assert route["actual_start"] <= group["actual_arrival"].min()
        assert route["actual_end"] >= group["actual_departure"].max()
    for table in ["route_events", "delivery_events"]:
        events = simulation.tables[table]
        assert events["event_id"].is_unique
        assert (events["sequence"] >= 1).all()


def test_simulation_reconciles_delivery_statuses(pipeline) -> None:
    _, _, _, simulation, _ = pipeline
    outcomes = simulation.tables["delivery_outcomes"].set_index("delivery_id")
    deliveries = simulation.tables["deliveries"].set_index("delivery_id")
    assert set(deliveries.index) == set(outcomes.index)
    for delivery_id, outcome in outcomes.iterrows():
        assert deliveries.loc[delivery_id, "status"] == outcome["status"]
    summary = simulation.summary
    assert (
        summary["delivered"] + summary["failed"] + summary["unserved"]
        == summary["deliveries"]
    )
    assert summary["routed_deliveries"] == summary["delivered"] + summary["failed"]


def test_unserved_deliveries_get_explicit_outcomes_and_reasons() -> None:
    config = tiny_config(
        handling=HandlingClass.AMBIENT,
        vehicle_type=VehicleType.FLATBED,
        seed=23,
    )
    dataset = generate_dataset(config)
    plan = build_plan(dataset, config)
    assignments = plan.tables["delivery_assignments"]
    assert plan.tables["routes"].empty
    assert (assignments["status"] == AssignmentStatus.UNSERVED.value).all()
    assert set(assignments["reason_code"]) == {"NO_ELIGIBLE_VEHICLE"}
    assert plan.summary["assigned_deliveries"] == 0
    assert plan.summary["unserved_deliveries"] == len(dataset["deliveries"])

    simulation = simulate_execution(dataset, plan.tables, config)
    outcomes = simulation.tables["delivery_outcomes"]
    assert (outcomes["status"] == OutcomeStatus.UNSERVED.value).all()
    assert set(outcomes["reason_code"]) == {"NO_ELIGIBLE_VEHICLE"}
    assert simulation.summary["unserved"] == len(dataset["deliveries"])
    assert simulation.tables["deliveries"]["status"].eq(
        DeliveryStatus.UNSERVED.value
    ).all()

    tables = dataset.copy_tables()
    tables.update(plan.tables)
    tables.update(simulation.tables)
    result = validate_full(tables, config)
    assert result.passed, result.issues.to_string()


@pytest.mark.parametrize(
    ("scenario", "seed", "orders", "vehicles", "sites", "buffer", "simulation"),
    [
        (
            "high_dispatch_delay",
            5,
            14,
            1,
            8,
            0,
            {"dispatch_delay_max_minutes": 120},
        ),
        (
            "high_travel_variance",
            13,
            30,
            2,
            10,
            0,
            {"travel_variance_pct": 0.5},
        ),
        (
            "high_service_variance",
            7,
            12,
            1,
            8,
            0,
            {"service_variance_pct": 0.5},
        ),
        (
            "natural_overrun_at_default_buffer",
            5,
            14,
            1,
            8,
            30,
            {
                "dispatch_delay_max_minutes": 120,
                "travel_variance_pct": 0.5,
                "service_variance_pct": 0.5,
            },
        ),
    ],
)
def test_high_variance_overrun_is_a_critical_validation_failure(
    scenario: str,
    seed: int,
    orders: int,
    vehicles: int,
    sites: int,
    buffer: int,
    simulation: dict,
) -> None:
    _, _, _, simulation_result, validation = run_variance_scenario(
        seed=seed,
        orders=orders,
        vehicles=vehicles,
        sites=sites,
        execution_buffer_minutes=buffer,
        simulation=simulation,
    )
    overruns = validation.issues[
        validation.issues["issue_type"] == "DRIVER_SHIFT_EXCEEDED"
    ]
    assert len(overruns) >= 1, f"{scenario} did not produce a natural overrun"
    assert set(overruns["severity"]) == {"CRITICAL"}
    assert not validation.passed
    assert validation.critical_count >= len(overruns)

    routes = simulation_result.tables["routes"].set_index("route_id")
    stops = simulation_result.tables["route_stops"]
    for route_id in overruns["entity_id"]:
        route = routes.loc[route_id]
        planned_end = stops.loc[
            stops["route_id"] == route_id, "planned_departure"
        ]
        assert not planned_end.empty
        assert pd.Timestamp(route["actual_end"]) > pd.Timestamp(
            planned_end.iloc[-1]
        )


def test_zero_execution_buffer_allows_overrun_that_a_larger_buffer_absorbs() -> None:
    # The default 30-minute buffer is deliberately not used as the control: the
    # high-variance case above already shows that it cannot absorb every natural
    # overrun. A 120-minute buffer leaves enough planning slack for this scenario.
    _, _, _, _, zero_buffer_validation = run_variance_scenario(
        seed=13,
        orders=30,
        vehicles=2,
        sites=10,
        execution_buffer_minutes=0,
        simulation={"travel_variance_pct": 0.5},
    )
    zero_overruns = zero_buffer_validation.issues[
        zero_buffer_validation.issues["issue_type"] == "DRIVER_SHIFT_EXCEEDED"
    ]
    assert len(zero_overruns) >= 1
    assert not zero_buffer_validation.passed

    _, _, _, _, buffered_validation = run_variance_scenario(
        seed=13,
        orders=30,
        vehicles=2,
        sites=10,
        execution_buffer_minutes=120,
        simulation={"travel_variance_pct": 0.5},
    )
    buffered_overruns = buffered_validation.issues[
        buffered_validation.issues["issue_type"] == "DRIVER_SHIFT_EXCEEDED"
    ]
    assert buffered_overruns.empty
    assert buffered_validation.passed


def test_clean_overrun_fails_run_and_skips_anomaly_injection() -> None:
    config = variance_config(
        seed=5,
        orders=14,
        vehicles=1,
        sites=8,
        execution_buffer_minutes=30,
        simulation={
            "dispatch_delay_max_minutes": 120,
            "travel_variance_pct": 0.5,
            "service_variance_pct": 0.5,
        },
    )
    run = build_pipeline_run(config, include_anomalies=True)

    assert run.status == "FAILED"
    assert run.error_message.startswith("clean snapshot failed validation")
    assert "critical issue" in run.error_message
    assert run.anomaly_injection is None
    assert run.anomaly_evaluation is None
    assert run.dirty_validation is None
    assert run.summary()["status"] == "FAILED"
    assert run.summary()["error_message"] == run.error_message

    actions = set(run.audit_records["action"])
    assert "PIPELINE_FAILED" in actions
    assert "ANOMALY_INJECTION_COMPLETED" not in actions
    assert "DETECTION_COMPLETED" not in actions
