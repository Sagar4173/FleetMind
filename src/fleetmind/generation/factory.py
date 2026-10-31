"""Deterministic synthetic logistics data factory.

Parents are generated before children. Every random decision is made from a
seeded generator derived from the run seed and a component label. Records are
validated with the Pydantic domain schemas before the immutable snapshot is
returned.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from faker import Faker

from fleetmind.config import AppConfig
from fleetmind.generation import ids
from fleetmind.generation.geography import sample_point_near
from fleetmind.generation.travel import TravelTimeModel
from fleetmind.models.entities import (
    CustomerSite,
    Delivery,
    Depot,
    Driver,
    DriverCertification,
    Order,
    ScenarioConfiguration,
    Shift,
    TimeWindow,
    Vehicle,
    VehicleCapacity,
    VehicleEquipment,
)
from fleetmind.models.enums import (
    Certification,
    DriverStatus,
    Equipment,
    FailureReason,
    HandlingClass,
    LicenseClass,
    OrderStatus,
    OwnerType,
    Priority,
    ShiftStatus,
    SiteAccessClass,
    SiteType,
    TimeWindowStrictness,
    VehicleStatus,
    VehicleType,
    Weather,
)

PIPELINE_TABLES = (
    "depots",
    "customer_sites",
    "time_windows",
    "orders",
    "deliveries",
    "vehicles",
    "vehicle_capacities",
    "vehicle_equipment",
    "drivers",
    "driver_certifications",
    "shifts",
    "travel_scenarios",
    "travel_time_matrix",
)

HANDLING_EQUIPMENT = {
    HandlingClass.AMBIENT: Equipment.AMBIENT,
    HandlingClass.REFRIGERATED: Equipment.REFRIGERATED,
    HandlingClass.FRAGILE: Equipment.FRAGILE,
    HandlingClass.BULK: Equipment.BULK,
}

VEHICLE_SPECS: dict[VehicleType, dict[str, Any]] = {
    VehicleType.CARGO_VAN: {
        "makes": [("Ford", "Transit 250"), ("Mercedes-Benz", "Sprinter 2500"), ("Ram", "ProMaster 2500")],
        "capacity_units": 120,
        "max_weight_kg": 1_500.0,
        "max_volume_m3": 13.0,
        "license": LicenseClass.CLASS_C,
        "equipment": [Equipment.AMBIENT, Equipment.FRAGILE],
        "max_duration": 540,
    },
    VehicleType.BOX_TRUCK: {
        "makes": [("Freightliner", "M2 106"), ("Isuzu", "NPR-HD"), ("International", "MV607")],
        "capacity_units": 420,
        "max_weight_kg": 7_000.0,
        "max_volume_m3": 45.0,
        "license": LicenseClass.CLASS_B,
        "equipment": [Equipment.AMBIENT, Equipment.FRAGILE, Equipment.BULK, Equipment.LIFT_GATE],
        "max_duration": 570,
    },
    VehicleType.REFRIGERATED_TRUCK: {
        "makes": [("Volvo", "VNR Electric"), ("Peterbilt", "220EV"), ("Freightliner", "eCascadia")],
        "capacity_units": 300,
        "max_weight_kg": 6_500.0,
        "max_volume_m3": 40.0,
        "license": LicenseClass.CLASS_A,
        "equipment": [Equipment.AMBIENT, Equipment.REFRIGERATED, Equipment.LIFT_GATE],
        "max_duration": 570,
    },
    VehicleType.FLATBED: {
        "makes": [("Peterbilt", "337"), ("Kenworth", "T280"), ("Hino", "338")],
        "capacity_units": 600,
        "max_weight_kg": 9_000.0,
        "max_volume_m3": 55.0,
        "license": LicenseClass.CLASS_A,
        "equipment": [Equipment.BULK, Equipment.LIFT_GATE],
        "max_duration": 600,
    },
}

SITE_TYPE_WEIGHTS = {
    SiteType.RETAIL: 0.30,
    SiteType.WAREHOUSE: 0.18,
    SiteType.CLINIC: 0.13,
    SiteType.RESTAURANT: 0.17,
    SiteType.OFFICE: 0.12,
    SiteType.RESIDENTIAL: 0.10,
}

ACCESS_WEIGHTS = {
    SiteType.RESIDENTIAL: {SiteAccessClass.VAN_ONLY: 0.75, SiteAccessClass.MEDIUM: 0.25},
    SiteType.OFFICE: {
        SiteAccessClass.VAN_ONLY: 0.40,
        SiteAccessClass.MEDIUM: 0.40,
        SiteAccessClass.ALL: 0.20,
    },
    SiteType.RETAIL: {
        SiteAccessClass.VAN_ONLY: 0.15,
        SiteAccessClass.MEDIUM: 0.50,
        SiteAccessClass.ALL: 0.35,
    },
    SiteType.WAREHOUSE: {
        SiteAccessClass.VAN_ONLY: 0.05,
        SiteAccessClass.MEDIUM: 0.15,
        SiteAccessClass.ALL: 0.80,
    },
    SiteType.CLINIC: {
        SiteAccessClass.VAN_ONLY: 0.20,
        SiteAccessClass.MEDIUM: 0.40,
        SiteAccessClass.ALL: 0.40,
    },
    SiteType.RESTAURANT: {
        SiteAccessClass.VAN_ONLY: 0.50,
        SiteAccessClass.MEDIUM: 0.40,
        SiteAccessClass.ALL: 0.10,
    },
}

SERVICE_LEAD_TEMPLATE = {
    Priority.LOW: 300,
    Priority.STANDARD: 240,
    Priority.HIGH: 180,
    Priority.URGENT: 90,
}
TIGHT_WINDOW_TEMPLATE = {
    Priority.LOW: 240,
    Priority.STANDARD: 150,
    Priority.HIGH: 90,
    Priority.URGENT: 60,
}


def _rng(seed: int, *labels: str) -> np.random.Generator:
    digest = hashlib.sha256(
        (f"{seed}:" + ":".join(labels)).encode("utf-8")
    ).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _weights(mapping: dict[Enum, float], keys: list[Enum]) -> list[float]:
    values = np.array([float(mapping.get(key, 0.0)) for key in keys], dtype=float)
    total = values.sum()
    if total <= 0:
        raise ValueError("probability mixture has no positive weight")
    return list(values / total)


def _choice(rng: np.random.Generator, values: list[Any], mapping: dict[Enum, float]) -> Any:
    weights = _weights(mapping, values)
    return values[int(rng.choice(len(values), p=weights))]


def _largest_remainder(count: int, mapping: dict[Enum, float], keys: list[Enum]) -> dict[Enum, int]:
    if count < 1:
        raise ValueError("fleet count must be positive")
    raw = {key: count * float(mapping.get(key, 0.0)) for key in keys}
    floors = {key: int(math.floor(value)) for key, value in raw.items()}
    remaining = count - sum(floors.values())
    order = sorted(keys, key=lambda key: (-(raw[key] - floors[key]), keys.index(key)))
    for key in order[:remaining]:
        floors[key] += 1
    # When count is smaller than the number of fleet types, only the largest
    # weights receive a vehicle.
    return {key: value for key, value in floors.items() if value > 0}


def _minute_time(service_date: date, minute: int) -> datetime:
    return datetime.combine(service_date, dtime()) + timedelta(minutes=minute)


def _to_dt(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column])
    return frame


def _canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in work.columns:
        series = work[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            work[column] = series.dt.strftime("%Y-%m-%dT%H:%M:%S").fillna("")
        elif pd.api.types.is_bool_dtype(series):
            work[column] = series.astype(int)
        elif pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            work[column] = series.map(
                lambda value: value.value if isinstance(value, Enum) else value
            ).astype("string").fillna("")
        else:
            work[column] = series.astype("string").fillna("")
    work = work.sort_values(by=list(work.columns), kind="mergesort").reset_index(drop=True)
    return work


VOLATILE_CHECKSUM_COLUMNS = frozenset({"solve_seconds"})


def logical_checksum(frame: pd.DataFrame) -> str:
    """Stable content hash independent of parquet metadata, row order, or wall time.

    ``solve_seconds`` is a measured wall-clock value, not business data. Excluding it
    keeps checksums comparable across reruns whose solver search ends a few
    milliseconds apart.
    """
    work = frame.drop(
        columns=[column for column in VOLATILE_CHECKSUM_COLUMNS if column in frame.columns]
    )
    canonical = _canonical_frame(work)
    text = canonical.to_csv(index=False, lineterminator="\n", float_format="%.6f")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def table_checksums(tables: dict[str, pd.DataFrame]) -> dict[str, str]:
    return {name: logical_checksum(frame) for name, frame in sorted(tables.items())}


@dataclass
class Dataset:
    config: AppConfig
    run_id: str
    tables: dict[str, pd.DataFrame]
    generation_seconds: float = 0.0
    checksums: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]

    def __contains__(self, name: str) -> bool:
        return name in self.tables

    def copy_tables(self) -> dict[str, pd.DataFrame]:
        return {name: frame.copy(deep=True) for name, frame in self.tables.items()}

    def counts(self) -> dict[str, int]:
        return {name: int(len(frame)) for name, frame in self.tables.items()}

    def to_tables(self) -> dict[str, pd.DataFrame]:
        return self.tables


def generate_dataset(config: AppConfig) -> Dataset:
    """Build the complete deterministic master/transaction snapshot."""
    started = time.perf_counter()
    run_id = config.run_id
    faker = Faker("en_US")
    Faker.seed(config.seed)
    base_date = config.horizon.start_date
    base_ts = datetime.combine(base_date, dtime(8, 0)) - timedelta(days=180)

    depots = _generate_depots(config, run_id)
    customer_sites, cluster_centers = _generate_sites(config, run_id, faker, base_ts)
    vehicles, capacities, vehicle_equipment = _generate_fleet(config, run_id, faker, base_date)
    drivers, certifications = _generate_drivers(
        config, run_id, faker, base_date, vehicles
    )
    shifts = _generate_shifts(
        config, run_id, vehicles, drivers, certifications, base_date
    )
    scenarios = _generate_scenarios(config, run_id, base_date)

    travel_model = TravelTimeModel(
        locations={
            row.depot_id: (row.latitude, row.longitude) for row in depots
        }
        | {
            row.site_id: (row.latitude, row.longitude) for row in customer_sites
        },
        depot_id=config.region.depot_id,
    )
    location_ids = [config.region.depot_id] + [row.site_id for row in customer_sites]
    matrix = travel_model.base_matrix_frame(location_ids)
    matrix.insert(0, "run_id", run_id)
    matrix["run_id"] = matrix["run_id"].astype(str)

    time_windows, orders, deliveries = _generate_orders(
        config, run_id, faker, customer_sites, base_date
    )

    def as_frame(models: list[Any], model: Any) -> pd.DataFrame:
        if not models:
            return pd.DataFrame(columns=list(model.model_fields.keys()))
        return pd.DataFrame([model.model_dump(mode="python") for model in models])

    tables: dict[str, pd.DataFrame] = {
        "depots": as_frame(depots, Depot),
        "customer_sites": as_frame(customer_sites, CustomerSite),
        "time_windows": as_frame(time_windows, TimeWindow),
        "orders": as_frame(orders, Order),
        "deliveries": as_frame(deliveries, Delivery),
        "vehicles": as_frame(vehicles, Vehicle),
        "vehicle_capacities": as_frame(capacities, VehicleCapacity),
        "vehicle_equipment": as_frame(vehicle_equipment, VehicleEquipment),
        "drivers": as_frame(drivers, Driver),
        "driver_certifications": as_frame(certifications, DriverCertification),
        "shifts": as_frame(shifts, Shift),
        "travel_scenarios": as_frame(scenarios, ScenarioConfiguration),
        "travel_time_matrix": matrix,
    }
    for name in (
        "created_at",
        "planned_start",
        "planned_end",
        "window_start",
        "window_end",
        "injected_at",
    ):
        for frame in tables.values():
            if name in frame.columns:
                _to_dt(frame, [name])
    checksums = table_checksums(tables)
    return Dataset(
        config=config,
        run_id=run_id,
        tables=tables,
        generation_seconds=time.perf_counter() - started,
        checksums=checksums,
    )


def _generate_depots(config: AppConfig, run_id: str) -> list[Depot]:
    region = config.region
    return [
        Depot(
            run_id=run_id,
            depot_id=region.depot_id,
            name=region.depot_name,
            address_line="1400 Logistics Parkway",
            city=region.cities[0],
            state=region.state,
            postal_code="75201",
            latitude=region.depot_lat,
            longitude=region.depot_lon,
            open_minute=6 * 60,
            close_minute=22 * 60,
            active=True,
        )
    ]


def _generate_sites(
    config: AppConfig,
    run_id: str,
    faker: Faker,
    base_ts: datetime,
) -> tuple[list[CustomerSite], list[tuple[float, float]]]:
    region = config.region
    rng = _rng(config.seed, "sites")
    bounds = (region.min_lat, region.max_lat, region.min_lon, region.max_lon)
    cluster_centers = [
        sample_point_near(
            region.center_lat,
            region.center_lon,
            spread_km=18.0,
            bounds=bounds,
            rng_seed=config.seed + 7000 + index,
        )
        for index in range(region.clusters)
    ]
    sites: list[CustomerSite] = []
    site_types = list(SITE_TYPE_WEIGHTS)
    for index in range(1, config.generation.customer_sites + 1):
        site_id = ids.customer_site_id(index)
        center = cluster_centers[(index - 1) % len(cluster_centers)]
        spread = 4.0 + float(rng.uniform(0.0, 5.0))
        latitude, longitude = sample_point_near(
            center[0],
            center[1],
            spread_km=spread,
            bounds=bounds,
            rng_seed=config.seed * 31 + index,
        )
        site_type = _choice(rng, site_types, SITE_TYPE_WEIGHTS)
        access_type = _choice(
            rng, list(ACCESS_WEIGHTS[site_type]), ACCESS_WEIGHTS[site_type]
        )
        if site_type == SiteType.WAREHOUSE:
            start_options = [360, 390, 420, 480]
            end_options = [960, 1020, 1080]
        else:
            start_options = [390, 420, 480, 540, 600]
            end_options = [900, 960, 1020, 1080]
        receiving_start = int(rng.choice(start_options))
        eligible_ends = [end for end in end_options if end > receiving_start + 300]
        receiving_end = int(rng.choice(eligible_ends)) if eligible_ends else 1080
        customer = faker.company()
        site_name = f"{customer} {site_type.value.replace('_', ' ').title()}"
        active = bool(rng.random() < config.generation.site_active_ratio) or index <= 2
        risk = float(np.clip(rng.beta(2.0, 14.0), 0.0, 0.35))
        effective_from = config.horizon.start_date - timedelta(
            days=int(rng.integers(400, 2200))
        )
        sites.append(
            CustomerSite(
                run_id=run_id,
                site_id=site_id,
                customer_name=customer,
                site_name=site_name,
                address_line=faker.street_address(),
                city=str(rng.choice(region.cities)),
                state=region.state,
                postal_code=faker.postcode(),
                latitude=latitude,
                longitude=longitude,
                site_type=site_type,
                access_class=access_type,
                receiving_start_minute=receiving_start,
                receiving_end_minute=receiving_end,
                dock_available=site_type in {SiteType.WAREHOUSE, SiteType.RETAIL}
                or bool(rng.random() < 0.25),
                contact_name=faker.name(),
                contact_phone=faker.numerify("(214) ###-####"),
                risk_score=round(risk, 4),
                active=active,
                effective_from=effective_from,
                effective_to=None,
                created_at=base_ts
                - timedelta(days=int(rng.integers(0, 300)), minutes=int(rng.integers(0, 600))),
            )
        )
    if not any(site.active for site in sites):
        sites[0] = sites[0].model_copy(update={"active": True})
    return sites, cluster_centers


def _generate_fleet(
    config: AppConfig,
    run_id: str,
    faker: Faker,
    base_date: date,
) -> tuple[list[Vehicle], list[VehicleCapacity], list[VehicleEquipment]]:
    rng = _rng(config.seed, "fleet")
    vehicle_types = list(VehicleType)
    type_counts = _largest_remainder(
        config.generation.vehicles, config.generation.fleet_mix, vehicle_types
    )
    ordered_types: list[VehicleType] = []
    for vehicle_type in vehicle_types:
        ordered_types.extend([vehicle_type] * type_counts.get(vehicle_type, 0))
    shuffled = [ordered_types[i] for i in rng.permutation(len(ordered_types))]
    vehicles: list[Vehicle] = []
    capacities: list[VehicleCapacity] = []
    equipment_rows: list[VehicleEquipment] = []
    forced_active_types: set[VehicleType] = set()
    for index, vehicle_type in enumerate(shuffled, start=1):
        spec = VEHICLE_SPECS[vehicle_type]
        make, model = spec["makes"][int(rng.integers(0, len(spec["makes"])))]
        if vehicle_type not in forced_active_types:
            status = VehicleStatus.ACTIVE
            forced_active_types.add(vehicle_type)
        else:
            roll = float(rng.random())
            status = (
                VehicleStatus.ACTIVE
                if roll < 0.92
                else VehicleStatus.MAINTENANCE
                if roll < 0.97
                else VehicleStatus.RETIRED
            )
        vehicle_id = ids.vehicle_id(index)
        acquisition = base_date - timedelta(days=int(rng.integers(180, 2400)))
        vehicles.append(
            Vehicle(
                run_id=run_id,
                vehicle_id=vehicle_id,
                vehicle_type=vehicle_type,
                make=make,
                model=model,
                model_year=int(rng.integers(2018, 2026)),
                license_plate=f"TX-{index:03d}-{int(rng.integers(1000, 9999))}",
                status=status,
                home_depot_id=config.region.depot_id,
                required_license_class=spec["license"],
                max_route_duration_min=spec["max_duration"],
                odometer_km=round(float(rng.uniform(10_000, 240_000)), 1),
                acquisition_date=acquisition,
                effective_from=acquisition,
                effective_to=None,
            )
        )
        capacities.append(
            VehicleCapacity(
                run_id=run_id,
                capacity_id=ids.capacity_id(index),
                vehicle_id=vehicle_id,
                capacity_units=spec["capacity_units"],
                max_weight_kg=spec["max_weight_kg"],
                max_volume_m3=spec["max_volume_m3"],
                dimension_note="units are synthetic dock handling units; weight/volume are independent hard limits",
            )
        )
        for equipment in spec["equipment"]:
            equipment_rows.append(
                VehicleEquipment(
                    run_id=run_id,
                    vehicle_id=vehicle_id,
                    equipment=equipment,
                )
            )
    return vehicles, capacities, equipment_rows


def _generate_drivers(
    config: AppConfig,
    run_id: str,
    faker: Faker,
    base_date: date,
    vehicles: list[Vehicle],
) -> tuple[list[Driver], list[DriverCertification]]:
    rng = _rng(config.seed, "drivers")
    drivers: list[Driver] = []
    certifications: list[DriverCertification] = []
    for index in range(1, config.generation.drivers + 1):
        vehicle = vehicles[(index - 1) % len(vehicles)]
        first_name = faker.first_name()
        last_name = faker.last_name()
        status = (
            DriverStatus.ACTIVE
            if index <= 3 or float(rng.random()) < 0.94
            else DriverStatus.INACTIVE
        )
        hire_date = base_date - timedelta(days=int(rng.integers(180, 3000)))
        effective_from = hire_date
        driver_id = ids.driver_id(index)
        drivers.append(
            Driver(
                run_id=run_id,
                driver_id=driver_id,
                first_name=first_name,
                last_name=last_name,
                phone=faker.numerify("(469) ###-####"),
                email=f"{first_name.lower()}.{last_name.lower()}{index}@fleetmind.example",
                license_number=f"TX{int(rng.integers(10_000_000, 99_999_999))}",
                license_class=vehicle.required_license_class,
                license_expiry=base_date + timedelta(days=int(rng.integers(400, 2200))),
                hire_date=hire_date,
                home_depot_id=config.region.depot_id,
                status=status,
                max_shift_hours=10.0,
                hourly_cost=round(float(rng.uniform(24.0, 38.0)), 2),
                effective_from=effective_from,
                effective_to=None,
            )
        )
        cert_set: set[Certification] = set()
        if vehicle.vehicle_type == VehicleType.REFRIGERATED_TRUCK and float(rng.random()) < 0.9:
            cert_set.add(Certification.COLD_CHAIN)
        elif float(rng.random()) < 0.08:
            cert_set.add(Certification.COLD_CHAIN)
        if vehicle.vehicle_type in {
            VehicleType.BOX_TRUCK,
            VehicleType.REFRIGERATED_TRUCK,
            VehicleType.FLATBED,
        } and float(rng.random()) < 0.85:
            cert_set.add(Certification.LIFT_GATE)
        if float(rng.random()) < 0.05:
            cert_set.add(Certification.HAZMAT)
        for cert in sorted(cert_set, key=lambda item: item.value):
            certifications.append(
                DriverCertification(
                    run_id=run_id,
                    driver_id=driver_id,
                    certification=cert,
                    issued_date=hire_date + timedelta(days=30),
                    expiry_date=base_date + timedelta(days=int(rng.integers(200, 1600))),
                )
            )
    return drivers, certifications


def _generate_shifts(
    config: AppConfig,
    run_id: str,
    vehicles: list[Vehicle],
    drivers: list[Driver],
    certifications: list[DriverCertification],
    base_date: date,
) -> list[Shift]:
    rng = _rng(config.seed, "shifts")
    vehicle_map = {vehicle.vehicle_id: vehicle for vehicle in vehicles}
    cert_map: dict[str, set[Certification]] = {}
    for cert in certifications:
        cert_map.setdefault(cert.driver_id, set()).add(cert.certification)
    shift_templates = [
        (390, 600),   # 06:30-16:30
        (420, 600),   # 07:00-17:00
        (480, 600),   # 08:00-18:00
        (510, 600),   # 08:30-18:30
        (540, 600),   # 09:00-19:00
        (570, 600),   # 09:30-19:30
    ]
    shifts: list[Shift] = []
    sequence = 0
    for day_index in range(config.horizon.days):
        service_date = base_date + timedelta(days=day_index)
        weekday = service_date.weekday()
        availability = (
            config.generation.shift_availability_weekday
            if weekday < 5
            else config.generation.shift_availability_weekend * (0.75 if weekday == 6 else 1.0)
        )
        day_shifts: list[Shift] = []
        active_drivers = [
            driver for driver in drivers if driver.status == DriverStatus.ACTIVE
        ]
        for driver in active_drivers:
            vehicle = vehicle_map[vehicles[(int(driver.driver_id[-6:]) - 1) % len(vehicles)].vehicle_id]
            if vehicle.status != VehicleStatus.ACTIVE:
                continue
            if float(rng.random()) > availability:
                continue
            start_minute, duration = shift_templates[int(rng.integers(0, len(shift_templates)))]
            start_dt = datetime.combine(service_date, dtime()) + timedelta(minutes=start_minute)
            end_dt = start_dt + timedelta(minutes=duration)
            sequence += 1
            day_shifts.append(
                Shift(
                    run_id=run_id,
                    shift_id=ids.shift_id(config.id_year, sequence),
                    driver_id=driver.driver_id,
                    vehicle_id=vehicle.vehicle_id,
                    service_date=service_date,
                    planned_start=start_dt,
                    planned_end=end_dt,
                    break_minutes=30 if start_minute % 60 == 0 else 45,
                    status=ShiftStatus.SCHEDULED,
                    created_at=start_dt - timedelta(days=7),
                )
            )
        if not day_shifts:
            driver = active_drivers[0]
            vehicle = vehicle_map[vehicles[(int(driver.driver_id[-6:]) - 1) % len(vehicles)].vehicle_id]
            start_dt = datetime.combine(service_date, dtime(8, 0))
            sequence += 1
            day_shifts.append(
                Shift(
                    run_id=run_id,
                    shift_id=ids.shift_id(config.id_year, sequence),
                    driver_id=driver.driver_id,
                    vehicle_id=vehicle.vehicle_id,
                    service_date=service_date,
                    planned_start=start_dt,
                    planned_end=start_dt + timedelta(hours=10),
                    break_minutes=30,
                    status=ShiftStatus.SCHEDULED,
                    created_at=start_dt - timedelta(days=7),
                )
            )
        shifts.extend(day_shifts)
    return shifts


def _generate_scenarios(
    config: AppConfig, run_id: str, base_date: date
) -> list[ScenarioConfiguration]:
    rng = _rng(config.seed, "scenarios")
    weather_weights = {
        Weather.CLEAR: 0.58,
        Weather.CLOUDY: 0.20,
        Weather.RAIN: 0.13,
        Weather.STORM: 0.04,
        Weather.FOG: 0.05,
    }
    weather_factor = {
        Weather.CLEAR: 1.00,
        Weather.CLOUDY: 1.02,
        Weather.RAIN: 1.10,
        Weather.STORM: 1.28,
        Weather.FOG: 1.14,
    }
    scenarios: list[ScenarioConfiguration] = []
    for index in range(config.horizon.days):
        scenario_date = base_date + timedelta(days=index)
        weekday = scenario_date.weekday()
        weather = _choice(rng, list(weather_weights), weather_weights)
        base_traffic = 1.02 if weekday < 5 else 0.88
        traffic = float(np.clip(rng.normal(base_traffic, 0.07), 0.78, 1.30))
        if weather == Weather.STORM:
            traffic = min(1.35, traffic + 0.08)
        am_peak = float(rng.uniform(1.16, 1.42)) if weekday < 5 else float(rng.uniform(1.0, 1.06))
        pm_peak = float(rng.uniform(1.20, 1.48)) if weekday < 5 else float(rng.uniform(1.0, 1.06))
        if weather in {Weather.STORM, Weather.FOG}:
            network_delay = float(rng.uniform(4.0, 10.0))
        elif weather == Weather.RAIN:
            network_delay = float(rng.uniform(1.0, 4.5))
        else:
            network_delay = float(rng.uniform(0.0, 1.5))
        incident_zone = (
            str(rng.choice(config.region.cities)) if network_delay >= 2.0 else "NONE"
        )
        scenarios.append(
            ScenarioConfiguration(
                run_id=run_id,
                scenario_id=ids.scenario_id(config.id_year, index + 1),
                scenario_date=scenario_date,
                day_name=scenario_date.strftime("%A"),
                traffic_index=round(traffic, 3),
                weather=weather,
                weather_factor=weather_factor[weather],
                am_peak_multiplier=round(am_peak, 3),
                pm_peak_multiplier=round(pm_peak, 3),
                network_delay_minutes=round(network_delay, 2),
                incident_zone=incident_zone,
                seed=int(rng.integers(0, 2**31 - 1)),
                notes="synthetic daily traffic and weather scenario",
            )
        )
    return scenarios


def _generate_orders(
    config: AppConfig,
    run_id: str,
    faker: Faker,
    sites: list[CustomerSite],
    base_date: date,
) -> tuple[list[TimeWindow], list[Order], list[Delivery]]:
    rng = _rng(config.seed, "orders")
    active_sites = [site for site in sites if site.active]
    if not active_sites:
        raise ValueError("no active customer sites available for order generation")
    site_map = {site.site_id: site for site in active_sites}
    handling_types = list(HandlingClass)
    priorities = list(Priority)
    source_systems = ["EDI", "PORTAL", "API", "MANUAL"]
    counts_by_day = _delivery_counts(config.generation.orders, config.horizon.days)
    time_windows: list[TimeWindow] = []
    orders: list[Order] = []
    deliveries: list[Delivery] = []
    sequence = 0
    delivery_dates = [base_date + timedelta(days=i) for i in range(config.horizon.days)]
    for day_index, service_date in enumerate(delivery_dates):
        for _ in range(counts_by_day[day_index]):
            sequence += 1
            site = active_sites[int(rng.integers(0, len(active_sites)))]
            handling = _choice(rng, handling_types, config.generation.handling_mix)
            if site.access_class == SiteAccessClass.VAN_ONLY and handling in {
                HandlingClass.REFRIGERATED,
                HandlingClass.BULK,
            }:
                handling = _choice(
                    rng,
                    [HandlingClass.AMBIENT, HandlingClass.FRAGILE],
                    {HandlingClass.AMBIENT: 0.8, HandlingClass.FRAGILE: 0.2},
                )
            priority = _choice(rng, priorities, config.generation.priority_mix)
            demand_units, weight_kg, volume_m3 = _demand_for(handling, rng)
            service_minutes = _service_minutes(handling, demand_units, site.site_type, rng)
            window_start_min, window_end_min = _time_window_bounds(site, priority, rng)
            window_start = _minute_time(service_date, window_start_min)
            window_end = _minute_time(service_date, window_end_min)
            window_id = ids.time_window_id(config.id_year, sequence)
            time_windows.append(
                TimeWindow(
                    run_id=run_id,
                    time_window_id=window_id,
                    owner_type=OwnerType.ORDER,
                    owner_id=ids.order_id(config.id_year, sequence),
                    window_start=window_start,
                    window_end=window_end,
                    strictness=TimeWindowStrictness.HARD,
                    grace_minutes=config.simulation.late_grace_minutes,
                )
            )
            lead_days = int(
                rng.integers(
                    config.generation.order_lead_days_min,
                    config.generation.order_lead_days_max + 1,
                )
            )
            order_date = service_date - timedelta(days=lead_days)
            created_at = datetime.combine(order_date, dtime(8, 0)) + timedelta(
                minutes=int(rng.integers(0, 600))
            )
            order_key = ids.order_id(config.id_year, sequence)
            delivery_key = ids.delivery_id(config.id_year, sequence)
            common = {
                "run_id": run_id,
                "site_id": site.site_id,
                "priority": priority,
                "handling_class": handling,
                "required_equipment": HANDLING_EQUIPMENT[handling],
                "demand_units": demand_units,
                "weight_kg": weight_kg,
                "volume_m3": volume_m3,
                "service_duration_min": service_minutes,
                "time_window_id": window_id,
                "created_at": created_at,
            }
            orders.append(
                Order(
                    order_id=order_key,
                    order_date=order_date,
                    requested_delivery_date=service_date,
                    status=OrderStatus.CONFIRMED,
                    source_system=source_systems[int(rng.integers(0, len(source_systems)))],
                    **common,
                )
            )
            deliveries.append(
                Delivery(
                    delivery_id=delivery_key,
                    order_id=order_key,
                    service_date=service_date,
                    **common,
                )
            )
    return time_windows, orders, deliveries


def _delivery_counts(total: int, days: int) -> list[int]:
    base, remainder = divmod(total, days)
    return [base + (1 if index < remainder else 0) for index in range(days)]


def _demand_for(
    handling: HandlingClass, rng: np.random.Generator
) -> tuple[int, float, float]:
    if handling == HandlingClass.AMBIENT:
        units = int(rng.integers(2, 51))
        kg_per, m3_per = 8.0, 0.10
    elif handling == HandlingClass.REFRIGERATED:
        units = int(rng.integers(5, 41))
        kg_per, m3_per = 12.0, 0.12
    elif handling == HandlingClass.FRAGILE:
        units = int(rng.integers(2, 31))
        kg_per, m3_per = 5.0, 0.08
    else:
        units = int(rng.integers(30, 181))
        kg_per, m3_per = 25.0, 0.18
    weight = round(units * kg_per * float(rng.uniform(0.85, 1.15)), 2)
    volume = round(units * m3_per * float(rng.uniform(0.85, 1.15)), 3)
    return units, weight, volume


def _service_minutes(
    handling: HandlingClass,
    units: int,
    site_type: SiteType,
    rng: np.random.Generator,
) -> int:
    base = 10 + units * (0.55 if handling == HandlingClass.BULK else 0.35)
    if site_type == SiteType.WAREHOUSE:
        base -= 4
    elif site_type == SiteType.RESIDENTIAL:
        base += 5
    base += float(rng.uniform(0, 6))
    return int(min(60, max(10, round(base))))


def _time_window_bounds(
    site: CustomerSite, priority: Priority, rng: np.random.Generator
) -> tuple[int, int]:
    earliest = max(site.receiving_start_minute, 8 * 60)
    latest_end = min(site.receiving_end_minute, 17 * 60 + 30)
    if latest_end - earliest < 90:
        latest_end = min(18 * 60, earliest + 120)
    tight = float(rng.random()) < 0.18
    template = TIGHT_WINDOW_TEMPLATE if tight else SERVICE_LEAD_TEMPLATE
    duration = int(template[priority] + rng.integers(-20, 21))
    duration = max(45, duration)
    duration = min(duration, latest_end - earliest)
    start_latest = latest_end - duration
    raw_start = int(rng.integers(earliest, max(earliest + 1, start_latest + 1)))
    start = (raw_start // 15) * 15
    end = min(latest_end, start + duration)
    if end <= start:
        end = min(latest_end, start + 45)
    if end <= start:
        start = max(earliest, latest_end - 60)
        end = latest_end
    return start, end
