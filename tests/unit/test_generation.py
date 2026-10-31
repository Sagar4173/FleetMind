from __future__ import annotations

import re

import pytest

from fleetmind.config import load_config
from fleetmind.generation.factory import generate_dataset
from fleetmind.models.enums import DeliveryStatus


@pytest.fixture(scope="module")
def starter():
    config = load_config("configs/starter.yaml")
    return config, generate_dataset(config)


def test_declared_entity_counts(starter) -> None:
    config, dataset = starter
    assert len(dataset["customer_sites"]) == config.generation.customer_sites
    assert len(dataset["orders"]) == config.generation.orders
    assert len(dataset["deliveries"]) == config.generation.orders
    assert len(dataset["vehicles"]) == config.generation.vehicles
    assert len(dataset["drivers"]) == config.generation.drivers
    assert len(dataset["vehicle_capacities"]) == config.generation.vehicles
    assert len(dataset["time_windows"]) == config.generation.orders
    assert len(dataset["shifts"]) > 0
    assert len(dataset["travel_scenarios"]) == config.horizon.days
    assert len(dataset["travel_time_matrix"]) == (config.generation.customer_sites + 1) ** 2


def test_ids_are_formatted_and_unique(starter) -> None:
    _, dataset = starter
    assert dataset["orders"]["order_id"].str.fullmatch(r"ORD-2026-\d{6}").all()
    assert dataset["deliveries"]["delivery_id"].str.fullmatch(r"DEL-2026-\d{6}").all()
    assert dataset["vehicles"]["vehicle_id"].str.fullmatch(r"VEH-\d{6}").all()
    assert dataset["drivers"]["driver_id"].str.fullmatch(r"DRV-\d{6}").all()
    assert dataset["customer_sites"]["site_id"].str.fullmatch(r"CUS-\d{6}").all()
    for table, key in [
        ("orders", "order_id"),
        ("deliveries", "delivery_id"),
        ("vehicles", "vehicle_id"),
        ("drivers", "driver_id"),
        ("customer_sites", "site_id"),
        ("shifts", "shift_id"),
        ("time_windows", "time_window_id"),
        ("travel_scenarios", "scenario_id"),
    ]:
        assert dataset[table][key].is_unique, table


def test_referential_integrity_of_master_data(starter) -> None:
    _, dataset = starter
    sites = set(dataset["customer_sites"]["site_id"])
    orders = dataset["orders"]
    deliveries = dataset["deliveries"]
    windows = set(dataset["time_windows"]["time_window_id"])
    drivers = set(dataset["drivers"]["driver_id"])
    vehicles = set(dataset["vehicles"]["vehicle_id"])
    assert set(orders["site_id"]).issubset(sites)
    assert set(deliveries["site_id"]).issubset(sites)
    assert set(orders["time_window_id"]).issubset(windows)
    assert set(deliveries["time_window_id"]).issubset(windows)
    assert set(dataset["shifts"]["driver_id"]).issubset(drivers)
    assert set(dataset["shifts"]["vehicle_id"]).issubset(vehicles)
    assert set(deliveries["order_id"]) == set(orders["order_id"])


def test_coordinates_and_dates_are_in_scope(starter) -> None:
    config, dataset = starter
    sites = dataset["customer_sites"]
    assert sites["latitude"].between(config.region.min_lat, config.region.max_lat).all()
    assert sites["longitude"].between(config.region.min_lon, config.region.max_lon).all()
    assert set(dataset["deliveries"]["service_date"]).issubset(set(config.service_dates))
    assert set(dataset["travel_scenarios"]["scenario_date"]) == set(config.service_dates)
    assert (dataset["deliveries"]["status"] == DeliveryStatus.PENDING.value).all()


def test_all_delivery_days_have_volume(starter) -> None:
    config, dataset = starter
    counts = dataset["deliveries"].groupby("service_date").size()
    assert set(counts.index) == set(config.service_dates)
    assert counts.min() >= 1
    assert counts.sum() == config.generation.orders


def test_shift_and_window_chronology(starter) -> None:
    _, dataset = starter
    shifts = dataset["shifts"]
    assert (shifts["planned_start"] < shifts["planned_end"]).all()
    assert (shifts["planned_start"].dt.date == shifts["service_date"]).all()
    windows = dataset["time_windows"]
    assert (windows["window_start"] < windows["window_end"]).all()
    deliveries = dataset["deliveries"].merge(
        windows, left_on="time_window_id", right_on="time_window_id"
    )
    assert (deliveries["window_start"].dt.date == deliveries["service_date"]).all()


def test_generation_is_deterministic(starter) -> None:
    config, first = starter
    second = generate_dataset(config)
    assert first.checksums == second.checksums
    for table in first.tables:
        assert first[table].equals(second[table]), table


def test_checksums_are_stable_and_complete(starter) -> None:
    config, dataset = starter
    assert set(dataset.checksums) == set(dataset.tables)
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in dataset.checksums.values())
    assert dataset.checksums["orders"] != dataset.checksums["deliveries"]


def test_fleet_mix_and_capacity_entities(starter) -> None:
    config, dataset = starter
    vehicles = dataset["vehicles"]
    capacities = dataset["vehicle_capacities"]
    assert len(vehicles) == config.generation.vehicles
    assert capacities["vehicle_id"].is_unique
    assert set(capacities["vehicle_id"]) == set(vehicles["vehicle_id"])
    assert (capacities["capacity_units"] > 0).all()
    assert dataset["vehicle_equipment"].groupby("vehicle_id").size().min() >= 1
    # Every vehicle type is represented when the fleet is large enough.
    assert set(vehicles["vehicle_type"]) == {
        "CARGO_VAN",
        "BOX_TRUCK",
        "REFRIGERATED_TRUCK",
        "FLATBED",
    }
