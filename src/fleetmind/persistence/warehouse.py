"""DuckDB warehouse with idempotent, run-scoped snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping

import duckdb
import pandas as pd

from fleetmind.persistence.schema import (
    DIRTY_BASE_NAMES,
    SCHEMA_VERSION,
    SPECS_BY_NAME,
    TABLE_SPECS,
    render_schema,
)


class WarehouseError(RuntimeError):
    """Raised when a warehouse operation cannot be completed safely."""


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a storable copy with enum values flattened to strings."""
    work = frame.copy(deep=True)
    for column in work.columns:
        series = work[column]
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            work[column] = series.map(
                lambda value: value.value if isinstance(value, Enum) else value
            )
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            work[column] = series.dt.tz_localize(None)
    return work


def frame_for_table(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    spec = SPECS_BY_NAME.get(table)
    if spec is None:
        raise WarehouseError(f"unknown warehouse table: {table}")
    work = normalize_frame(frame)
    missing = [column for column in spec.columns if column not in work.columns]
    if missing:
        raise WarehouseError(
            f"table '{table}' is missing required columns: {', '.join(missing)}"
        )
    return work.loc[:, list(spec.columns)]


class Warehouse:
    """A small transactional wrapper around a FleetMind DuckDB database."""

    def __init__(
        self,
        path: Path | str,
        *,
        read_only: bool = False,
        initialize: bool = True,
    ) -> None:
        self.path = Path(path)
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = duckdb.connect(str(self.path), read_only=read_only)
        except Exception as exc:  # pragma: no cover - driver-specific message
            raise WarehouseError(f"could not open warehouse {self.path}: {exc}") from exc
        self.read_only = read_only
        if initialize and not read_only:
            self.initialize()

    def __enter__(self) -> "Warehouse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        if self.read_only:
            raise WarehouseError("cannot initialize a read-only warehouse")
        self.connection.execute(render_schema())
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_info ("
            "schema_version VARCHAR PRIMARY KEY, created_at TIMESTAMP)"
        )
        self.connection.execute(
            "INSERT INTO schema_info VALUES (?, current_timestamp) "
            "ON CONFLICT DO NOTHING",
            [SCHEMA_VERSION],
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def write_snapshot(
        self,
        run_id: str,
        tables: Mapping[str, pd.DataFrame],
        *,
        replace_all_run_tables: bool = True,
    ) -> dict[str, int]:
        """Replace a run's snapshot atomically and return inserted row counts."""
        if self.read_only:
            raise WarehouseError("cannot write through a read-only warehouse")
        if not run_id:
            raise WarehouseError("run_id is required")
        unknown = sorted(set(tables) - set(SPECS_BY_NAME))
        if unknown:
            raise WarehouseError(f"unknown warehouse tables: {', '.join(unknown)}")
        prepared = {
            name: frame_for_table(frame, name) for name, frame in sorted(tables.items())
        }
        for name, frame in prepared.items():
            if "run_id" in frame.columns and len(frame):
                values = frame["run_id"].astype(str).unique().tolist()
                if values != [run_id]:
                    raise WarehouseError(
                        f"table '{name}' contains run_id values {values!r}, expected {run_id!r}"
                    )

        self.connection.execute("BEGIN TRANSACTION")
        try:
            if replace_all_run_tables:
                for spec in TABLE_SPECS:
                    self.connection.execute(
                        f'DELETE FROM "{spec.name}" WHERE run_id = ?', [run_id]
                    )
            else:
                for name in prepared:
                    self.connection.execute(
                        f'DELETE FROM "{name}" WHERE run_id = ?', [run_id]
                    )
            for name, frame in prepared.items():
                self._insert_frame(name, frame)
            self.connection.execute("COMMIT")
        except Exception as exc:
            self.connection.execute("ROLLBACK")
            raise WarehouseError(f"snapshot write rolled back for {run_id}: {exc}") from exc
        return {name: int(len(frame)) for name, frame in prepared.items()}

    def _insert_frame(self, table: str, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        spec = SPECS_BY_NAME[table]
        register_name = f"fm_insert_{table}"
        columns = ", ".join(f'"{column}"' for column in spec.columns)
        self.connection.register(register_name, frame)
        try:
            self.connection.execute(
                f'INSERT INTO "{table}" ({columns}) '
                f'SELECT {columns} FROM "{register_name}"'
            )
        finally:
            self.connection.unregister(register_name)

    def upsert_table(self, run_id: str, table: str, frame: pd.DataFrame) -> int:
        """Replace just one table for a run (used for failure metadata)."""
        if self.read_only:
            raise WarehouseError("cannot write through a read-only warehouse")
        spec = SPECS_BY_NAME.get(table)
        if spec is None:
            raise WarehouseError(f"unknown warehouse table: {table}")
        prepared = frame_for_table(frame, table)
        if "run_id" not in prepared.columns:
            raise WarehouseError("snapshot tables must include run_id")
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                f'DELETE FROM "{table}" WHERE run_id = ?', [run_id]
            )
            self._insert_frame(table, prepared)
            self.connection.execute("COMMIT")
        except Exception as exc:
            self.connection.execute("ROLLBACK")
            raise WarehouseError(f"upsert rolled back for {table}: {exc}") from exc
        return int(len(prepared))

    def delete_run(self, run_id: str) -> None:
        if self.read_only:
            raise WarehouseError("cannot delete through a read-only warehouse")
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for spec in TABLE_SPECS:
                self.connection.execute(
                    f'DELETE FROM "{spec.name}" WHERE run_id = ?', [run_id]
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def fetch(self, query: str, params: list[Any] | tuple[Any, ...] | None = None) -> pd.DataFrame:
        try:
            return self.connection.execute(query, params or []).fetch_df()
        except Exception as exc:
            raise WarehouseError(f"query failed: {exc}") from exc

    def table(self, name: str, run_id: str | None = None) -> pd.DataFrame:
        spec = SPECS_BY_NAME.get(name)
        if spec is None:
            raise WarehouseError(f"unknown warehouse table: {name}")
        if run_id is None:
            return self.fetch(f'SELECT * FROM "{name}"')
        return self.fetch(f'SELECT * FROM "{name}" WHERE run_id = ?', [run_id])

    def counts(self, run_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for spec in TABLE_SPECS:
            row = self.connection.execute(
                f'SELECT count(*) FROM "{spec.name}" WHERE run_id = ?', [run_id]
            ).fetchone()
            counts[spec.name] = int(row[0]) if row else 0
        return counts

    def runs(self) -> pd.DataFrame:
        return self.fetch(
            "SELECT * FROM run_metadata ORDER BY started_at DESC, run_id"
        )

    def latest_run_id(self) -> str | None:
        row = self.connection.execute(
            "SELECT run_id FROM run_metadata ORDER BY started_at DESC, run_id LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None

    @property
    def dirty_tables(self) -> tuple[str, ...]:
        return tuple(f"dirty_{name}" for name in DIRTY_BASE_NAMES)


@contextmanager
def open_warehouse(
    path: Path | str, *, read_only: bool = False
) -> Iterator[Warehouse]:
    warehouse = Warehouse(path, read_only=read_only)
    try:
        yield warehouse
    finally:
        warehouse.close()
