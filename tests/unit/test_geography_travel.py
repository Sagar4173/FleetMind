from __future__ import annotations

from datetime import date, datetime

import pytest

from fleetmind.generation.geography import (
    UnknownLocationError,
    haversine_km,
    sample_point_near,
)
from fleetmind.generation.travel import TravelTimeModel
from fleetmind.models.entities import ScenarioConfiguration
from fleetmind.models.enums import Weather


def scenario(
    traffic_index: float = 1.0,
    am: float = 1.4,
    pm: float = 1.3,
    weather_factor: float = 1.0,
) -> ScenarioConfiguration:
    return ScenarioConfiguration(
        run_id="RUN-TEST",
        scenario_id="SCN-2026-0001",
        scenario_date=date(2026, 11, 3),
        day_name="Tuesday",
        traffic_index=traffic_index,
        weather=Weather.CLEAR,
        weather_factor=weather_factor,
        am_peak_multiplier=am,
        pm_peak_multiplier=pm,
        network_delay_minutes=0,
        incident_zone="NONE",
        seed=42,
    )


@pytest.fixture()
def locations() -> dict[str, tuple[float, float]]:
    return {
        "DEP-001": (32.7767, -96.7970),
        "CUS-000001": (32.9000, -96.9000),  # northwest of depot
        "CUS-000002": (32.6500, -96.6500),  # southeast of depot
        "CUS-000003": (32.7767, -96.7970),
    }


@pytest.fixture()
def model(locations: dict[str, tuple[float, float]]) -> TravelTimeModel:
    return TravelTimeModel(locations=locations, model_version="test-v1")


def test_haversine_is_symmetric_and_plausible() -> None:
    dallas = (32.7767, -96.7970)
    fort_worth = (32.7555, -97.3308)
    distance = haversine_km(dallas, fort_worth)
    assert haversine_km(fort_worth, dallas) == pytest.approx(distance)
    assert 45 <= distance <= 60
    assert haversine_km(dallas, dallas) == pytest.approx(0.0)


def test_distance_uses_road_factor_and_is_symmetric(
    model: TravelTimeModel,
) -> None:
    distance = model.distance_km("DEP-001", "CUS-000001")
    reverse = model.distance_km("CUS-000001", "DEP-001")
    assert distance > 0
    assert distance == pytest.approx(reverse)
    assert distance > haversine_km((32.7767, -96.7970), (32.9, -96.9))


def test_same_location_zero_distance_and_time(model: TravelTimeModel) -> None:
    assert model.distance_km("DEP-001", "DEP-001") == 0
    assert model.base_travel_minutes("DEP-001", "DEP-001") == 0
    assert (
        model.travel_minutes(
            "DEP-001", "DEP-001", scenario(), datetime(2026, 11, 3, 8, 0)
        )
        == 0
    )


def test_unknown_location_raises(model: TravelTimeModel) -> None:
    with pytest.raises(UnknownLocationError):
        model.distance_km("CUS-999999", "DEP-001")
    with pytest.raises(UnknownLocationError):
        model.travel_minutes(
            "DEP-001", "CUS-999999", scenario(), datetime(2026, 11, 3, 8, 0)
        )


def test_peak_travel_slower_than_midday(model: TravelTimeModel) -> None:
    sc = scenario()
    peak = model.travel_minutes(
        "DEP-001", "CUS-000001", sc, datetime(2026, 11, 3, 8, 0)
    )
    midday = model.travel_minutes(
        "DEP-001", "CUS-000001", sc, datetime(2026, 11, 3, 12, 0)
    )
    assert peak > midday > 0


def test_higher_traffic_index_increases_travel(model: TravelTimeModel) -> None:
    low = model.travel_minutes(
        "DEP-001", "CUS-000001", scenario(traffic_index=0.9), datetime(2026, 11, 3, 12, 0)
    )
    high = model.travel_minutes(
        "DEP-001", "CUS-000001", scenario(traffic_index=1.3), datetime(2026, 11, 3, 12, 0)
    )
    assert high > low


def test_weather_factor_increases_travel(model: TravelTimeModel) -> None:
    clear = model.travel_minutes(
        "DEP-001", "CUS-000001", scenario(weather_factor=1.0), datetime(2026, 11, 3, 12, 0)
    )
    storm = model.travel_minutes(
        "DEP-001", "CUS-000001", scenario(weather_factor=1.3), datetime(2026, 11, 3, 12, 0)
    )
    assert storm > clear


def test_planning_matrix_entries_are_positive_and_recorded(
    model: TravelTimeModel,
) -> None:
    frame = model.base_matrix_frame(["DEP-001", "CUS-000001", "CUS-000002"])
    assert len(frame) == 9
    assert set(frame.columns) == {
        "origin_id",
        "destination_id",
        "distance_km",
        "base_travel_minutes",
        "model_version",
    }
    entries = frame[(frame.origin_id == "DEP-001") & (frame.destination_id == "CUS-000001")]
    assert entries.iloc[0]["distance_km"] > 0
    assert entries.iloc[0]["base_travel_minutes"] > 0
    planning = model.planning_minutes("DEP-001", "CUS-000001", scenario())
    assert planning >= model.base_travel_minutes("DEP-001", "CUS-000001")


def test_sample_point_near_stays_inside_bounds() -> None:
    bounds = (32.55, 33.05, -97.15, -96.45)
    for index in range(200):
        lat, lon = sample_point_near(
            center_lat=32.7767,
            center_lon=-96.7970,
            spread_km=20,
            bounds=bounds,
            rng_seed=1000 + index,
        )
        assert bounds[0] <= lat <= bounds[1]
        assert bounds[2] <= lon <= bounds[3]
