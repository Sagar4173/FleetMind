"""Validated, mergeable configuration for FleetMind runs."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleetmind.models.enums import HandlingClass, Priority, VehicleType

DATASET_VERSION = "1.0.0"
PIPELINE_VERSION = "1.0.0"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegionConfig(StrictModel):
    name: str = "DFW Metro"
    center_lat: float = 32.7767
    center_lon: float = -96.7970
    min_lat: float = 32.55
    max_lat: float = 33.05
    min_lon: float = -97.15
    max_lon: float = -96.45
    depot_id: str = "DEP-001"
    depot_name: str = "FleetMind Dallas Depot"
    depot_lat: float = 32.7767
    depot_lon: float = -96.7970
    state: str = "TX"
    cities: list[str] = Field(
        default_factory=lambda: ["Dallas", "Fort Worth", "Arlington", "Plano", "Irving"]
    )
    clusters: int = Field(default=7, ge=1, le=50)
    cluster_spread_km: float = Field(default=11.0, gt=0, le=100)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "RegionConfig":
        if self.min_lat >= self.max_lat or self.min_lon >= self.max_lon:
            raise ValueError("region minimum bounds must be less than maximum bounds")
        if not (self.min_lat <= self.center_lat <= self.max_lat):
            raise ValueError("center_lat must lie inside region bounds")
        if not (self.min_lon <= self.center_lon <= self.max_lon):
            raise ValueError("center_lon must lie inside region bounds")
        if not (self.min_lat <= self.depot_lat <= self.max_lat):
            raise ValueError("depot_lat must lie inside region bounds")
        if not (self.min_lon <= self.depot_lon <= self.max_lon):
            raise ValueError("depot_lon must lie inside region bounds")
        if not self.cities:
            raise ValueError("at least one city is required")
        return self


class HorizonConfig(StrictModel):
    start_date: date = date(2026, 11, 2)
    days: int = Field(default=30, ge=1, le=366)

    @field_validator("start_date", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        return date.fromisoformat(str(value))


class GenerationConfig(StrictModel):
    customer_sites: int = Field(default=500, ge=2, le=100_000)
    orders: int = Field(default=2000, ge=1, le=500_000)
    vehicles: int = Field(default=100, ge=1, le=10_000)
    drivers: int = Field(default=100, ge=1, le=10_000)
    site_active_ratio: float = Field(default=0.97, ge=0.5, le=1.0)
    shift_availability_weekday: float = Field(default=0.92, ge=0.2, le=1.0)
    shift_availability_weekend: float = Field(default=0.62, ge=0.2, le=1.0)
    depot_ready_time: str = "06:30"
    order_lead_days_min: int = Field(default=1, ge=0, le=30)
    order_lead_days_max: int = Field(default=5, ge=0, le=60)
    priority_mix: dict[Priority, float] = Field(
        default_factory=lambda: {
            Priority.LOW: 0.15,
            Priority.STANDARD: 0.55,
            Priority.HIGH: 0.23,
            Priority.URGENT: 0.07,
        }
    )
    handling_mix: dict[HandlingClass, float] = Field(
        default_factory=lambda: {
            HandlingClass.AMBIENT: 0.62,
            HandlingClass.REFRIGERATED: 0.14,
            HandlingClass.FRAGILE: 0.16,
            HandlingClass.BULK: 0.08,
        }
    )
    fleet_mix: dict[VehicleType, float] = Field(
        default_factory=lambda: {
            VehicleType.CARGO_VAN: 0.45,
            VehicleType.BOX_TRUCK: 0.35,
            VehicleType.REFRIGERATED_TRUCK: 0.12,
            VehicleType.FLATBED: 0.08,
        }
    )

    @field_validator("depot_ready_time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value

    @model_validator(mode="after")
    def _validate_mix(self) -> "GenerationConfig":
        if self.order_lead_days_min > self.order_lead_days_max:
            raise ValueError("order_lead_days_min must be <= order_lead_days_max")
        for name, mix in (
            ("priority_mix", self.priority_mix),
            ("handling_mix", self.handling_mix),
            ("fleet_mix", self.fleet_mix),
        ):
            if not mix:
                raise ValueError(f"{name} cannot be empty")
            if any(v < 0 for v in mix.values()):
                raise ValueError(f"{name} values must be non-negative")
            if abs(sum(mix.values()) - 1.0) > 1e-6:
                raise ValueError(f"{name} values must sum to 1.0")
        if self.drivers < 1:
            raise ValueError("at least one driver is required")
        return self


class RoutingConfig(StrictModel):
    time_limit_seconds: int = Field(default=10, ge=1, le=3600)
    workers: int = Field(default=1, ge=1, le=16)
    max_stops_per_route: int = Field(default=14, ge=1, le=200)
    fixed_vehicle_cost: int = Field(default=80_000, ge=0)
    unserved_penalty: int = Field(default=5_000_000, ge=1)
    distance_weight: float = Field(default=1.0, gt=0)
    execution_buffer_minutes: int = Field(default=30, ge=0, le=240)
    priority_multipliers: dict[Priority, float] = Field(
        default_factory=lambda: {
            Priority.LOW: 0.7,
            Priority.STANDARD: 1.0,
            Priority.HIGH: 1.6,
            Priority.URGENT: 2.5,
        }
    )
    allow_dropping: bool = True

    @model_validator(mode="after")
    def _validate_priority_multipliers(self) -> "RoutingConfig":
        if set(self.priority_multipliers) != set(Priority):
            raise ValueError("priority_multipliers must define every Priority")
        if any(v <= 0 for v in self.priority_multipliers.values()):
            raise ValueError("priority multipliers must be positive")
        return self


class SimulationConfig(StrictModel):
    dispatch_delay_max_minutes: int = Field(default=8, ge=0, le=120)
    travel_variance_pct: float = Field(default=0.06, ge=0, le=0.5)
    service_variance_pct: float = Field(default=0.15, ge=0, le=0.5)
    late_grace_minutes: int = Field(default=15, ge=0, le=120)
    failed_attempt_service_minutes: int = Field(default=8, ge=1, le=120)
    customer_unavailable_base_rate: float = Field(default=0.006, ge=0, le=0.2)
    proof_of_delivery: bool = True


class AnomalyConfig(StrictModel):
    instances_per_type: int = Field(default=3, ge=1, le=50)
    coordinate_margin_degrees: float = Field(default=0.35, gt=0, le=10)


class ReportingConfig(StrictModel):
    on_time_grace_minutes: int = Field(default=15, ge=0, le=120)
    top_n: int = Field(default=10, ge=1, le=100)


class AppConfig(StrictModel):
    project: str = "FleetMind"
    seed: int = 20261022
    dataset_version: str = DATASET_VERSION
    region: RegionConfig = Field(default_factory=RegionConfig)
    horizon: HorizonConfig = Field(default_factory=HorizonConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    anomaly: AnomalyConfig = Field(default_factory=AnomalyConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    @model_validator(mode="after")
    def _validate_fleet(self) -> "AppConfig":
        if self.generation.vehicles < 1:
            raise ValueError("at least one vehicle is required")
        if self.generation.orders > 999_999:
            raise ValueError("order count exceeds supported six-digit ID range")
        if self.horizon.days > 366:
            raise ValueError("horizon cannot exceed one year")
        return self

    @property
    def id_year(self) -> int:
        return self.horizon.start_date.year

    @property
    def service_dates(self) -> list[date]:
        from datetime import timedelta

        return [self.horizon.start_date + timedelta(days=i) for i in range(self.horizon.days)]

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def config_hash(self) -> str:
        payload = json.dumps(
            {"dataset_version": self.dataset_version, "config": self.canonical_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def run_id(self) -> str:
        return f"RUN-{self.config_hash()[:12].upper()}"

    def to_yaml(self, path: Path | str) -> None:
        Path(path).write_text(
            yaml.safe_dump(self.canonical_dict(), sort_keys=False), encoding="utf-8"
        )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml_with_extends(path: Path, stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    if path in stack:
        chain = " -> ".join(str(p) for p in (*stack, path))
        raise ValueError(f"configuration extends cycle detected: {chain}")
    if not path.exists():
        raise FileNotFoundError(f"configuration file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"configuration must be a YAML mapping: {path}")
    parent_name = raw.pop("extends", None)
    if parent_name:
        parent = (path.parent / str(parent_name)).resolve()
        parent_data = _read_yaml_with_extends(parent, (*stack, path))
        return _deep_merge(parent_data, raw)
    return raw


def load_config(path: Path | str) -> AppConfig:
    resolved = Path(path)
    if not resolved.is_absolute() and not resolved.exists():
        resolved = CONFIG_DIR / resolved
    payload = _read_yaml_with_extends(resolved.resolve())
    payload.setdefault("dataset_version", DATASET_VERSION)
    return AppConfig.model_validate(payload)


def load_config_dict(payload: dict[str, Any]) -> AppConfig:
    payload = dict(payload)
    payload.setdefault("dataset_version", DATASET_VERSION)
    return AppConfig.model_validate(payload)
