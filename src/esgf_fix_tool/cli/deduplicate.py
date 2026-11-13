"""Remove approved ATM/SRF overlaps independently of QA reports and rules."""

from pathlib import Path
from typing import Annotated

import typer

from ..deduplicate import deduplicate

NAME = "deduplicate"


def run(
    root: Annotated[Path, typer.Argument(help="simulation directory containing ATM/SRF", exists=True, file_okay=False)],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="show validated deletions without removing files")] = False,
    fixlog: Annotated[Path, typer.Option(help="deduplication JSONL log, appended")] = Path("deduplicate.jsonl"),
    only_under: Annotated[Path | None, typer.Option(
        help="required to delete: the directory deletions are allowed in (the simulation directory or a parent)")] = None,
):
    """Keep ATM areacella/evspsbl hxy-u and SRF snc hxy-lnd when both exist."""
    if not dry_run and only_under is None:
        raise typer.BadParameter(
            "deleting simulation files requires --only-under DIR (or use --dry-run to preview)",
            param_hint="--only-under")
    try:
        result = deduplicate(root, fixlog, dry_run=dry_run, only_under=only_under)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None
    print(result, flush=True)
    if result["failed"]:
        raise typer.Exit(1)
