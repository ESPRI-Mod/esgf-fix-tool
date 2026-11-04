"""Rules: which QA message gets which treatment."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

from .report import Finding


class Action(StrEnum):
    FIX = "fix"
    WAIVE = "waive"
    DEFER = "defer"
    OTHER_STEP = "other_step"
    UPSTREAM_ONLY = "upstream_only"
    REVIEW = "review"


@dataclass
class Rule:
    id: str
    check: str
    message: str
    action: Action
    fix: str | None = None
    params: dict = field(default_factory=dict)
    note: str | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.action = Action(self.action)
        self._check = re.compile(_anchor(self.check))
        self._message = re.compile(_anchor(self.message))

    def match(self, finding: Finding) -> dict[str, str] | None:
        """Named groups of check + message if the rule applies, else None."""
        c = self._check.search(finding.check)
        if not c:
            return None
        m = self._message.search(finding.message)
        if not m:
            return None
        return c.groupdict() | m.groupdict()

    def resolve_params(self, groups: dict[str, str]) -> dict:
        return {k: v.format(**groups) if isinstance(v, str) else v for k, v in self.params.items()}


@dataclass
class RuleMatch:
    rule: Rule
    groups: dict[str, str]


KNOWN_KEYS = {"id", "check", "message", "action", "fix", "params", "note"}


class RuleSet:
    def __init__(self, rules: list[Rule], version: str = "unversioned"):
        self.rules = rules
        self.version = version
        ids = [r.id for r in rules]
        if dupes := {i for i in ids if ids.count(i) > 1}:
            raise ValueError(f"duplicate rule ids: {sorted(dupes)}")

    @classmethod
    def load(cls, *paths: str | Path, version: str | None = None) -> RuleSet:
        rules = []
        for p in paths:
            for raw in yaml.safe_load(Path(p).read_text()) or []:
                known = {k: raw[k] for k in KNOWN_KEYS if k in raw}
                extra = {k: v for k, v in raw.items() if k not in KNOWN_KEYS}
                rules.append(Rule(**known, extra=extra))
        return cls(rules, version or ",".join(Path(p).name for p in paths))

    def match(self, finding: Finding) -> RuleMatch | None:
        for rule in self.rules:
            groups = rule.match(finding)
            if groups is not None:
                return RuleMatch(rule, groups)
        return None


def _anchor(pattern: str) -> str:
    return pattern if pattern.startswith("^") else "^" + pattern
