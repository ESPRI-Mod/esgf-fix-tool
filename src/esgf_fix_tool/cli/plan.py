"""Write the fix plan (JSON) for a report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..plan import FixPlan
from .common import Final, Previous, Report, Rules, classification

NAME = "plan"


def run(
    report: Report,
    rules: Rules,
    previous: Previous = None,
    final: Final = False,
    output: Annotated[Path, typer.Option("--output", "-o", help="plan file")] = Path("fixplan.json"),
    map_path: Annotated[str | None, typer.Option(
        help="OLD=NEW path prefix, e.g. the report was made on the source, the fixes go to staging")] = None,
):
    """Write the fix plan (JSON) for a report."""
    p = FixPlan.from_classification(classification(report, rules, previous, final))
    if map_path:
        old, sep, new = map_path.partition("=")
        if not sep or not old or not new:
            raise typer.BadParameter("expected OLD=NEW", param_hint="--map-path")
        print(f"{p.map_paths(old, new)} of {len(p.files)} paths mapped {old} → {new}")
    p.dump(output)
    print(f"{sum(len(s) for s in p.files.values())} fixes for {len(p.files)} files → {output}")
