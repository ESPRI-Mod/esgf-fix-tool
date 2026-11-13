"""Give every finding an Outcome and the whole QA iteration a Decision."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from . import fixes
from .report import Finding, QAReport
from .ruleset import Action, RuleMatch, RuleSet

BLOCKING_SEVERITY = 3


class Outcome(StrEnum):
    FIX = "fix"
    FIX_MISSING = "fix_missing"
    WAIVED = "waived"
    DEFERRED = "deferred"
    OTHER_STEP = "other_step"
    UPSTREAM = "upstream"
    REVIEW = "review"
    UNKNOWN = "unknown"


class Decision(IntEnum):
    """Also the CLI exit code."""

    CLEAN = 0
    KNOWN = 10
    NEW_RULE = 20
    BLOCKED_UPSTREAM = 21
    NO_CHANGE = 30
    CHECKER_ERROR = 40


@dataclass(frozen=True)
class Classified:
    finding: Finding
    outcome: Outcome
    match: RuleMatch | None


def outcome_of(match: RuleMatch | None) -> Outcome:
    if match is None:
        return Outcome.UNKNOWN
    rule = match.rule
    match rule.action:
        case Action.FIX:
            return Outcome.FIX if rule.fix and rule.fix in fixes.REGISTRY else Outcome.FIX_MISSING
        case Action.WAIVE:
            return Outcome.WAIVED
        case Action.DEFER:
            return Outcome.DEFERRED
        case Action.OTHER_STEP:
            return Outcome.OTHER_STEP
        case Action.UPSTREAM_ONLY:
            return Outcome.UPSTREAM
        case Action.REVIEW:
            return Outcome.REVIEW


@dataclass
class Classification:
    report: QAReport
    rules: RuleSet
    items: list[Classified]
    final: bool
    previous: QAReport | None = None

    @property
    def rules_version(self) -> str:
        return self.rules.version

    @property
    def rules_digest(self) -> str:
        return self.rules.digest

    def blocking(self) -> list[Classified]:
        accepted = {Outcome.WAIVED} if self.final else {Outcome.WAIVED, Outcome.DEFERRED, Outcome.OTHER_STEP}
        return [c for c in self.items if c.finding.severity >= BLOCKING_SEVERITY and c.outcome not in accepted]

    def decision(self) -> Decision:
        if self.report.errors:
            return Decision.CHECKER_ERROR
        outcomes = {c.outcome for c in self.blocking()}
        if outcomes & {Outcome.UNKNOWN, Outcome.REVIEW, Outcome.FIX_MISSING}:
            return Decision.NEW_RULE
        if Outcome.UPSTREAM in outcomes:
            return Decision.BLOCKED_UPSTREAM
        if self.final and outcomes & {Outcome.DEFERRED, Outcome.OTHER_STEP, Outcome.FIX}:
            # at the gate nothing is left to fix: whatever remains means the loop or a step failed
            return Decision.NO_CHANGE
        if Outcome.FIX in outcomes:
            if self.previous is not None and self.previous.keys() == self.report.keys():
                return Decision.NO_CHANGE
            return Decision.KNOWN
        return Decision.CLEAN

    def counts(self) -> dict[int, Counter]:
        out: dict[int, Counter] = {}
        for c in self.items:
            out.setdefault(c.finding.severity, Counter())[c.outcome] += 1
        return dict(sorted(out.items(), reverse=True))

    def unmatched_templates(self, severity: int = BLOCKING_SEVERITY) -> Counter:
        return Counter(
            (c.finding.check, c.finding.message)
            for c in self.items
            if c.outcome is Outcome.UNKNOWN and c.finding.severity >= severity
        )


def classify(report: QAReport, rules: RuleSet, *, final: bool = False,
             previous: QAReport | None = None) -> Classification:
    items = []
    for f in report.findings:
        m = rules.match(f)
        items.append(Classified(f, outcome_of(m), m))
    return Classification(report, rules, items, final, previous)
