"""Deterministic synthetic geography helpers."""

from __future__ import annotations

import math
import random

EARTH_RADIUS_KM = 6371.0088


class UnknownLocationError(KeyError):
    """Raised when a travel lookup references an unknown location."""


def haversine_km(
    origin: tuple[float, float], destination: tuple[float, float]
) -> float:
    """Great-circle distance in kilometres between two (lat, lon) points."""
    lat1, lon1 = origin
    lat2, lon2 = destination
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _km_to_degrees(km: float, latitude: float) -> tuple[float, float]:
    lat_degrees = km / 111.32
    lon_degrees = km / (111.32 * max(0.01, math.cos(math.radians(latitude))))
    return lat_degrees, lon_degrees


def sample_point_near(
    center_lat: float,
    center_lon: float,
    spread_km: float,
    bounds: tuple[float, float, float, float],
    rng_seed: int,
) -> tuple[float, float]:
    """Sample a point around a cluster centre, clipped to geographic bounds.

    ``bounds`` is ``(min_lat, max_lat, min_lon, max_lon)``. Sampling is local
    and seeded so that the same seed always yields the same coordinate.
    """
    rng = random.Random(rng_seed)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    radius = abs(rng.gauss(0.0, spread_km / 2.3))
    radius = min(radius, spread_km * 1.6)
    dlat_km = radius * math.cos(angle)
    dlon_km = radius * math.sin(angle)
    lat_offset, lon_offset = _km_to_degrees(1.0, center_lat)
    lat = center_lat + dlat_km * lat_offset
    lon = center_lon + dlon_km * lon_offset
    min_lat, max_lat, min_lon, max_lon = bounds
    lat = min(max(lat, min_lat + 1e-4), max_lat - 1e-4)
    lon = min(max(lon, min_lon + 1e-4), max_lon - 1e-4)
    return round(lat, 6), round(lon, 6)


def point_inside_bounds(
    lat: float, lon: float, bounds: tuple[float, float, float, float]
) -> bool:
    min_lat, max_lat, min_lon, max_lon = bounds
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
