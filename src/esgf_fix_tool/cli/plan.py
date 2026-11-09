"""Write the fix plan (JSON) for a report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..plan import FixPlan
from .common import Final, Previous, Report, classification
from .catalogues import resolve_catalogues

NAME = "plan"


def run(
    report: Report,
    rules: Annotated[list[str] | None, typer.Option('--rules', '-r',
        help='catalogue path or rule ID, repeatable; default: all project catalogues')] = None,
    previous: Previous = None,
    final: Final = False,
    output: Annotated[Path, typer.Option("--output", "-o", help="plan file")] = Path("fixplan.json"),
    map_path: Annotated[str | None, typer.Option(
        help="OLD=NEW path prefix, e.g. the report was made on the source, the fixes go to staging")] = None,
    only_rule: Annotated[list[str] | None, typer.Option(
        "--only-rule", help="keep fixes from this rule only (repeatable, e.g. --only-rule C001)")] = None,
    exclude_rule: Annotated[list[str] | None, typer.Option(
        '--exclude-rule', help='exclude fixes by exact rule ID (repeatable, e.g. --exclude-rule C103c)')] = None,
):
    """Write the fix plan (JSON) for a report."""
    paths, requested = resolve_catalogues(rules)
    classified = classification(report, paths, previous, final)
    p = FixPlan.from_classification(classified)
    known = {r.id for r in classified.rules.rules}
    for values, option in ((requested, '--rules'), (set(only_rule or ()), '--only-rule'),
                           (set(exclude_rule or ()), '--exclude-rule')):
        if unknown := values - known:
            raise typer.BadParameter(f"unknown rule ids: {', '.join(sorted(unknown))}", param_hint=option)
    selected = requested or known
    if only_rule:
        selected &= set(only_rule)
    selected -= set(exclude_rule or ())
    # Filter planned steps, never the matching catalogue: an excluded finding must
    # not fall through to a broader seed rule and reappear under another identifier.
    p.files = {path: [s for s in steps if s.rule in selected] for path, steps in p.files.items()}
    p.files = {path: steps for path, steps in p.files.items() if steps}
    if map_path:
        old, sep, new = map_path.partition("=")
        if not sep or not old or not new:
            raise typer.BadParameter("expected OLD=NEW", param_hint="--map-path")
        print(f"{p.map_paths(old, new)} of {len(p.files)} paths mapped {old} → {new}")
    p.dump(output)
    print(f"{sum(len(s) for s in p.files.values())} fixes for {len(p.files)} files → {output}")
