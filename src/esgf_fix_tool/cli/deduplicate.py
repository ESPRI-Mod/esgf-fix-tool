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
):
    """Keep ATM areacella/evspsbl hxy-u and SRF snc hxy-lnd when both exist."""
    result = deduplicate(root, fixlog, dry_run=dry_run)
    print(result, flush=True)
    if result["failed"]:
        raise typer.Exit(1)
