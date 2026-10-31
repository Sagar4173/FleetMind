"""Deterministic anomaly injection with a separate ground-truth ledger.

The injector works on an in-memory copy of the clean execution snapshot.  It
deliberately introduces exactly one instance of each configured anomaly class
per selected entity and records every change in a standalone ground-truth
table.  Detection code must never read that table.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from fleetmind.config import AppConfig
from fleetmind.generation import ids
from fleetmind.models.entities import GroundTruthAnomaly
from fleetmind.models.enums import AnomalyType, AssignmentStatus

REQUIRED_TABLES = {
    "routes",
    "route_stops",
    "route_legs",
    "delivery_assignments",
    "customer_sites",
    "vehicles",
    "vehicle_capacities",
    "shifts",
    "deliveries",
}


class AnomalyInjectionError(RuntimeError):
    """Raised when the requested anomalies cannot be injected safely."""


@dataclass
class AnomalyInjectionResult:
    tables: dict[str, pd.DataFrame]
    ground_truth: pd.DataFrame
    counts_by_type: dict[str, int] = field(default_factory=dict)


def _stable_rng(seed: int, *labels: str) -> np.random.Generator:
    payload = f"{seed}|{'|'.join(labels)}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return pd.Timestamp(value).to_pydatetime()


def _enum(value: Any) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _timestamp(config: AppConfig) -> datetime:
    return datetime.combine(config.service_dates[-1], dtime(23, 59))


def _pick(
    rng: np.random.Generator,
    candidates: list[Any],
    count: int,
    label: str,
) -> list[Any]:
    unique = sorted(set(candidates), key=lambda value: str(value))
    if len(unique) < count:
        raise AnomalyInjectionError(
            f"cannot inject {count} {label} anomalies; only {len(unique)} candidates exist"
        )
    indices = rng.permutation(len(unique))[:count]
    return [unique[int(index)] for index in indices]


def _route_sequence(route_id: str) -> int:
    return int(str(route_id).split("-")[-1])


class AnomalyInjector:
    """Inject the six required anomaly classes into an execution snapshot.

    Injection is deterministic for a fixed configuration and never mutates the
    input tables.  Selected routes are kept disjoint where practical so that
    one corruption does not accidentally create a second anomaly class.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.year = config.id_year

    def inject(
        self,
        tables: dict[str, pd.DataFrame],
        run_id: str | None = None,
    ) -> AnomalyInjectionResult:
        missing = REQUIRED_TABLES - set(tables)
        if missing:
            raise AnomalyInjectionError(
                f"anomaly injection requires tables: {', '.join(sorted(missing))}"
            )
        injected = {name: frame.copy() for name, frame in tables.items()}
        run_id = run_id or str(tables["routes"]["run_id"].iloc[0])
        count = self.config.anomaly.instances_per_type
        rng = _stable_rng(self.config.seed, "anomaly-injection")
        injected_at = _timestamp(self.config)
        ground_truth: list[GroundTruthAnomaly] = []
        counts: dict[str, int] = {item.value: 0 for item in AnomalyType}

        routes = injected["routes"].copy()
        stops = injected["route_stops"].copy().reset_index(drop=True)
        capacities = injected["vehicle_capacities"].set_index("vehicle_id", drop=False)
        shifts = injected["shifts"].set_index("shift_id", drop=False)
        sites = injected["customer_sites"]
        assignments = injected["delivery_assignments"]

        route_capacity: dict[str, float] = {}
        route_weight_capacity: dict[str, float] = {}
        for _, route in routes.iterrows():
            vehicle_id = str(route["vehicle_id"])
            if vehicle_id not in capacities.index:
                continue
            capacity_row = capacities.loc[vehicle_id]
            if isinstance(capacity_row, pd.DataFrame):
                capacity_row = capacity_row.iloc[0]
            route_capacity[str(route["route_id"])] = float(
                capacity_row["capacity_units"]
            )
            route_weight_capacity[str(route["route_id"])] = float(
                capacity_row["max_weight_kg"]
            )

        used_routes: set[str] = set()
        used_deliveries: set[str] = set()

        def record(
            issue_type: AnomalyType,
            entity_type: str,
            entity_id: str,
            source_table: str,
            original_value: str,
            injected_value: str,
            notes: str,
        ) -> None:
            ground_truth.append(
                GroundTruthAnomaly(
                    run_id=run_id,
                    anomaly_id=ids.ground_truth_id(self.year, len(ground_truth) + 1),
                    issue_type=issue_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    source_table=source_table,
                    original_value=original_value,
                    injected_value=injected_value,
                    injected_at=injected_at,
                    notes=notes,
                )
            )
            counts[issue_type.value] += 1

        # 1. Capacity exceeded: inflate one stop's demand so the route load
        #    exceeds the assigned vehicle's unit capacity.
        capacity_routes = _pick(
            rng,
            [
                str(route_id)
                for route_id in routes["route_id"]
                if str(route_id) in route_capacity
                and (stops["route_id"].astype(str) == str(route_id)).any()
            ],
            count,
            "capacity",
        )
        capacity_deliveries: set[str] = set()
        for route_id in capacity_routes:
            used_routes.add(route_id)
            route_stops = stops[
                stops["route_id"].astype(str) == route_id
            ].sort_values("stop_sequence")
            row_index = route_stops.index[0]
            stop_id = str(stops.loc[row_index, "stop_id"])
            delivery_id = str(stops.loc[row_index, "delivery_id"])
            capacity_deliveries.add(delivery_id)
            original_units = int(stops.loc[row_index, "demand_units"])
            capacity = route_capacity[route_id]
            original_load = float(
                stops.loc[stops["route_id"].astype(str) == route_id, "demand_units"].sum()
            )
            injected_units = max(int(round(capacity + 25)), original_units + 1)
            stops.loc[row_index, "demand_units"] = injected_units
            record(
                AnomalyType.ROUTE_CAPACITY_EXCEEDED,
                "route",
                route_id,
                "route_stops",
                (
                    f"stop {stop_id} demand_units={original_units}; "
                    f"route load={original_load:g}; capacity={capacity:g}"
                ),
                (
                    f"stop {stop_id} demand_units={injected_units}; route load now "
                    f"exceeds capacity={capacity:g}"
                ),
                "Capacity anomaly injected on the route's first stop.",
            )

        # 2. Stop omitted: remove a stop while its assignment still says
        #    ASSIGNED.  Routes used by capacity anomalies are avoided, as is
        #    the inflated stop itself.
        assigned = assignments[
            assignments["status"].map(_enum) == AssignmentStatus.ASSIGNED.value
        ]
        assigned_delivery_ids = set(assigned["delivery_id"].astype(str))
        stop_counts = stops.groupby(stops["delivery_id"].astype(str)).size()
        route_of_delivery = (
            stops.assign(_delivery_key=stops["delivery_id"].astype(str))
            .drop_duplicates("_delivery_key")
            .set_index("_delivery_key")["route_id"]
            .astype(str)
            .to_dict()
        )
        omit_candidates = [
            delivery_id
            for delivery_id in stops["delivery_id"].astype(str).unique()
            if delivery_id in assigned_delivery_ids
            and delivery_id not in capacity_deliveries
            and route_of_delivery.get(delivery_id) not in used_routes
            and int(stop_counts.get(delivery_id, 0)) == 1
            and int(
                (
                    stops["route_id"].astype(str)
                    == route_of_delivery.get(delivery_id)
                ).sum()
            )
            > 1
        ]
        omitted_deliveries = _pick(rng, omit_candidates, count, "omitted-stop")
        for delivery_id in omitted_deliveries:
            used_deliveries.add(delivery_id)
            mask = stops["delivery_id"].astype(str) == delivery_id
            row = stops.loc[mask].iloc[0]
            route_id = str(row["route_id"])
            used_routes.add(route_id)
            original = (
                f"stop_id={row['stop_id']}; route_id={route_id}; "
                f"sequence={row['stop_sequence']}"
            )
            stops = stops.drop(stops.index[mask]).reset_index(drop=True)
            record(
                AnomalyType.STOP_OMITTED,
                "delivery",
                delivery_id,
                "route_stops",
                original,
                "route stop removed while assignment remains ASSIGNED",
                "The delivery assignment was intentionally left intact.",
            )

        # 3. Stop duplicated: append a second stop for an assigned delivery on
        #    a different route, keeping unit and weight capacity within limits.
        route_loads = (
            stops.groupby(stops["route_id"].astype(str))["demand_units"].sum().to_dict()
        )
        route_weights = (
            stops.groupby(stops["route_id"].astype(str))["weight_kg"].sum().to_dict()
        )
        duplicate_candidates: list[str] = []
        for delivery_id in stops["delivery_id"].astype(str).unique():
            if delivery_id not in assigned_delivery_ids:
                continue
            if delivery_id in used_deliveries or delivery_id in capacity_deliveries:
                continue
            rows = stops[stops["delivery_id"].astype(str) == delivery_id]
            if len(rows) != 1:
                continue
            row = rows.iloc[0]
            route_id = str(row["route_id"])
            if route_id in used_routes or route_id not in route_capacity:
                continue
            demand = float(row["demand_units"])
            weight = float(row["weight_kg"])
            if float(route_loads.get(route_id, 0.0)) + demand > route_capacity[route_id]:
                continue
            if (
                float(route_weights.get(route_id, 0.0)) + weight
                > route_weight_capacity.get(route_id, float("inf"))
            ):
                continue
            duplicate_candidates.append(delivery_id)
        duplicated_deliveries = _pick(
            rng, duplicate_candidates, count, "duplicated-stop"
        )
        duplicated_stop_ids: set[str] = set()
        for delivery_id in duplicated_deliveries:
            used_deliveries.add(delivery_id)
            mask = stops["delivery_id"].astype(str) == delivery_id
            row = stops.loc[mask].iloc[0].copy()
            original_stop_id = str(row["stop_id"])
            route_id = str(row["route_id"])
            used_routes.add(route_id)
            max_sequence = int(
                stops.loc[stops["route_id"].astype(str) == route_id, "stop_sequence"].max()
            )
            new_sequence = max_sequence + 1
            new_stop_id = ids.stop_id(self.year, _route_sequence(route_id), new_sequence)
            row["stop_id"] = new_stop_id
            row["stop_sequence"] = new_sequence
            # The duplicated row is a record-level corruption, not an executed
            # stop.  Clearing actual timestamps prevents an accidental
            # arrival-order anomaly from being introduced at the same time.
            row["actual_arrival"] = pd.NaT
            row["actual_service_start"] = pd.NaT
            row["actual_departure"] = pd.NaT
            row["wait_minutes"] = 0.0
            duplicated_stop_ids.add(new_stop_id)
            stops = pd.concat([stops, pd.DataFrame([row])], ignore_index=True)
            route_loads[route_id] = route_loads.get(route_id, 0.0) + float(
                row["demand_units"]
            )
            route_weights[route_id] = route_weights.get(route_id, 0.0) + float(
                row["weight_kg"]
            )
            record(
                AnomalyType.STOP_DUPLICATED,
                "delivery",
                delivery_id,
                "route_stops",
                f"one stop ({original_stop_id}) for delivery {delivery_id}",
                (
                    f"second stop ({new_stop_id}) at sequence {new_sequence} "
                    f"for delivery {delivery_id}"
                ),
                "The duplicate carries demand and service data but no execution timestamps.",
            )

        # Rebuild route aggregates so every injected demand change remains
        # internally consistent; capacity detection still recomputes from stops.
        self._recompute_route_aggregates(injected, stops)

        # 4. Arrival before previous departure: move a later stop's actual
        #    arrival exactly ten minutes before the preceding stop's departure.
        stops = injected["route_stops"].copy().reset_index(drop=True)
        arrival_candidates: list[str] = []
        for route_id, group in stops.groupby(stops["route_id"].astype(str), sort=True):
            if str(route_id) in used_routes:
                continue
            ordered = group.sort_values("stop_sequence").reset_index(drop=True)
            for position in range(1, len(ordered)):
                stop = ordered.iloc[position]
                previous = ordered.iloc[position - 1]
                if str(stop["stop_id"]) in duplicated_stop_ids:
                    continue
                if pd.notna(stop["actual_arrival"]) and pd.notna(
                    previous["actual_departure"]
                ):
                    arrival_candidates.append(str(stop["stop_id"]))
        arrival_stops = _pick(rng, arrival_candidates, count, "arrival-order")
        for stop_id in arrival_stops:
            mask = stops["stop_id"].astype(str) == stop_id
            row = stops.loc[mask].iloc[0]
            route_id = str(row["route_id"])
            ordered = stops[
                stops["route_id"].astype(str) == route_id
            ].sort_values("stop_sequence").reset_index(drop=True)
            position = int(
                ordered.index[ordered["stop_id"].astype(str) == stop_id][0]
            )
            previous_departure = _as_datetime(
                ordered.iloc[position - 1]["actual_departure"]
            )
            original_arrival = _as_datetime(row["actual_arrival"])
            injected_arrival = previous_departure - timedelta(minutes=10)
            stops.loc[mask, "actual_arrival"] = injected_arrival
            used_routes.add(route_id)
            record(
                AnomalyType.ARRIVAL_BEFORE_PREVIOUS_DEPARTURE,
                "route_stop",
                stop_id,
                "route_stops",
                f"actual_arrival={original_arrival.isoformat()}",
                (
                    f"actual_arrival={injected_arrival.isoformat()} before previous "
                    f"actual_departure={previous_departure.isoformat()}"
                ),
                "Only the arrival timestamp was corrupted; service remained valid.",
            )
        injected["route_stops"] = stops

        # 5. Driver shift exceeded: extend a route's execution end beyond the
        #    scheduled shift end on its own, otherwise untouched route.
        routes_with_end = [
            str(route["route_id"])
            for _, route in routes.iterrows()
            if pd.notna(route.get("actual_end"))
            and str(route["route_id"]) not in used_routes
            and str(route["route_id"]) in route_capacity
        ]
        shift_routes = _pick(rng, routes_with_end, count, "shift-overrun")
        for route_id in shift_routes:
            route_mask = injected["routes"]["route_id"].astype(str) == route_id
            route_row = injected["routes"].loc[route_mask].iloc[0]
            shift = shifts.loc[str(route_row["shift_id"])]
            if isinstance(shift, pd.DataFrame):
                shift = shift.iloc[0]
            shift_end = _as_datetime(shift["planned_end"])
            original_end = _as_datetime(route_row["actual_end"])
            overrun = int(rng.integers(30, 91))
            injected_end = max(
                shift_end + timedelta(minutes=overrun),
                original_end + timedelta(minutes=overrun),
            )
            injected["routes"].loc[route_mask, "actual_end"] = injected_end
            injected["routes"].loc[route_mask, "shift_overrun_minutes"] = round(
                max(0.0, (injected_end - shift_end).total_seconds() / 60.0), 2
            )
            used_routes.add(route_id)
            record(
                AnomalyType.DRIVER_SHIFT_EXCEEDED,
                "route",
                route_id,
                "routes",
                f"actual_end={original_end.isoformat()}",
                (
                    f"actual_end={injected_end.isoformat()} after shift end="
                    f"{shift_end.isoformat()}"
                ),
                "Route execution end was moved beyond the scheduled shift.",
            )

        # 6. Coordinate outside region: corrupt a customer site coordinate.
        coordinate_sites = _pick(
            rng,
            [str(site_id) for site_id in sites["site_id"].astype(str)],
            count,
            "coordinate",
        )
        region = self.config.region
        for site_id in coordinate_sites:
            mask = injected["customer_sites"]["site_id"].astype(str) == site_id
            row = injected["customer_sites"].loc[mask].iloc[0]
            original_lat = float(row["latitude"])
            original_lon = float(row["longitude"])
            injected_lat = round(
                region.max_lat + self.config.anomaly.coordinate_margin_degrees, 6
            )
            injected_lon = round(
                region.max_lon + self.config.anomaly.coordinate_margin_degrees, 6
            )
            injected["customer_sites"].loc[mask, "latitude"] = injected_lat
            injected["customer_sites"].loc[mask, "longitude"] = injected_lon
            record(
                AnomalyType.COORDINATE_OUT_OF_REGION,
                "customer_site",
                site_id,
                "customer_sites",
                f"latitude={original_lat}, longitude={original_lon}",
                (
                    f"latitude={injected_lat}, longitude={injected_lon} "
                    f"(region max={region.max_lat},{region.max_lon})"
                ),
                "Coordinate moved beyond the configured regional envelope.",
            )

        self._assert_counts(counts, count)
        ground_truth_frame = (
            pd.DataFrame([row.model_dump(mode="python") for row in ground_truth])
            if ground_truth
            else pd.DataFrame(columns=list(GroundTruthAnomaly.model_fields.keys()))
        )
        return AnomalyInjectionResult(
            tables=injected,
            ground_truth=ground_truth_frame,
            counts_by_type=counts,
        )

    @staticmethod
    def _assert_counts(counts: dict[str, int], expected: int) -> None:
        wrong = {
            issue_type: (counts.get(issue_type, 0), expected)
            for issue_type in (item.value for item in AnomalyType)
            if counts.get(issue_type, 0) != expected
        }
        if wrong:
            details = ", ".join(
                f"{name}={actual} (expected {wanted})"
                for name, (actual, wanted) in sorted(wrong.items())
            )
            raise AnomalyInjectionError(
                f"anomaly injection produced incorrect counts: {details}"
            )

    @staticmethod
    def _recompute_route_aggregates(
        tables: dict[str, pd.DataFrame], stops: pd.DataFrame
    ) -> None:
        routes = tables["routes"]
        aggregates = stops.groupby(stops["route_id"].astype(str)).agg(
            stop_count=("stop_id", "count"),
            load_units=("demand_units", "sum"),
            load_weight_kg=("weight_kg", "sum"),
            service_minutes=("service_minutes", "sum"),
            wait_minutes=("wait_minutes", "sum"),
        )
        for index, route in routes.iterrows():
            route_id = str(route["route_id"])
            if route_id not in aggregates.index:
                continue
            aggregate = aggregates.loc[route_id]
            routes.loc[index, "stop_count"] = int(aggregate["stop_count"])
            routes.loc[index, "load_units"] = int(aggregate["load_units"])
            routes.loc[index, "load_weight_kg"] = round(
                float(aggregate["load_weight_kg"]), 3
            )
            routes.loc[index, "service_minutes"] = round(
                float(aggregate["service_minutes"]), 2
            )
            routes.loc[index, "wait_minutes"] = round(
                float(aggregate["wait_minutes"]), 2
            )
        tables["route_stops"] = stops


def inject_anomalies(
    tables: dict[str, pd.DataFrame],
    config: AppConfig,
    run_id: str | None = None,
) -> AnomalyInjectionResult:
    return AnomalyInjector(config).inject(tables, run_id=run_id)
