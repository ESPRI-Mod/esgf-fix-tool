"""Classify a report, print the decision, exit with its code."""

from __future__ import annotations

import json
from collections import Counter
from typing import Annotated

import typer

from ..classify import BLOCKING_SEVERITY
from .common import Final, OnlyRules, Previous, Report, Rules, classification

NAME = "classify"


def run(
    report: Report,
    rules: Rules,
    previous: Previous = None,
    final: Final = False,
    as_json: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
    show: Annotated[int, typer.Option(help="rules / unmatched messages to show")] = 15,
    rule: OnlyRules = None,
):
    """Classify a report, print the decision, exit with its code
    (0 clean, 10 known, 20 new_rule, 21 blocked_upstream, 30 no_change, 40 checker_error)."""
    c = classification(report, rules, previous, final)
    decision = c.decision()
    if as_json:
        print(json.dumps({
            "decision": decision.name.lower(), "exit_code": int(decision),
            "files": len(c.report.files), "rules_version": c.rules_version,
            "counts": {str(s): dict(cnt) for s, cnt in c.counts().items()},
            "checker_errors": len(c.report.errors),
        }, indent=1))
        raise typer.Exit(int(decision))

    print(f"report     : {c.report.source}")
    print(f"files      : {len(c.report.files)}    rules: {c.rules_version}    mode: {'final' if c.final else 'loop'}")
    for sev, cnt in c.counts().items():
        print(f"severity {sev} : " + ", ".join(f"{o} {n}" for o, n in cnt.most_common()))
    if c.report.errors:
        print(f"checker errors: {len(c.report.errors)} ({len({e.path for e in c.report.errors})} files not fully checked)")
    blocking = c.blocking()
    by_outcome = Counter(str(b.outcome) for b in blocking)
    print(f"blocking   : {len(blocking)} findings in {len({b.finding.path for b in blocking})} files {dict(by_outcome)}")
    by_rule = Counter((b.match.rule.id, b.outcome) if b.match else ("-", b.outcome) for b in blocking)
    if by_rule:
        notes = {r.id: (r.note or "") for r in c.rules.rules}
        print(f"\nblocking findings by rule (top {show}):")
        for (rid, outcome), n in by_rule.most_common(show):
            print(f"  {n:5d}  {rid:6s} {outcome:12s} {notes.get(rid, '')[:90]}")
    unmatched = c.unmatched_templates(BLOCKING_SEVERITY)
    if unmatched:
        print(f"\nno rule for {len(unmatched)} severity-{BLOCKING_SEVERITY} messages, e.g.:")
        for (check, msg), n in unmatched.most_common(show):
            print(f"  {n:4d}  {check} | {msg[:120]}")
    for rid in rule or ():
        mine = [i for i in c.items if i.match and i.match.rule.id == rid]
        files = {i.finding.path for i in mine}
        outcomes = dict(Counter(str(i.outcome) for i in mine))
        print(f"\nrule {rid}: {len(mine)} findings in {len(files)} files {outcomes or '(none: nothing left for this rule)'}")
    print(f"\ndecision   : {decision.name.lower()} (exit {int(decision)})")
    raise typer.Exit(int(decision))
