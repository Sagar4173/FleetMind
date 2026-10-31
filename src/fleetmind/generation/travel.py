"""Deterministic base distance and traffic-aware travel-time model.

The model deliberately separates two artifacts:

* ``travel_time_matrix`` stores the static, distance-derived base matrix for a
  site set (one row per ordered location pair).
* route legs persist the exact multiplier, scenario and travel minutes used by
  planning and execution. This avoids materialising a second 30-day matrix
  while keeping every routing lookup traceable.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime

import pandas as pd

from fleetmind.generation.geography import UnknownLocationError, haversine_km
from fleetmind.models.entities import ScenarioConfiguration

TRAVEL_MODEL_VERSION = "haversine-road-v1"
DEFAULT_SPEED_KMH = 45.0
DEFAULT_ROAD_FACTOR = 1.22
DEFAULT_FIXED_MINUTES = 2.5
AM_PEAK_START = 6 * 60 + 30
AM_PEAK_END = 9 * 60 + 30
PM_PEAK_START = 15 * 60 + 30
PM_PEAK_END = 18 * 60 + 30


class TravelTimeModel:
    """Base distance matrix plus deterministic traffic-adjusted travel times."""

    def __init__(
        self,
        locations: Mapping[str, tuple[float, float]],
        model_version: str = TRAVEL_MODEL_VERSION,
        speed_kmh: float = DEFAULT_SPEED_KMH,
        road_factor: float = DEFAULT_ROAD_FACTOR,
        depot_id: str | None = None,
    ) -> None:
        if speed_kmh <= 0 or road_factor <= 0:
            raise ValueError("speed and road factor must be positive")
        if len(locations) == 0:
            raise ValueError("at least one location is required")
        self.locations = {key: (float(value[0]), float(value[1])) for key, value in locations.items()}
        self.model_version = model_version
        self.speed_kmh = speed_kmh
        self.road_factor = road_factor
        if depot_id is None:
            depot_id = next((k for k in self.locations if k.startswith("DEP-")), None)
        self.depot_id = depot_id
        self._distance_cache: dict[tuple[str, str], float] = {}
        self._base_cache: dict[tuple[str, str], float] = {}

    def _coords(self, location_id: str) -> tuple[float, float]:
        try:
            return self.locations[location_id]
        except KeyError as exc:
            raise UnknownLocationError(location_id) from exc

    def distance_km(self, origin_id: str, destination_id: str) -> float:
        if origin_id == destination_id:
            self._coords(origin_id)
            return 0.0
        key = (origin_id, destination_id)
        if key not in self._distance_cache:
            raw = haversine_km(self._coords(origin_id), self._coords(destination_id))
            self._distance_cache[key] = round(raw * self.road_factor, 3)
        return self._distance_cache[key]

    def base_travel_minutes(self, origin_id: str, destination_id: str) -> float:
        if origin_id == destination_id:
            self._coords(origin_id)
            return 0.0
        key = (origin_id, destination_id)
        if key not in self._base_cache:
            distance = self.distance_km(origin_id, destination_id)
            minutes = (distance / self.speed_kmh) * 60.0 + DEFAULT_FIXED_MINUTES
            self._base_cache[key] = round(minutes, 2)
        return self._base_cache[key]

    def base_matrix_frame(self, location_ids: Iterable[str]) -> pd.DataFrame:
        ids = list(dict.fromkeys(location_ids))
        for location_id in ids:
            self._coords(location_id)
        rows = [
            {
                "origin_id": origin,
                "destination_id": destination,
                "distance_km": self.distance_km(origin, destination),
                "base_travel_minutes": self.base_travel_minutes(origin, destination),
                "model_version": self.model_version,
            }
            for origin in ids
            for destination in ids
        ]
        return pd.DataFrame(rows)

    @staticmethod
    def _is_weekday(day: int) -> bool:
        return day < 5

    def _minute_of_day(self, departure: datetime) -> int:
        return departure.hour * 60 + departure.minute

    def _direction_factor(
        self, origin_id: str, destination_id: str, peak: bool
    ) -> float:
        if not peak or self.depot_id is None:
            return 1.0
        depot = self.locations.get(self.depot_id)
        if depot is None:
            return 1.0
        origin_to_depot = haversine_km(self._coords(origin_id), depot)
        destination_to_depot = haversine_km(self._coords(destination_id), depot)
        if destination_to_depot < origin_to_depot - 0.1:
            return 1.05  # inbound toward the depot during a peak
        if destination_to_depot > origin_to_depot + 0.1:
            return 0.98  # outbound is slightly freer
        return 1.0

    def travel_minutes(
        self,
        origin_id: str,
        destination_id: str,
        scenario: ScenarioConfiguration,
        departure: datetime,
    ) -> float:
        """Time-of-day travel time using a specific day's scenario."""
        if departure.date() != scenario.scenario_date:
            raise ValueError(
                "departure date must match scenario date for traceable lookups"
            )
        if origin_id == destination_id:
            self._coords(origin_id)
            return 0.0
        base = self.base_travel_minutes(origin_id, destination_id)
        minute = self._minute_of_day(departure)
        am_peak = AM_PEAK_START <= minute < AM_PEAK_END
        pm_peak = PM_PEAK_START <= minute < PM_PEAK_END
        weekday = self._is_weekday(departure.weekday())
        peak = weekday and (am_peak or pm_peak)
        if peak:
            peak_factor = scenario.am_peak_multiplier if am_peak else scenario.pm_peak_multiplier
        else:
            peak_factor = 1.0
        direction_factor = self._direction_factor(origin_id, destination_id, peak)
        multiplier = max(
            0.5,
            scenario.traffic_index
            * peak_factor
            * scenario.weather_factor
            * direction_factor,
        )
        delay = scenario.network_delay_minutes if base > 3.0 else 0.0
        return round(base * multiplier + delay, 2)

    def planning_minutes(
        self, origin_id: str, destination_id: str, scenario: ScenarioConfiguration
    ) -> float:
        """Static day-level travel time used inside the routing model."""
        if origin_id == destination_id:
            self._coords(origin_id)
            return 0.0
        base = self.base_travel_minutes(origin_id, destination_id)
        weekday = self._is_weekday(scenario.scenario_date.weekday())
        if weekday:
            peak_factor = (scenario.am_peak_multiplier + scenario.pm_peak_multiplier) / 2.0
        else:
            peak_factor = 1.0
        multiplier = max(
            0.5,
            scenario.traffic_index * peak_factor * scenario.weather_factor * 1.02,
        )
        return round(base * multiplier, 2)

    def planning_multiplier(self, scenario: ScenarioConfiguration) -> float:
        weekday = self._is_weekday(scenario.scenario_date.weekday())
        peak_factor = (
            (scenario.am_peak_multiplier + scenario.pm_peak_multiplier) / 2.0
            if weekday
            else 1.0
        )
        return max(
            0.5,
            scenario.traffic_index * peak_factor * scenario.weather_factor * 1.02,
        )

    def matrix_for_day(
        self,
        location_ids: Iterable[str],
        scenario: ScenarioConfiguration,
    ) -> tuple[list[str], list[list[int]], list[list[float]]]:
        """Return integer-minute and kilometre matrices for one planning day."""
        ids = list(dict.fromkeys(location_ids))
        minutes: list[list[int]] = []
        distances: list[list[float]] = []
        for origin in ids:
            minute_row: list[int] = []
            distance_row: list[float] = []
            for destination in ids:
                base = self.base_travel_minutes(origin, destination)
                scaled = base * self.planning_multiplier(scenario)
                # OR-Tools requires integer transit values; round up to avoid
                # optimistic feasibility at time-window boundaries.
                minute_row.append(int(math.ceil(scaled)) if scaled > 0 else 0)
                distance_row.append(self.distance_km(origin, destination))
            minutes.append(minute_row)
            distances.append(distance_row)
        return ids, minutes, distances
