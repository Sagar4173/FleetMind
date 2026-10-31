# Prompt history

This directory records the working prompts that drove the FleetMind build and the decisions they produced. The
entries are **normalized engineering prompts**, not a verbatim chat transcript: they preserve scope, constraints,
acceptance criteria, and the resulting implementation decisions while removing conversational noise. Commands and
verification statements are the ones actually used in this repository.

## Timeline

| # | Prompt theme | Outcome |
|---|---|---|
| 1 | [Scope and architecture](01-scope-and-architecture.md) | End-to-end pipeline, module boundaries, acceptance criteria. |
| 2 | [Environment and toolchain](02-environment-and-toolchain.md) | Python 3.13 venv, OR-Tools compatibility, project scaffold. |
| 3 | [Data foundation](03-data-foundation.md) | Pydantic schema, deterministic generation, travel model. |
| 4 | [Routing and simulation](04-routing-and-simulation.md) | Per-day OR-Tools planning, execution engine, event ledgers. |
| 5 | [Validation and quarantine](05-validation-and-quarantine.md) | Rule catalog, severity semantics, quarantine, reconciliation. |
| 6 | [Anomalies](06-anomalies.md) | Six injectors, independent detectors, exact-match evaluation. |
| 7 | [Persistence, CLI, dashboard](07-persistence-cli-dashboard.md) | DuckDB snapshots, artifacts, Typer CLI, Streamlit control tower. |
| 8 | [Benchmarks, verification, docs](08-benchmarks-verification-and-docs.md) | Measured benchmarks, regression fixes, documentation, sample outputs. |

## How to read an entry

Each entry has:

- **Context** — the state of the project when the prompt was issued.
- **Prompt** — the normalized request.
- **Decisions** — the engineering choices made in response.
- **Verification** — commands and evidence.
- **Follow-ups** — issues discovered and how they were resolved.

The history is intentionally candid about failures (for example, the OR-Tools Python binding workaround and the
benchmark CLI rendering bug) because those are part of the engineering record.
