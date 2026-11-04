"""Generate rules from the Excel triage."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

NAME = "seed-rules"


def run(
    excel: Annotated[Path, typer.Argument(help="esgqa diagnostics Excel (2026-10-29 triage)", exists=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("rules/seed.yaml"),
):
    """Generate rules from the Excel triage (needs the 'seed' extra: openpyxl)."""
    from ..seed import build_rules, read_excel, write_rules

    rules = build_rules(read_excel(excel))
    write_rules(rules, output, source=str(excel))
    print(f"{len(rules)} rules → {output} ({dict(Counter(r['action'] for r in rules))})")
