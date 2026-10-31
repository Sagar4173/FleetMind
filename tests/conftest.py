from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from fleetmind.config import (
    AnomalyConfig,
    AppConfig,
    GenerationConfig,
    HorizonConfig,
    RoutingConfig,
    SimulationConfig,
)
from fleetmind.models.enums import HandlingClass, VehicleType
from fleetmind.pipeline import PipelinePaths, PipelineRun, build_pipeline_run, persist_pipeline_run


def make_tiny_config(seed: int = 11) -> AppConfig:
    return AppConfig(
        seed=seed,
        horizon=HorizonConfig(start_date=date(2026, 11, 2), days=1),
        generation=GenerationConfig(
            customer_sites=12,
            orders=60,
            vehicles=10,
            drivers=10,
            site_active_ratio=1.0,
            shift_availability_weekday=1.0,
            shift_availability_weekend=1.0,
            handling_mix={HandlingClass.AMBIENT: 1.0},
            fleet_mix={VehicleType.CARGO_VAN: 0.6, VehicleType.BOX_TRUCK: 0.4},
        ),
        routing=RoutingConfig(time_limit_seconds=1),
        simulation=SimulationConfig(customer_unavailable_base_rate=0.0),
        anomaly=AnomalyConfig(instances_per_type=1),
    )


@dataclass
class TinyPipeline:
    config: AppConfig
    run: PipelineRun
    paths: PipelinePaths | None


@pytest.fixture(scope="session")
def tiny_pipeline() -> TinyPipeline:
    config = make_tiny_config()
    run = build_pipeline_run(config, include_anomalies=True)
    return TinyPipeline(config=config, run=run, paths=None)


@pytest.fixture(scope="session")
def tiny_persisted(tmp_path_factory, tiny_pipeline) -> TinyPipeline:
    output = tmp_path_factory.mktemp("fleetmind_tiny")
    paths = persist_pipeline_run(tiny_pipeline.run, output)
    return TinyPipeline(
        config=tiny_pipeline.config,
        run=tiny_pipeline.run,
        paths=paths,
    )
