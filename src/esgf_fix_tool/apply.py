"""Apply a FixPlan without ever writing a file in place.

Staging files start as hardlinks to the simulator's files (``cp -al``): writing one in place would modify
the source. So each file is copied to a temp file in the same directory, fixed there, then renamed over
the original name (new inode, atomic).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import netCDF4

from . import __version__, fixes
from .plan import FixPlan, Step


def apply_file(path: str | Path, steps: list[Step], *, dry_run: bool = False) -> list[dict]:
    path = Path(path)
    unknown = [s.fix for s in steps if s.fix not in fixes.REGISTRY]
    if unknown:
        raise KeyError(f"{path.name}: fixes not implemented: {unknown}")
    structural = [s.fix for s in steps if fixes.REGISTRY[s.fix].kind != "metadata"]
    if structural:
        raise NotImplementedError(f"{path.name}: structural fixes not supported yet: {structural}")

    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".fixtmp", dir=path.parent)
    os.close(fd)
    log = []
    try:
        shutil.copyfile(path, tmp)
        with netCDF4.Dataset(tmp, "a") as ds:
            for s in steps:
                changed = fixes.REGISTRY[s.fix].func(ds, **s.params)
                log.append({"rule": s.rule, "fix": s.fix, "params": s.params, "changed": bool(changed)})
        if dry_run or not any(e["changed"] for e in log):
            os.unlink(tmp)
        else:
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return log


def check_under(plan: FixPlan, root: str | Path) -> None:
    """Refuse the whole plan if any file is outside ``root`` (e.g. a source path left in a plan meant for staging)."""
    root = Path(root).resolve()
    outside = [p for p in plan.files if not Path(p).resolve().is_relative_to(root)]
    if outside:
        raise PermissionError(f"{len(outside)} file(s) outside {root}, e.g. {outside[0]}: nothing applied")


def apply_plan(plan: FixPlan, fixlog: str | Path, *, dry_run: bool = False,
               only_under: str | Path | None = None,
               progress: Callable[[int, int, str, bool, float], None] | None = None) -> dict[str, int]:
    """Apply every file of the plan. ``progress(done, total, path, changed, elapsed_s)`` is called after each file;
    each fixlog line is flushed at once, so the log can be followed while a long run goes on."""
    if only_under is not None:
        check_under(plan, only_under)
    missing = [p for p in plan.files if not Path(p).exists()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} file(s) of the plan don't exist, e.g. {missing[0]}: nothing applied")
    stats = {"files": 0, "changed": 0, "unchanged": 0}
    total, start = len(plan.files), time.monotonic()
    with open(fixlog, "a") as out:
        for path, steps in plan.files.items():
            entries = apply_file(path, steps, dry_run=dry_run)
            changed = any(e["changed"] for e in entries)
            stats["files"] += 1
            stats["changed" if changed else "unchanged"] += 1
            out.write(json.dumps({
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                "file": path, "tool_version": __version__, "rules_version": plan.rules_version,
                "dry_run": dry_run, "steps": entries,
            }) + "\n")
            out.flush()
            if progress:
                progress(stats["files"], total, path, changed, time.monotonic() - start)
    return stats
