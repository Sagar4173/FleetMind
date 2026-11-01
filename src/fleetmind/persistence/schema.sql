-- FleetMind warehouse schema (generated from fleetmind.models.entities).
-- schema_version: 1.1.0
-- Every snapshot table is keyed by run_id plus the entity key.

CREATE TABLE IF NOT EXISTS depots (
  run_id VARCHAR NOT NULL,
  "depot_id" VARCHAR,
  "name" VARCHAR,
  "address_line" VARCHAR,
  "city" VARCHAR,
  "state" VARCHAR,
  "postal_code" VARCHAR,
  "latitude" DOUBLE,
  "longitude" DOUBLE,
  "open_minute" BIGINT,
  "close_minute" BIGINT,
  "active" BOOLEAN,
  PRIMARY KEY ("run_id", "depot_id")
);

CREATE TABLE IF NOT EXISTS customer_sites (
  run_id VARCHAR NOT NULL,
  "site_id" VARCHAR,
  "customer_name" VARCHAR,
  "site_name" VARCHAR,
  "address_line" VARCHAR,
  "city" VARCHAR,
  "state" VARCHAR,
  "postal_code" VARCHAR,
  "latitude" DOUBLE,
  "longitude" DOUBLE,
  "site_type" VARCHAR,
  "access_class" VARCHAR,
  "receiving_start_minute" BIGINT,
  "receiving_end_minute" BIGINT,
  "dock_available" BOOLEAN,
  "contact_name" VARCHAR,
  "contact_phone" VARCHAR,
  "risk_score" DOUBLE,
  "active" BOOLEAN,
  "effective_from" DATE,
  "effective_to" DATE,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "site_id")
);

CREATE TABLE IF NOT EXISTS time_windows (
  run_id VARCHAR NOT NULL,
  "time_window_id" VARCHAR,
  "owner_type" VARCHAR,
  "owner_id" VARCHAR,
  "window_start" TIMESTAMP,
  "window_end" TIMESTAMP,
  "strictness" VARCHAR,
  "grace_minutes" BIGINT,
  PRIMARY KEY ("run_id", "time_window_id")
);

CREATE TABLE IF NOT EXISTS orders (
  run_id VARCHAR NOT NULL,
  "order_id" VARCHAR,
  "site_id" VARCHAR,
  "order_date" DATE,
  "requested_delivery_date" DATE,
  "priority" VARCHAR,
  "handling_class" VARCHAR,
  "required_equipment" VARCHAR,
  "demand_units" BIGINT,
  "weight_kg" DOUBLE,
  "volume_m3" DOUBLE,
  "service_duration_min" BIGINT,
  "time_window_id" VARCHAR,
  "status" VARCHAR,
  "source_system" VARCHAR,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "order_id")
);

CREATE TABLE IF NOT EXISTS deliveries (
  run_id VARCHAR NOT NULL,
  "delivery_id" VARCHAR,
  "order_id" VARCHAR,
  "site_id" VARCHAR,
  "service_date" DATE,
  "priority" VARCHAR,
  "handling_class" VARCHAR,
  "required_equipment" VARCHAR,
  "demand_units" BIGINT,
  "weight_kg" DOUBLE,
  "volume_m3" DOUBLE,
  "service_duration_min" BIGINT,
  "time_window_id" VARCHAR,
  "status" VARCHAR,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "delivery_id")
);

CREATE TABLE IF NOT EXISTS vehicles (
  run_id VARCHAR NOT NULL,
  "vehicle_id" VARCHAR,
  "vehicle_type" VARCHAR,
  "make" VARCHAR,
  "model" VARCHAR,
  "model_year" BIGINT,
  "license_plate" VARCHAR,
  "status" VARCHAR,
  "home_depot_id" VARCHAR,
  "required_license_class" VARCHAR,
  "max_route_duration_min" BIGINT,
  "odometer_km" DOUBLE,
  "acquisition_date" DATE,
  "effective_from" DATE,
  "effective_to" DATE,
  PRIMARY KEY ("run_id", "vehicle_id")
);

CREATE TABLE IF NOT EXISTS vehicle_capacities (
  run_id VARCHAR NOT NULL,
  "capacity_id" VARCHAR,
  "vehicle_id" VARCHAR,
  "capacity_units" BIGINT,
  "max_weight_kg" DOUBLE,
  "max_volume_m3" DOUBLE,
  "dimension_note" VARCHAR,
  PRIMARY KEY ("run_id", "capacity_id")
);

CREATE TABLE IF NOT EXISTS vehicle_equipment (
  run_id VARCHAR NOT NULL,
  "vehicle_id" VARCHAR,
  "equipment" VARCHAR,
  PRIMARY KEY ("run_id", "vehicle_id", "equipment")
);

CREATE TABLE IF NOT EXISTS drivers (
  run_id VARCHAR NOT NULL,
  "driver_id" VARCHAR,
  "first_name" VARCHAR,
  "last_name" VARCHAR,
  "phone" VARCHAR,
  "email" VARCHAR,
  "license_number" VARCHAR,
  "license_class" VARCHAR,
  "license_expiry" DATE,
  "hire_date" DATE,
  "home_depot_id" VARCHAR,
  "status" VARCHAR,
  "max_shift_hours" DOUBLE,
  "hourly_cost" DOUBLE,
  "effective_from" DATE,
  "effective_to" DATE,
  PRIMARY KEY ("run_id", "driver_id")
);

CREATE TABLE IF NOT EXISTS driver_certifications (
  run_id VARCHAR NOT NULL,
  "driver_id" VARCHAR,
  "certification" VARCHAR,
  "issued_date" DATE,
  "expiry_date" DATE,
  PRIMARY KEY ("run_id", "driver_id", "certification")
);

CREATE TABLE IF NOT EXISTS shifts (
  run_id VARCHAR NOT NULL,
  "shift_id" VARCHAR,
  "driver_id" VARCHAR,
  "vehicle_id" VARCHAR,
  "service_date" DATE,
  "planned_start" TIMESTAMP,
  "planned_end" TIMESTAMP,
  "break_minutes" BIGINT,
  "status" VARCHAR,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "shift_id")
);

CREATE TABLE IF NOT EXISTS travel_scenarios (
  run_id VARCHAR NOT NULL,
  "scenario_id" VARCHAR,
  "scenario_date" DATE,
  "day_name" VARCHAR,
  "traffic_index" DOUBLE,
  "weather" VARCHAR,
  "weather_factor" DOUBLE,
  "am_peak_multiplier" DOUBLE,
  "pm_peak_multiplier" DOUBLE,
  "network_delay_minutes" DOUBLE,
  "incident_zone" VARCHAR,
  "seed" BIGINT,
  "notes" VARCHAR,
  PRIMARY KEY ("run_id", "scenario_id")
);

CREATE TABLE IF NOT EXISTS travel_time_matrix (
  run_id VARCHAR NOT NULL,
  "origin_id" VARCHAR,
  "destination_id" VARCHAR,
  "distance_km" DOUBLE,
  "base_travel_minutes" DOUBLE,
  "model_version" VARCHAR,
  PRIMARY KEY ("run_id", "origin_id", "destination_id")
);

CREATE TABLE IF NOT EXISTS routes (
  run_id VARCHAR NOT NULL,
  "route_id" VARCHAR,
  "scenario_id" VARCHAR,
  "service_date" DATE,
  "depot_id" VARCHAR,
  "vehicle_id" VARCHAR,
  "driver_id" VARCHAR,
  "shift_id" VARCHAR,
  "status" VARCHAR,
  "planned_start" TIMESTAMP,
  "planned_end" TIMESTAMP,
  "actual_start" TIMESTAMP,
  "actual_end" TIMESTAMP,
  "stop_count" BIGINT,
  "delivered_stops" BIGINT,
  "failed_stops" BIGINT,
  "load_units" BIGINT,
  "capacity_units" BIGINT,
  "load_weight_kg" DOUBLE,
  "capacity_weight_kg" DOUBLE,
  "distance_km" DOUBLE,
  "travel_minutes" DOUBLE,
  "service_minutes" DOUBLE,
  "wait_minutes" DOUBLE,
  "actual_service_minutes" DOUBLE,
  "actual_wait_minutes" DOUBLE,
  "solver_status" VARCHAR,
  "objective_value" DOUBLE,
  "solve_seconds" DOUBLE,
  "shift_overrun_minutes" DOUBLE,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "route_id")
);

CREATE TABLE IF NOT EXISTS route_stops (
  run_id VARCHAR NOT NULL,
  "stop_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_sequence" BIGINT,
  "delivery_id" VARCHAR,
  "site_id" VARCHAR,
  "time_window_id" VARCHAR,
  "planned_arrival" TIMESTAMP,
  "planned_service_start" TIMESTAMP,
  "planned_departure" TIMESTAMP,
  "actual_arrival" TIMESTAMP,
  "actual_service_start" TIMESTAMP,
  "actual_departure" TIMESTAMP,
  "wait_minutes" DOUBLE,
  "service_minutes" DOUBLE,
  "actual_wait_minutes" DOUBLE,
  "actual_service_minutes" DOUBLE,
  "demand_units" BIGINT,
  "weight_kg" DOUBLE,
  "status" VARCHAR,
  PRIMARY KEY ("run_id", "stop_id")
);

CREATE TABLE IF NOT EXISTS route_legs (
  run_id VARCHAR NOT NULL,
  "leg_id" VARCHAR,
  "route_id" VARCHAR,
  "leg_sequence" BIGINT,
  "origin_type" VARCHAR,
  "origin_id" VARCHAR,
  "destination_type" VARCHAR,
  "destination_id" VARCHAR,
  "planned_departure" TIMESTAMP,
  "planned_arrival" TIMESTAMP,
  "planned_distance_km" DOUBLE,
  "planned_travel_minutes" DOUBLE,
  "actual_departure" TIMESTAMP,
  "actual_arrival" TIMESTAMP,
  "actual_distance_km" DOUBLE,
  "actual_travel_minutes" DOUBLE,
  "scenario_id" VARCHAR,
  "traffic_multiplier" DOUBLE,
  PRIMARY KEY ("run_id", "leg_id")
);

CREATE TABLE IF NOT EXISTS delivery_assignments (
  run_id VARCHAR NOT NULL,
  "assignment_id" VARCHAR,
  "delivery_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_id" VARCHAR,
  "service_date" DATE,
  "status" VARCHAR,
  "reason_code" VARCHAR,
  "explanation" VARCHAR,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "assignment_id")
);

CREATE TABLE IF NOT EXISTS solver_runs (
  run_id VARCHAR NOT NULL,
  "solve_id" VARCHAR,
  "service_date" DATE,
  "status" VARCHAR,
  "objective_value" DOUBLE,
  "solve_seconds" DOUBLE,
  "assigned_count" BIGINT,
  "unserved_count" BIGINT,
  "vehicle_count" BIGINT,
  "message" VARCHAR,
  PRIMARY KEY ("run_id", "solve_id")
);

CREATE TABLE IF NOT EXISTS delivery_outcomes (
  run_id VARCHAR NOT NULL,
  "outcome_id" VARCHAR,
  "delivery_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_id" VARCHAR,
  "status" VARCHAR,
  "reason_code" VARCHAR,
  "actual_arrival" TIMESTAMP,
  "service_start" TIMESTAMP,
  "service_end" TIMESTAMP,
  "on_time" BOOLEAN,
  "proof_of_delivery" VARCHAR,
  "notes" VARCHAR,
  "recorded_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "outcome_id")
);

CREATE TABLE IF NOT EXISTS route_events (
  run_id VARCHAR NOT NULL,
  "event_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_id" VARCHAR,
  "event_type" VARCHAR,
  "event_time" TIMESTAMP,
  "planned_time" TIMESTAMP,
  "sequence" BIGINT,
  "details" VARCHAR,
  PRIMARY KEY ("run_id", "event_id")
);

CREATE TABLE IF NOT EXISTS delivery_events (
  run_id VARCHAR NOT NULL,
  "event_id" VARCHAR,
  "delivery_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_id" VARCHAR,
  "event_type" VARCHAR,
  "event_time" TIMESTAMP,
  "planned_time" TIMESTAMP,
  "sequence" BIGINT,
  "details" VARCHAR,
  PRIMARY KEY ("run_id", "event_id")
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
  run_id VARCHAR NOT NULL,
  "issue_id" VARCHAR,
  "issue_type" VARCHAR,
  "severity" VARCHAR,
  "entity_type" VARCHAR,
  "entity_id" VARCHAR,
  "field_name" VARCHAR,
  "message" VARCHAR,
  "source_table" VARCHAR,
  "detected_at" TIMESTAMP,
  "quarantined" BOOLEAN,
  "details" VARCHAR,
  PRIMARY KEY ("run_id", "issue_id")
);

CREATE TABLE IF NOT EXISTS quarantine_records (
  run_id VARCHAR NOT NULL,
  "quarantine_id" VARCHAR,
  "issue_id" VARCHAR,
  "source_table" VARCHAR,
  "entity_id" VARCHAR,
  "record_json" VARCHAR,
  "reason" VARCHAR,
  "quarantined_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "quarantine_id")
);

CREATE TABLE IF NOT EXISTS ground_truth_anomalies (
  run_id VARCHAR NOT NULL,
  "anomaly_id" VARCHAR,
  "issue_type" VARCHAR,
  "entity_type" VARCHAR,
  "entity_id" VARCHAR,
  "source_table" VARCHAR,
  "original_value" VARCHAR,
  "injected_value" VARCHAR,
  "injected_at" TIMESTAMP,
  "notes" VARCHAR,
  PRIMARY KEY ("run_id", "anomaly_id")
);

CREATE TABLE IF NOT EXISTS anomaly_detections (
  run_id VARCHAR NOT NULL,
  "detection_id" VARCHAR,
  "issue_type" VARCHAR,
  "entity_type" VARCHAR,
  "entity_id" VARCHAR,
  "source_table" VARCHAR,
  "detection_rule" VARCHAR,
  "observed_value" VARCHAR,
  "detected_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "detection_id")
);

CREATE TABLE IF NOT EXISTS anomaly_detection_metrics (
  run_id VARCHAR NOT NULL,
  "issue_type" VARCHAR,
  "tp" BIGINT,
  "fp" BIGINT,
  "tn" BIGINT,
  "fn" BIGINT,
  "precision" DOUBLE,
  "recall" DOUBLE,
  "f1" DOUBLE,
  "evaluated_universe" BIGINT,
  "notes" VARCHAR,
  PRIMARY KEY ("run_id", "issue_type")
);

CREATE TABLE IF NOT EXISTS report_metrics (
  run_id VARCHAR NOT NULL,
  "category" VARCHAR,
  "metric_name" VARCHAR,
  "value" DOUBLE,
  "unit" VARCHAR,
  "numerator" DOUBLE,
  "denominator" DOUBLE,
  "formula" VARCHAR,
  "dimensions" VARCHAR,
  PRIMARY KEY ("run_id", "category", "metric_name", "dimensions")
);

CREATE TABLE IF NOT EXISTS reconciliation_checks (
  run_id VARCHAR NOT NULL,
  "check_name" VARCHAR,
  "expected" DOUBLE,
  "actual" DOUBLE,
  "difference" DOUBLE,
  "passed" BOOLEAN,
  "details" VARCHAR,
  PRIMARY KEY ("run_id", "check_name")
);

CREATE TABLE IF NOT EXISTS run_metadata (
  run_id VARCHAR NOT NULL,
  "mode" VARCHAR,
  "seed" BIGINT,
  "dataset_version" VARCHAR,
  "pipeline_version" VARCHAR,
  "config_hash" VARCHAR,
  "status" VARCHAR,
  "started_at" TIMESTAMP,
  "completed_at" TIMESTAMP,
  "stage_durations_json" VARCHAR,
  "counts_json" VARCHAR,
  "checksums_json" VARCHAR,
  "error_message" VARCHAR,
  PRIMARY KEY ("run_id")
);

CREATE TABLE IF NOT EXISTS audit_records (
  run_id VARCHAR NOT NULL,
  "audit_id" VARCHAR,
  "execution_id" VARCHAR,
  "timestamp" TIMESTAMP,
  "actor" VARCHAR,
  "action" VARCHAR,
  "details_json" VARCHAR,
  PRIMARY KEY ("run_id", "audit_id")
);

CREATE TABLE IF NOT EXISTS dirty_routes (
  run_id VARCHAR NOT NULL,
  "route_id" VARCHAR,
  "scenario_id" VARCHAR,
  "service_date" DATE,
  "depot_id" VARCHAR,
  "vehicle_id" VARCHAR,
  "driver_id" VARCHAR,
  "shift_id" VARCHAR,
  "status" VARCHAR,
  "planned_start" TIMESTAMP,
  "planned_end" TIMESTAMP,
  "actual_start" TIMESTAMP,
  "actual_end" TIMESTAMP,
  "stop_count" BIGINT,
  "delivered_stops" BIGINT,
  "failed_stops" BIGINT,
  "load_units" BIGINT,
  "capacity_units" BIGINT,
  "load_weight_kg" DOUBLE,
  "capacity_weight_kg" DOUBLE,
  "distance_km" DOUBLE,
  "travel_minutes" DOUBLE,
  "service_minutes" DOUBLE,
  "wait_minutes" DOUBLE,
  "actual_service_minutes" DOUBLE,
  "actual_wait_minutes" DOUBLE,
  "solver_status" VARCHAR,
  "objective_value" DOUBLE,
  "solve_seconds" DOUBLE,
  "shift_overrun_minutes" DOUBLE,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "route_id")
);

CREATE TABLE IF NOT EXISTS dirty_route_stops (
  run_id VARCHAR NOT NULL,
  "stop_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_sequence" BIGINT,
  "delivery_id" VARCHAR,
  "site_id" VARCHAR,
  "time_window_id" VARCHAR,
  "planned_arrival" TIMESTAMP,
  "planned_service_start" TIMESTAMP,
  "planned_departure" TIMESTAMP,
  "actual_arrival" TIMESTAMP,
  "actual_service_start" TIMESTAMP,
  "actual_departure" TIMESTAMP,
  "wait_minutes" DOUBLE,
  "service_minutes" DOUBLE,
  "actual_wait_minutes" DOUBLE,
  "actual_service_minutes" DOUBLE,
  "demand_units" BIGINT,
  "weight_kg" DOUBLE,
  "status" VARCHAR,
  PRIMARY KEY ("run_id", "stop_id")
);

CREATE TABLE IF NOT EXISTS dirty_delivery_assignments (
  run_id VARCHAR NOT NULL,
  "assignment_id" VARCHAR,
  "delivery_id" VARCHAR,
  "route_id" VARCHAR,
  "stop_id" VARCHAR,
  "service_date" DATE,
  "status" VARCHAR,
  "reason_code" VARCHAR,
  "explanation" VARCHAR,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "assignment_id")
);

CREATE TABLE IF NOT EXISTS dirty_customer_sites (
  run_id VARCHAR NOT NULL,
  "site_id" VARCHAR,
  "customer_name" VARCHAR,
  "site_name" VARCHAR,
  "address_line" VARCHAR,
  "city" VARCHAR,
  "state" VARCHAR,
  "postal_code" VARCHAR,
  "latitude" DOUBLE,
  "longitude" DOUBLE,
  "site_type" VARCHAR,
  "access_class" VARCHAR,
  "receiving_start_minute" BIGINT,
  "receiving_end_minute" BIGINT,
  "dock_available" BOOLEAN,
  "contact_name" VARCHAR,
  "contact_phone" VARCHAR,
  "risk_score" DOUBLE,
  "active" BOOLEAN,
  "effective_from" DATE,
  "effective_to" DATE,
  "created_at" TIMESTAMP,
  PRIMARY KEY ("run_id", "site_id")
);
