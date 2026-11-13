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


def order_steps(steps: list[Step]) -> list[Step]:
    """Keep a stable order while enforcing dependencies among selected methods.

    Do not silently add an excluded rule: an absent prerequisite must either be
    satisfied by the file already or fail during read-only preflight.
    """
    dependencies = {
        "fix_hybrid_coefficients": {"center_hybrid_interfaces"},
        "fix_hybrid_coordinate_metadata": {"center_hybrid_interfaces"},
        "normalize_hybrid_coordinate": {"fix_hybrid_coefficients", "center_hybrid_interfaces"},
        "drop_unused_horizontal_bounds": {"reduce_replicated_zostoga"},
        "add_basin_latitude_bounds": {"fix_msfty_coordinates"},
        "drop_basin_latitude_bounds": {"fix_msfty_coordinates"},
    }
    pending, ordered = list(steps), []
    while pending:
        for index, step in enumerate(pending):
            if not any(s.fix in dependencies.get(step.fix, ()) for s in pending):
                ordered.append(pending.pop(index))
                break
        else:
            raise ValueError("Cyclic fix dependencies")
    return ordered


@dataclass
class FixPlan:
    rules_version: str
    report: str
    files: dict[str, list[Step]] = field(default_factory=dict)
    rules_digest: str = ""  # absent from plans written before 0.1.0

    @classmethod
    def from_classification(
        cls, c: Classification, only_rules: set[str] | None = None
    ) -> FixPlan:
        """Fixes of every finding classified FIX; ``only_rules`` keeps the fixes of these rule ids only."""
        plan = cls(c.rules_version, str(c.report.source), rules_digest=c.rules_digest)
        for item in c.items:
            if item.outcome is not Outcome.FIX:
                continue
            if only_rules and item.match.rule.id not in only_rules:
                continue
            rule = item.match.rule
            step = Step(rule.id, rule.fix, rule.resolve_params(item.match.groups))
            steps = plan.files.setdefault(item.finding.path, [])
            if (
                step not in steps
            ):  # the same message can be reported for several datasets
                steps.append(step)
        # An intra-dataset consistency finding can list only the comparison file,
        # although the missing attribute is in its peer. Harmonize all reported
        # time chunks of that exact dataset, never unrelated products.
        for item in c.items:
            if (
                item.outcome is Outcome.FIX
                and (
                    item.match.rule.fix == "harmonize_main_description"
                    or item.match.rule.fix == "normalize_bounds_dimension"
                    and item.finding.check == "[Consistency] Dimensions"
                )
                and (not only_rules or item.match.rule.id in only_rules)
            ):
                step = Step(
                    item.match.rule.id,
                    item.match.rule.fix,
                    item.match.rule.resolve_params(item.match.groups),
                )
                for finding in c.report.findings:
                    if finding.dataset == item.finding.dataset:
                        steps = plan.files.setdefault(finding.path, [])
                        if step not in steps:
                            steps.append(step)
        plan.files = {path: order_steps(steps) for path, steps in plan.files.items()}
        return plan

    def map_paths(self, old: str, new: str) -> int:
        """Rewrite path prefixes, e.g. the report was made on the source but the fixes go to the staging copy."""
        old, new = old.rstrip("/") + "/", new.rstrip("/") + "/"
        files, n = {}, 0
        for path, steps in self.files.items():
            if path.startswith(old):
                path, n = new + path[len(old) :], n + 1
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
