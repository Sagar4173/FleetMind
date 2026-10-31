"""FleetMind command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fleetmind.config import load_config
from fleetmind.pipeline import (
    PipelineError,
    PipelineIncompleteError,
    PipelineValidationError,
    execute_pipeline,
)
from fleetmind.persistence import WarehouseError, render_schema

app = typer.Typer(
    name="fleetmind",
    help="Deterministic synthetic logistics data engineering and delivery planning.",
    no_args_is_help=True,
)
console = Console()


def _format_seconds(value: float) -> str:
    return f"{value:.2f}s"


def _run_summary(run) -> None:
    summary = run.report_summary
    table = Table(title="Run summary", show_header=True, header_style="bold cyan")
    table.add_column("KPI")
    table.add_column("Value", justify="right")
    table.add_row("Run ID", run.run_id)
    table.add_row("Status", run.status)
    table.add_row("Mode", run.mode)
    table.add_row("Requested deliveries", f"{summary.get('total_deliveries', 0):,}")
    table.add_row("Delivered", f"{summary.get('delivered_deliveries', 0):,}")
    table.add_row("Failed", f"{summary.get('failed_deliveries', 0):,}")
    table.add_row("Unserved", f"{summary.get('unserved_deliveries', 0):,}")
    table.add_row("Service rate", f"{summary.get('delivery_service_rate', 0):.1%}")
    table.add_row("On-time rate", f"{summary.get('on_time_rate', 0):.1%}")
    table.add_row("Routes", f"{summary.get('route_count', 0):,}")
    table.add_row("Total distance", f"{summary.get('total_distance_km', 0):,.1f} km")
    table.add_row("Capacity utilization", f"{summary.get('capacity_utilization_units', 0):.1%}")
    table.add_row("Fleet utilization", f"{summary.get('fleet_utilization', 0):.1%}")
    table.add_row("Shift overrun routes", f"{summary.get('routes_with_shift_overrun', 0):,}")
    table.add_row("Clean validation issues", f"{len(run.validation.issues):,}")
    if run.anomaly_evaluation is not None:
        metrics = run.anomaly_evaluation.metrics
        table.add_row(
            "Anomaly detection mean F1",
            f"{metrics['f1'].mean():.3f}" if not metrics.empty else "n/a",
        )
    console.print(table)


@app.command("run")
def run_command(
    config: Path = typer.Option(
        Path("configs/starter.yaml"),
        "--config",
        "-c",
        help="YAML configuration file. Bare names resolve under configs/.",
    ),
    output: Path = typer.Option(
        Path("outputs"), "--output", "-o", help="Artifact root directory."
    ),
    database: Optional[Path] = typer.Option(
        None,
        "--database",
        "-d",
        help="DuckDB path (defaults to <output>/fleetmind.duckdb).",
    ),
    anomalies: bool = typer.Option(
        True,
        "--anomalies/--no-anomalies",
        help="Inject and independently detect the six anomaly classes.",
    ),
    formats: str = typer.Option(
        "parquet,csv",
        "--formats",
        help="Comma-separated artifact formats (parquet, csv).",
    ),
) -> None:
    """Run the complete generate-plan-simulate-validate-detect-report pipeline."""
    try:
        cfg = load_config(config)
    except Exception as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    selected_formats = tuple(
        item.strip().lower() for item in formats.split(",") if item.strip()
    )
    invalid = set(selected_formats) - {"parquet", "csv"}
    if invalid:
        console.print(f"[bold red]Unsupported formats:[/bold red] {', '.join(sorted(invalid))}")
        raise typer.Exit(code=2)
    console.print(
        Panel(
            f"Run [bold]{cfg.run_id}[/bold]\n"
            f"{cfg.generation.customer_sites} sites, {cfg.generation.orders} orders, "
            f"{cfg.generation.vehicles} vehicles, {cfg.horizon.days} days, seed {cfg.seed}",
            title="FleetMind",
        )
    )
    try:
        run, paths = execute_pipeline(
            cfg,
            output,
            include_anomalies=anomalies,
            warehouse_path=database,
            artifact_formats=selected_formats,
        )
    except PipelineValidationError as exc:
        console.print(f"[bold red]Validation failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except PipelineIncompleteError as exc:
        console.print(f"[bold yellow]Pipeline incomplete:[/bold yellow] {exc}")
        raise typer.Exit(code=1) from exc
    except (PipelineError, WarehouseError, Exception) as exc:
        console.print(f"[bold red]Pipeline failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    _run_summary(run)
    stages = Table(title="Stage timings", show_header=True, header_style="bold cyan")
    stages.add_column("Stage")
    stages.add_column("Seconds", justify="right")
    for stage, duration in run.stage_durations.items():
        stages.add_row(stage, _format_seconds(float(duration)))
    console.print(stages)
    console.print(
        Panel(
            f"Artifacts: [bold]{paths.run_directory}[/bold]\n"
            f"Warehouse: [bold]{paths.warehouse_path}[/bold]\n"
            f"Summary: {paths.summary_path}\n"
            f"Manifest: {paths.manifest_path}",
            title="Outputs",
        )
    )


@app.command("info")
def info_command(
    config: Path = typer.Option(Path("configs/starter.yaml"), "--config", "-c"),
) -> None:
    """Print the resolved configuration and deterministic run identity."""
    try:
        cfg = load_config(config)
    except Exception as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    payload = {
        "run_id": cfg.run_id,
        "config_hash": cfg.config_hash(),
        "seed": cfg.seed,
        "dataset_version": cfg.dataset_version,
        "start_date": cfg.horizon.start_date.isoformat(),
        "days": cfg.horizon.days,
        "sites": cfg.generation.customer_sites,
        "orders": cfg.generation.orders,
        "vehicles": cfg.generation.vehicles,
        "drivers": cfg.generation.drivers,
        "solver_time_limit_seconds": cfg.routing.time_limit_seconds,
        "anomalies_per_type": cfg.anomaly.instances_per_type,
    }
    console.print_json(json.dumps(payload, indent=2))


@app.command("runs")
def runs_command(
    database: Path = typer.Option(
        Path("outputs/fleetmind.duckdb"), "--database", "-d"
    ),
) -> None:
    """List runs stored in a DuckDB warehouse."""
    from fleetmind.persistence import Warehouse

    if not database.exists():
        console.print(f"[bold red]Warehouse not found:[/bold red] {database}")
        raise typer.Exit(code=2)
    try:
        with Warehouse(database, read_only=True, initialize=False) as warehouse:
            runs = warehouse.runs()
    except WarehouseError as exc:
        console.print(f"[bold red]Warehouse error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    if runs.empty:
        console.print("No runs found.")
        return
    columns = ["run_id", "status", "mode", "seed", "started_at", "completed_at"]
    console.print(f"Runs in {database}", markup=False)
    console.print(runs[columns].to_string(index=False), markup=False)


@app.command("schema")
def schema_command(
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write generated DDL to this path."
    ),
    show: bool = typer.Option(False, "--show", help="Print DDL to stdout."),
) -> None:
    """Print or export the generated warehouse schema."""
    ddl = render_schema()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(ddl, encoding="utf-8")
        console.print(f"Wrote schema to {output}")
    if show or output is None:
        console.print(ddl, markup=False)


@app.command("dashboard")
def dashboard_command(
    port: int = typer.Option(8501, "--port", help="Streamlit server port."),
    database: Optional[Path] = typer.Option(
        None, "--database", "-d", help="Default warehouse passed via FLEETMIND_DB."
    ),
) -> None:
    """Launch the read-only Streamlit control tower."""
    app_path = Path(__file__).resolve().with_name("dashboard") / "app.py"
    if not app_path.exists():  # pragma: no cover - package layout fallback
        app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    environment = None
    if database is not None:
        import os

        environment = dict(os.environ)
        environment["FLEETMIND_DB"] = str(database)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
    ]
    console.print(f"Starting dashboard: {' '.join(command)}")
    try:
        subprocess.run(command, check=False, env=environment)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        console.print("Dashboard stopped.")
    except Exception as exc:  # pragma: no cover - environment-specific
        console.print(f"[bold red]Could not start dashboard:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("benchmark")
def benchmark_command(
    configs: str = typer.Option(
        "starter,baseline",
        "--configs",
        help="Comma-separated config names or paths.",
    ),
    output: Path = typer.Option(Path("outputs/benchmarks"), "--output", "-o"),
    database: Optional[Path] = typer.Option(None, "--database", "-d"),
    skip_larger: bool = typer.Option(False, "--skip-larger", help="Accepted for compatibility."),
) -> None:
    """Run measured stage benchmarks and write JSON + Markdown reports."""
    from fleetmind.benchmarks import run_benchmark_suite

    names = [item.strip() for item in configs.split(",") if item.strip()]
    try:
        report = run_benchmark_suite(
            names,
            output_directory=output,
            warehouse_path=database,
        )
    except Exception as exc:
        console.print(f"[bold red]Benchmark failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    table = Table(title="Benchmark results", header_style="bold cyan")
    for column in ("config", "generate_s", "plan_s", "simulate_s", "validate_s", "total_s", "peak_rss_mb"):
        table.add_column(column, justify="right")
    for item in report["results"]:
        if item.get("status") == "FAILED":
            table.add_row(str(item["config"]), "-", "-", "-", "-", "-", "-")
            continue
        table.add_row(
            str(item["config"]),
            f"{item['stages']['generate']:.2f}",
            f"{item['stages']['plan']:.2f}",
            f"{item['stages']['simulate']:.2f}",
            f"{item['stages']['validate_clean']:.2f}",
            f"{item['stages']['total']:.2f}",
            f"{item['memory']['peak_rss_mb']:.1f}",
        )
    console.print(table)
    console.print(f"Reports written under [bold]{output}[/bold]")


if __name__ == "__main__":  # pragma: no cover
    app()
