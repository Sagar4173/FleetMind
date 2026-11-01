"""Pydantic entity and supporting schemas for the FleetMind warehouse."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleetmind.models.enums import (
    AnomalyType,
    AssignmentStatus,
    Certification,
    DeliveryEventType,
    DeliveryStatus,
    DQIssueType,
    DQSeverity,
    DriverStatus,
    Equipment,
    FailureReason,
    HandlingClass,
    LicenseClass,
    LocationType,
    OrderStatus,
    OutcomeStatus,
    OwnerType,
    Priority,
    RouteEventType,
    RouteStatus,
    ShiftStatus,
    SiteAccessClass,
    SiteType,
    SolverStatus,
    TimeWindowStrictness,
    UnservedReason,
    VehicleStatus,
    VehicleType,
    Weather,
)

ORDER_ID = r"^ORD-\d{4}-\d{6}$"
DELIVERY_ID = r"^DEL-\d{4}-\d{6}$"
VEHICLE_ID = r"^VEH-\d{6}$"
DRIVER_ID = r"^DRV-\d{6}$"
ROUTE_ID = r"^RTE-\d{4}-\d{6}$"
STOP_ID = r"^STOP-RTE-\d{4}-\d{6}-\d{3}$"
SITE_ID = r"^CUS-\d{6}$"
DEPOT_ID = r"^DEP-\d{3}$"
SHIFT_ID = r"^SHT-\d{4}-\d{6}$"
TIME_WINDOW_ID = r"^TW-\d{4}-\d{6}$"
SCENARIO_ID = r"^SCN-\d{4}-\d{4}$"
OUTCOME_ID = r"^OUT-\d{4}-\d{6}$"
ASSIGNMENT_ID = r"^ASN-\d{4}-\d{6}$"
CAPACITY_ID = r"^CAP-\d{6}$"
LEG_ID = r"^LEG-\d{4}-\d{6}-\d{4}$"
SOLVE_ID = r"^SOL-\d{4}-\d{6}$"


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Depot(Entity):
    run_id: str = Field(min_length=3)
    depot_id: str = Field(pattern=DEPOT_ID)
    name: str = Field(min_length=1)
    address_line: str
    city: str
    state: str = Field(min_length=2, max_length=2)
    postal_code: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    open_minute: int = Field(ge=0, le=1439)
    close_minute: int = Field(ge=0, le=1440)
    active: bool = True

    @model_validator(mode="after")
    def _hours(self) -> "Depot":
        if self.open_minute >= self.close_minute:
            raise ValueError("depot open_minute must be before close_minute")
        return self


class CustomerSite(Entity):
    run_id: str = Field(min_length=3)
    site_id: str = Field(pattern=SITE_ID)
    customer_name: str
    site_name: str
    address_line: str
    city: str
    state: str = Field(min_length=2, max_length=2)
    postal_code: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    site_type: SiteType
    access_class: SiteAccessClass
    receiving_start_minute: int = Field(ge=0, le=1439)
    receiving_end_minute: int = Field(ge=1, le=1440)
    dock_available: bool
    contact_name: str
    contact_phone: str
    risk_score: float = Field(ge=0, le=1)
    active: bool
    effective_from: date
    effective_to: date | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _validate_site(self) -> "CustomerSite":
        if self.receiving_start_minute >= self.receiving_end_minute:
            raise ValueError("receiving hours must have positive duration")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on/after effective_from")
        return self


class TimeWindow(Entity):
    run_id: str = Field(min_length=3)
    time_window_id: str = Field(pattern=TIME_WINDOW_ID)
    owner_type: OwnerType
    owner_id: str
    window_start: datetime
    window_end: datetime
    strictness: TimeWindowStrictness = TimeWindowStrictness.HARD
    grace_minutes: int = Field(default=15, ge=0, le=240)

    @model_validator(mode="after")
    def _validate_window(self) -> "TimeWindow":
        if self.window_start >= self.window_end:
            raise ValueError("time window start must be before end")
        return self


class Order(Entity):
    run_id: str = Field(min_length=3)
    order_id: str = Field(pattern=ORDER_ID)
    site_id: str = Field(pattern=SITE_ID)
    order_date: date
    requested_delivery_date: date
    priority: Priority
    handling_class: HandlingClass
    required_equipment: Equipment
    demand_units: int = Field(gt=0, le=1000)
    weight_kg: float = Field(gt=0, le=100_000)
    volume_m3: float = Field(gt=0, le=1000)
    service_duration_min: int = Field(gt=0, le=480)
    time_window_id: str = Field(pattern=TIME_WINDOW_ID)
    status: OrderStatus = OrderStatus.CONFIRMED
    source_system: str
    created_at: datetime

    @model_validator(mode="after")
    def _dates(self) -> "Order":
        if self.order_date > self.requested_delivery_date:
            raise ValueError("order_date cannot be after requested_delivery_date")
        return self


class Delivery(Entity):
    run_id: str = Field(min_length=3)
    delivery_id: str = Field(pattern=DELIVERY_ID)
    order_id: str = Field(pattern=ORDER_ID)
    site_id: str = Field(pattern=SITE_ID)
    service_date: date
    priority: Priority
    handling_class: HandlingClass
    required_equipment: Equipment
    demand_units: int = Field(gt=0, le=1000)
    weight_kg: float = Field(gt=0, le=100_000)
    volume_m3: float = Field(gt=0, le=1000)
    service_duration_min: int = Field(gt=0, le=480)
    time_window_id: str = Field(pattern=TIME_WINDOW_ID)
    status: DeliveryStatus = DeliveryStatus.PENDING
    created_at: datetime


class Vehicle(Entity):
    run_id: str = Field(min_length=3)
    vehicle_id: str = Field(pattern=VEHICLE_ID)
    vehicle_type: VehicleType
    make: str
    model: str
    model_year: int = Field(ge=1990, le=2035)
    license_plate: str
    status: VehicleStatus
    home_depot_id: str = Field(pattern=DEPOT_ID)
    required_license_class: LicenseClass
    max_route_duration_min: int = Field(ge=60, le=1440)
    odometer_km: float = Field(ge=0)
    acquisition_date: date
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _effective(self) -> "Vehicle":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("vehicle effective_to must be on/after effective_from")
        return self


class VehicleCapacity(Entity):
    run_id: str = Field(min_length=3)
    capacity_id: str = Field(pattern=CAPACITY_ID)
    vehicle_id: str = Field(pattern=VEHICLE_ID)
    capacity_units: int = Field(gt=0, le=100_000)
    max_weight_kg: float = Field(gt=0, le=1_000_000)
    max_volume_m3: float = Field(gt=0, le=10_000)
    dimension_note: str = ""


class VehicleEquipment(Entity):
    run_id: str = Field(min_length=3)
    vehicle_id: str = Field(pattern=VEHICLE_ID)
    equipment: Equipment


class Driver(Entity):
    run_id: str = Field(min_length=3)
    driver_id: str = Field(pattern=DRIVER_ID)
    first_name: str
    last_name: str
    phone: str
    email: str
    license_number: str
    license_class: LicenseClass
    license_expiry: date
    hire_date: date
    home_depot_id: str = Field(pattern=DEPOT_ID)
    status: DriverStatus
    max_shift_hours: float = Field(gt=0, le=16)
    hourly_cost: float = Field(gt=0)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _dates(self) -> "Driver":
        if self.license_expiry < self.effective_from:
            raise ValueError("license must be valid during employment")
        if self.hire_date > self.effective_from:
            raise ValueError("hire_date cannot be after effective_from")
        return self


class DriverCertification(Entity):
    run_id: str = Field(min_length=3)
    driver_id: str = Field(pattern=DRIVER_ID)
    certification: Certification
    issued_date: date
    expiry_date: date


class Shift(Entity):
    run_id: str = Field(min_length=3)
    shift_id: str = Field(pattern=SHIFT_ID)
    driver_id: str = Field(pattern=DRIVER_ID)
    vehicle_id: str = Field(pattern=VEHICLE_ID)
    service_date: date
    planned_start: datetime
    planned_end: datetime
    break_minutes: int = Field(ge=0, le=180)
    status: ShiftStatus = ShiftStatus.SCHEDULED
    created_at: datetime

    @model_validator(mode="after")
    def _validate_shift(self) -> "Shift":
        if self.planned_start >= self.planned_end:
            raise ValueError("shift start must be before end")
        if self.planned_start.date() != self.service_date:
            raise ValueError("shift start must occur on service_date")
        return self


class TravelTimeMatrix(Entity):
    run_id: str = Field(min_length=3)
    origin_id: str
    destination_id: str
    distance_km: float = Field(ge=0)
    base_travel_minutes: float = Field(ge=0)
    model_version: str

    @model_validator(mode="after")
    def _not_self(self) -> "TravelTimeMatrix":
        if self.origin_id == self.destination_id and self.base_travel_minutes != 0:
            raise ValueError("same-location travel time must be zero")
        return self


class ScenarioConfiguration(Entity):
    run_id: str = Field(min_length=3)
    scenario_id: str = Field(pattern=SCENARIO_ID)
    scenario_date: date
    day_name: str
    traffic_index: float = Field(ge=0.5, le=3.0)
    weather: Weather
    weather_factor: float = Field(ge=1.0, le=2.0)
    am_peak_multiplier: float = Field(ge=1.0, le=3.0)
    pm_peak_multiplier: float = Field(ge=1.0, le=3.0)
    network_delay_minutes: float = Field(ge=0, le=120)
    incident_zone: str
    seed: int
    notes: str = ""


class Route(Entity):
    run_id: str = Field(min_length=3)
    route_id: str = Field(pattern=ROUTE_ID)
    scenario_id: str = Field(pattern=SCENARIO_ID)
    service_date: date
    depot_id: str = Field(pattern=DEPOT_ID)
    vehicle_id: str = Field(pattern=VEHICLE_ID)
    driver_id: str = Field(pattern=DRIVER_ID)
    shift_id: str = Field(pattern=SHIFT_ID)
    status: RouteStatus = RouteStatus.PLANNED
    planned_start: datetime
    planned_end: datetime
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    stop_count: int = Field(ge=0)
    delivered_stops: int = Field(default=0, ge=0)
    failed_stops: int = Field(default=0, ge=0)
    load_units: int = Field(default=0, ge=0)
    capacity_units: int = Field(gt=0)
    load_weight_kg: float = Field(default=0, ge=0)
    capacity_weight_kg: float = Field(gt=0)
    distance_km: float = Field(default=0, ge=0)
    travel_minutes: float = Field(default=0, ge=0)
    # Planned dwell aggregates from the routing model.
    service_minutes: float = Field(default=0, ge=0)
    wait_minutes: float = Field(default=0, ge=0)
    # Executed dwell aggregates derived from stop timestamps.
    actual_service_minutes: float | None = Field(default=None, ge=0)
    actual_wait_minutes: float | None = Field(default=None, ge=0)
    solver_status: SolverStatus
    objective_value: float | None = None
    solve_seconds: float = Field(default=0, ge=0)
    shift_overrun_minutes: float = Field(default=0, ge=0)
    created_at: datetime

    @model_validator(mode="after")
    def _times(self) -> "Route":
        if self.planned_start > self.planned_end:
            raise ValueError("planned route start must be before end")
        if self.actual_start and self.actual_end and self.actual_start > self.actual_end:
            raise ValueError("actual route start must be before end")
        return self


class RouteStop(Entity):
    run_id: str = Field(min_length=3)
    stop_id: str = Field(pattern=STOP_ID)
    route_id: str = Field(pattern=ROUTE_ID)
    stop_sequence: int = Field(ge=1, le=1000)
    delivery_id: str = Field(pattern=DELIVERY_ID)
    site_id: str = Field(pattern=SITE_ID)
    time_window_id: str = Field(pattern=TIME_WINDOW_ID)
    planned_arrival: datetime
    planned_service_start: datetime
    planned_departure: datetime
    actual_arrival: datetime | None = None
    actual_service_start: datetime | None = None
    actual_departure: datetime | None = None
    # Planned dwell values from the routing model.
    wait_minutes: float = Field(default=0, ge=0)
    service_minutes: float = Field(gt=0)
    # Executed dwell values derived from the actual timestamps.
    actual_wait_minutes: float | None = Field(default=None, ge=0)
    actual_service_minutes: float | None = Field(default=None, ge=0)
    demand_units: int = Field(gt=0)
    weight_kg: float = Field(gt=0)
    status: DeliveryStatus = DeliveryStatus.ASSIGNED

    @model_validator(mode="after")
    def _chronology(self) -> "RouteStop":
        if not (self.planned_arrival <= self.planned_service_start <= self.planned_departure):
            raise ValueError("planned stop chronology is invalid")
        if self.actual_arrival is not None:
            if self.actual_service_start is None or self.actual_departure is None:
                raise ValueError("actual stop requires arrival, service start and departure")
            if not (
                self.actual_arrival <= self.actual_service_start <= self.actual_departure
            ):
                raise ValueError("actual stop chronology is invalid")
            if (
                self.actual_wait_minutes is None
                or self.actual_service_minutes is None
            ):
                raise ValueError("actual stop requires actual dwell durations")
            expected_wait = max(
                0.0,
                (self.actual_service_start - self.actual_arrival).total_seconds() / 60.0,
            )
            expected_service = (
                self.actual_departure - self.actual_service_start
            ).total_seconds() / 60.0
            if abs(self.actual_wait_minutes - expected_wait) > 1e-4:
                raise ValueError("actual dwell wait does not match the timestamps")
            if abs(self.actual_service_minutes - expected_service) > 1e-4:
                raise ValueError("actual dwell service does not match the timestamps")
        elif self.actual_wait_minutes is not None or self.actual_service_minutes is not None:
            raise ValueError("actual dwell requires actual timestamps")
        return self


class DeliveryAssignment(Entity):
    run_id: str = Field(min_length=3)
    assignment_id: str = Field(pattern=ASSIGNMENT_ID)
    delivery_id: str = Field(pattern=DELIVERY_ID)
    route_id: str | None = Field(default=None, pattern=ROUTE_ID)
    stop_id: str | None = Field(default=None, pattern=STOP_ID)
    service_date: date
    status: AssignmentStatus
    reason_code: str | None = None
    explanation: str
    created_at: datetime

    @model_validator(mode="after")
    def _consistency(self) -> "DeliveryAssignment":
        if self.status == AssignmentStatus.ASSIGNED:
            if not self.route_id or not self.stop_id:
                raise ValueError("assigned delivery requires route_id and stop_id")
            if self.reason_code:
                raise ValueError("assigned delivery cannot have an unserved reason")
        else:
            if self.route_id or self.stop_id:
                raise ValueError("unserved delivery cannot reference a route or stop")
            try:
                UnservedReason(self.reason_code or "")
            except ValueError as exc:
                raise ValueError("unserved delivery requires a valid reason_code") from exc
        return self


class RouteLeg(Entity):
    run_id: str = Field(min_length=3)
    leg_id: str = Field(pattern=LEG_ID)
    route_id: str = Field(pattern=ROUTE_ID)
    leg_sequence: int = Field(ge=1)
    origin_type: LocationType
    origin_id: str
    destination_type: LocationType
    destination_id: str
    planned_departure: datetime
    planned_arrival: datetime
    planned_distance_km: float = Field(ge=0)
    planned_travel_minutes: float = Field(ge=0)
    actual_departure: datetime | None = None
    actual_arrival: datetime | None = None
    actual_distance_km: float | None = Field(default=None, ge=0)
    actual_travel_minutes: float | None = Field(default=None, ge=0)
    scenario_id: str = Field(pattern=SCENARIO_ID)
    traffic_multiplier: float = Field(ge=0.5, le=5.0)


class DeliveryOutcome(Entity):
    run_id: str = Field(min_length=3)
    outcome_id: str = Field(pattern=OUTCOME_ID)
    delivery_id: str = Field(pattern=DELIVERY_ID)
    route_id: str | None = Field(default=None, pattern=ROUTE_ID)
    stop_id: str | None = Field(default=None, pattern=STOP_ID)
    status: OutcomeStatus
    reason_code: str | None = None
    actual_arrival: datetime | None = None
    service_start: datetime | None = None
    service_end: datetime | None = None
    on_time: bool | None = None
    proof_of_delivery: str | None = None
    notes: str = ""
    recorded_at: datetime

    @model_validator(mode="after")
    def _outcome(self) -> "DeliveryOutcome":
        if self.status == OutcomeStatus.DELIVERED:
            if self.reason_code is not None:
                raise ValueError("delivered outcome must not have a failure reason")
            if self.on_time is None:
                raise ValueError("delivered outcome requires on_time")
        elif self.status == OutcomeStatus.FAILED:
            try:
                FailureReason(self.reason_code or "")
            except ValueError as exc:
                raise ValueError("failed outcome requires a failure reason") from exc
        else:
            if self.route_id or self.stop_id:
                raise ValueError("unserved outcome must not have a route or stop")
            try:
                UnservedReason(self.reason_code or "")
            except ValueError as exc:
                raise ValueError("unserved outcome requires an unserved reason") from exc
        return self


class RouteEvent(Entity):
    run_id: str = Field(min_length=3)
    event_id: str
    route_id: str = Field(pattern=ROUTE_ID)
    stop_id: str | None = Field(default=None, pattern=STOP_ID)
    event_type: RouteEventType
    event_time: datetime
    planned_time: datetime | None = None
    sequence: int = Field(ge=1)
    details: str = ""


class DeliveryEvent(Entity):
    run_id: str = Field(min_length=3)
    event_id: str
    delivery_id: str = Field(pattern=DELIVERY_ID)
    route_id: str | None = Field(default=None, pattern=ROUTE_ID)
    stop_id: str | None = Field(default=None, pattern=STOP_ID)
    event_type: DeliveryEventType
    event_time: datetime
    planned_time: datetime | None = None
    sequence: int = Field(ge=1)
    details: str = ""


class SolverRun(Entity):
    run_id: str = Field(min_length=3)
    solve_id: str = Field(pattern=SOLVE_ID)
    service_date: date
    status: SolverStatus
    objective_value: float | None = None
    solve_seconds: float = Field(ge=0)
    assigned_count: int = Field(ge=0)
    unserved_count: int = Field(ge=0)
    vehicle_count: int = Field(ge=0)
    message: str = ""


class DataQualityIssue(Entity):
    run_id: str = Field(min_length=3)
    issue_id: str
    issue_type: DQIssueType
    severity: DQSeverity
    entity_type: str
    entity_id: str
    field_name: str = ""
    message: str
    source_table: str
    detected_at: datetime
    quarantined: bool = False
    details: str = ""


class GroundTruthAnomaly(Entity):
    run_id: str = Field(min_length=3)
    anomaly_id: str
    issue_type: AnomalyType
    entity_type: str
    entity_id: str
    source_table: str
    original_value: str
    injected_value: str
    injected_at: datetime
    notes: str = ""


class AnomalyDetectionResult(Entity):
    run_id: str = Field(min_length=3)
    detection_id: str
    issue_type: AnomalyType
    entity_type: str
    entity_id: str
    source_table: str
    detection_rule: str
    observed_value: str
    detected_at: datetime


class AnomalyDetectionMetric(Entity):
    run_id: str = Field(min_length=3)
    issue_type: str
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    tn: int = Field(ge=0)
    fn: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    evaluated_universe: int = Field(ge=0)
    notes: str = ""


class ReportMetric(Entity):
    run_id: str = Field(min_length=3)
    category: str
    metric_name: str
    value: float
    unit: str
    numerator: float | None = None
    denominator: float | None = None
    formula: str = ""
    dimensions: str = ""


class ReconciliationCheck(Entity):
    run_id: str = Field(min_length=3)
    check_name: str
    expected: float
    actual: float
    difference: float
    passed: bool
    details: str = ""


class RunMetadata(Entity):
    run_id: str = Field(min_length=3)
    mode: str
    seed: int
    dataset_version: str
    pipeline_version: str
    config_hash: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "PARTIAL"]
    started_at: datetime
    completed_at: datetime | None = None
    stage_durations_json: str = "{}"
    counts_json: str = "{}"
    checksums_json: str = "{}"
    error_message: str = ""


class AuditRecord(Entity):
    run_id: str = Field(min_length=3)
    audit_id: str
    execution_id: str
    timestamp: datetime
    actor: str
    action: str
    details_json: str = "{}"


class QuarantineRecord(Entity):
    run_id: str = Field(min_length=3)
    quarantine_id: str
    issue_id: str
    source_table: str
    entity_id: str
    record_json: str
    reason: str
    quarantined_at: datetime
