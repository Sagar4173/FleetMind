"""Independent data-quality and relational validation.

The validator never mutates or discards source records. Records implicated in
critical issues are copied into a quarantine collection while the original
snapshot remains available for inspection and reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fleetmind.config import AppConfig
from fleetmind.models.enums import (
    AnomalyType,
    AssignmentStatus,
    DeliveryEventType,
    DeliveryStatus,
    DQIssueType,
    DQSeverity,
    Equipment,
    FailureReason,
    HandlingClass,
    LicenseClass,
    OrderStatus,
    OutcomeStatus,
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

ISSUE_COLUMNS = [
    "run_id",
    "issue_id",
    "issue_type",
    "severity",
    "entity_type",
    "entity_id",
    "field_name",
    "message",
    "source_table",
    "detected_at",
    "quarantined",
    "details",
]

MASTER_TABLES = [
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
]

PLAN_TABLES = [
    "solver_runs",
    "routes",
    "route_stops",
    "route_legs",
    "delivery_assignments",
    "delivery_outcomes",
    "route_events",
    "delivery_events",
]

PRIMARY_KEYS: dict[str, list[str]] = {
    "depots": ["depot_id"],
    "customer_sites": ["site_id"],
    "time_windows": ["time_window_id"],
    "orders": ["order_id"],
    "deliveries": ["delivery_id"],
    "vehicles": ["vehicle_id"],
    "vehicle_capacities": ["capacity_id"],
    "vehicle_equipment": ["vehicle_id", "equipment"],
    "drivers": ["driver_id"],
    "driver_certifications": ["driver_id", "certification"],
    "shifts": ["shift_id"],
    "travel_scenarios": ["scenario_id"],
    "travel_time_matrix": ["origin_id", "destination_id"],
    "solver_runs": ["solve_id"],
    "routes": ["route_id"],
    "route_stops": ["stop_id"],
    "route_legs": ["leg_id"],
    "delivery_assignments": ["assignment_id"],
    "delivery_outcomes": ["outcome_id"],
    "route_events": ["event_id"],
    "delivery_events": ["event_id"],
    "ground_truth_anomalies": ["anomaly_id"],
    "report_metrics": ["run_id", "category", "metric_name"],
    "reconciliation_checks": ["run_id", "check_name"],
    "run_metadata": ["run_id"],
    "audit_records": ["audit_id"],
    "data_quality_issues": ["issue_id"],
}

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "depots": ["run_id", "depot_id", "name", "latitude", "longitude"],
    "customer_sites": [
        "run_id",
        "site_id",
        "customer_name",
        "address_line",
        "latitude",
        "longitude",
        "site_type",
        "access_class",
        "receiving_start_minute",
        "receiving_end_minute",
        "active",
        "effective_from",
    ],
    "time_windows": [
        "run_id",
        "time_window_id",
        "owner_type",
        "owner_id",
        "window_start",
        "window_end",
    ],
    "orders": [
        "run_id",
        "order_id",
        "site_id",
        "order_date",
        "requested_delivery_date",
        "priority",
        "handling_class",
        "demand_units",
        "weight_kg",
        "service_duration_min",
        "time_window_id",
        "status",
    ],
    "deliveries": [
        "run_id",
        "delivery_id",
        "order_id",
        "site_id",
        "service_date",
        "priority",
        "handling_class",
        "demand_units",
        "weight_kg",
        "service_duration_min",
        "time_window_id",
        "status",
    ],
    "vehicles": [
        "run_id",
        "vehicle_id",
        "vehicle_type",
        "status",
        "home_depot_id",
        "required_license_class",
    ],
    "vehicle_capacities": [
        "run_id",
        "capacity_id",
        "vehicle_id",
        "capacity_units",
        "max_weight_kg",
    ],
    "vehicle_equipment": ["run_id", "vehicle_id", "equipment"],
    "drivers": [
        "run_id",
        "driver_id",
        "license_class",
        "license_expiry",
        "status",
        "home_depot_id",
    ],
    "driver_certifications": ["run_id", "driver_id", "certification"],
    "shifts": [
        "run_id",
        "shift_id",
        "driver_id",
        "vehicle_id",
        "service_date",
        "planned_start",
        "planned_end",
        "status",
    ],
    "travel_scenarios": [
        "run_id",
        "scenario_id",
        "scenario_date",
        "traffic_index",
        "weather",
        "am_peak_multiplier",
        "pm_peak_multiplier",
    ],
    "travel_time_matrix": [
        "run_id",
        "origin_id",
        "destination_id",
        "distance_km",
        "base_travel_minutes",
    ],
    "solver_runs": [
        "run_id",
        "solve_id",
        "service_date",
        "status",
        "assigned_count",
        "unserved_count",
    ],
    "routes": [
        "run_id",
        "route_id",
        "scenario_id",
        "service_date",
        "depot_id",
        "vehicle_id",
        "driver_id",
        "shift_id",
        "status",
        "planned_start",
        "planned_end",
        "stop_count",
        "capacity_units",
        "capacity_weight_kg",
        "solver_status",
    ],
    "route_stops": [
        "run_id",
        "stop_id",
        "route_id",
        "stop_sequence",
        "delivery_id",
        "site_id",
        "planned_arrival",
        "planned_service_start",
        "planned_departure",
        "service_minutes",
        "demand_units",
        "weight_kg",
        "status",
    ],
    "route_legs": [
        "run_id",
        "leg_id",
        "route_id",
        "leg_sequence",
        "origin_id",
        "destination_id",
        "planned_departure",
        "planned_arrival",
        "planned_distance_km",
        "planned_travel_minutes",
    ],
    "delivery_assignments": [
        "run_id",
        "assignment_id",
        "delivery_id",
        "service_date",
        "status",
        "explanation",
    ],
    "delivery_outcomes": [
        "run_id",
        "outcome_id",
        "delivery_id",
        "status",
        "recorded_at",
    ],
    "route_events": [
        "run_id",
        "event_id",
        "route_id",
        "event_type",
        "event_time",
        "sequence",
    ],
    "delivery_events": [
        "run_id",
        "event_id",
        "delivery_id",
        "event_type",
        "event_time",
        "sequence",
    ],
}

ENUM_COLUMNS: dict[str, dict[str, Any]] = {
    "customer_sites": {
        "site_type": SiteType,
        "access_class": SiteAccessClass,
    },
    "orders": {
        "priority": Priority,
        "handling_class": HandlingClass,
        "status": OrderStatus,
    },
    "deliveries": {
        "priority": Priority,
        "handling_class": HandlingClass,
        "status": DeliveryStatus,
    },
    "vehicles": {
        "vehicle_type": VehicleType,
        "status": VehicleStatus,
        "required_license_class": LicenseClass,
    },
    "vehicle_equipment": {"equipment": Equipment},
    "drivers": {"license_class": LicenseClass, "status": "driver_status"},
    "driver_certifications": {"certification": "certification"},
    "shifts": {"status": ShiftStatus},
    "travel_scenarios": {"weather": Weather},
    "time_windows": {"strictness": TimeWindowStrictness},
    "solver_runs": {"status": SolverStatus},
    "routes": {"status": RouteStatus, "solver_status": SolverStatus},
    "route_stops": {"status": DeliveryStatus},
    "delivery_assignments": {"status": AssignmentStatus},
    "delivery_outcomes": {"status": OutcomeStatus},
    "route_events": {"event_type": RouteEventType},
    "delivery_events": {"event_type": DeliveryEventType},
}

NUMERIC_COLUMNS: dict[str, list[str]] = {
    "customer_sites": [
        "latitude",
        "longitude",
        "receiving_start_minute",
        "receiving_end_minute",
        "risk_score",
    ],
    "orders": ["demand_units", "weight_kg", "volume_m3", "service_duration_min"],
    "deliveries": ["demand_units", "weight_kg", "volume_m3", "service_duration_min"],
    "vehicle_capacities": ["capacity_units", "max_weight_kg", "max_volume_m3"],
    "route_stops": [
        "stop_sequence",
        "wait_minutes",
        "service_minutes",
        "demand_units",
        "weight_kg",
    ],
    "routes": [
        "stop_count",
        "load_units",
        "capacity_units",
        "load_weight_kg",
        "capacity_weight_kg",
        "distance_km",
        "travel_minutes",
        "service_minutes",
        "wait_minutes",
    ],
    "travel_time_matrix": ["distance_km", "base_travel_minutes"],
    "travel_scenarios": [
        "traffic_index",
        "weather_factor",
        "am_peak_multiplier",
        "pm_peak_multiplier",
    ],
    "route_legs": ["planned_distance_km", "planned_travel_minutes"],
}

DATETIME_COLUMNS: dict[str, list[str]] = {
    "customer_sites": ["effective_from", "effective_to", "created_at"],
    "time_windows": ["window_start", "window_end"],
    "orders": ["order_date", "requested_delivery_date", "created_at"],
    "deliveries": ["service_date", "created_at"],
    "vehicles": ["acquisition_date", "effective_from", "effective_to"],
    "drivers": ["license_expiry", "hire_date", "effective_from", "effective_to"],
    "driver_certifications": ["issued_date", "expiry_date"],
    "shifts": ["service_date", "planned_start", "planned_end", "created_at"],
    "travel_scenarios": ["scenario_date"],
    "routes": ["service_date", "planned_start", "planned_end", "actual_start", "actual_end"],
    "route_stops": [
        "planned_arrival",
        "planned_service_start",
        "planned_departure",
        "actual_arrival",
        "actual_service_start",
        "actual_departure",
    ],
    "route_legs": [
        "planned_departure",
        "planned_arrival",
        "actual_departure",
        "actual_arrival",
    ],
    "delivery_assignments": ["service_date", "created_at"],
    "delivery_outcomes": [
        "actual_arrival",
        "service_start",
        "service_end",
        "recorded_at",
    ],
    "route_events": ["event_time", "planned_time"],
    "delivery_events": ["event_time", "planned_time"],
}

ID_PATTERNS: dict[str, str] = {
    "customer_sites": r"^CUS-\d{6}$",
    "orders": r"^ORD-\d{4}-\d{6}$",
    "deliveries": r"^DEL-\d{4}-\d{6}$",
    "vehicles": r"^VEH-\d{6}$",
    "vehicle_capacities": r"^CAP-\d{6}$",
    "drivers": r"^DRV-\d{6}$",
    "shifts": r"^SHT-\d{4}-\d{6}$",
    "time_windows": r"^TW-\d{4}-\d{6}$",
    "travel_scenarios": r"^SCN-\d{4}-\d{4}$",
    "routes": r"^RTE-\d{4}-\d{6}$",
    "route_stops": r"^STOP-RTE-\d{4}-\d{6}-\d{3}$",
    "route_legs": r"^LEG-\d{4}-\d{6}-\d{4}$",
    "delivery_assignments": r"^ASN-\d{4}-\d{6}$",
    "delivery_outcomes": r"^OUT-\d{4}-\d{6}$",
    "solver_runs": r"^SOL-\d{4}-\d{6}$",
}

QUARANTINE_TYPES = {
    DQIssueType.DUPLICATE_PRIMARY_KEY,
    DQIssueType.MISSING_REQUIRED_FIELD,
    DQIssueType.INVALID_DATA_TYPE,
    DQIssueType.INVALID_FOREIGN_KEY,
    DQIssueType.INVALID_ENUM,
    DQIssueType.INVALID_REFERENCE,
}


@dataclass
class ValidationResult:
    issues: pd.DataFrame
    quarantined: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        if self.issues.empty:
            return 0
        return int(
            self.issues["severity"].isin(
                [DQSeverity.ERROR.value, DQSeverity.CRITICAL.value]
            ).sum()
        )

    @property
    def critical_count(self) -> int:
        if self.issues.empty:
            return 0
        return int((self.issues["severity"] == DQSeverity.CRITICAL.value).sum())

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    def counts_by_type(self) -> dict[str, int]:
        if self.issues.empty:
            return {}
        return {
            str(key): int(value)
            for key, value in self.issues["issue_type"].value_counts().items()
        }


class IssueCollector:
    def __init__(
        self,
        run_id: str,
        prefix: str = "DQ",
        detected_at: datetime | None = None,
    ) -> None:
        self.run_id = run_id
        self.prefix = prefix
        self.detected_at = detected_at or datetime.now().replace(microsecond=0)
        self._rows: list[dict[str, Any]] = []

    def add(
        self,
        issue_type: DQIssueType,
        entity_type: str,
        entity_id: str,
        message: str,
        source_table: str,
        field_name: str = "",
        severity: DQSeverity = DQSeverity.ERROR,
        details: str = "",
        quarantined: bool = False,
    ) -> None:
        self._rows.append(
            {
                "run_id": self.run_id,
                "issue_id": f"{self.prefix}-{len(self._rows) + 1:06d}",
                "issue_type": issue_type.value,
                "severity": severity.value,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "field_name": field_name,
                "message": message,
                "source_table": source_table,
                "detected_at": self.detected_at,
                "quarantined": quarantined,
                "details": details,
            }
        )

    def frame(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=ISSUE_COLUMNS)
        return pd.DataFrame(self._rows, columns=ISSUE_COLUMNS)


class DataQualityValidator:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.run_id = config.run_id

    def validate_master(
        self, tables: dict[str, pd.DataFrame], prefix: str = "DQ"
    ) -> ValidationResult:
        collector = IssueCollector(self.run_id, prefix=prefix)
        self._validate_structure(tables, collector, MASTER_TABLES)
        self._check_primary_keys(tables, collector)
        self._check_required(tables, collector)
        self._check_numeric_types(tables, collector)
        self._check_datetime_types(tables, collector)
        self._check_enums(tables, collector)
        self._check_id_patterns(tables, collector)
        self._check_foreign_keys(tables, collector)
        self._check_uniqueness(tables, collector)
        self._check_coordinates(tables, collector)
        self._check_master_dates(tables, collector)
        self._check_master_cross_table(tables, collector)
        result = ValidationResult(issues=collector.frame())
        result.quarantined = self._build_quarantine(tables, result)
        return result

    def validate_full(
        self, tables: dict[str, pd.DataFrame], prefix: str = "DQ"
    ) -> ValidationResult:
        result = self.validate_master(tables, prefix=prefix)
        collector = IssueCollector(self.run_id, prefix=prefix)
        # Rebuild a single collector to keep issue IDs deterministic from zero.
        collector._rows = result.issues.to_dict("records") if not result.issues.empty else []
        self._validate_structure(tables, collector, PLAN_TABLES)
        self._check_plan_keys_and_required(tables, collector)
        self._check_plan_references(tables, collector)
        self._check_route_capacity(tables, collector)
        self._check_route_time_windows(tables, collector)
        self._check_route_shifts(tables, collector)
        self._check_route_chronology(tables, collector)
        self._check_delivery_assignment_reconciliation(tables, collector)
        self._check_route_arithmetic(tables, collector)
        self._check_events(tables, collector)
        self._check_lifecycle(tables, collector)
        result = ValidationResult(issues=collector.frame())
        result.quarantined = self._build_quarantine(tables, result)
        return result

    # ------------------------------------------------------------------
    # Structural helpers
    # ------------------------------------------------------------------
    def _validate_structure(
        self,
        tables: dict[str, pd.DataFrame],
        collector: IssueCollector,
        required_tables: list[str],
    ) -> None:
        for table in required_tables:
            if table not in tables:
                collector.add(
                    DQIssueType.MISSING_REQUIRED_FIELD,
                    "table",
                    table,
                    f"required table '{table}' is missing",
                    table,
                    severity=DQSeverity.CRITICAL,
                )
                continue
            frame = tables[table]
            if not isinstance(frame, pd.DataFrame):
                collector.add(
                    DQIssueType.INVALID_DATA_TYPE,
                    "table",
                    table,
                    f"table '{table}' is not a pandas DataFrame",
                    table,
                    severity=DQSeverity.CRITICAL,
                )
                continue
            for column in REQUIRED_COLUMNS.get(table, []):
                if column not in frame.columns:
                    collector.add(
                        DQIssueType.MISSING_REQUIRED_FIELD,
                        "column",
                        f"{table}.{column}",
                        f"required column '{column}' is missing from {table}",
                        table,
                        field_name=column,
                        severity=DQSeverity.CRITICAL,
                    )

    @staticmethod
    def _row_ids(frame: pd.DataFrame, table: str) -> pd.Series:
        keys = [key for key in PRIMARY_KEYS.get(table, []) if key in frame.columns]
        if not keys:
            return pd.Series(frame.index.astype(str), index=frame.index)
        if len(keys) == 1:
            return frame[keys[0]].astype(str)
        return frame[keys].astype(str).agg("|".join, axis=1)

    @staticmethod
    def _missing_mask(frame: pd.DataFrame, column: str) -> pd.Series:
        series = frame[column]
        return series.isna() | (series.astype("string").fillna("").str.strip() == "")

    def _check_primary_keys(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table in [t for t in MASTER_TABLES if t in tables]:
            frame = tables[table]
            keys = PRIMARY_KEYS.get(table, [])
            if not keys or any(key not in frame.columns for key in keys):
                continue
            mask = frame[keys].isna().any(axis=1)
            if mask.any():
                ids = self._row_ids(frame, table)
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.MISSING_REQUIRED_FIELD,
                        table,
                        ids.loc[index],
                        f"{table} primary key contains null values",
                        table,
                        field_name=",".join(keys),
                        severity=DQSeverity.CRITICAL,
                    )
            duplicates = frame[~mask].duplicated(subset=keys, keep=False)
            if duplicates.any():
                dup_frame = frame[~mask & duplicates]
                grouped = dup_frame.groupby(keys, dropna=False).size()
                for key, count in grouped[grouped > 1].items():
                    entity_id = "|".join(str(part) for part in (key if isinstance(key, tuple) else (key,)))
                    collector.add(
                        DQIssueType.DUPLICATE_PRIMARY_KEY,
                        table,
                        entity_id,
                        f"{count} records share the {table} primary key {entity_id}",
                        table,
                        field_name=",".join(keys),
                        severity=DQSeverity.CRITICAL,
                        details=f"count={count}",
                    )

    def _check_required(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table in [t for t in MASTER_TABLES if t in tables]:
            frame = tables[table]
            ids = self._row_ids(frame, table)
            for column in REQUIRED_COLUMNS.get(table, []):
                if column not in frame.columns:
                    continue
                mask = self._missing_mask(frame, column)
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.MISSING_REQUIRED_FIELD,
                        table,
                        ids.loc[index],
                        f"{table}.{column} is required but empty",
                        table,
                        field_name=column,
                        severity=DQSeverity.ERROR,
                    )

    def _check_numeric_types(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table, columns in NUMERIC_COLUMNS.items():
            if table not in tables:
                continue
            frame = tables[table]
            ids = self._row_ids(frame, table)
            for column in columns:
                if column not in frame.columns:
                    continue
                original = frame[column]
                coerced = pd.to_numeric(original, errors="coerce")
                mask = original.notna() & coerced.isna()
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATA_TYPE,
                        table,
                        ids.loc[index],
                        f"{table}.{column} contains non-numeric value {original.loc[index]!r}",
                        table,
                        field_name=column,
                        severity=DQSeverity.ERROR,
                    )

    def _check_datetime_types(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table, columns in DATETIME_COLUMNS.items():
            if table not in tables:
                continue
            frame = tables[table]
            ids = self._row_ids(frame, table)
            for column in columns:
                if column not in frame.columns:
                    continue
                original = frame[column]
                coerced = pd.to_datetime(original, errors="coerce")
                mask = original.notna() & coerced.isna()
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATA_TYPE,
                        table,
                        ids.loc[index],
                        f"{table}.{column} contains an invalid timestamp {original.loc[index]!r}",
                        table,
                        field_name=column,
                        severity=DQSeverity.ERROR,
                    )

    def _check_enums(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table, columns in ENUM_COLUMNS.items():
            if table not in tables:
                continue
            frame = tables[table]
            ids = self._row_ids(frame, table)
            for column, enum_type in columns.items():
                if column not in frame.columns:
                    continue
                if isinstance(enum_type, str):
                    allowed = {
                        "driver_status": {item.value for item in __import__(
                            "fleetmind.models.enums", fromlist=["DriverStatus"]
                        ).DriverStatus},
                        "certification": {item.value for item in __import__(
                            "fleetmind.models.enums", fromlist=["Certification"]
                        ).Certification},
                    }[enum_type]
                else:
                    allowed = {item.value for item in enum_type}
                mask = frame[column].notna() & ~frame[column].astype(str).isin(allowed)
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_ENUM,
                        table,
                        ids.loc[index],
                        f"{table}.{column} has unsupported value {frame.loc[index, column]!r}",
                        table,
                        field_name=column,
                        severity=DQSeverity.ERROR,
                    )

    def _check_id_patterns(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table, pattern in ID_PATTERNS.items():
            if table not in tables:
                continue
            frame = tables[table]
            column = PRIMARY_KEYS[table][0]
            if column not in frame.columns:
                continue
            mask = frame[column].notna() & ~frame[column].astype(str).str.fullmatch(pattern)
            for index in frame.index[mask]:
                collector.add(
                    DQIssueType.INVALID_REFERENCE,
                    table,
                    str(frame.loc[index, column]),
                    f"{table}.{column} does not match required format {pattern}",
                    table,
                    field_name=column,
                    severity=DQSeverity.ERROR,
                )

    def _check_foreign_keys(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        site_ids = set(tables["customer_sites"]["site_id"]) if "customer_sites" in tables else set()
        depot_ids = set(tables["depots"]["depot_id"]) if "depots" in tables else set()
        location_ids = site_ids | depot_ids

        fk_rules: list[tuple[str, str, str, set[str], str]] = []
        if "orders" in tables:
            fk_rules += [
                ("orders", "site_id", "site_id", site_ids, "customer_sites"),
                ("orders", "time_window_id", "time_window_id", set(tables["time_windows"]["time_window_id"]), "time_windows"),
            ]
        if "deliveries" in tables:
            fk_rules += [
                ("deliveries", "order_id", "order_id", set(tables["orders"]["order_id"]), "orders"),
                ("deliveries", "site_id", "site_id", site_ids, "customer_sites"),
                ("deliveries", "time_window_id", "time_window_id", set(tables["time_windows"]["time_window_id"]), "time_windows"),
            ]
        if "vehicle_capacities" in tables:
            fk_rules.append(("vehicle_capacities", "vehicle_id", "vehicle_id", set(tables["vehicles"]["vehicle_id"]), "vehicles"))
        if "vehicle_equipment" in tables:
            fk_rules.append(("vehicle_equipment", "vehicle_id", "vehicle_id", set(tables["vehicles"]["vehicle_id"]), "vehicles"))
        if "driver_certifications" in tables:
            fk_rules.append(("driver_certifications", "driver_id", "driver_id", set(tables["drivers"]["driver_id"]), "drivers"))
        if "shifts" in tables:
            fk_rules += [
                ("shifts", "driver_id", "driver_id", set(tables["drivers"]["driver_id"]), "drivers"),
                ("shifts", "vehicle_id", "vehicle_id", set(tables["vehicles"]["vehicle_id"]), "vehicles"),
            ]
        if "travel_time_matrix" in tables:
            fk_rules += [
                ("travel_time_matrix", "origin_id", "origin_id", location_ids, "depots/customer_sites"),
                ("travel_time_matrix", "destination_id", "destination_id", location_ids, "depots/customer_sites"),
            ]
        if "routes" in tables:
            fk_rules += [
                ("routes", "vehicle_id", "vehicle_id", set(tables["vehicles"]["vehicle_id"]), "vehicles"),
                ("routes", "driver_id", "driver_id", set(tables["drivers"]["driver_id"]), "drivers"),
                ("routes", "shift_id", "shift_id", set(tables["shifts"]["shift_id"]), "shifts"),
                ("routes", "scenario_id", "scenario_id", set(tables["travel_scenarios"]["scenario_id"]), "travel_scenarios"),
                ("routes", "depot_id", "depot_id", depot_ids, "depots"),
            ]
        if "route_stops" in tables:
            fk_rules += [
                ("route_stops", "route_id", "route_id", set(tables["routes"]["route_id"]), "routes"),
                ("route_stops", "delivery_id", "delivery_id", set(tables["deliveries"]["delivery_id"]), "deliveries"),
                ("route_stops", "site_id", "site_id", site_ids, "customer_sites"),
                ("route_stops", "time_window_id", "time_window_id", set(tables["time_windows"]["time_window_id"]), "time_windows"),
            ]
        if "route_legs" in tables:
            fk_rules += [
                ("route_legs", "route_id", "route_id", set(tables["routes"]["route_id"]), "routes"),
                ("route_legs", "origin_id", "origin_id", location_ids, "depots/customer_sites"),
                ("route_legs", "destination_id", "destination_id", location_ids, "depots/customer_sites"),
            ]
        if "delivery_assignments" in tables:
            fk_rules += [
                ("delivery_assignments", "delivery_id", "delivery_id", set(tables["deliveries"]["delivery_id"]), "deliveries"),
                ("delivery_assignments", "route_id", "route_id", set(tables["routes"]["route_id"]), "routes"),
                ("delivery_assignments", "stop_id", "stop_id", set(tables["route_stops"]["stop_id"]), "route_stops"),
            ]
        if "delivery_outcomes" in tables:
            fk_rules += [
                ("delivery_outcomes", "delivery_id", "delivery_id", set(tables["deliveries"]["delivery_id"]), "deliveries"),
                ("delivery_outcomes", "route_id", "route_id", set(tables["routes"]["route_id"]), "routes"),
                ("delivery_outcomes", "stop_id", "stop_id", set(tables["route_stops"]["stop_id"]), "route_stops"),
            ]
        if "route_events" in tables:
            fk_rules += [
                ("route_events", "route_id", "route_id", set(tables["routes"]["route_id"]), "routes"),
                ("route_events", "stop_id", "stop_id", set(tables["route_stops"]["stop_id"]), "route_stops"),
            ]
        if "delivery_events" in tables:
            fk_rules += [
                ("delivery_events", "delivery_id", "delivery_id", set(tables["deliveries"]["delivery_id"]), "deliveries"),
                ("delivery_events", "route_id", "route_id", set(tables["routes"]["route_id"]), "routes"),
                ("delivery_events", "stop_id", "stop_id", set(tables["route_stops"]["stop_id"]), "route_stops"),
            ]

        for table, column, ref_column, valid, parent in fk_rules:
            if table not in tables or column not in tables[table].columns:
                continue
            frame = tables[table]
            ids = self._row_ids(frame, table)
            mask = frame[column].notna() & ~frame[column].astype(str).isin({str(v) for v in valid})
            for index in frame.index[mask]:
                collector.add(
                    DQIssueType.INVALID_FOREIGN_KEY,
                    table,
                    ids.loc[index],
                    f"{table}.{column}={frame.loc[index, column]!r} does not exist in {parent}.{ref_column}",
                    table,
                    field_name=column,
                    severity=DQSeverity.ERROR,
                )

    def _check_uniqueness(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        unique_rules = [
            ("deliveries", ["order_id"], "one delivery per order"),
            ("vehicle_capacities", ["vehicle_id"], "one capacity record per vehicle"),
            ("time_windows", ["owner_id"], "one time window per owner"),
            ("shifts", ["driver_id", "service_date"], "one shift per driver per date"),
            ("shifts", ["vehicle_id", "service_date"], "one shift per vehicle per date"),
        ]
        for table, columns, description in unique_rules:
            if table not in tables or any(column not in tables[table].columns for column in columns):
                continue
            frame = tables[table]
            mask = frame.duplicated(subset=columns, keep=False)
            if mask.any():
                for _, group in frame[mask].groupby(columns, dropna=False):
                    entity = "|".join(str(group.iloc[0][c]) for c in columns)
                    collector.add(
                        DQIssueType.DUPLICATE_PRIMARY_KEY,
                        table,
                        entity,
                        f"{description}: duplicate key {entity}",
                        table,
                        field_name=",".join(columns),
                        severity=DQSeverity.CRITICAL,
                    )

    def _check_coordinates(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        region = self.config.region
        for table in ["depots", "customer_sites"]:
            if table not in tables:
                continue
            frame = tables[table]
            ids = self._row_ids(frame, table)
            for column in ["latitude", "longitude"]:
                if column not in frame.columns:
                    continue
                values = pd.to_numeric(frame[column], errors="coerce")
                if column == "latitude":
                    mask = values.notna() & ~values.between(region.min_lat, region.max_lat)
                else:
                    mask = values.notna() & ~values.between(region.min_lon, region.max_lon)
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.COORDINATE_OUT_OF_REGION,
                        table,
                        ids.loc[index],
                        f"{table}.{column}={frame.loc[index, column]!r} is outside the {region.name} region",
                        table,
                        field_name=column,
                        severity=DQSeverity.ERROR,
                    )

    def _check_master_dates(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if "customer_sites" in tables:
            frame = tables["customer_sites"]
            if {"effective_from", "effective_to"}.issubset(frame.columns):
                start = pd.to_datetime(frame["effective_from"], errors="coerce")
                end = pd.to_datetime(frame["effective_to"], errors="coerce")
                mask = start.notna() & end.notna() & (end < start)
                ids = self._row_ids(frame, "customer_sites")
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATE_ORDER,
                        "customer_sites",
                        ids.loc[index],
                        "site effective_to precedes effective_from",
                        "customer_sites",
                        field_name="effective_to",
                    )
        if "orders" in tables:
            frame = tables["orders"]
            if {"order_date", "requested_delivery_date"}.issubset(frame.columns):
                start = pd.to_datetime(frame["order_date"], errors="coerce")
                end = pd.to_datetime(frame["requested_delivery_date"], errors="coerce")
                mask = start.notna() & end.notna() & (start > end)
                ids = self._row_ids(frame, "orders")
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATE_ORDER,
                        "orders",
                        ids.loc[index],
                        "order_date is after requested_delivery_date",
                        "orders",
                        field_name="requested_delivery_date",
                    )
        if "time_windows" in tables:
            frame = tables["time_windows"]
            if {"window_start", "window_end"}.issubset(frame.columns):
                start = pd.to_datetime(frame["window_start"], errors="coerce")
                end = pd.to_datetime(frame["window_end"], errors="coerce")
                mask = start.notna() & end.notna() & (start >= end)
                ids = self._row_ids(frame, "time_windows")
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATE_ORDER,
                        "time_windows",
                        ids.loc[index],
                        "time window start must precede end",
                        "time_windows",
                        field_name="window_end",
                    )
        if "shifts" in tables:
            frame = tables["shifts"]
            if {"planned_start", "planned_end"}.issubset(frame.columns):
                start = pd.to_datetime(frame["planned_start"], errors="coerce")
                end = pd.to_datetime(frame["planned_end"], errors="coerce")
                mask = start.notna() & end.notna() & (start >= end)
                ids = self._row_ids(frame, "shifts")
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATE_ORDER,
                        "shifts",
                        ids.loc[index],
                        "shift planned_start must precede planned_end",
                        "shifts",
                        field_name="planned_end",
                    )
                if "service_date" in frame.columns:
                    dates = pd.to_datetime(frame["service_date"], errors="coerce")
                    mask = start.notna() & dates.notna() & (start.dt.date != dates.dt.date)
                    for index in frame.index[mask]:
                        collector.add(
                            DQIssueType.INVALID_DATE_ORDER,
                            "shifts",
                            ids.loc[index],
                            "shift start date differs from service_date",
                            "shifts",
                            field_name="service_date",
                        )

    def _check_master_cross_table(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if "deliveries" in tables and "orders" in tables:
            deliveries = tables["deliveries"]
            orders = tables["orders"]
            merged = deliveries.merge(
                orders,
                on="order_id",
                how="left",
                suffixes=("_delivery", "_order"),
            )
            ids = merged["delivery_id"]
            if "site_id_delivery" in merged.columns and "site_id_order" in merged.columns:
                mask = merged["site_id_delivery"] != merged["site_id_order"]
                for index in merged.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_REFERENCE,
                        "deliveries",
                        ids.loc[index],
                        "delivery site does not match its order site",
                        "deliveries",
                        field_name="site_id",
                    )
            if "service_date" in merged.columns and "requested_delivery_date" in merged.columns:
                service = pd.to_datetime(merged["service_date"], errors="coerce")
                requested = pd.to_datetime(merged["requested_delivery_date"], errors="coerce")
                mask = service.notna() & requested.notna() & (service.dt.date != requested.dt.date)
                for index in merged.index[mask]:
                    collector.add(
                        DQIssueType.INVALID_DATE_ORDER,
                        "deliveries",
                        ids.loc[index],
                        "delivery service_date differs from order requested date",
                        "deliveries",
                        field_name="service_date",
                    )
        if "time_windows" in tables and "deliveries" in tables:
            windows = tables["time_windows"]
            deliveries = tables["deliveries"]
            merged = deliveries.merge(
                windows, on="time_window_id", how="left", suffixes=("", "_window")
            )
            ids = merged["delivery_id"]
            mask = (
                merged["owner_type"].notna()
                & (merged["owner_type"].astype(str) != "ORDER")
            )
            for index in merged.index[mask]:
                collector.add(
                    DQIssueType.INVALID_REFERENCE,
                    "time_windows",
                    merged.loc[index, "time_window_id"],
                    "delivery time window is not owned by an order",
                    "time_windows",
                    field_name="owner_type",
                )
            mask = (
                merged["owner_id"].notna()
                & (merged["owner_id"].astype(str) != merged["order_id"].astype(str))
            )
            for index in merged.index[mask]:
                collector.add(
                    DQIssueType.INVALID_REFERENCE,
                    "time_windows",
                    merged.loc[index, "time_window_id"],
                    "time window owner does not match the delivery order",
                    "time_windows",
                    field_name="owner_id",
                )
            start = pd.to_datetime(merged["window_start"], errors="coerce")
            end = pd.to_datetime(merged["window_end"], errors="coerce")
            service = pd.to_datetime(merged["service_date"], errors="coerce")
            mask = start.notna() & end.notna() & service.notna() & (
                start.dt.date != service.dt.date
            )
            for index in merged.index[mask]:
                collector.add(
                    DQIssueType.INVALID_DATE_ORDER,
                    "time_windows",
                    merged.loc[index, "time_window_id"],
                    "time window does not fall on the delivery service date",
                    "time_windows",
                    field_name="window_start",
                )
        if "shifts" in tables and "vehicles" in tables and "drivers" in tables:
            shifts = tables["shifts"]
            vehicles = tables["vehicles"]
            drivers = tables["drivers"]
            merged = shifts.merge(vehicles, on="vehicle_id", how="left").merge(
                drivers, on="driver_id", how="left", suffixes=("_vehicle", "_driver")
            )
            mask = (
                merged["required_license_class"].notna()
                & merged["license_class"].notna()
                & (merged["required_license_class"].astype(str) != merged["license_class"].astype(str))
            )
            ids = merged["shift_id"]
            for index in merged.index[mask]:
                collector.add(
                    DQIssueType.INVALID_REFERENCE,
                    "shifts",
                    ids.loc[index],
                    "driver license class is incompatible with the assigned vehicle",
                    "shifts",
                    field_name="driver_id",
                )

    # ------------------------------------------------------------------
    # Plan validation
    # ------------------------------------------------------------------
    def _check_plan_keys_and_required(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table in [t for t in PLAN_TABLES if t in tables]:
            frame = tables[table]
            self._check_primary_keys({table: frame}, collector)
            ids = self._row_ids(frame, table)
            for column in REQUIRED_COLUMNS.get(table, []):
                if column not in frame.columns:
                    continue
                mask = self._missing_mask(frame, column)
                for index in frame.index[mask]:
                    collector.add(
                        DQIssueType.MISSING_REQUIRED_FIELD,
                        table,
                        ids.loc[index],
                        f"{table}.{column} is required but empty",
                        table,
                        field_name=column,
                    )

    def _check_plan_references(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        # Foreign keys were already checked once by validate_master (which
        # receives the complete snapshot in validate_full); only plan-specific
        # uniqueness rules remain here.
        if "route_stops" in tables:
            frame = tables["route_stops"]
            for columns, description in [
                (["delivery_id"], "a delivery may appear on only one route"),
                (["route_id", "stop_sequence"], "route stop sequence must be unique"),
            ]:
                if not all(column in frame.columns for column in columns):
                    continue
                mask = frame.duplicated(subset=columns, keep=False)
                for _, group in frame[mask].groupby(columns, dropna=False):
                    entity = "|".join(str(group.iloc[0][column]) for column in columns)
                    collector.add(
                        DQIssueType.STOP_DUPLICATED,
                        "route_stops",
                        entity,
                        f"{description}: duplicate {entity}",
                        "route_stops",
                        field_name=",".join(columns),
                        severity=DQSeverity.CRITICAL,
                    )
        if "delivery_assignments" in tables:
            frame = tables["delivery_assignments"]
            mask = frame.duplicated(subset=["delivery_id"], keep=False)
            for _, group in frame[mask].groupby("delivery_id", dropna=False):
                collector.add(
                    DQIssueType.DUPLICATE_PRIMARY_KEY,
                    "delivery_assignments",
                    str(group.iloc[0]["delivery_id"]),
                    "more than one assignment exists for a delivery",
                    "delivery_assignments",
                    field_name="delivery_id",
                    severity=DQSeverity.CRITICAL,
                )
        if "delivery_outcomes" in tables:
            frame = tables["delivery_outcomes"]
            mask = frame.duplicated(subset=["delivery_id"], keep=False)
            for _, group in frame[mask].groupby("delivery_id", dropna=False):
                collector.add(
                    DQIssueType.DUPLICATE_PRIMARY_KEY,
                    "delivery_outcomes",
                    str(group.iloc[0]["delivery_id"]),
                    "more than one outcome exists for a delivery",
                    "delivery_outcomes",
                    field_name="delivery_id",
                    severity=DQSeverity.CRITICAL,
                )
        if "routes" in tables:
            frame = tables["routes"]
            for columns, description in [
                (["vehicle_id", "service_date"], "one route per vehicle per date"),
                (["driver_id", "service_date"], "one route per driver per date"),
            ]:
                mask = frame.duplicated(subset=columns, keep=False)
                for _, group in frame[mask].groupby(columns, dropna=False):
                    entity = "|".join(str(group.iloc[0][c]) for c in columns)
                    collector.add(
                        DQIssueType.DUPLICATE_PRIMARY_KEY,
                        "routes",
                        entity,
                        f"{description}: {entity}",
                        "routes",
                        field_name=",".join(columns),
                        severity=DQSeverity.CRITICAL,
                    )

    def _check_route_capacity(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if not {"routes", "route_stops", "vehicles", "vehicle_capacities"}.issubset(tables):
            return
        stops = tables["route_stops"]
        loads = stops.groupby("route_id", as_index=False).agg(
            stop_units=("demand_units", "sum"),
            stop_weight=("weight_kg", "sum"),
            stop_count=("stop_id", "count"),
        )
        routes = tables["routes"].merge(loads, on="route_id", how="left")
        caps = tables["vehicle_capacities"][
            ["vehicle_id", "capacity_units", "max_weight_kg"]
        ].rename(
            columns={
                "capacity_units": "vehicle_capacity_units",
                "max_weight_kg": "vehicle_capacity_weight_kg",
            }
        )
        routes = routes.merge(caps, on="vehicle_id", how="left")
        for index, row in routes.iterrows():
            units = float(row.get("stop_units", 0) or 0)
            weight = float(row.get("stop_weight", 0) or 0)
            if pd.notna(row.get("vehicle_capacity_units")) and units > float(
                row["vehicle_capacity_units"]
            ) + 1e-9:
                collector.add(
                    DQIssueType.ROUTE_CAPACITY_EXCEEDED,
                    "routes",
                    str(row["route_id"]),
                    f"route load {units:g} units exceeds vehicle capacity {row['vehicle_capacity_units']:g}",
                    "routes",
                    field_name="load_units",
                    severity=DQSeverity.CRITICAL,
                )
            if pd.notna(row.get("vehicle_capacity_weight_kg")) and weight > float(
                row["vehicle_capacity_weight_kg"]
            ) + 1e-9:
                collector.add(
                    DQIssueType.WEIGHT_CAPACITY_EXCEEDED,
                    "routes",
                    str(row["route_id"]),
                    f"route weight {weight:g} kg exceeds vehicle limit {row['vehicle_capacity_weight_kg']:g}",
                    "routes",
                    field_name="load_weight_kg",
                    severity=DQSeverity.CRITICAL,
                )

    def _check_route_time_windows(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if not {"route_stops", "time_windows"}.issubset(tables):
            return
        stops = tables["route_stops"].merge(
            tables["time_windows"], on="time_window_id", how="left"
        )
        service = pd.to_datetime(stops["planned_service_start"], errors="coerce")
        start = pd.to_datetime(stops["window_start"], errors="coerce")
        end = pd.to_datetime(stops["window_end"], errors="coerce")
        mask = service.notna() & start.notna() & end.notna() & (
            (service < start) | (service > end)
        )
        for index in stops.index[mask]:
            collector.add(
                DQIssueType.TIME_WINDOW_VIOLATION,
                "route_stops",
                str(stops.loc[index, "stop_id"]),
                "planned service start falls outside the hard delivery time window",
                "route_stops",
                field_name="planned_service_start",
                severity=DQSeverity.CRITICAL,
            )

    def _check_route_shifts(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if not {"routes", "shifts"}.issubset(tables):
            return
        routes = tables["routes"].merge(
            tables["shifts"], on="shift_id", how="left", suffixes=("", "_shift")
        )
        start = pd.to_datetime(routes["planned_start"], errors="coerce")
        end = pd.to_datetime(routes["planned_end"], errors="coerce")
        shift_start = pd.to_datetime(routes["planned_start_shift"], errors="coerce")
        shift_end = pd.to_datetime(routes["planned_end_shift"], errors="coerce")
        buffer_minutes = self.config.routing.execution_buffer_minutes
        effective_end = shift_end - pd.to_timedelta(
            routes["break_minutes"].fillna(0) + buffer_minutes, unit="m"
        )
        mask = start.notna() & end.notna() & shift_start.notna() & effective_end.notna() & (
            (start < shift_start) | (end > effective_end)
        )
        for index in routes.index[mask]:
            collector.add(
                DQIssueType.DRIVER_SHIFT_EXCEEDED,
                "routes",
                str(routes.loc[index, "route_id"]),
                "planned route lies outside the driver's effective shift (break and execution buffer reserved)",
                "routes",
                field_name="planned_end",
                severity=DQSeverity.CRITICAL,
            )
        if "actual_end" in routes.columns:
            actual_end = pd.to_datetime(routes["actual_end"], errors="coerce")
            mask = actual_end.notna() & shift_end.notna() & (actual_end > shift_end)
            for index in routes.index[mask]:
                collector.add(
                    DQIssueType.DRIVER_SHIFT_EXCEEDED,
                    "routes",
                    str(routes.loc[index, "route_id"]),
                    "actual route end exceeds the scheduled driver shift end",
                    "routes",
                    field_name="actual_end",
                    severity=DQSeverity.CRITICAL,
                )

    def _check_route_chronology(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if "route_stops" not in tables:
            return
        stops = tables["route_stops"].copy()
        stops["planned_arrival"] = pd.to_datetime(stops["planned_arrival"], errors="coerce")
        stops["planned_service_start"] = pd.to_datetime(
            stops["planned_service_start"], errors="coerce"
        )
        stops["planned_departure"] = pd.to_datetime(stops["planned_departure"], errors="coerce")
        for index, row in stops.iterrows():
            if not (
                pd.notna(row["planned_arrival"])
                and pd.notna(row["planned_service_start"])
                and pd.notna(row["planned_departure"])
            ):
                continue
            if not (
                row["planned_arrival"]
                <= row["planned_service_start"]
                <= row["planned_departure"]
            ):
                collector.add(
                    DQIssueType.INVALID_DATE_ORDER,
                    "route_stops",
                    str(row["stop_id"]),
                    "planned stop chronology is invalid",
                    "route_stops",
                    field_name="planned_service_start",
                )
        if "actual_arrival" in stops.columns:
            stops["actual_arrival"] = pd.to_datetime(stops["actual_arrival"], errors="coerce")
            stops["actual_service_start"] = pd.to_datetime(
                stops["actual_service_start"], errors="coerce"
            )
            stops["actual_departure"] = pd.to_datetime(stops["actual_departure"], errors="coerce")
            for index, row in stops.iterrows():
                if pd.isna(row["actual_arrival"]):
                    continue
                if not (
                    row["actual_arrival"]
                    <= row["actual_service_start"]
                    <= row["actual_departure"]
                ):
                    collector.add(
                        DQIssueType.INVALID_DATE_ORDER,
                        "route_stops",
                        str(row["stop_id"]),
                        "actual stop chronology is invalid",
                        "route_stops",
                        field_name="actual_service_start",
                    )
            ordered = stops.sort_values(["route_id", "stop_sequence"])
            previous_departure: pd.Timestamp | None = None
            previous_route: str | None = None
            for index, row in ordered.iterrows():
                if row["route_id"] != previous_route:
                    previous_departure = None
                    previous_route = str(row["route_id"])
                if (
                    previous_departure is not None
                    and pd.notna(row["actual_arrival"])
                    and row["actual_arrival"] < previous_departure
                ):
                    collector.add(
                        DQIssueType.ARRIVAL_BEFORE_PREVIOUS_DEPARTURE,
                        "route_stops",
                        str(row["stop_id"]),
                        "actual arrival precedes the previous stop's actual departure",
                        "route_stops",
                        field_name="actual_arrival",
                        severity=DQSeverity.CRITICAL,
                    )
                if pd.notna(row["actual_departure"]):
                    previous_departure = row["actual_departure"]
        if "routes" in tables:
            routes = tables["routes"].copy()
            for column in ["planned_start", "planned_end", "actual_start", "actual_end"]:
                if column in routes.columns:
                    routes[column] = pd.to_datetime(routes[column], errors="coerce")
            for index, row in routes.iterrows():
                if pd.notna(row.get("actual_start")) and pd.notna(row.get("actual_end")):
                    if row["actual_start"] > row["actual_end"]:
                        collector.add(
                            DQIssueType.INVALID_DATE_ORDER,
                            "routes",
                            str(row["route_id"]),
                            "actual route start is after actual route end",
                            "routes",
                            field_name="actual_end",
                        )

    def _check_delivery_assignment_reconciliation(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if not {"deliveries", "delivery_assignments", "route_stops"}.issubset(tables):
            return
        deliveries = tables["deliveries"]
        assignments = tables["delivery_assignments"]
        stops = tables["route_stops"]
        assigned_ids = set(
            assignments.loc[
                assignments["status"].astype(str) == AssignmentStatus.ASSIGNED.value,
                "delivery_id",
            ]
        )
        unserved_ids = set(
            assignments.loc[
                assignments["status"].astype(str) == AssignmentStatus.UNSERVED.value,
                "delivery_id",
            ]
        )
        stop_delivery_ids = set(stops["delivery_id"])
        assignment_ids = set(assignments["delivery_id"])
        all_ids = set(deliveries["delivery_id"])
        for delivery_id in sorted(all_ids - assignment_ids):
            collector.add(
                DQIssueType.UNASSIGNED_DELIVERY,
                "deliveries",
                str(delivery_id),
                "delivery has no assignment status",
                "deliveries",
                severity=DQSeverity.CRITICAL,
            )
        for delivery_id in sorted(assigned_ids - stop_delivery_ids):
            collector.add(
                DQIssueType.STOP_OMITTED,
                "deliveries",
                str(delivery_id),
                "assigned delivery has no route stop",
                "route_stops",
                severity=DQSeverity.CRITICAL,
            )
        for delivery_id in sorted(stop_delivery_ids - assigned_ids):
            collector.add(
                DQIssueType.INVALID_LIFECYCLE_TRANSITION,
                "route_stops",
                str(delivery_id),
                "route stop references a delivery that is not assigned",
                "route_stops",
                severity=DQSeverity.ERROR,
            )
        stop_rows = stops.groupby("delivery_id").size()
        for delivery_id in sorted(assigned_ids & stop_delivery_ids):
            if int(stop_rows.get(delivery_id, 0)) != 1:
                collector.add(
                    DQIssueType.STOP_DUPLICATED,
                    "deliveries",
                    str(delivery_id),
                    "assigned delivery has multiple route stops",
                    "route_stops",
                    severity=DQSeverity.CRITICAL,
                )
        for delivery_id in sorted(unserved_ids & stop_delivery_ids):
            collector.add(
                DQIssueType.INVALID_LIFECYCLE_TRANSITION,
                "deliveries",
                str(delivery_id),
                "unserved delivery has a route stop",
                "delivery_assignments",
                severity=DQSeverity.ERROR,
            )

    def _check_route_arithmetic(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if not {"routes", "route_stops"}.issubset(tables):
            return
        stops = tables["route_stops"]
        aggregates = stops.groupby("route_id").agg(
            stop_count=("stop_id", "count"),
            load_units=("demand_units", "sum"),
            load_weight_kg=("weight_kg", "sum"),
            service_minutes=("service_minutes", "sum"),
            wait_minutes=("wait_minutes", "sum"),
        )
        for index, route in tables["routes"].iterrows():
            route_id = str(route["route_id"])
            if route_id not in aggregates.index:
                if int(route.get("stop_count", 0) or 0) != 0:
                    collector.add(
                        DQIssueType.ARITHMETIC_RECONCILIATION_ERROR,
                        "routes",
                        route_id,
                        "route stop_count is positive but no route stops exist",
                        "routes",
                        field_name="stop_count",
                        severity=DQSeverity.CRITICAL,
                    )
                continue
            aggregate = aggregates.loc[route_id]
            checks = [
                ("stop_count", float(aggregate["stop_count"])),
                ("load_units", float(aggregate["load_units"])),
                ("load_weight_kg", float(aggregate["load_weight_kg"])),
                ("service_minutes", float(aggregate["service_minutes"])),
                ("wait_minutes", float(aggregate["wait_minutes"])),
            ]
            for column, actual in checks:
                if column not in route or pd.isna(route[column]):
                    continue
                expected = float(route[column])
                if abs(expected - actual) > 0.05:
                    collector.add(
                        DQIssueType.ARITHMETIC_RECONCILIATION_ERROR,
                        "routes",
                        route_id,
                        f"route {column}={expected:g} does not reconcile to stops ({actual:g})",
                        "routes",
                        field_name=column,
                    )
        if "route_legs" in tables:
            legs = tables["route_legs"]
            leg_stats = legs.groupby("route_id").agg(
                distance_km=("planned_distance_km", "sum"),
                travel_minutes=("planned_travel_minutes", "sum"),
            )
            for index, route in tables["routes"].iterrows():
                route_id = str(route["route_id"])
                if route_id not in leg_stats.index:
                    continue
                stats = leg_stats.loc[route_id]
                if abs(float(route.get("distance_km", 0) or 0) - float(stats["distance_km"])) > 0.1:
                    collector.add(
                        DQIssueType.ARITHMETIC_RECONCILIATION_ERROR,
                        "routes",
                        route_id,
                        "route distance does not equal the sum of its route legs",
                        "routes",
                        field_name="distance_km",
                    )
                if abs(float(route.get("travel_minutes", 0) or 0) - float(stats["travel_minutes"])) > 0.2:
                    collector.add(
                        DQIssueType.ARITHMETIC_RECONCILIATION_ERROR,
                        "routes",
                        route_id,
                        "route travel minutes do not equal the sum of its route legs",
                        "routes",
                        field_name="travel_minutes",
                    )

    def _check_events(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        for table, id_column in [("route_events", "route_id"), ("delivery_events", "delivery_id")]:
            if table not in tables:
                continue
            events = tables[table].copy()
            events["event_time"] = pd.to_datetime(events["event_time"], errors="coerce")
            natural = [id_column, "event_type", "event_time"]
            mask = events.duplicated(subset=natural, keep=False)
            for _, group in events[mask].groupby(natural, dropna=False):
                collector.add(
                    DQIssueType.DUPLICATE_EVENT,
                    table,
                    str(group.iloc[0]["event_id"]),
                    "duplicate event with identical entity/type/time",
                    table,
                    severity=DQSeverity.ERROR,
                )
            ordered = events.sort_values([id_column, "sequence", "event_time"])
            previous_time = None
            previous_id = None
            for index, row in ordered.iterrows():
                if row[id_column] != previous_id:
                    previous_time = None
                    previous_id = row[id_column]
                if pd.notna(row["event_time"]) and previous_time is not None and row["event_time"] < previous_time:
                    collector.add(
                        DQIssueType.OUT_OF_ORDER_EVENT,
                        table,
                        str(row["event_id"]),
                        "event time moves backwards within its entity sequence",
                        table,
                        field_name="event_time",
                    )
                if pd.notna(row["event_time"]):
                    previous_time = row["event_time"]

    def _check_lifecycle(
        self, tables: dict[str, pd.DataFrame], collector: IssueCollector
    ) -> None:
        if {"deliveries", "delivery_outcomes"}.issubset(tables):
            merged = tables["deliveries"].merge(
                tables["delivery_outcomes"], on="delivery_id", how="left", suffixes=("", "_outcome")
            )
            for index, row in merged.iterrows():
                delivery_status = str(row["status"])
                outcome_status = str(row["status_outcome"]) if pd.notna(row["status_outcome"]) else ""
                if delivery_status == DeliveryStatus.PENDING.value:
                    collector.add(
                        DQIssueType.INVALID_LIFECYCLE_TRANSITION,
                        "deliveries",
                        str(row["delivery_id"]),
                        "delivery is still PENDING after execution",
                        "deliveries",
                        field_name="status",
                    )
                elif delivery_status in {
                    DeliveryStatus.ASSIGNED.value,
                    DeliveryStatus.DELIVERED.value,
                    DeliveryStatus.FAILED.value,
                    DeliveryStatus.UNSERVED.value,
                } and outcome_status != delivery_status:
                    collector.add(
                        DQIssueType.INVALID_LIFECYCLE_TRANSITION,
                        "deliveries",
                        str(row["delivery_id"]),
                        f"delivery status {delivery_status} disagrees with outcome {outcome_status}",
                        "deliveries",
                        field_name="status",
                    )
        if {"route_stops", "delivery_outcomes"}.issubset(tables):
            merged = tables["route_stops"].merge(
                tables["delivery_outcomes"], on="delivery_id", how="left", suffixes=("", "_outcome")
            )
            for index, row in merged.iterrows():
                if pd.isna(row.get("status_outcome")):
                    continue
                outcome = str(row["status_outcome"])
                valid = {
                    OutcomeStatus.DELIVERED.value: DeliveryStatus.DELIVERED.value,
                    OutcomeStatus.FAILED.value: DeliveryStatus.FAILED.value,
                }.get(outcome)
                if valid and str(row["status"]) != valid:
                    collector.add(
                        DQIssueType.INVALID_LIFECYCLE_TRANSITION,
                        "route_stops",
                        str(row["stop_id"]),
                        f"route stop status {row['status']} disagrees with outcome {outcome}",
                        "route_stops",
                        field_name="status",
                    )

    # ------------------------------------------------------------------
    # Quarantine
    # ------------------------------------------------------------------
    def _build_quarantine(
        self, tables: dict[str, pd.DataFrame], result: ValidationResult
    ) -> dict[str, pd.DataFrame]:
        if result.issues.empty:
            return {}
        candidates = result.issues[
            result.issues["issue_type"].isin({item.value for item in QUARANTINE_TYPES})
        ]
        if candidates.empty:
            return {}
        quarantine: dict[str, pd.DataFrame] = {}
        marked_ids: set[str] = set()
        for table, issues in candidates.groupby("source_table"):
            if table not in tables:
                continue
            frame = tables[table]
            keys = PRIMARY_KEYS.get(table, [])
            if len(keys) != 1 or keys[0] not in frame.columns:
                continue
            key = keys[0]
            entity_ids = set(issues["entity_id"].astype(str))
            mask = frame[key].astype(str).isin(entity_ids)
            if mask.any():
                quarantine[table] = frame[mask].copy()
                marked_ids.update(issues["issue_id"].tolist())
        if marked_ids:
            result.issues.loc[
                result.issues["issue_id"].isin(marked_ids), "quarantined"
            ] = True
        return quarantine

    def quarantine_frame(self, quarantine: dict[str, pd.DataFrame]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for table, frame in quarantine.items():
            for record in frame.to_dict("records"):
                rows.append(
                    {
                        "run_id": self.run_id,
                        "source_table": table,
                        "record_json": pd.Series(record).to_json(
                            orient="index", date_format="iso"
                        ),
                        "quarantined_at": datetime.now().replace(microsecond=0),
                    }
                )
        return pd.DataFrame(rows)


def validate_master(
    tables: dict[str, pd.DataFrame], config: AppConfig
) -> ValidationResult:
    return DataQualityValidator(config).validate_master(tables)


def validate_full(
    tables: dict[str, pd.DataFrame], config: AppConfig
) -> ValidationResult:
    return DataQualityValidator(config).validate_full(tables)
