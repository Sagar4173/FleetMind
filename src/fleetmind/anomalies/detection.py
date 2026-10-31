"""Independent anomaly detection and ground-truth evaluation.

Detectors read only operational tables.  Ground truth is accepted by the
evaluation layer exclusively, so a detector cannot accidentally learn which
records were corrupted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Callable

import pandas as pd

from fleetmind.config import AppConfig
from fleetmind.generation import ids
from fleetmind.models.entities import AnomalyDetectionMetric, AnomalyDetectionResult
from fleetmind.models.enums import AnomalyType, AssignmentStatus

DETECTION_COLUMNS = list(AnomalyDetectionResult.model_fields.keys())
METRIC_COLUMNS = list(AnomalyDetectionMetric.model_fields.keys())

REQUIRED_TABLES = {
    "routes",
    "route_stops",
    "delivery_assignments",
    "vehicle_capacities",
    "shifts",
    "customer_sites",
}

DETECTION_RULES = {
    AnomalyType.ROUTE_CAPACITY_EXCEEDED: "ROUTE_LOAD_EXCEEDS_CAPACITY",
    AnomalyType.STOP_OMITTED: "ASSIGNED_DELIVERY_WITHOUT_STOP",
    AnomalyType.STOP_DUPLICATED: "DELIVERY_WITH_MULTIPLE_STOPS",
    AnomalyType.ARRIVAL_BEFORE_PREVIOUS_DEPARTURE: (
        "ARRIVAL_PRECEDES_PREVIOUS_DEPARTURE"
    ),
    AnomalyType.DRIVER_SHIFT_EXCEEDED: "ROUTE_END_AFTER_SHIFT_END",
    AnomalyType.COORDINATE_OUT_OF_REGION: "SITE_COORDINATE_OUTSIDE_REGION",
}

UNIVERSE_NOTES = {
    AnomalyType.ROUTE_CAPACITY_EXCEEDED: (
        "Universe = routes with a known vehicle capacity; load is recomputed "
        "from route stops and compared with the assigned vehicle capacity."
    ),
    AnomalyType.STOP_OMITTED: (
        "Universe = deliveries whose assignment status is ASSIGNED; a detection "
        "requires the delivery to have no route stop."
    ),
    AnomalyType.STOP_DUPLICATED: (
        "Universe = assigned deliveries plus deliveries referenced by route "
        "stops; a detection requires more than one stop for one delivery."
    ),
    AnomalyType.ARRIVAL_BEFORE_PREVIOUS_DEPARTURE: (
        "Universe = route stops with an actual arrival and a preceding actual "
        "departure on the same route, ordered by stop sequence."
    ),
    AnomalyType.DRIVER_SHIFT_EXCEEDED: (
        "Universe = routes with an actual end and a resolvable shift planned "
        "end; a detection requires actual_end > planned_end."
    ),
    AnomalyType.COORDINATE_OUT_OF_REGION: (
        "Universe = customer sites with numeric coordinates inside the global "
        "latitude/longitude ranges; the configured region bounds are applied."
    ),
}


class AnomalyDetectionError(RuntimeError):
    """Raised when detection input is incomplete."""


@dataclass
class DetectionOutput:
    detections: pd.DataFrame
    universes: dict[str, set[str]] = field(default_factory=dict)
    counts_by_type: dict[str, int] = field(default_factory=dict)


@dataclass
class AnomalyEvaluationResult:
    detections: pd.DataFrame
    metrics: pd.DataFrame
    universes: dict[str, set[str]] = field(default_factory=dict)
    counts_by_type: dict[str, int] = field(default_factory=dict)


def _enum(value: Any) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _timestamp(config: AppConfig) -> datetime:
    return datetime.combine(config.service_dates[-1], dtime(23, 59))


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _detection(
    rule: str,
    entity_type: str,
    entity_id: str,
    source_table: str,
    observed_value: str,
) -> dict[str, str]:
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "source_table": source_table,
        "detection_rule": rule,
        "observed_value": observed_value,
    }


class AnomalyDetector:
    """Run six independent rule-based detectors over an execution snapshot."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.year = config.id_year

    def detect(
        self,
        tables: dict[str, pd.DataFrame],
        run_id: str | None = None,
    ) -> DetectionOutput:
        missing = REQUIRED_TABLES - set(tables)
        if missing:
            raise AnomalyDetectionError(
                f"anomaly detection requires tables: {', '.join(sorted(missing))}"
            )
        run_id = run_id or str(tables["routes"]["run_id"].iloc[0])
        detected_at = _timestamp(self.config)
        raw: list[dict[str, str]] = []
        universes: dict[str, set[str]] = {}
        detectors: list[
            Callable[[dict[str, pd.DataFrame]], tuple[list[dict[str, str]], set[str]]]
        ] = [
            self._detect_capacity,
            self._detect_omitted,
            self._detect_duplicated,
            self._detect_arrival_order,
            self._detect_shift,
            self._detect_coordinates,
        ]
        for anomaly_type, detector in zip(AnomalyType, detectors, strict=True):
            detected, universe = detector(tables)
            universes[anomaly_type.value] = universe
            for item in sorted(detected, key=lambda row: row["entity_id"]):
                raw.append({"issue_type": anomaly_type.value, **item})
        models = [
            AnomalyDetectionResult(
                run_id=run_id,
                detection_id=ids.detection_id(self.year, index + 1),
                issue_type=AnomalyType(item["issue_type"]),
                entity_type=item["entity_type"],
                entity_id=item["entity_id"],
                source_table=item["source_table"],
                detection_rule=item["detection_rule"],
                observed_value=item["observed_value"],
                detected_at=detected_at,
            )
            for index, item in enumerate(raw)
        ]
        detections = (
            pd.DataFrame([model.model_dump(mode="python") for model in models])
            if models
            else pd.DataFrame(columns=DETECTION_COLUMNS)
        )
        counts = {item.value: 0 for item in AnomalyType}
        if not detections.empty:
            for key, value in detections["issue_type"].value_counts().items():
                counts[_enum(key)] = int(value)
        return DetectionOutput(
            detections=detections,
            universes=universes,
            counts_by_type=counts,
        )

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------
    def _detect_capacity(
        self, tables: dict[str, pd.DataFrame]
    ) -> tuple[list[dict[str, str]], set[str]]:
        rule = DETECTION_RULES[AnomalyType.ROUTE_CAPACITY_EXCEEDED]
        routes = tables["routes"]
        stops = tables["route_stops"]
        capacities = tables["vehicle_capacities"]
        cap_by_vehicle = (
            capacities.assign(
                _vehicle=capacities["vehicle_id"].astype(str),
                _capacity=_numeric(capacities["capacity_units"]),
            )
            .drop_duplicates("_vehicle")
            .set_index("_vehicle")["_capacity"]
            .to_dict()
        )
        load_by_route = (
            stops.assign(
                _route=stops["route_id"].astype(str),
                _demand=_numeric(stops["demand_units"]),
            )
            .groupby("_route")["_demand"]
            .sum()
            .to_dict()
        )
        findings: list[dict[str, str]] = []
        universe: set[str] = set()
        for _, route in routes.iterrows():
            route_id = str(route["route_id"])
            capacity = cap_by_vehicle.get(str(route["vehicle_id"]))
            if capacity is None or pd.isna(capacity):
                continue
            universe.add(route_id)
            load = float(load_by_route.get(route_id, 0.0))
            if load > float(capacity) + 1e-9:
                findings.append(
                    _detection(
                        rule,
                        "route",
                        route_id,
                        "route_stops",
                        f"load_units={load:g} > capacity_units={float(capacity):g}",
                    )
                )
        return findings, universe

    def _detect_omitted(
        self, tables: dict[str, pd.DataFrame]
    ) -> tuple[list[dict[str, str]], set[str]]:
        rule = DETECTION_RULES[AnomalyType.STOP_OMITTED]
        assignments = tables["delivery_assignments"]
        stops = tables["route_stops"]
        assigned_ids = {
            str(value)
            for value in assignments.loc[
                assignments["status"].map(_enum) == AssignmentStatus.ASSIGNED.value,
                "delivery_id",
            ]
        }
        stop_ids = set(stops["delivery_id"].astype(str))
        findings = [
            _detection(
                rule,
                "delivery",
                delivery_id,
                "route_stops",
                "assignment status=ASSIGNED but no route stop exists",
            )
            for delivery_id in sorted(assigned_ids - stop_ids)
        ]
        return findings, assigned_ids

    def _detect_duplicated(
        self, tables: dict[str, pd.DataFrame]
    ) -> tuple[list[dict[str, str]], set[str]]:
        rule = DETECTION_RULES[AnomalyType.STOP_DUPLICATED]
        assignments = tables["delivery_assignments"]
        stops = tables["route_stops"]
        assigned_ids = {
            str(value)
            for value in assignments.loc[
                assignments["status"].map(_enum) == AssignmentStatus.ASSIGNED.value,
                "delivery_id",
            ]
        }
        stop_ids = set(stops["delivery_id"].astype(str))
        counts = stops.groupby(stops["delivery_id"].astype(str)).size()
        findings: list[dict[str, str]] = []
        for delivery_id in sorted(stop_ids):
            count = int(counts.get(delivery_id, 0))
            if count <= 1:
                continue
            related_stops = sorted(
                stops.loc[
                    stops["delivery_id"].astype(str) == delivery_id, "stop_id"
                ].astype(str)
            )
            findings.append(
                _detection(
                    rule,
                    "delivery",
                    delivery_id,
                    "route_stops",
                    f"stop_count={count}; stop_ids={','.join(related_stops)}",
                )
            )
        return findings, assigned_ids | stop_ids

    def _detect_arrival_order(
        self, tables: dict[str, pd.DataFrame]
    ) -> tuple[list[dict[str, str]], set[str]]:
        rule = DETECTION_RULES[AnomalyType.ARRIVAL_BEFORE_PREVIOUS_DEPARTURE]
        stops = tables["route_stops"].copy()
        stops["_route"] = stops["route_id"].astype(str)
        stops["_sequence"] = _numeric(stops["stop_sequence"])
        stops["_arrival"] = pd.to_datetime(stops["actual_arrival"], errors="coerce")
        stops["_departure"] = pd.to_datetime(stops["actual_departure"], errors="coerce")
        findings: list[dict[str, str]] = []
        universe: set[str] = set()
        for _, group in stops.sort_values(["_route", "_sequence"]).groupby(
            "_route", sort=True
        ):
            previous_departure: pd.Timestamp | None = None
            for _, row in group.iterrows():
                arrival = row["_arrival"]
                if previous_departure is not None and pd.notna(arrival):
                    stop_id = str(row["stop_id"])
                    universe.add(stop_id)
                    if arrival < previous_departure:
                        findings.append(
                            _detection(
                                rule,
                                "route_stop",
                                stop_id,
                                "route_stops",
                                (
                                    f"actual_arrival={arrival.isoformat()} < "
                                    f"previous_actual_departure="
                                    f"{previous_departure.isoformat()}"
                                ),
                            )
                        )
                previous_departure = (
                    row["_departure"] if pd.notna(row["_departure"]) else None
                )
        return findings, universe

    def _detect_shift(
        self, tables: dict[str, pd.DataFrame]
    ) -> tuple[list[dict[str, str]], set[str]]:
        rule = DETECTION_RULES[AnomalyType.DRIVER_SHIFT_EXCEEDED]
        routes = tables["routes"]
        shifts = tables["shifts"]
        shift_end = (
            shifts.assign(
                _shift=shifts["shift_id"].astype(str),
                _end=pd.to_datetime(shifts["planned_end"], errors="coerce"),
            )
            .drop_duplicates("_shift")
            .set_index("_shift")["_end"]
            .to_dict()
        )
        findings: list[dict[str, str]] = []
        universe: set[str] = set()
        for _, route in routes.iterrows():
            route_id = str(route["route_id"])
            actual_end = pd.to_datetime(route.get("actual_end"), errors="coerce")
            end = shift_end.get(str(route["shift_id"]))
            if pd.isna(actual_end) or end is None or pd.isna(end):
                continue
            universe.add(route_id)
            if actual_end > end:
                findings.append(
                    _detection(
                        rule,
                        "route",
                        route_id,
                        "routes",
                        (
                            f"actual_end={actual_end.isoformat()} > "
                            f"shift_planned_end={end.isoformat()}"
                        ),
                    )
                )
        return findings, universe

    def _detect_coordinates(
        self, tables: dict[str, pd.DataFrame]
    ) -> tuple[list[dict[str, str]], set[str]]:
        rule = DETECTION_RULES[AnomalyType.COORDINATE_OUT_OF_REGION]
        sites = tables["customer_sites"]
        region = self.config.region
        findings: list[dict[str, str]] = []
        universe: set[str] = set()
        for _, site in sites.iterrows():
            latitude = pd.to_numeric(site.get("latitude"), errors="coerce")
            longitude = pd.to_numeric(site.get("longitude"), errors="coerce")
            if pd.isna(latitude) or pd.isna(longitude):
                continue
            site_id = str(site["site_id"])
            universe.add(site_id)
            outside = not (
                region.min_lat <= float(latitude) <= region.max_lat
                and region.min_lon <= float(longitude) <= region.max_lon
            )
            if outside:
                findings.append(
                    _detection(
                        rule,
                        "customer_site",
                        site_id,
                        "customer_sites",
                        (
                            f"latitude={float(latitude):g}, longitude={float(longitude):g}; "
                            f"allowed latitude=[{region.min_lat},{region.max_lat}], "
                            f"longitude=[{region.min_lon},{region.max_lon}]"
                        ),
                    )
                )
        return findings, universe


def detect_anomalies(
    tables: dict[str, pd.DataFrame],
    config: AppConfig,
    run_id: str | None = None,
) -> DetectionOutput:
    return AnomalyDetector(config).detect(tables, run_id=run_id)


def evaluate_detections(
    ground_truth: pd.DataFrame,
    detections: pd.DataFrame,
    universes: dict[str, set[str]],
    config: AppConfig,
    run_id: str | None = None,
) -> pd.DataFrame:
    """Score predictions per anomaly type with exact entity matching."""
    if ground_truth.empty:
        run_id = run_id or config.run_id
    else:
        run_id = run_id or str(ground_truth["run_id"].iloc[0])
    truth_by_type: dict[str, set[str]] = {}
    if not ground_truth.empty:
        for anomaly_type in AnomalyType:
            mask = ground_truth["issue_type"].map(_enum) == anomaly_type.value
            truth_by_type[anomaly_type.value] = set(
                ground_truth.loc[mask, "entity_id"].astype(str)
            )
    detection_by_type: dict[str, set[str]] = {}
    if not detections.empty:
        for anomaly_type in AnomalyType:
            mask = detections["issue_type"].map(_enum) == anomaly_type.value
            detection_by_type[anomaly_type.value] = set(
                detections.loc[mask, "entity_id"].astype(str)
            )
    metrics: list[AnomalyDetectionMetric] = []
    for anomaly_type in AnomalyType:
        key = anomaly_type.value
        truth = truth_by_type.get(key, set())
        predicted = detection_by_type.get(key, set())
        tp = len(truth & predicted)
        fp = len(predicted - truth)
        fn = len(truth - predicted)
        universe = set(universes.get(key, set())) | truth | predicted
        tn = len(universe - truth - predicted)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        metrics.append(
            AnomalyDetectionMetric(
                run_id=run_id,
                issue_type=key,
                tp=tp,
                fp=fp,
                tn=tn,
                fn=fn,
                precision=round(precision, 6),
                recall=round(recall, 6),
                f1=round(f1, 6),
                evaluated_universe=len(universe),
                notes=UNIVERSE_NOTES[anomaly_type],
            )
        )
    return pd.DataFrame([metric.model_dump(mode="python") for metric in metrics])


def detect_and_evaluate(
    tables: dict[str, pd.DataFrame],
    ground_truth: pd.DataFrame,
    config: AppConfig,
    run_id: str | None = None,
) -> AnomalyEvaluationResult:
    output = detect_anomalies(tables, config, run_id=run_id)
    metrics = evaluate_detections(
        ground_truth,
        output.detections,
        output.universes,
        config,
        run_id=run_id,
    )
    return AnomalyEvaluationResult(
        detections=output.detections,
        metrics=metrics,
        universes=output.universes,
        counts_by_type=output.counts_by_type,
    )
