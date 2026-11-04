"""Turn classified findings into an ordered list of fixes per file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .classify import Classification, Outcome


@dataclass(frozen=True)
class Step:
    rule: str
    fix: str
    params: dict


@dataclass
class FixPlan:
    rules_version: str
    report: str
    files: dict[str, list[Step]] = field(default_factory=dict)

    @classmethod
    def from_classification(cls, c: Classification) -> FixPlan:
        plan = cls(c.rules_version, str(c.report.source))
        for item in c.items:
            if item.outcome is not Outcome.FIX:
                continue
            rule = item.match.rule
            step = Step(rule.id, rule.fix, rule.resolve_params(item.match.groups))
            steps = plan.files.setdefault(item.finding.path, [])
            if step not in steps:  # the same message can be reported for several datasets
                steps.append(step)
        return plan

    def map_paths(self, old: str, new: str) -> int:
        """Rewrite path prefixes, e.g. the report was made on the source but the fixes go to the staging copy."""
        old, new = old.rstrip("/") + "/", new.rstrip("/") + "/"
        files, n = {}, 0
        for path, steps in self.files.items():
            if path.startswith(old):
                path, n = new + path[len(old):], n + 1
            files[path] = steps
        self.files = files
        return n

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1))

    @classmethod
    def read(cls, path: str | Path) -> FixPlan:
        raw = json.loads(Path(path).read_text())
        files = {p: [Step(**s) for s in steps] for p, steps in raw.pop("files").items()}
        return cls(**raw, files=files)
