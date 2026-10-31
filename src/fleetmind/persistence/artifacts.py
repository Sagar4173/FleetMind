"""Deterministic parquet/CSV artifacts for a pipeline run."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from fleetmind.generation.factory import logical_checksum
from fleetmind.persistence.warehouse import normalize_frame


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def write_json(path: Path | str, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return target


def write_frame_set(
    directory: Path | str,
    tables: Mapping[str, pd.DataFrame],
    *,
    formats: tuple[str, ...] = ("parquet", "csv"),
) -> dict[str, dict[str, str]]:
    """Write every frame in both parquet and CSV form.

    Tables are sorted by name so the artifact layout is stable across runs.
    """
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict[str, str]] = {}
    for name, frame in sorted(tables.items()):
        work = normalize_frame(frame)
        paths: dict[str, str] = {}
        for output_format in formats:
            if output_format == "parquet":
                target = base / f"{name}.parquet"
                work.to_parquet(target, index=False)
            elif output_format == "csv":
                target = base / f"{name}.csv"
                work.to_csv(
                    target,
                    index=False,
                    date_format="%Y-%m-%dT%H:%M:%S",
                    lineterminator="\n",
                )
            else:  # pragma: no cover - defensive
                raise ValueError(f"unsupported artifact format: {output_format}")
            paths[output_format] = str(target)
        written[name] = paths
    return written


def write_table_checksums(
    directory: Path | str, tables: Mapping[str, pd.DataFrame]
) -> Path:
    return write_json(
        Path(directory) / "checksums.json",
        {name: logical_checksum(frame) for name, frame in sorted(tables.items())},
    )


def read_artifact_table(
    run_directory: Path | str, group: str, table: str
) -> pd.DataFrame:
    path = Path(run_directory) / group / f"{table}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def artifact_manifest(
    directory: Path | str,
    table_groups: Mapping[str, Mapping[str, pd.DataFrame]],
    *,
    formats: tuple[str, ...] = ("parquet", "csv"),
) -> dict[str, Any]:
    """Describe row counts, columns and logical checksums for every artifact."""
    base = Path(directory)
    manifest: dict[str, Any] = {}
    for group, tables in sorted(table_groups.items()):
        entries: dict[str, Any] = {}
        for name, frame in sorted(tables.items()):
            entry: dict[str, Any] = {
                "rows": int(len(frame)),
                "columns": [str(column) for column in frame.columns],
                "checksum": logical_checksum(frame),
            }
            for output_format in formats:
                entry[output_format] = str(base / group / f"{name}.{output_format}")
            entries[name] = entry
        manifest[group] = entries
    return manifest
