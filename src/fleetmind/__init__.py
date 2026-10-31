"""FleetMind: deterministic synthetic logistics planning platform."""

from fleetmind.config import DATASET_VERSION, PIPELINE_VERSION, AppConfig, load_config

__all__ = ["AppConfig", "load_config", "DATASET_VERSION", "PIPELINE_VERSION"]
__version__ = "1.0.0"
