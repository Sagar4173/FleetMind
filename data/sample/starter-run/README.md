# FleetMind starter-run sample

This directory is a complete copy of one deterministic `fleetmind run --config starter`
execution (`RUN-C16424724715`). Regenerate it with `scripts/build_sample.py`.

## Synthetic data

Every customer name, contact, street address, phone number, email address, coordinate,
and identifier in this sample is synthetic. The dataset is produced by seeded generators
and Faker; it does not describe real people, companies, or locations.

## Contents

- `master/` contains the pre-execution master snapshot. `deliveries.status` is `PENDING`
  and `orders.status` is `CONFIRMED`.
- `plan/` contains the generated routes, stops, legs, assignments, and solver runs.
- `simulation/` contains the post-execution versions of routes, stops, legs, deliveries,
  orders, outcomes, and event streams. Assignments remain in `plan/` because execution does
  not rewrite them. Checksums in `checksums.json` describe the final version of each table:
  the `simulation/` copy where execution supersedes planning or master data, otherwise the
  `plan/` or `master/` copy. `quarantine_dirty/` holds the quarantined copies of dirty rows
  and is not part of the 21 clean-table checksums.
- `anomalies/`, `validation/`, `quarantine_dirty/`, and `reports/` contain the anomaly
  ground truth, detections, dirty snapshot issues, quarantine evidence, and reconciliation
  output.
- `manifest.json` lists every artifact with its row count, columns, logical checksum, and
  a path relative to this directory.
- `checksums.json` contains the 21 clean-table logical checksums.

The DuckDB warehouse is intentionally **not** included: it is a runtime artifact and is
much larger than the CSV/Parquet evidence. `summary.json` records its table counts for
provenance with `warehouse.included` set to `false`.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_sample_artifacts.py -q
```

CSV files are provided for reviewer convenience. Parquet is the supported format for
checksum reproduction because CSV readers can infer different column types.
