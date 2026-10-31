# 05 — Validation and quarantine

## Context

Generation and planning produced tables, but the platform needed an independent quality layer that could fail a run,
explain why, quarantine bad records, and prove that arithmetic reconciles across tables.

## Prompt

> Implement a comprehensive data-quality engine for master, plan, simulation, and lifecycle data. Use typed issues
> with severity. Quarantine records that are structurally unusable, but keep semantic evidence. Produce clean and dirty
> issue sets, quarantine artifacts, and independent reconciliation checks. A clean run must fail loudly rather than
> persisting invalid data silently.

## Decisions

- `DataQualityValidator.validate_master` covers structure, primary keys, required fields, numeric and datetime types,
  enums, ID patterns, foreign keys, natural-key uniqueness, coordinates, master date ordering, and master cross-table
  consistency.
- `validate_full` runs master checks, then plan checks: keys/required fields, delivery uniqueness, route stop sequence,
  capacity, time windows, shifts, stop chronology, assignment reconciliation, route arithmetic, event integrity, and
  lifecycle transitions.
- Issue IDs use a phase prefix: `DQ-######` clean, `DQD-######` dirty. This keeps both frames readable together.
- Severity: structural and constraint violations are `CRITICAL`; type/format/order issues are `ERROR`; info/warning
  levels are reserved for non-blocking findings. `passed` is false for any `ERROR`/`CRITICAL`.
- Quarantine types are limited to record-level structural problems: duplicate PK, missing required field, invalid
  type, invalid FK, invalid enum, invalid reference. Semantic plan issues are flagged but not deleted.
- Quarantine stores the original record as JSON plus a reason and timestamp. It does not remove the row from the
  snapshot.
- `compute_reconciliation` independently re-derives 15 invariants from related tables. These are persisted, not just
  logged.
- Pipeline semantics:
  - clean validation failure → `FAILED`, persist evidence, then raise;
  - anomaly-stage failure → `PARTIAL`, retain clean evidence;
  - `--no-anomalies` → `CLEAN` mode, no dirty evidence.

## Verification

- Starter clean snapshot: 0 issues, 0 quarantine records, all 15 reconciliation checks pass.
- Tiny integration pipeline: clean validation passes; all six anomaly types appear in dirty validation; quarantine
  records are produced.
- Tests cover duplicate keys, foreign keys, coordinate bounds, capacity, time windows, shifts, stop duplication,
  omission, event duplication/order, and lifecycle mismatch.
- Dirty validation on the starter run produced 40 issues including six injected classes plus expected cascading
  invalid foreign keys, and 24 quarantine records.

## Follow-ups

- The dirty validation is intentionally noisy because removing or duplicating a stop cascades into references. The
  anomaly evaluation uses only exact injected entities, so cascades do not distort precision/recall.
- `validate_full` accepts a prefix so dirty issues cannot collide with clean issue IDs.
