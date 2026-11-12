"""Apply a plan: update metadata directly or combine structural changes in one rewrite."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..plan import FixPlan
from .progress import ConsoleProgress, hms

NAME = "apply"


def _progress(
    done: int, total: int, path: str, changed: bool | None, elapsed: float
) -> None:
    # File costs differ by orders of magnitude; an ETA from the first tiny file is misleading.
    status = "failed" if changed is None else "fixed" if changed else "nothing"
    print(
        f"[{done}/{total}] {status} {Path(path).name}   elapsed {hms(elapsed)}",
        flush=True,
    )


def run(
    plan: Annotated[Path, typer.Argument(help="plan written by `plan`", exists=True)],
    fixlog: Annotated[
        Path,
        typer.Option(
            help="JSON lines log, appended (one line per file, written at once)"
        ),
    ] = Path("fixlog.jsonl"),
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="validate recipe preconditions in memory, without writing a file",
        ),
    ] = False,
    only_under: Annotated[
        Path | None,
        typer.Option(
            help="refuse the plan if any file is outside this directory (use the staging dir)"
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet", "-q", help="no console progress (event log is retained)"
        ),
    ] = False,
    fail_fast: Annotated[
        bool,
        typer.Option(
            "--fail-fast",
            help="stop at the first file error; default: report errors and continue",
        ),
    ] = False,
):
    """Apply a plan: update metadata directly or combine structural changes in one rewrite."""
    from ..apply import apply_plan  # needs netCDF4

    events_log = fixlog.with_name(f"{fixlog.stem}.events.jsonl")
    with ConsoleProgress(events_log, quiet=quiet) as display:
        stats = apply_plan(
            FixPlan.read(plan),
            fixlog,
            dry_run=dry_run,
            only_under=only_under,
            progress=None if quiet else _progress,
            events=display,
            continue_on_error=not fail_fast,
        )
    print(stats, flush=True)
    if stats.get("failed", 0):
        raise typer.Exit(1)
