"""Apply a FixPlan with direct metadata updates or one combined structural output."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, fixes
from .plan import FixPlan, Step, order_steps
from .progress import ProgressEvent, activity, notify, reporting, step_scope


def _rename_request(path, steps):
    requests = [
        (i, s) for i, s in enumerate(steps) if s.fix == "sync_filename_grid_label"
    ]
    if not requests:
        return Path(path), None, None
    if len(requests) != 1:
        raise ValueError("Expected a single filename synchronization per file")
    i, step = requests[0]
    target = fixes.grid_labels.filename_grid_target(path, **step.params)
    if target is None:
        return Path(path), None, i
    original = Path(path)
    if original.is_symlink() or target.is_symlink():
        raise ValueError(
            "Filename synchronization requires regular independent file paths"
        )
    if original.exists() and target.exists():
        raise FileExistsError(
            f"Filename synchronization would overwrite {target}: nothing applied"
        )
    # Replay a completed plan through its explicit final basename.
    if not original.exists() and target.is_file():
        return target, None, i
    return original, target, i


def apply_file(
    path: str | Path,
    steps: list[Step],
    *,
    dry_run: bool = False,
    events: Callable[[ProgressEvent], None] | None = None,
    file_index: int = 1,
    file_total: int = 1,
    break_hardlinks: bool = False,
) -> list[dict]:
    """Report the current file before any potentially long I/O, including cancellation."""
    steps = order_steps(steps)
    with reporting(events, path):
        notify("file", "start", index=file_index, total=file_total, dry_run=dry_run)
        try:
            actual, target, rename_index = _rename_request(path, steps)
            entries = _apply_file(actual, steps, dry_run=dry_run, break_hardlinks=break_hardlinks)
            if target is not None:
                # Reserve the destination without ever overwriting an existing file.
                # Both paths have the same parent, and all NetCDF handles are closed.
                if not dry_run:
                    with (
                        step_scope(steps[rename_index]),
                        activity("Synchronisation du nom de fichier"),
                    ):
                        os.link(actual, target)
                        try:
                            actual.unlink()
                        except BaseException:
                            target.unlink()
                            raise
                entry = entries[rename_index]
                entry.update(
                    changed=True,
                    filename_source=str(actual),
                    filename_target=str(target),
                    filename_renamed=not dry_run,
                )
                if entry["write_mode"] == "none":
                    entry["write_mode"] = "rename-only"
            elif rename_index is not None:
                entries[rename_index]["filename_target"] = str(actual)
        except BaseException as exc:
            notify(
                "file",
                "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                error=str(exc) or type(exc).__name__,
            )
            raise

        # Done means that all hooks/verifiers succeeded and the file was committed (or
        # dry-run preflight succeeded). Metadata preparation alone is not Done.
        for step, entry in zip(steps, entries):
            with step_scope(step):
                notify("fix", "done", changed=entry["changed"], dry_run=dry_run)
        notify(
            "file", "done", changed=any(e["changed"] for e in entries), dry_run=dry_run
        )
        return entries


def _apply_file(
    path: str | Path, steps: list[Step], *, dry_run: bool = False, break_hardlinks: bool = False
) -> list[dict]:
    unknown = [s.fix for s in steps if s.fix not in fixes.REGISTRY]
    if unknown:
        raise KeyError(f"{Path(path).name}: fixes not implemented: {unknown}")
    from .execute import execute

    return execute(path, steps, dry_run=dry_run, break_hardlinks=break_hardlinks)


def check_under(plan: FixPlan, root: str | Path) -> None:
    """Refuse the whole plan if any file is outside ``root`` (e.g. a source path left in a plan meant for staging)."""
    root = Path(root).resolve()
    outside = [p for p in plan.files if not Path(p).resolve().is_relative_to(root)]
    if outside:
        raise PermissionError(
            f"{len(outside)} file(s) outside {root}, e.g. {outside[0]}: nothing applied"
        )


def apply_plan(
    plan: FixPlan,
    fixlog: str | Path,
    *,
    dry_run: bool = False,
    only_under: str | Path | None = None,
    progress: Callable[[int, int, str, bool | None, float], None] | None = None,
    events: Callable[[ProgressEvent], None] | None = None,
    continue_on_error: bool = False,
    break_hardlinks: bool = False,
) -> dict[str, int]:
    """Apply every file of the plan. ``progress(done, total, path, changed, elapsed_s)`` is called after each file;
    each fixlog line is flushed at once, so the log can be followed while a long run goes on.

    The Python API stays fail-fast unless continue_on_error is requested. In that
    mode ordinary per-file exceptions are recorded and printed, while cancellation
    and global plan validation still abort. A failed progress callback uses changed=None.
    """
    if only_under is not None:
        check_under(plan, only_under)
    # Preserve plan-wide target checks, but defer individual validation errors to
    # their file so continuation also handles bad renames and missing inputs.
    effective, preflight_errors = {}, {}
    for path, steps in plan.files.items():
        try:
            effective[path] = _rename_request(path, steps)
        except Exception as exc:
            if not continue_on_error:
                raise
            preflight_errors[path] = exc
    final_paths = [str(target or actual) for actual, target, _ in effective.values()]
    if len(final_paths) != len(set(final_paths)):
        raise FileExistsError(
            "Multiple plan entries target the same final filename: nothing applied"
        )
    missing = [p for p, (actual, _, _) in effective.items() if not actual.exists()]
    if missing and not continue_on_error:
        raise FileNotFoundError(
            f"{len(missing)} file(s) of the plan don't exist, e.g. {missing[0]}: nothing applied"
        )
    # A grid-label edit changes a DRS component. Validate the proposed filename
    # before any file is written and report it for the separate relink stage.
    drs_targets = {}
    for path, steps in plan.files.items():
        for step in steps:
            if step.fix != "set_reduced_grid_label":
                continue
            try:
                target = fixes.grid_labels.proposed_drs_path(path, **step.params)
            except Exception as exc:
                if not continue_on_error:
                    raise
                preflight_errors[path] = exc
                continue
            if target is not None:
                drs_targets[path] = str(target)
    stats = {"files": 0, "changed": 0, "unchanged": 0}
    if continue_on_error:
        stats["failed"] = 0
    total, start = len(plan.files), time.monotonic()
    # Set by `etb run` in the esgf-toolbox image: links each fixlog line to the run that wrote it.
    etb_run_id = os.environ.get("ETB_RUN_ID") or None
    with open(fixlog, "a") as out:
        for path, steps in plan.files.items():
            started = time.monotonic()
            try:
                if path in preflight_errors:
                    exc = preflight_errors[path]
                    with reporting(events, path):
                        notify(
                            "file",
                            "start",
                            index=stats["files"] + 1,
                            total=total,
                            dry_run=dry_run,
                        )
                        notify("file", "failed", error=str(exc) or type(exc).__name__)
                    raise exc
                entries = apply_file(
                    path,
                    steps,
                    dry_run=dry_run,
                    events=events,
                    file_index=stats["files"] + 1,
                    file_total=total,
                    break_hardlinks=break_hardlinks,
                )
            except Exception as exc:
                if not continue_on_error:
                    raise
                # Do not catch KeyboardInterrupt/SystemExit. Never report a failed
                # file as unchanged: a direct update can fail after partial edits.
                stats["files"] += 1
                stats["failed"] += 1
                trace = traceback.format_exc()
                record = {
                    "time": datetime.now(UTC).isoformat(timespec="seconds"),
                    "file": path,
                    "tool_version": __version__,
                    "rules_version": plan.rules_version,
                    "rules_digest": plan.rules_digest,
                    "etb_run_id": etb_run_id,
                    "dry_run": dry_run,
                    "status": "failed",
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": trace,
                    },
                    "planned_steps": [
                        {"rule": s.rule, "fix": s.fix, "params": s.params}
                        for s in order_steps(steps)
                    ],
                    "steps": [],
                    "write_mode": "unknown",
                    "rewrite_count": None,
                    "changes_may_have_been_applied": not dry_run,
                    "elapsed_seconds": time.monotonic() - started,
                }
                out.write(json.dumps(record) + "\n")
                out.flush()
                print(
                    f"Échec du fichier {path} — poursuite du plan.",
                    file=sys.stderr,
                    flush=True,
                )
                print(trace, file=sys.stderr, end="", flush=True)
                if progress:
                    progress(
                        stats["files"], total, path, None, time.monotonic() - start
                    )
                continue
            changed = any(e["changed"] for e in entries)
            stats["files"] += 1
            stats["changed" if changed else "unchanged"] += 1
            record = {
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                "file": path,
                "tool_version": __version__,
                "rules_version": plan.rules_version,
                "rules_digest": plan.rules_digest,
                "etb_run_id": etb_run_id,
                "dry_run": dry_run,
                "status": "done",
                "steps": entries,
                "write_mode": entries[0]["write_mode"] if entries else "none",
                "rewrite_count": max(
                    (e.get("rewrite_count", 0) for e in entries), default=0
                ),
                "elapsed_seconds": time.monotonic() - started,
            }
            if path in drs_targets:
                record["drs_rename"] = {"source": path, "target": drs_targets[path]}
            renamed = next((e for e in entries if "filename_target" in e), None)
            if renamed is not None:
                record["final_file"] = renamed["filename_target"]
                record["filename_renamed"] = renamed.get("filename_renamed", False)
            out.write(json.dumps(record) + "\n")
            out.flush()
            if progress:
                progress(stats["files"], total, path, changed, time.monotonic() - start)
    return stats
