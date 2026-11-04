"""Read a full esgqa report (``qa_result_*.json``, not the ``.cluster.json``)."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    severity: int
    check: str
    message: str
    dataset: str
    path: str


@dataclass
class QAReport:
    source: Path
    info: dict
    findings: list[Finding]
    # checker crashes: same shape as findings, severity 0
    errors: list[Finding] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> QAReport:
        path = Path(path)
        if path.name.endswith(".cluster.json"):
            raise ValueError(f"{path.name} is a cluster report; give the full qa_result_*.json")
        raw = json.loads(path.read_text())
        return cls(
            source=path,
            info=raw.get("info", {}),
            findings=list(_walk(raw.get("fail", {}))),
            errors=[
                Finding(0, check, message, dataset, p)
                for check, messages in raw.get("error", {}).items()
                for message, datasets in messages.items()
                for dataset, paths in datasets.items()
                for p in paths
            ],
        )

    @property
    def files(self) -> set[str]:
        return {f.path for f in self.findings} | {e.path for e in self.errors}

    def by_file(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            out[f.path].append(f)
        return dict(out)

    def keys(self) -> set[tuple[str, str, str]]:
        """(path, check, message) of every finding, compared between two iterations to detect 'no change'.

        Paths are made relative to the checked directory (``info.parent_dir``), in the path and in the message
        (PATH003 quotes it): iteration 1 may run on the source and iteration 2 on the staging copy.
        """
        root = self.info.get("parent_dir", "").rstrip("/") + "/"

        def rel(text: str) -> str:
            return text.replace(root, "") if root != "/" else text

        return {(rel(f.path), f.check, rel(f.message)) for f in self.findings}


def _walk(fail: dict):
    for severity, checks in fail.items():
        for check, messages in checks.items():
            for message, datasets in messages.items():
                for dataset, paths in datasets.items():
                    for p in paths:
                        yield Finding(int(severity), check, message, dataset, p)
