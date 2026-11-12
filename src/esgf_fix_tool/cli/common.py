"""Options shared by the commands that read a report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..classify import Classification, classify
from ..report import QAReport
from ..ruleset import RuleSet

Report = Annotated[Path, typer.Argument(help="full esgqa report qa_result_*.json (not .cluster.json)", exists=True)]
Rules = Annotated[list[Path] | None, typer.Option("--rules", "-r", exists=True,
    help="rule file, repeatable, first match wins; default: all packaged rules")]
Previous = Annotated[Path | None, typer.Option(help="report of the previous iteration (detects 'no change')")]
Final = Annotated[bool, typer.Option("--final", help="final gate: deferred / other-step findings block")]
OnlyRules = Annotated[list[str] | None, typer.Option("--rule", help="rule id, repeatable: only these rules")]


def classification(report: Path, rules: list[Path], previous: Path | None, final: bool) -> Classification:
    return classify(QAReport.load(report), RuleSet.load(*rules), final=final,
                    previous=QAReport.load(previous) if previous else None)
