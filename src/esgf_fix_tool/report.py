"""Read a full esgqa report (``qa_result_*.json``, not the ``.cluster.json``)."""

from __future__ import annotations

import json
import os
import re
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
    def load(cls, path: str | Path, work: str | Path | None = None) -> QAReport:
        """Read a report. A report on the DRS (final QA) names datasets by their DRS directories; they are given
        their WORK form back (see :func:`work_dataset_id`), the realm found from the file names in ``work``, or by
        default in the directory the latest loop report next to it checked (``../qa_<N>``)."""
        path = Path(path)
        if path.name.endswith(".cluster.json"):
            raise ValueError(f"{path.name} is a cluster report; give the full qa_result_*.json")
        raw = json.loads(path.read_text())
        findings = list(_walk(raw.get("fail", {})))
        errors = [
            Finding(0, check, message, dataset, p)
            for check, messages in raw.get("error", {}).items()
            for message, datasets in messages.items()
            for dataset, paths in datasets.items()
            for p in paths
        ]
        if any(DRS_DATASET.match(f.dataset) for f in findings + errors):
            realms = work_realms(work if work is not None else _loop_work(path))
            findings = [_with_work_id(f, realms) for f in findings]
            errors = [_with_work_id(f, realms) for f in errors]
        return cls(source=path, info=raw.get("info", {}), findings=findings, errors=errors)

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


# A dataset as esgqa names it on the DRS: its directories, from the project to the version (or esgdrs' files/dDATE).
DRS_DATASET = re.compile(
    r"CMIP7\.(?P<activity>[^.]+)\.(?P<institution>[^.]+)\.(?P<source>[^.]+)\.(?P<experiment>[^.]+)\.(?P<variant>[^.]+)"
    r"\.(?P<region>[^.]+)\.(?P<frequency>[^.]+)\.(?P<variable>[^.]+)\.(?P<branding>[^.]+)\.(?P<grid>[^.]+)"
    r"\.(?:v\d{8}|latest|files\.d\d{8})$")


def work_dataset_id(dataset: str, path: str, realms: dict[str, str]) -> str:
    """The WORK form of a DRS dataset id, the one the rules are written for:
    CMIP7.<realm>.<variable>.<branding>.<frequency>.<region>.<grid>.<source>.<experiment>.<variant>.

    The DRS has no realm (the model component directory of WORK: ATM, OCE, SRF, ...): it comes from ``realms``
    (file name -> realm; the DRS files are hard links to the WORK ones). Unknown: the id is kept, and the rules
    scoped to datasets do not match it (the finding stays blocking, never wrongly waived)."""
    m = DRS_DATASET.match(dataset)
    realm = realms.get(os.path.basename(path)) if m else None
    if not realm:
        return dataset
    g = m.groupdict()
    return ".".join(("CMIP7", realm, g["variable"], g["branding"], g["frequency"], g["region"], g["grid"],
                     g["source"], g["experiment"], g["variant"]))


def work_realms(work: str | Path | None) -> dict[str, str]:
    """File name -> realm (the directory under WORK or WORK/CMIP7) of the NetCDF files of a WORK directory."""
    out: dict[str, str] = {}
    if work is None or not Path(work).is_dir():
        return out
    work = Path(work)
    base = work / "CMIP7" if (work / "CMIP7").is_dir() else work
    for realm in sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink()):
        for directory, _, files in os.walk(base / realm):
            for name in files:
                if name.endswith(".nc"):
                    out.setdefault(name, realm)
    return out


def _loop_work(report: Path) -> str | None:
    """The directory the latest loop report next to this one checked (<run>/qa_<N>/qa_result_*.json)."""
    loops = []
    for directory in report.resolve().parent.parent.glob("qa_[0-9]*"):
        if directory.name[3:].isdigit():
            loops += [(int(directory.name[3:]), r) for r in directory.glob("qa_result_*[0-9a-f].json")]
    if not loops:
        return None
    try:
        return json.loads(max(loops)[1].read_text()).get("info", {}).get("parent_dir")
    except (OSError, ValueError):
        return None


def _with_work_id(f: Finding, realms: dict[str, str]) -> Finding:
    dataset = work_dataset_id(f.dataset, f.path, realms)
    return f if dataset == f.dataset else Finding(f.severity, f.check, f.message, dataset, f.path)
