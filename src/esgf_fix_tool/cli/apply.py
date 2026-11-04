"""Apply a fix plan: copy, fix the copy, rename over (never in place)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..plan import FixPlan

NAME = "apply"


def _progress(done: int, total: int, path: str, changed: bool, elapsed: float) -> None:
    eta = elapsed / done * (total - done)
    print(f"[{done}/{total}] {'fixed  ' if changed else 'nothing'} {Path(path).name}   "
          f"elapsed {_hms(elapsed)}, left ~{_hms(eta)}", flush=True)


def _hms(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


def run(
    plan: Annotated[Path, typer.Argument(help="plan written by `plan`", exists=True)],
    fixlog: Annotated[Path, typer.Option(help="JSON lines log, appended (one line per file, written at once)")] = Path(
        "fixlog.jsonl"),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="fix temp copies, then throw them away")] = False,
    only_under: Annotated[Path | None, typer.Option(
        help="refuse the plan if any file is outside this directory (use the staging dir)")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="no per-file progress line")] = False,
):
    """Apply a fix plan: copy, fix the copy, rename over (never in place)."""
    from ..apply import apply_plan  # needs netCDF4

    stats = apply_plan(FixPlan.read(plan), fixlog, dry_run=dry_run, only_under=only_under,
                       progress=None if quiet else _progress)
    print(stats, flush=True)
