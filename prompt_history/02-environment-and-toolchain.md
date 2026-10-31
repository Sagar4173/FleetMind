# 02 — Environment and toolchain

## Context

The host has multiple Python installations. OR-Tools is the critical dependency and does not publish wheels for every
Python release.

## Prompt

> Set up a reproducible local environment for FleetMind, install the mandatory stack, verify OR-Tools works, and keep
> the repository free of generated artifacts. Do not assume the system Python is usable.

## Decisions

- Use a project-local `.venv` created from Anaconda Python 3.13.9. The system Python 3.14 had no compatible OR-Tools
  wheel, so it was rejected rather than worked around.
- Pin broad but bounded versions in `pyproject.toml`: OR-Tools `>=9.15,<9.16`, DuckDB `>=1.4,<2.1`, Pydantic
  `>=2.10,<3`, Pandas `>=2.2,<2.4`, NumPy `>=2.0,<2.4`, Streamlit `>=1.49,<2`, Plotly `>=6,<7`, PyArrow
  `>=20,<22`, plus Faker, PyYAML, Typer, Rich, psutil, and pytest.
- Install editable with `pip install -e ".[dev]"` so `fleetmind` and `python -m fleetmind` both work.
- Add `.gitignore` for `.venv`, caches, outputs, DuckDB files, logs, and editor files. Generated run artifacts stay out
  of Git except the small sample and benchmark evidence.
- Use `pytest` markers `integration` and `slow`; the full suite uses a tiny config rather than mocking OR-Tools.

## Discovered compatibility issues

1. **`SetAllowedVehiclesForIndex` rejects Python lists** in the OR-Tools 9.15 wheel. The binding expects an
   `absl::Span` that the wheel does not construct from a list.
   **Resolution:** set the underlying vehicle-variable domain with
   `routing.VehicleVar(index).SetValues([-1, *allowed])`, preserving the unperformed sentinel for disjunctions.
2. **Routing status constants are not exported** by the Python wheel.
   **Resolution:** define the numeric constants from the public C++ `RoutingModel::Status` enum in
   `routing/optimizer.py` with a comment explaining why.
3. **`params.random_seed` is absent** in this wheel.
   **Resolution:** remove it. Determinism comes from the seeded data and the fixed search strategy, not from a solver
   RNG knob.

## Verification

- `python -c "import ortools; print(ortools.__version__)"` → `9.15.6755`.
- `python -m fleetmind --help` lists `run`, `info`, `runs`, `schema`, `dashboard`, `benchmark`.
- Editable install present in `.venv`.
- Full suite later passed in this environment; see `docs/verification.md`.

## Follow-ups

- The OR-Tools workaround is documented in code comments so a future wheel upgrade can remove it deliberately.
- Python 3.11–3.13 is documented as the supported range in the README.
