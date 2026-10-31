from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from fleetmind.anomalies import (
    AnomalyDetectionError,
    AnomalyInjectionError,
    detect_and_evaluate,
    detect_anomalies,
    evaluate_detections,
    inject_anomalies,
)
from fleetmind.config import AnomalyConfig, AppConfig
from fleetmind.models.enums import AnomalyType

RUN_ID = "RUN-TEST00000001"


def _stop(
    route_number: int,
    sequence: int,
    delivery_number: int,
    site_number: int,
) -> dict:
    hour = 8 + sequence - 1
    arrival = datetime(2026, 11, 2, hour, 0)
    departure = datetime(2026, 11, 2, hour, 30)
    return {
        "run_id": RUN_ID,
        "stop_id": f"STOP-RTE-2026-{route_number:06d}-{sequence:03d}",
        "route_id": f"RTE-2026-{route_number:06d}",
        "stop_sequence": sequence,
        "delivery_id": f"DEL-2026-{delivery_number:06d}",
        "site_id": f"CUS-{site_number:06d}",
        "demand_units": 40,
        "weight_kg": 10.0,
        "service_minutes": 30.0,
        "wait_minutes": 0.0,
        "actual_arrival": arrival,
        "actual_service_start": arrival,
        "actual_departure": departure,
    }


def make_clean_tables() -> dict[str, pd.DataFrame]:
    route_ids = [f"RTE-2026-{index:06d}" for index in range(1, 6)]
    routes = pd.DataFrame(
        [
            {
                "run_id": RUN_ID,
                "route_id": route_id,
                "vehicle_id": f"VEH-{index:06d}",
                "shift_id": f"SHT-2026-{index:06d}",
                "actual_start": datetime(2026, 11, 2, 7, 45),
                "actual_end": datetime(2026, 11, 2, 12, 0),
                "stop_count": 2,
                "load_units": 80,
                "load_weight_kg": 20.0,
                "service_minutes": 60.0,
                "wait_minutes": 0.0,
            }
            for index, route_id in enumerate(route_ids, start=1)
        ]
    )
    stops = [
        _stop(1, 1, 1, 1),
        _stop(1, 2, 2, 2),
        _stop(2, 1, 3, 2),
        _stop(2, 2, 4, 3),
        _stop(3, 1, 5, 1),
        _stop(3, 2, 6, 3),
        _stop(4, 1, 7, 1),
        _stop(4, 2, 8, 2),
        _stop(5, 1, 9, 3),
    ]
    route_stops = pd.DataFrame(stops)
    routes.loc[routes["route_id"] == route_ids[4], "stop_count"] = 1
    assignments = pd.DataFrame(
        {
            "run_id": RUN_ID,
            "assignment_id": [f"ASN-2026-{index:06d}" for index in range(1, 10)],
            "delivery_id": [f"DEL-2026-{index:06d}" for index in range(1, 10)],
            "status": ["ASSIGNED"] * 9,
        }
    )
    capacities = pd.DataFrame(
        {
            "run_id": RUN_ID,
            "capacity_id": [f"CAP-{index:06d}" for index in range(1, 6)],
            "vehicle_id": [f"VEH-{index:06d}" for index in range(1, 6)],
            "capacity_units": [200] * 5,
            "max_weight_kg": [1000.0] * 5,
        }
    )
    shifts = pd.DataFrame(
        {
            "run_id": RUN_ID,
            "shift_id": [f"SHT-2026-{index:06d}" for index in range(1, 6)],
            "planned_end": [datetime(2026, 11, 2, 13, 0)] * 5,
        }
    )
    sites = pd.DataFrame(
        {
            "run_id": RUN_ID,
            "site_id": [f"CUS-{index:06d}" for index in range(1, 4)],
            "latitude": [32.80, 32.81, 32.82],
            "longitude": [-96.80, -96.81, -96.82],
        }
    )
    empty_route_legs = pd.DataFrame(
        columns=[
            "run_id",
            "leg_id",
            "route_id",
            "leg_sequence",
            "origin_id",
            "destination_id",
        ]
    )
    empty_vehicles = pd.DataFrame(
        columns=["run_id", "vehicle_id", "vehicle_type", "status"]
    )
    empty_deliveries = pd.DataFrame(
        columns=["run_id", "delivery_id", "status"]
    )
    return {
        "routes": routes,
        "route_stops": route_stops,
        "route_legs": empty_route_legs,
        "delivery_assignments": assignments,
        "customer_sites": sites,
        "vehicles": empty_vehicles,
        "vehicle_capacities": capacities,
        "shifts": shifts,
        "deliveries": empty_deliveries,
    }


@pytest.fixture()
def config() -> AppConfig:
    return AppConfig(seed=77, anomaly=AnomalyConfig(instances_per_type=1))


@pytest.fixture()
def tables() -> dict[str, pd.DataFrame]:
    return make_clean_tables()


def test_clean_snapshot_has_no_detections(tables, config) -> None:
    output = detect_anomalies(tables, config)
    assert output.detections.empty
    assert set(output.counts_by_type.values()) == {0}
    assert set(output.counts_by_type) == {item.value for item in AnomalyType}


def test_injection_is_complete_and_does_not_mutate_input(tables, config) -> None:
    originals = {name: frame.copy(deep=True) for name, frame in tables.items()}
    result = inject_anomalies(tables, config)
    for name, original in originals.items():
        pd.testing.assert_frame_equal(tables[name], original)
    expected = {item.value: 1 for item in AnomalyType}
    assert result.counts_by_type == expected
    assert len(result.ground_truth) == 6
    assert result.ground_truth["anomaly_id"].is_unique
    assert result.ground_truth["issue_type"].astype(str).value_counts().to_dict() == expected
    assert result.ground_truth["anomaly_id"].str.fullmatch(r"GT-\d{4}-\d{6}").all()


def test_injection_is_deterministic(tables, config) -> None:
    first = inject_anomalies(tables, config)
    second = inject_anomalies(tables, config)
    pd.testing.assert_frame_equal(first.ground_truth, second.ground_truth)
    for name in first.tables:
        pd.testing.assert_frame_equal(first.tables[name], second.tables[name])


def test_detection_recovers_every_injected_anomaly(tables, config) -> None:
    injection = inject_anomalies(tables, config)
    evaluation = detect_and_evaluate(
        injection.tables, injection.ground_truth, config
    )
    expected = {item.value: 1 for item in AnomalyType}
    assert evaluation.counts_by_type == expected
    assert evaluation.metrics["tp"].eq(1).all()
    assert evaluation.metrics["fp"].eq(0).all()
    assert evaluation.metrics["fn"].eq(0).all()
    assert evaluation.metrics["precision"].eq(1.0).all()
    assert evaluation.metrics["recall"].eq(1.0).all()
    assert evaluation.metrics["f1"].eq(1.0).all()
    assert evaluation.detections["detection_id"].is_unique
    assert evaluation.detections["detection_id"].str.fullmatch(r"DET-\d{4}-\d{6}").all()


def test_detection_is_deterministic(tables, config) -> None:
    injection = inject_anomalies(tables, config)
    first = detect_and_evaluate(injection.tables, injection.ground_truth, config)
    second = detect_and_evaluate(injection.tables, injection.ground_truth, config)
    pd.testing.assert_frame_equal(first.detections, second.detections)
    pd.testing.assert_frame_equal(first.metrics, second.metrics)


def test_detection_requires_operational_tables(config) -> None:
    with pytest.raises(AnomalyDetectionError):
        detect_anomalies({}, config)


def test_injection_rejects_insufficient_candidates(tables) -> None:
    config = AppConfig(seed=77, anomaly=AnomalyConfig(instances_per_type=6))
    with pytest.raises(AnomalyInjectionError, match="candidates"):
        inject_anomalies(tables, config)


def test_evaluation_counts_false_positives_and_true_negatives(config) -> None:
    ground_truth = pd.DataFrame(
        {
            "run_id": [RUN_ID],
            "issue_type": [AnomalyType.STOP_OMITTED.value],
            "entity_id": ["DEL-2026-000001"],
        }
    )
    detections = pd.DataFrame(
        {
            "issue_type": [
                AnomalyType.STOP_OMITTED.value,
                AnomalyType.STOP_OMITTED.value,
            ],
            "entity_id": ["DEL-2026-000001", "DEL-2026-000099"],
        }
    )
    metrics = evaluate_detections(
        ground_truth,
        detections,
        {AnomalyType.STOP_OMITTED.value: {"DEL-2026-000001", "DEL-2026-000002", "DEL-2026-000003"}},
        config,
    )
    row = metrics.set_index("issue_type").loc[AnomalyType.STOP_OMITTED.value]
    assert (row["tp"], row["fp"], row["fn"], row["tn"]) == (1, 1, 0, 2)
    assert row["precision"] == 0.5
    assert row["recall"] == 1.0
    assert row["f1"] == pytest.approx(0.666667)
    assert row["evaluated_universe"] == 4


def test_evaluation_reports_missed_anomalies(config) -> None:
    ground_truth = pd.DataFrame(
        {
            "run_id": [RUN_ID],
            "issue_type": [AnomalyType.DRIVER_SHIFT_EXCEEDED.value],
            "entity_id": ["RTE-2026-000001"],
        }
    )
    detections = pd.DataFrame(columns=["issue_type", "entity_id"])
    metrics = evaluate_detections(
        ground_truth,
        detections,
        {AnomalyType.DRIVER_SHIFT_EXCEEDED.value: {"RTE-2026-000001"}},
        config,
    )
    row = metrics.set_index("issue_type").loc[
        AnomalyType.DRIVER_SHIFT_EXCEEDED.value
    ]
    assert (row["tp"], row["fp"], row["fn"], row["tn"]) == (0, 0, 1, 0)
    assert row["precision"] == 0.0
    assert row["recall"] == 0.0
    assert row["f1"] == 0.0
