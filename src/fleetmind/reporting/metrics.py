"""Business KPI reporting and cross-table reconciliation."""

from __future__ import annotations

from typing import Any

import pandas as pd

from fleetmind.config import AppConfig
from fleetmind.models.entities import ReconciliationCheck, ReportMetric
from fleetmind.models.enums import AssignmentStatus, DeliveryStatus, OutcomeStatus


def _frame(tables: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    frame = tables.get(name)
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _contains(series: pd.Series, value: Any) -> pd.Series:
    return series.map(lambda item: str(getattr(item, "value", item)) == str(value))


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _truthy(series: pd.Series) -> pd.Series:
    return series.map(lambda value: bool(value) if pd.notna(value) else False)


def _metric(
    run_id: str,
    category: str,
    name: str,
    value: float,
    unit: str,
    *,
    numerator: float | None = None,
    denominator: float | None = None,
    formula: str = "",
    dimensions: str = "",
) -> ReportMetric:
    return ReportMetric(
        run_id=run_id,
        category=category,
        metric_name=name,
        value=float(round(value, 6)),
        unit=unit,
        numerator=None if numerator is None else float(round(numerator, 6)),
        denominator=None if denominator is None else float(round(denominator, 6)),
        formula=formula,
        dimensions=dimensions,
    )


def _id_set(frame: pd.DataFrame, column: str) -> set[str]:
    if frame.empty or column not in frame:
        return set()
    return set(frame[column].astype(str))


def compute_report_metrics(
    tables: dict[str, pd.DataFrame], config: AppConfig
) -> tuple[pd.DataFrame, dict[str, Any]]:
    run_id = config.run_id
    deliveries = _frame(tables, "deliveries")
    outcomes = _frame(tables, "delivery_outcomes")
    assignments = _frame(tables, "delivery_assignments")
    routes = _frame(tables, "routes")
    stops = _frame(tables, "route_stops")
    legs = _frame(tables, "route_legs")
    vehicles = _frame(tables, "vehicles")
    sites = _frame(tables, "customer_sites")
    shifts = _frame(tables, "shifts")

    total = len(deliveries)
    assigned = assignments[
        _contains(assignments.get("status", pd.Series(dtype=str)), AssignmentStatus.ASSIGNED.value)
    ] if not assignments.empty else assignments
    unserved_assignments = assignments[
        _contains(assignments.get("status", pd.Series(dtype=str)), AssignmentStatus.UNSERVED.value)
    ] if not assignments.empty else assignments
    delivered = outcomes[
        _contains(outcomes.get("status", pd.Series(dtype=str)), OutcomeStatus.DELIVERED.value)
    ] if not outcomes.empty else outcomes
    failed = outcomes[
        _contains(outcomes.get("status", pd.Series(dtype=str)), OutcomeStatus.FAILED.value)
    ] if not outcomes.empty else outcomes
    unserved_outcomes = outcomes[
        _contains(outcomes.get("status", pd.Series(dtype=str)), OutcomeStatus.UNSERVED.value)
    ] if not outcomes.empty else outcomes

    delivered_count = len(delivered)
    failed_count = len(failed)
    assigned_count = len(assigned)
    on_time_mask = _truthy(delivered.get("on_time", pd.Series(dtype=bool))) if not delivered.empty else pd.Series(dtype=bool)
    on_time_count = int(on_time_mask.sum())
    unserved_count = len(unserved_assignments)

    rows: list[ReportMetric] = []

    def add(category: str, name: str, value: float, unit: str, **kwargs: Any) -> None:
        rows.append(_metric(run_id, category, name, value, unit, **kwargs))

    add(
        "service", "delivery_service_rate", _ratio(delivered_count, total), "ratio",
        numerator=delivered_count, denominator=total,
        formula="delivered outcomes / requested deliveries",
    )
    add(
        "service", "plan_assignment_rate", _ratio(assigned_count, total), "ratio",
        numerator=assigned_count, denominator=total,
        formula="assigned deliveries / requested deliveries",
    )
    add(
        "service", "execution_completion_rate", _ratio(delivered_count, assigned_count), "ratio",
        numerator=delivered_count, denominator=assigned_count,
        formula="delivered outcomes / assigned deliveries",
    )
    add(
        "service", "failure_rate", _ratio(failed_count, assigned_count), "ratio",
        numerator=failed_count, denominator=assigned_count,
        formula="failed outcomes / assigned deliveries",
    )
    add(
        "service", "unserved_rate", _ratio(unserved_count, total), "ratio",
        numerator=unserved_count, denominator=total,
        formula="unserved assignments / requested deliveries",
    )
    add(
        "service", "on_time_rate", _ratio(on_time_count, delivered_count), "ratio",
        numerator=on_time_count, denominator=delivered_count,
        formula="on-time delivered outcomes / delivered outcomes",
    )
    add("service", "delivered_deliveries", delivered_count, "count")
    add("service", "failed_deliveries", failed_count, "count")
    add("service", "unserved_deliveries", unserved_count, "count")
    add("service", "total_deliveries", total, "count")

    # --------------------------------------------------------------
    # Dimensional service metrics
    # --------------------------------------------------------------
    if not deliveries.empty and not outcomes.empty:
        outcome_view = outcomes.copy()
        outcome_view["_outcome_status"] = outcome_view["status"].map(
            lambda value: str(getattr(value, "value", value))
        )
        outcome_view["_on_time"] = outcome_view.get("on_time", False)
        joined = deliveries[
            ["delivery_id", "priority", "handling_class", "service_date"]
        ].merge(outcome_view, on="delivery_id", how="left", suffixes=("", "_outcome"))
        joined["_priority"] = joined["priority"].map(
            lambda value: str(getattr(value, "value", value))
        )
        joined["_handling"] = joined["handling_class"].map(
            lambda value: str(getattr(value, "value", value))
        )
        for priority, group in joined.groupby("_priority", sort=True):
            delivered_group = group[group["_outcome_status"] == OutcomeStatus.DELIVERED.value]
            on_time_group = _truthy(delivered_group["_on_time"])
            add(
                "service_priority", "on_time_rate", _ratio(int(on_time_group.sum()), len(delivered_group)),
                "ratio", numerator=int(on_time_group.sum()), denominator=len(delivered_group),
                formula="on-time deliveries / delivered deliveries",
                dimensions=f"priority={priority}",
            )
            add(
                "service_priority", "delivery_service_rate", _ratio(len(delivered_group), len(group)),
                "ratio", numerator=len(delivered_group), denominator=len(group),
                formula="delivered outcomes / requested deliveries",
                dimensions=f"priority={priority}",
            )
        for handling, group in joined.groupby("_handling", sort=True):
            delivered_group = group[group["_outcome_status"] == OutcomeStatus.DELIVERED.value]
            add(
                "service_handling", "delivery_service_rate", _ratio(len(delivered_group), len(group)),
                "ratio", numerator=len(delivered_group), denominator=len(group),
                formula="delivered outcomes / requested deliveries",
                dimensions=f"handling_class={handling}",
            )
        for service_date, group in joined.groupby("service_date", sort=True):
            delivered_group = group[group["_outcome_status"] == OutcomeStatus.DELIVERED.value]
            on_time_group = _truthy(delivered_group["_on_time"])
            label = pd.Timestamp(service_date).date().isoformat()
            add(
                "daily", "delivered_deliveries", len(delivered_group), "count",
                dimensions=f"service_date={label}",
            )
            add(
                "daily", "failed_deliveries",
                int((group["_outcome_status"] == OutcomeStatus.FAILED.value).sum()), "count",
                dimensions=f"service_date={label}",
            )
            add(
                "daily", "on_time_rate", _ratio(int(on_time_group.sum()), len(delivered_group)),
                "ratio", numerator=int(on_time_group.sum()), denominator=len(delivered_group),
                formula="on-time deliveries / delivered deliveries",
                dimensions=f"service_date={label}",
            )

    # --------------------------------------------------------------
    # Routing and fleet metrics
    # --------------------------------------------------------------
    route_count = len(routes)
    add("routing", "route_count", route_count, "count")
    add(
        "routing", "stops_per_route", _ratio(len(stops), route_count), "stops/route",
        numerator=len(stops), denominator=route_count,
        formula="route stops / routes",
    )
    total_distance = float(pd.to_numeric(routes.get("distance_km", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    add("routing", "total_distance_km", total_distance, "km")
    add(
        "routing", "average_distance_km_per_route", _ratio(total_distance, route_count), "km/route",
        numerator=total_distance, denominator=route_count,
        formula="total route distance / routes",
    )
    total_travel = float(pd.to_numeric(routes.get("travel_minutes", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_service = float(pd.to_numeric(routes.get("service_minutes", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_wait = float(pd.to_numeric(routes.get("wait_minutes", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    add("routing", "total_travel_hours", total_travel / 60.0, "hours")
    add("routing", "total_service_hours", total_service / 60.0, "hours")
    add("routing", "total_wait_hours", total_wait / 60.0, "hours")
    add(
        "routing", "average_route_hours",
        _ratio(total_travel + total_service + total_wait, route_count) / 60.0,
        "hours/route", numerator=total_travel + total_service + total_wait,
        denominator=route_count, formula="(travel + service + wait) / routes / 60",
    )
    add(
        "routing", "distance_per_delivered_stop",
        _ratio(total_distance, len(stops)), "km/stop",
        numerator=total_distance, denominator=len(stops),
        formula="total route distance / route stops",
    )
    load_units = float(pd.to_numeric(routes.get("load_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    capacity_units = float(pd.to_numeric(routes.get("capacity_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    load_weight = float(pd.to_numeric(routes.get("load_weight_kg", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    capacity_weight = float(pd.to_numeric(routes.get("capacity_weight_kg", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    add(
        "routing", "capacity_utilization_units", _ratio(load_units, capacity_units), "ratio",
        numerator=load_units, denominator=capacity_units,
        formula="sum route load units / sum route capacity units",
    )
    add(
        "routing", "capacity_utilization_weight", _ratio(load_weight, capacity_weight), "ratio",
        numerator=load_weight, denominator=capacity_weight,
        formula="sum route load weight / sum route weight capacity",
    )
    if not routes.empty and {"load_units", "capacity_units"} <= set(routes.columns):
        utilization = (
            pd.to_numeric(routes["load_units"], errors="coerce")
            / pd.to_numeric(routes["capacity_units"], errors="coerce").replace(0, pd.NA)
        ).dropna()
        add(
            "routing", "peak_route_capacity_utilization",
            float(utilization.max()) if len(utilization) else 0.0, "ratio",
            formula="max(route load units / route capacity units)",
        )
        add(
            "routing", "average_route_capacity_utilization",
            float(utilization.mean()) if len(utilization) else 0.0, "ratio",
            formula="mean(route load units / route capacity units)",
        )

    active_vehicle_ids = _id_set(
        vehicles[_contains(vehicles.get("status", pd.Series(dtype=str)), "ACTIVE")]
        if not vehicles.empty
        else vehicles,
        "vehicle_id",
    )
    used_vehicle_ids = _id_set(routes, "vehicle_id")
    active_driver_ids = _id_set(
        pd.DataFrame(columns=["driver_id"]) if "drivers" not in tables else tables["drivers"],
        "driver_id",
    )
    used_driver_ids = _id_set(routes, "driver_id")
    add("fleet", "active_vehicles", len(active_vehicle_ids), "count")
    add("fleet", "vehicles_used", len(used_vehicle_ids), "count")
    add(
        "fleet", "fleet_utilization", _ratio(len(used_vehicle_ids), len(active_vehicle_ids)), "ratio",
        numerator=len(used_vehicle_ids), denominator=len(active_vehicle_ids),
        formula="distinct routed vehicles / active vehicles",
    )
    add("fleet", "active_drivers", len(active_driver_ids), "count")
    add("fleet", "drivers_used", len(used_driver_ids), "count")
    add(
        "fleet", "driver_utilization", _ratio(len(used_driver_ids), len(active_driver_ids)), "ratio",
        numerator=len(used_driver_ids), denominator=len(active_driver_ids),
        formula="distinct routed drivers / active drivers",
    )
    add(
        "fleet", "distance_per_delivery", _ratio(total_distance, delivered_count), "km/delivery",
        numerator=total_distance, denominator=delivered_count,
        formula="total route distance / delivered deliveries",
    )
    shift_overruns = int(
        (pd.to_numeric(routes.get("shift_overrun_minutes", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()
    ) if not routes.empty else 0
    overrun_minutes = float(pd.to_numeric(routes.get("shift_overrun_minutes", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    add("reliability", "routes_with_shift_overrun", shift_overruns, "count")
    add("reliability", "shift_overrun_minutes", overrun_minutes, "minutes")
    add("reliability", "planned_shifts", len(shifts), "count")

    if not routes.empty and not vehicles.empty and "vehicle_type" in vehicles:
        vehicle_lookup = vehicles[["vehicle_id", "vehicle_type"]].copy()
        vehicle_lookup["_vehicle_type"] = vehicle_lookup["vehicle_type"].map(
            lambda value: str(getattr(value, "value", value))
        )
        route_view = routes.merge(vehicle_lookup[["vehicle_id", "_vehicle_type"]], on="vehicle_id", how="left")
        for vehicle_type, group in route_view.groupby("_vehicle_type", sort=True):
            add(
                "fleet_type", "route_count", len(group), "count",
                dimensions=f"vehicle_type={vehicle_type}",
            )
            add(
                "fleet_type", "distance_km", float(pd.to_numeric(group["distance_km"], errors="coerce").fillna(0).sum()), "km",
                dimensions=f"vehicle_type={vehicle_type}",
            )
            add(
                "fleet_type", "capacity_utilization_units",
                _ratio(
                    float(pd.to_numeric(group["load_units"], errors="coerce").fillna(0).sum()),
                    float(pd.to_numeric(group["capacity_units"], errors="coerce").fillna(0).sum()),
                ),
                "ratio",
                numerator=float(pd.to_numeric(group["load_units"], errors="coerce").fillna(0).sum()),
                denominator=float(pd.to_numeric(group["capacity_units"], errors="coerce").fillna(0).sum()),
                formula="route load units / route capacity units",
                dimensions=f"vehicle_type={vehicle_type}",
            )

    if not deliveries.empty and not sites.empty and not outcomes.empty:
        site_lookup = sites[["site_id", "city"]].copy()
        delivery_site = deliveries[["delivery_id", "site_id"]].merge(site_lookup, on="site_id", how="left")
        outcome_view = outcomes[["delivery_id", "status"]].copy()
        outcome_view["_status"] = outcome_view["status"].map(
            lambda value: str(getattr(value, "value", value))
        )
        joined = delivery_site.merge(outcome_view, on="delivery_id", how="left")
        for city, group in joined.groupby("city", sort=True):
            delivered_group = group[group["_status"] == OutcomeStatus.DELIVERED.value]
            add(
                "site_city", "delivered_deliveries", len(delivered_group), "count",
                dimensions=f"city={city}",
            )
            add(
                "site_city", "delivery_service_rate", _ratio(len(delivered_group), len(group)), "ratio",
                numerator=len(delivered_group), denominator=len(group),
                formula="delivered outcomes / requested deliveries",
                dimensions=f"city={city}",
            )
        top_sites = (
            joined[joined["_status"] == OutcomeStatus.DELIVERED.value]
            .groupby("site_id", dropna=False)
            .size()
            .sort_values(ascending=False)
            .head(config.reporting.top_n)
        )
        for site_id, count in top_sites.items():
            add(
                "top_site", "delivered_deliveries", int(count), "count",
                dimensions=f"site_id={site_id}",
            )
        top_routes = routes.sort_values("distance_km", ascending=False).head(config.reporting.top_n)
        for row in top_routes.itertuples(index=False):
            add(
                "top_route", "distance_km", float(row.distance_km), "km",
                dimensions=f"route_id={row.route_id}",
            )

    metrics = pd.DataFrame([row.model_dump(mode="python") for row in rows])
    if metrics.empty:
        metrics = pd.DataFrame(columns=list(ReportMetric.model_fields))

    def value(category: str, name: str) -> float:
        if metrics.empty:
            return 0.0
        found = metrics[
            (metrics["category"] == category) & (metrics["metric_name"] == name)
            & (metrics["dimensions"] == "")
        ]
        return float(found["value"].iloc[0]) if len(found) else 0.0

    summary = {
        "delivery_service_rate": value("service", "delivery_service_rate"),
        "on_time_rate": value("service", "on_time_rate"),
        "failure_rate": value("service", "failure_rate"),
        "unserved_rate": value("service", "unserved_rate"),
        "route_count": int(value("routing", "route_count")),
        "total_distance_km": value("routing", "total_distance_km"),
        "capacity_utilization_units": value("routing", "capacity_utilization_units"),
        "fleet_utilization": value("fleet", "fleet_utilization"),
        "routes_with_shift_overrun": int(value("reliability", "routes_with_shift_overrun")),
        "delivered_deliveries": int(value("service", "delivered_deliveries")),
        "failed_deliveries": int(value("service", "failed_deliveries")),
        "unserved_deliveries": int(value("service", "unserved_deliveries")),
        "total_deliveries": int(value("service", "total_deliveries")),
    }
    return metrics, summary


def compute_reconciliation(
    tables: dict[str, pd.DataFrame], config: AppConfig
) -> pd.DataFrame:
    run_id = config.run_id
    deliveries = _frame(tables, "deliveries")
    outcomes = _frame(tables, "delivery_outcomes")
    assignments = _frame(tables, "delivery_assignments")
    routes = _frame(tables, "routes")
    stops = _frame(tables, "route_stops")
    legs = _frame(tables, "route_legs")
    shifts = _frame(tables, "shifts")

    def partition(frame: pd.DataFrame, column: str) -> dict[str, int]:
        if frame.empty or column not in frame:
            return {}
        return {
            str(key): int(value)
            for key, value in frame[column]
            .map(lambda item: str(getattr(item, "value", item)))
            .value_counts()
            .items()
        }

    assignment_counts = partition(assignments, "status")
    outcome_counts = partition(outcomes, "status")
    stop_counts = partition(stops, "status")
    numeric = lambda frame, column: pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce").fillna(0)

    checks: list[tuple[str, float, float, str]] = []

    def add(name: str, expected: float, actual: float, details: str = "") -> None:
        checks.append((name, float(expected), float(actual), details))

    add(
        "delivery_assignment_coverage",
        len(deliveries),
        len(assignments),
        "Every requested delivery must have exactly one assignment row.",
    )
    add(
        "assignment_partition",
        len(assignments),
        assignment_counts.get(AssignmentStatus.ASSIGNED.value, 0)
        + assignment_counts.get(AssignmentStatus.UNSERVED.value, 0),
        "Assignments must be either ASSIGNED or UNSERVED.",
    )
    add(
        "outcome_coverage",
        len(deliveries),
        len(outcomes),
        "Every requested delivery must have exactly one outcome row.",
    )
    add(
        "outcome_partition",
        len(outcomes),
        outcome_counts.get(OutcomeStatus.DELIVERED.value, 0)
        + outcome_counts.get(OutcomeStatus.FAILED.value, 0)
        + outcome_counts.get(OutcomeStatus.UNSERVED.value, 0),
        "Outcomes must be DELIVERED, FAILED or UNSERVED.",
    )
    add(
        "route_stop_count",
        float(numeric(routes, "stop_count").sum()),
        float(len(stops)),
        "Route stop_count must equal the number of route stop rows.",
    )
    add(
        "assignment_stop_coverage",
        float(assignment_counts.get(AssignmentStatus.ASSIGNED.value, 0)),
        float(len(stops)),
        "Assigned delivery rows must equal route stop rows.",
    )
    add(
        "route_load_units",
        float(numeric(routes, "load_units").sum()),
        float(numeric(stops, "demand_units").sum()),
        "Route load_units must equal the sum of stop demand.",
    )
    add(
        "route_load_weight",
        float(numeric(routes, "load_weight_kg").sum()),
        float(numeric(stops, "weight_kg").sum()),
        "Route load weight must equal the sum of stop weight.",
    )
    add(
        "route_service_minutes",
        float(numeric(routes, "service_minutes").sum()),
        float(numeric(stops, "service_minutes").sum()),
        "Route service minutes must equal the sum of stop service minutes.",
    )
    add(
        "route_wait_minutes",
        float(numeric(routes, "wait_minutes").sum()),
        float(numeric(stops, "wait_minutes").sum()),
        "Route wait minutes must equal the sum of stop wait minutes.",
    )
    add(
        "route_distance",
        float(numeric(routes, "distance_km").sum()),
        float(numeric(legs, "planned_distance_km").sum()),
        "Route distance must equal the sum of planned leg distance.",
    )
    add(
        "route_travel_minutes",
        float(numeric(routes, "travel_minutes").sum()),
        float(numeric(legs, "planned_travel_minutes").sum()),
        "Route travel minutes must equal the sum of planned leg travel.",
    )
    add(
        "route_delivered_stops",
        float(numeric(routes, "delivered_stops").sum()),
        float(stop_counts.get(DeliveryStatus.DELIVERED.value, 0)),
        "Route delivered_stops must equal stop rows marked DELIVERED.",
    )
    add(
        "route_failed_stops",
        float(numeric(routes, "failed_stops").sum()),
        float(stop_counts.get(DeliveryStatus.FAILED.value, 0)),
        "Route failed_stops must equal stop rows marked FAILED.",
    )
    add(
        "planned_shifts",
        float(len(routes)),
        float(routes["shift_id"].astype(str).nunique()) if not routes.empty else 0.0,
        "Each planned route must reference a distinct shift.",
    )

    rows: list[ReconciliationCheck] = []
    for name, expected, actual, details in checks:
        difference = round(actual - expected, 6)
        rows.append(
            ReconciliationCheck(
                run_id=run_id,
                check_name=name,
                expected=round(expected, 6),
                actual=round(actual, 6),
                difference=difference,
                passed=abs(difference) <= 1e-6,
                details=details,
            )
        )
    result = pd.DataFrame([row.model_dump(mode="python") for row in rows])
    return result if not result.empty else pd.DataFrame(columns=list(ReconciliationCheck.model_fields))
