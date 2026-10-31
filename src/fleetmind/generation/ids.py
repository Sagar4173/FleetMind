"""Deterministic, human-readable identifier construction."""

from __future__ import annotations


def seq(value: int) -> str:
    if value < 1 or value > 999_999:
        raise ValueError("identifier sequence must be between 1 and 999999")
    return f"{value:06d}"


def order_id(year: int, value: int) -> str:
    return f"ORD-{year}-{seq(value)}"


def delivery_id(year: int, value: int) -> str:
    return f"DEL-{year}-{seq(value)}"


def vehicle_id(value: int) -> str:
    return f"VEH-{seq(value)}"


def driver_id(value: int) -> str:
    return f"DRV-{seq(value)}"


def customer_site_id(value: int) -> str:
    if value < 1 or value > 999_999:
        raise ValueError("site sequence out of range")
    return f"CUS-{seq(value)}"


def capacity_id(value: int) -> str:
    return f"CAP-{seq(value)}"


def shift_id(year: int, value: int) -> str:
    return f"SHT-{year}-{seq(value)}"


def time_window_id(year: int, value: int) -> str:
    return f"TW-{year}-{seq(value)}"


def scenario_id(year: int, value: int) -> str:
    if value < 1 or value > 9999:
        raise ValueError("scenario sequence out of range")
    return f"SCN-{year}-{value:04d}"


def route_id(year: int, value: int) -> str:
    return f"RTE-{year}-{seq(value)}"


def stop_id(year: int, route_seq: int, stop_sequence: int) -> str:
    if stop_sequence < 1 or stop_sequence > 999:
        raise ValueError("stop sequence out of range")
    return f"STOP-RTE-{year}-{seq(route_seq)}-{stop_sequence:03d}"


def outcome_id(year: int, value: int) -> str:
    return f"OUT-{year}-{seq(value)}"


def assignment_id(year: int, value: int) -> str:
    return f"ASN-{year}-{seq(value)}"


def leg_id(year: int, route_seq: int, leg_sequence: int) -> str:
    if leg_sequence < 1 or leg_sequence > 9999:
        raise ValueError("leg sequence out of range")
    return f"LEG-{year}-{seq(route_seq)}-{leg_sequence:04d}"


def solve_id(year: int, value: int) -> str:
    return f"SOL-{year}-{seq(value)}"


def route_event_id(year: int, route_seq: int, sequence: int) -> str:
    if sequence < 1 or sequence > 9999:
        raise ValueError("event sequence out of range")
    return f"EVT-RTE-{year}-{seq(route_seq)}-{sequence:04d}"


def delivery_event_id(year: int, delivery_seq: int, sequence: int) -> str:
    if sequence < 1 or sequence > 9999:
        raise ValueError("event sequence out of range")
    return f"EVT-DEL-{year}-{seq(delivery_seq)}-{sequence:04d}"


def ground_truth_id(year: int, value: int) -> str:
    return f"GT-{year}-{seq(value)}"


def detection_id(year: int, value: int) -> str:
    return f"DET-{year}-{seq(value)}"
