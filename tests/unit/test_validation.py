from __future__ import annotations

import pytest

from fleetmind.config import load_config
from fleetmind.generation.factory import generate_dataset
from fleetmind.models.enums import DQIssueType
from fleetmind.validation.engine import DataQualityValidator, validate_master


@pytest.fixture(scope="module")
def config():
    return load_config("configs/starter.yaml")


@pytest.fixture(scope="module")
def tables(config):
    return generate_dataset(config).copy_tables()


def issue_types(result) -> set[str]:
    return set(result.issues["issue_type"]) if not result.issues.empty else set()


def test_clean_master_data_passes(tables, config) -> None:
    result = validate_master(tables, config)
    assert result.issues.empty
    assert result.passed
    assert result.error_count == 0


def test_duplicate_primary_key_is_detected_and_quarantined(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    duplicate = broken["orders"].iloc[0]["order_id"]
    broken["orders"].loc[broken["orders"].index[1], "order_id"] = duplicate
    result = validate_master(broken, config)
    assert DQIssueType.DUPLICATE_PRIMARY_KEY.value in issue_types(result)
    assert "orders" in result.quarantined
    assert len(result.quarantined["orders"]) >= 2
    assert result.quarantined["orders"]["order_id"].eq(duplicate).all()
    assert result.issues["quarantined"].any()


def test_invalid_foreign_key_is_detected(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["deliveries"].loc[broken["deliveries"].index[0], "site_id"] = "CUS-999999"
    result = validate_master(broken, config)
    assert DQIssueType.INVALID_FOREIGN_KEY.value in issue_types(result)
    assert "deliveries" in result.quarantined


def test_missing_required_value_is_detected(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["customer_sites"].loc[broken["customer_sites"].index[0], "customer_name"] = ""
    result = validate_master(broken, config)
    assert DQIssueType.MISSING_REQUIRED_FIELD.value in issue_types(result)
    matching = result.issues[
        result.issues["issue_type"] == DQIssueType.MISSING_REQUIRED_FIELD.value
    ]
    assert matching["field_name"].eq("customer_name").any()


def test_invalid_enum_is_detected(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["vehicles"].loc[broken["vehicles"].index[0], "vehicle_type"] = "HOVERCRAFT"
    result = validate_master(broken, config)
    assert DQIssueType.INVALID_ENUM.value in issue_types(result)
    assert "vehicles" in result.quarantined


def test_coordinate_outside_region_is_detected(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["customer_sites"].loc[broken["customer_sites"].index[0], "latitude"] = 99.0
    result = validate_master(broken, config)
    assert DQIssueType.COORDINATE_OUT_OF_REGION.value in issue_types(result)


def test_invalid_date_order_is_detected(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    index = broken["orders"].index[0]
    broken["orders"].loc[index, "order_date"] = (
        broken["orders"].loc[index, "requested_delivery_date"]
    )
    broken["orders"].loc[index, "requested_delivery_date"] = (
        broken["orders"].loc[index, "order_date"] - __import__("datetime").timedelta(days=2)
    )
    result = validate_master(broken, config)
    assert DQIssueType.INVALID_DATE_ORDER.value in issue_types(result)


def test_invalid_data_type_is_detected(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["customer_sites"] = broken["customer_sites"].astype({"latitude": object})
    broken["customer_sites"].loc[broken["customer_sites"].index[0], "latitude"] = "not-a-number"
    result = validate_master(broken, config)
    assert DQIssueType.INVALID_DATA_TYPE.value in issue_types(result)


def test_malformed_structure_does_not_crash(config) -> None:
    result = validate_master({}, config)
    assert not result.passed
    assert DQIssueType.MISSING_REQUIRED_FIELD.value in issue_types(result)


def test_validator_is_deterministic(tables, config) -> None:
    first = validate_master(tables, config)
    second = validate_master(tables, config)
    assert first.issues.empty == second.issues.empty


def test_quarantine_frame_serializes_records(tables, config) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["deliveries"].loc[broken["deliveries"].index[0], "site_id"] = "CUS-999999"
    result = validate_master(broken, config)
    validator = DataQualityValidator(config)
    frame = validator.quarantine_frame(result.quarantined)
    assert not frame.empty
    assert set(frame.columns) == {"run_id", "source_table", "record_json", "quarantined_at"}
