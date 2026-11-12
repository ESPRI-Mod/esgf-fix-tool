"""Rules: which QA message gets which treatment."""

from __future__ import annotations

import hashlib
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
    dataset: str | None = None

    def __post_init__(self):
        self.action = Action(self.action)
        self._check = re.compile(_anchor(self.check))
        self._message = re.compile(_anchor(self.message))
        self._dataset = re.compile(_anchor(self.dataset)) if self.dataset is not None else None

    def match(self, finding: Finding) -> dict[str, str] | None:
        """Named groups of check + message if the rule applies, else None."""
        c = self._check.search(finding.check)
        if not c:
            return None
        m = self._message.search(finding.message)
        if not m:
            return None
        d = self._dataset.search(finding.dataset) if self._dataset is not None else None
        if self._dataset is not None and d is None:
            return None
        return c.groupdict() | m.groupdict() | (d.groupdict() if d is not None else {})

    def resolve_params(self, groups: dict[str, str]) -> dict:
        return {k: v.format(**groups) if isinstance(v, str) else v for k, v in self.params.items()}


@dataclass
class RuleMatch:
    rule: Rule
    groups: dict[str, str]


KNOWN_KEYS = {"id", "check", "message", "action", "fix", "params", "note", "dataset"}


class RuleSet:
    def __init__(self, rules: list[Rule], version: str = "unversioned", digest: str = ""):
        self.rules = rules
        self.version = version
        # sha256 of every loaded catalogue's content, in load order: identifies the exact rules used.
        self.digest = digest
        ids = [r.id for r in rules]
        if dupes := {i for i in ids if ids.count(i) > 1}:
            raise ValueError(f"duplicate rule ids: {sorted(dupes)}")

    @classmethod
    def load(cls, *paths: str | Path, version: str | None = None) -> RuleSet:
        rules = []
        loaded, active = set(), set()
        digest = hashlib.sha256()

        def load_file(path):
            path = Path(path).resolve()
            if path in active:
                raise ValueError(f'Circular catalogue include: {path}')
            if path in loaded:
                return
            active.add(path)
            text = path.read_text()
            digest.update(text.encode())
            data = yaml.safe_load(text) or []
            if isinstance(data, dict):
                if set(data) - {'include', 'rules'}:
                    raise ValueError(f'Unknown catalogue manifest keys in {path}')
                includes = data.get('include', [])
                if not isinstance(includes, list) or not all(isinstance(p, str) for p in includes):
                    raise ValueError(f'Expected a list of catalogue paths in {path}')
                for included in includes:
                    load_file(path.parent / included)
                data = data.get('rules', [])
            if not isinstance(data, list):
                raise ValueError(f'Expected a rule list in {path}')
            for raw in data:
                known = {k: raw[k] for k in KNOWN_KEYS if k in raw}
                extra = {k: v for k, v in raw.items() if k not in KNOWN_KEYS}
                rules.append(Rule(**known, extra=extra))
            active.remove(path)
            loaded.add(path)

        for p in paths:
            load_file(p)
        return cls(rules, version or ",".join(Path(p).name for p in paths), digest.hexdigest())

    def match(self, finding: Finding) -> RuleMatch | None:
        for rule in self.rules:
            groups = rule.match(finding)
            if groups is not None:
                return RuleMatch(rule, groups)
        return None


def _anchor(pattern: str) -> str:
    return pattern if pattern.startswith("^") else "^" + pattern
