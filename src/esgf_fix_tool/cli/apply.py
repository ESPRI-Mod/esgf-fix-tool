"""Apply a plan: update metadata directly or combine structural changes in one rewrite."""

from __future__ import annotations

import glob
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
    break_hardlinks: Annotated[
        bool,
        typer.Option(
            "--break-hardlinks",
            help="copy a hard-linked file before a metadata update, so its other links (e.g. a published "
            "DRS version) keep the previous content; default: refuse such files",
        ),
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", "-j", min=1,
                          help="number of independent files processed concurrently")
    ] = 1,
    resume: Annotated[
        list[str] | None,
        typer.Option("--resume", help="fixlog(s) of earlier runs of this plan: skip the files they record as done "
                                      "(failed ones are retried); a quoted pattern is expanded, e.g. "
                                      "--resume 'run/fixlog*.jsonl'; may be repeated"),
    ] = None,
    skip_failed: Annotated[
        bool, typer.Option("--skip-failed", help="with --resume: also skip the files recorded as failed")
    ] = False,
    shard: Annotated[
        str | None,
        typer.Option("--shard", help="k/K: only share k of K of the plan's files, to split one plan over K jobs "
                                     "(each with its own --fixlog); no automatic deduplication then"),
    ] = None,
):
    """Apply a plan: update metadata directly or combine structural changes in one rewrite."""
    from ..apply import apply_plan, parse_shard  # needs netCDF4

    try:
        shard_k = parse_shard(shard) if shard is not None else None
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--shard") from None
    if skip_failed and not resume:
        raise typer.BadParameter("--skip-failed needs --resume", param_hint="--skip-failed")
    logs = []
    for pattern in resume or ():
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern] * Path(pattern).is_file()
        if not matches:
            raise typer.BadParameter(f"no fixlog matches {pattern}", param_hint="--resume")
        logs += [m for m in matches if m not in logs]

    events_log = fixlog.with_name(f"{fixlog.stem}.events.jsonl")
    with ConsoleProgress(events_log, quiet=quiet, parallel=workers > 1) as display:
        stats = apply_plan(
            FixPlan.read(plan),
            fixlog,
            dry_run=dry_run,
            only_under=only_under,
            progress=None if quiet or display.interactive else _progress,
            events=display,
            continue_on_error=not fail_fast,
            break_hardlinks=break_hardlinks,
            workers=workers,
            resume=logs,
            skip_failed=skip_failed,
            shard=shard_k,
        )
    if not display.interactive:
        print(stats, flush=True)
    if stats.get("failed", 0) or stats.get("duplicates_failed", 0):
        raise typer.Exit(1)
