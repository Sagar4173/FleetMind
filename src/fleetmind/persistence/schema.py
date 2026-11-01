"""Warehouse schema generated from the canonical Pydantic entities.

Every table is a run-scoped snapshot.  Composite primary keys always start with
``run_id`` so that independent runs can share a warehouse without collisions.
The generated SQL is also checked into ``schema.sql`` for documentation and
external reviewers.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel

from fleetmind.models import entities

SCHEMA_VERSION = "1.1.0"


@dataclass(frozen=True)
class TableSpec:
    name: str
    model: type[BaseModel]
    key: tuple[str, ...]
    group: str

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.model.model_fields)

    @property
    def primary_key(self) -> tuple[str, ...]:
        return ("run_id", *self.key)


CANONICAL_SPECS: tuple[TableSpec, ...] = (
    TableSpec("depots", entities.Depot, ("depot_id",), "master"),
    TableSpec("customer_sites", entities.CustomerSite, ("site_id",), "master"),
    TableSpec("time_windows", entities.TimeWindow, ("time_window_id",), "master"),
    TableSpec("orders", entities.Order, ("order_id",), "master"),
    TableSpec("deliveries", entities.Delivery, ("delivery_id",), "master"),
    TableSpec("vehicles", entities.Vehicle, ("vehicle_id",), "master"),
    TableSpec(
        "vehicle_capacities",
        entities.VehicleCapacity,
        ("capacity_id",),
        "master",
    ),
    TableSpec(
        "vehicle_equipment",
        entities.VehicleEquipment,
        ("vehicle_id", "equipment"),
        "master",
    ),
    TableSpec("drivers", entities.Driver, ("driver_id",), "master"),
    TableSpec(
        "driver_certifications",
        entities.DriverCertification,
        ("driver_id", "certification"),
        "master",
    ),
    TableSpec("shifts", entities.Shift, ("shift_id",), "master"),
    TableSpec(
        "travel_scenarios",
        entities.ScenarioConfiguration,
        ("scenario_id",),
        "master",
    ),
    TableSpec(
        "travel_time_matrix",
        entities.TravelTimeMatrix,
        ("origin_id", "destination_id"),
        "master",
    ),
    TableSpec("routes", entities.Route, ("route_id",), "plan"),
    TableSpec("route_stops", entities.RouteStop, ("stop_id",), "plan"),
    TableSpec("route_legs", entities.RouteLeg, ("leg_id",), "plan"),
    TableSpec(
        "delivery_assignments",
        entities.DeliveryAssignment,
        ("assignment_id",),
        "plan",
    ),
    TableSpec("solver_runs", entities.SolverRun, ("solve_id",), "plan"),
    TableSpec("delivery_outcomes", entities.DeliveryOutcome, ("outcome_id",), "simulation"),
    TableSpec("route_events", entities.RouteEvent, ("event_id",), "simulation"),
    TableSpec("delivery_events", entities.DeliveryEvent, ("event_id",), "simulation"),
    TableSpec(
        "data_quality_issues",
        entities.DataQualityIssue,
        ("issue_id",),
        "quality",
    ),
    TableSpec(
        "quarantine_records",
        entities.QuarantineRecord,
        ("quarantine_id",),
        "quality",
    ),
    TableSpec(
        "ground_truth_anomalies",
        entities.GroundTruthAnomaly,
        ("anomaly_id",),
        "anomaly",
    ),
    TableSpec(
        "anomaly_detections",
        entities.AnomalyDetectionResult,
        ("detection_id",),
        "anomaly",
    ),
    TableSpec(
        "anomaly_detection_metrics",
        entities.AnomalyDetectionMetric,
        ("issue_type",),
        "anomaly",
    ),
    TableSpec("report_metrics", entities.ReportMetric, ("category", "metric_name", "dimensions"), "report"),
    TableSpec(
        "reconciliation_checks",
        entities.ReconciliationCheck,
        ("check_name",),
        "report",
    ),
    TableSpec("run_metadata", entities.RunMetadata, (), "governance"),
    TableSpec("audit_records", entities.AuditRecord, ("audit_id",), "governance"),
)

# Anomaly injection mutates only these operational tables.  The dirty copies
# preserve the corrupted snapshot without overwriting the validated clean one.
DIRTY_BASE_NAMES: tuple[str, ...] = (
    "routes",
    "route_stops",
    "delivery_assignments",
    "customer_sites",
)

_SPECS_BY_NAME = {spec.name: spec for spec in CANONICAL_SPECS}
for _base in DIRTY_BASE_NAMES:
    _base_spec = _SPECS_BY_NAME[_base]
    _dirty = TableSpec(
        f"dirty_{_base}",
        _base_spec.model,
        _base_spec.key,
        "dirty",
    )
    _SPECS_BY_NAME[_dirty.name] = _dirty

TABLE_SPECS: tuple[TableSpec, ...] = tuple(_SPECS_BY_NAME.values())
SPECS_BY_NAME = _SPECS_BY_NAME


def table_spec(name: str) -> TableSpec:
    try:
        return SPECS_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"unknown warehouse table: {name}") from exc


def duckdb_type(annotation: object) -> str:
    if annotation is datetime:
        return "TIMESTAMP"
    if annotation is date:
        return "DATE"
    if annotation is bool:
        return "BOOLEAN"
    if annotation is int:
        return "BIGINT"
    if annotation is float:
        return "DOUBLE"
    if annotation is str:
        return "VARCHAR"
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        members = [item for item in get_args(annotation) if item is not type(None)]
        return duckdb_type(members[0]) if len(members) == 1 else "VARCHAR"
    if origin is Literal:
        return "VARCHAR"
    return "VARCHAR"


def render_schema() -> str:
    lines = [
        "-- FleetMind warehouse schema (generated from fleetmind.models.entities).",
        f"-- schema_version: {SCHEMA_VERSION}",
        "-- Every snapshot table is keyed by run_id plus the entity key.",
        "",
    ]
    for spec in TABLE_SPECS:
        definitions = ["  run_id VARCHAR NOT NULL"]
        for field_name, field in spec.model.model_fields.items():
            if field_name == "run_id":
                continue
            definitions.append(f'  "{field_name}" {duckdb_type(field.annotation)}')
        primary_key = ", ".join(f'"{column}"' for column in spec.primary_key)
        definitions.append(f"  PRIMARY KEY ({primary_key})")
        lines.append(f"CREATE TABLE IF NOT EXISTS {spec.name} (")
        lines.append(",\n".join(definitions))
        lines.append(");")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
