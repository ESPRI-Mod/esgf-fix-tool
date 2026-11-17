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
    """Resolve both explicit C097 renames and a grid edit's paired filename edit."""
    requests = []
    for i, step in enumerate(steps):
        if step.fix == "sync_filename_grid_label":
            target = fixes.grid_labels.filename_grid_target(path, **step.params)
        elif step.fix == "set_reduced_grid_label":
            # The filename is part of this grid-label correction. Do not wait
            # for a later QA iteration to discover its mismatch (C097).
            target = fixes.grid_labels.proposed_drs_path(path, **step.params)
        elif step.fix == "reduce_replicated_zostoga":
            fields = Path(path).name.split("_", 5)
            if len(fields) != 6 or fields[0] != "zostoga":
                continue  # A non-DRS basename contains no grid field to change.
            target = fixes.grid_labels.filename_grid_target(path, "g112", "g010")
        else:
            continue
        requests.append((i, target))
    if not requests:
        return Path(path), None, None
    if len({str(target or path) for _, target in requests}) != 1:
        raise ValueError("Conflicting filename synchronizations for one file")
    # Prefer the explicit rename's log entry when both diagnostics were selected.
    i, target = next(
        ((i, target) for i, target in requests if steps[i].fix == "sync_filename_grid_label"),
        requests[0],
    )
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
    allow_hardlinks: bool = False,
    events: Callable[[ProgressEvent], None] | None = None,
    file_index: int = 1,
    file_total: int = 1,
) -> list[dict]:
    """Report the current file before any potentially long I/O, including cancellation."""
    steps = order_steps(steps)
    with reporting(events, path):
        notify("file", "start", index=file_index, total=file_total, dry_run=dry_run)
        try:
            actual, target, rename_index = _rename_request(path, steps)
            _check_shared_rename(actual, target, allow_hardlinks)
            entries = _apply_file(
                actual, steps, dry_run=dry_run, allow_hardlinks=allow_hardlinks
            )
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
    path: str | Path,
    steps: list[Step],
    *,
    dry_run: bool = False,
    allow_hardlinks: bool = False,
) -> list[dict]:
    unknown = [s.fix for s in steps if s.fix not in fixes.REGISTRY]
    if unknown:
        raise KeyError(f"{Path(path).name}: fixes not implemented: {unknown}")
    from .execute import execute

    return execute(path, steps, dry_run=dry_run, allow_hardlinks=allow_hardlinks)


def _check_shared_rename(actual, target, allow_hardlinks):
    """A filename change cannot synchronize unlisted names of the same inode."""
    if (
        allow_hardlinks
        and target is not None
        and actual.exists()
        and actual.stat().st_nlink > 1
    ):
        raise PermissionError(
            "--allow-hardlinks cannot synchronize filenames of all physical aliases; "
            "use an independent copy and rebuild its publication links."
        )


def _group_hardlink_aliases(files, effective):
    """Merge selected fixes of regular hardlink paths into one physical-file job.

    Preserve every distinct planned step and record all aliases. This prevents
    concurrent HDF5 updates through files/d... and v... without scanning the tree
    or guessing which other links exist outside the plan.
    """
    owners, aliases = {}, {}
    for path, (actual, _, _) in list(effective.items()):
        if not actual.exists() or actual.is_symlink():
            continue
        stat = actual.stat()
        if stat.st_nlink < 2:
            continue
        key = (stat.st_dev, stat.st_ino)
        if key not in owners:
            owners[key] = path
            continue
        primary = owners[key]
        for step in files[path]:
            if step not in files[primary]:
                files[primary] = [*files[primary], step]
        aliases.setdefault(primary, []).append(path)
        del files[path]
        del effective[path]
    return aliases


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
    workers: int = 1,
    allow_hardlinks: bool = False,
) -> dict[str, int]:
    """Apply every file of the plan. ``progress(done, total, path, changed, elapsed_s)`` is called after each file;
    each fixlog line is flushed at once, so the log can be followed while a long run goes on.

    The Python API stays fail-fast unless continue_on_error is requested. In that
    mode ordinary per-file exceptions are recorded and printed, while cancellation
    and global plan validation still abort. A failed progress callback uses changed=None.
    With allow_hardlinks, planned physical aliases share one direct-update job;
    the files/changed/unchanged counters then count jobs rather than alias paths.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if only_under is not None:
        check_under(plan, only_under)
    from .deduplicate import apply_root, completed_discard, deduplicate

    # Resolve the simulation boundary before modifying any planned file. The
    # deduplication itself runs after the workers have closed all NetCDF handles.
    deduplicate_root = apply_root(plan, only_under)
    active_files = dict(plan.files)
    removed_duplicates = {}
    if deduplicate_root is not None:
        for path in plan.files:
            keep = completed_discard(deduplicate_root, path)
            if keep is not None:
                removed_duplicates[path] = str(keep)
                del active_files[path]
    # Preserve plan-wide target checks, but defer individual validation errors to
    # their file so continuation also handles bad renames and missing inputs.
    effective, preflight_errors = {}, {}
    for path, steps in active_files.items():
        try:
            resolved = _rename_request(path, steps)
            _check_shared_rename(resolved[0], resolved[1], allow_hardlinks)
            effective[path] = resolved
        except Exception as exc:
            if not continue_on_error:
                raise
            preflight_errors[path] = exc
    final_paths = [str(target or actual) for actual, target, _ in effective.values()]
    if len(final_paths) != len(set(final_paths)):
        raise FileExistsError(
            "Multiple plan entries target the same final filename: nothing applied"
        )
    hardlink_aliases = (
        _group_hardlink_aliases(active_files, effective) if allow_hardlinks else {}
    )
    if workers > 1:
        from .parallel import check_independent

        check_independent(effective)
    missing = [p for p, (actual, _, _) in effective.items() if not actual.exists()]
    if missing and not continue_on_error:
        raise FileNotFoundError(
            f"{len(missing)} file(s) of the plan don't exist, e.g. {missing[0]}: nothing applied"
        )
    # Keep the original DRS proposal in the log for existing consumers. The
    # basename itself is now synchronized by apply_file in the same operation.
    drs_targets = {}
    for path, steps in active_files.items():
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
    if allow_hardlinks:
        stats["hardlink_aliases"] = sum(map(len, hardlink_aliases.values()))
    if continue_on_error:
        stats["failed"] = 0
    total, start = len(active_files), time.monotonic()
    from .parallel import file_jobs

    first_error = None
    failed_paths = set()
    with open(fixlog, "a") as out, file_jobs(
        active_files, workers, dry_run, events, preflight_errors,
        stop_on_error=not continue_on_error, allow_hardlinks=allow_hardlinks,
    ) as jobs:
        for path, steps, result in jobs:
            started = time.monotonic() - (result["elapsed"] if result else 0)
            try:
                if result is not None and result["error"] is not None:
                    raise result["error"]
                if result is None and path in preflight_errors:
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
                entries = result["entries"] if result is not None else apply_file(
                    path,
                    steps,
                    dry_run=dry_run,
                    allow_hardlinks=allow_hardlinks,
                    events=events,
                    file_index=stats["files"] + 1,
                    file_total=total,
                )
            except Exception as exc:
                failed_paths.add(path)
                failed_paths.update(hardlink_aliases.get(path, []))
                if path in effective:
                    actual, target, _ = effective[path]
                    failed_paths.add(str(actual))
                    if target is not None:
                        failed_paths.add(str(target))
                trace = result["traceback"] if result is not None else traceback.format_exc()
                if events:
                    events(ProgressEvent("error", "failed", path=path,
                                         details={"traceback": trace}))
                if not continue_on_error and workers == 1:
                    raise
                if not continue_on_error and first_error is None:
                    first_error = exc
                # Do not catch KeyboardInterrupt/SystemExit. Never report a failed
                # file as unchanged: a direct update can fail after partial edits.
                stats["files"] += 1
                stats["failed"] = stats.get("failed", 0) + 1
                record = {
                    "time": datetime.now(UTC).isoformat(timespec="seconds"),
                    "file": path,
                    "tool_version": __version__,
                    "rules_version": plan.rules_version,
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
                if path in hardlink_aliases:
                    record["hardlink_aliases"] = hardlink_aliases[path]
                if result is not None:
                    record["worker_pid"] = result["worker_pid"]
                out.write(json.dumps(record) + "\n")
                out.flush()
                print(
                    f"Échec du fichier {Path(path).name} — "
                    + ("poursuite du plan." if continue_on_error
                       else "arrêt après les fichiers déjà en cours."),
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
                "dry_run": dry_run,
                "status": "done",
                "steps": entries,
                "write_mode": entries[0]["write_mode"] if entries else "none",
                "rewrite_count": max(
                    (e.get("rewrite_count", 0) for e in entries), default=0
                ),
                "elapsed_seconds": time.monotonic() - started,
            }
            if path in hardlink_aliases:
                record["hardlink_aliases"] = hardlink_aliases[path]
            if path in drs_targets:
                record["drs_rename"] = {"source": path, "target": drs_targets[path]}
            if result is not None:
                record["worker_pid"] = result["worker_pid"]
            renamed = next((e for e in entries if "filename_target" in e), None)
            if renamed is not None:
                record["final_file"] = renamed["filename_target"]
                record["filename_renamed"] = renamed.get("filename_renamed", False)
            out.write(json.dumps(record) + "\n")
            out.flush()
            if progress:
                progress(stats["files"], total, path, changed, time.monotonic() - start)
    if first_error is not None:
        raise first_error
    if deduplicate_root is not None:
        log_path = Path(fixlog)
        dedup_log = log_path.with_name(f"{log_path.stem}.deduplicate.jsonl")
        with reporting(events, deduplicate_root), activity("Dédoublonnage ATM/SRF"):
            if removed_duplicates:
                with dedup_log.open("a", encoding="utf-8") as log:
                    for discard, keep in removed_duplicates.items():
                        log.write(json.dumps(dict(
                            time=datetime.now(UTC).isoformat(), operation="component-deduplication",
                            status="already_removed", discard=discard, keep=keep, dry_run=dry_run,
                        )) + "\n")
                        notify("deduplication", "result", message=f"already_removed: {Path(discard).name}")
            result = deduplicate(
                deduplicate_root, dedup_log, dry_run=dry_run,
                only_under=only_under or deduplicate_root,
                blocked_paths=failed_paths,
                emit=lambda message: notify("deduplication", "result", message=message),
            )
            notify("deduplication", "summary", message=(
                f"Doublons ATM/SRF : {result['deleted']} supprimés, "
                f"{result['would_delete']} à supprimer (simulation), "
                f"{len(removed_duplicates)} déjà supprimés, {result['failed']} échecs"
            ))
        stats.update({f"duplicates_{key}": value for key, value in result.items()})
        stats["duplicates_already_removed"] = len(removed_duplicates)
    return stats
