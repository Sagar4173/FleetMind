from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from fleetmind.config import AppConfig, load_config, load_config_dict


def test_starter_inherits_and_overrides_baseline() -> None:
    config = load_config("configs/starter.yaml")
    assert config.generation.customer_sites == 40
    assert config.generation.orders == 120
    assert config.generation.vehicles == 12
    assert config.horizon.days == 7
    assert config.region.state == "TX"
    assert config.routing.time_limit_seconds == 5
    # inherited defaults remain available
    assert config.routing.unserved_penalty == 5_000_000


def test_baseline_matches_declared_counts() -> None:
    config = load_config("configs/baseline.yaml")
    assert config.generation.customer_sites == 500
    assert config.generation.orders == 2_000
    assert config.generation.vehicles == 100
    assert config.horizon.days == 30
    assert config.seed == 20261022
    assert config.horizon.start_date == date(2026, 11, 2)


def test_config_hash_and_run_id_are_deterministic() -> None:
    first = load_config("configs/baseline.yaml")
    second = load_config("configs/baseline.yaml")
    assert first.config_hash() == second.config_hash()
    assert first.run_id == second.run_id
    assert len(first.config_hash()) == 64


def test_config_hash_changes_when_seed_changes() -> None:
    base = load_config("configs/baseline.yaml")
    changed = base.model_copy(update={"seed": base.seed + 1})
    assert base.config_hash() != changed.config_hash()
    assert base.run_id != changed.run_id


def test_invalid_region_bounds_rejected() -> None:
    with pytest.raises(ValidationError, match="minimum bounds"):
        load_config_dict(
            {
                "region": {
                    "min_lat": 33.0,
                    "max_lat": 32.0,
                    "center_lat": 32.5,
                    "depot_lat": 32.5,
                }
            }
        )


def test_mix_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to 1.0"):
        AppConfig.model_validate(
            {
                "generation": {
                    "priority_mix": {
                        "LOW": 0.5,
                        "STANDARD": 0.5,
                        "HIGH": 0.5,
                        "URGENT": 0.0,
                    }
                }
            }
        )


def test_generation_mix_defaults_cover_every_priority() -> None:
    config = load_config("configs/baseline.yaml")
    assert sum(config.generation.priority_mix.values()) == pytest.approx(1.0)
    assert sum(config.generation.fleet_mix.values()) == pytest.approx(1.0)


def test_extends_cycle_is_rejected(tmp_path) -> None:
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extends cycle"):
        load_config(tmp_path / "a.yaml")


def test_bare_config_name_resolves_under_configs() -> None:
    config = load_config("starter")
    assert config.generation.customer_sites == 40
    assert config.run_id == load_config("configs/starter.yaml").run_id


def test_missing_config_reports_path() -> None:
    with pytest.raises(FileNotFoundError, match="does-not-exist.yaml"):
        load_config("does-not-exist.yaml")


def test_unknown_config_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_config_dict({"generation": {"customer_sites": 10, "mystery": 1}})


def test_yaml_round_trip_preserves_hash(tmp_path) -> None:
    config = load_config("configs/starter.yaml")
    path = tmp_path / "round-trip.yaml"
    config.to_yaml(path)
    reloaded = load_config(path)
    assert reloaded.config_hash() == config.config_hash()
    assert reloaded.run_id == config.run_id
