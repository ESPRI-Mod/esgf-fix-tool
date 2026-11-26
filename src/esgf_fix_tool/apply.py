"""Apply a FixPlan with direct metadata updates or one combined structural output."""

from __future__ import annotations

import glob
import json
import os
import re
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
    """Resolve explicit renames (C097 grid label, C125 time range) and a grid edit's paired filename edit."""
    requests = []
    for i, step in enumerate(steps):
        if step.fix == "sync_filename_grid_label":
            target = fixes.grid_labels.filename_grid_target(path, **step.params)
        elif step.fix == "set_reduced_grid_label":
            # The filename is part of this grid-label correction. Do not wait
            # for a later QA iteration to discover its mismatch (C097).
            target = fixes.grid_labels.proposed_drs_path(path, **step.params)
        elif step.fix == "sync_filename_time_range":
            target = fixes.time_range.filename_time_target(path, **step.params)
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
        ((i, target) for i, target in requests
         if steps[i].fix in ("sync_filename_grid_label", "sync_filename_time_range")),
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
    continue_on_fix_error: bool = False,
    events: Callable[[ProgressEvent], None] | None = None,
    file_index: int = 1,
    file_total: int = 1,
    break_hardlinks: bool = False,
) -> list[dict]:
    """Report the current file before any potentially long I/O, including cancellation."""
    steps = order_steps(steps)
    # Inserting time steps changes a dimension's length, which the grouped rewrite refuses by design: those
    # steps run last, on their own, once the other fixes (and a rename) are committed.
    fills = [s for s in steps if s.fix == "fill_missing_time_steps"]
    steps = [s for s in steps if s.fix != "fill_missing_time_steps"] + fills
    with reporting(events, path):
        notify("file", "start", index=file_index, total=file_total, dry_run=dry_run)
        try:
            others = steps[:len(steps) - len(fills)]
            actual, target, rename_index = _rename_request(path, others)
            entries = _apply_file(actual, others, dry_run=dry_run, break_hardlinks=break_hardlinks,
                                  continue_on_fix_error=continue_on_fix_error) if others else []
            if target is not None and entries[rename_index].get("status") in ("failed", "blocked"):
                target = None
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
            final = target if target is not None and not dry_run else actual
            for step in fills:
                entries.append(_fill_entry(final, step, dry_run, break_hardlinks, continue_on_fix_error))
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
                notify("fix", entry.get("status", "done"), changed=entry["changed"],
                       dry_run=dry_run, error=entry.get("error", ""))
        notify(
            "file", "failed" if any(e.get("status") in ("failed", "blocked") for e in entries) else "done",
            changed=any(e["changed"] for e in entries), dry_run=dry_run,
            error="Résultat partiel : les correctifs indépendants ont été conservés" if any(e.get("status") in ("failed", "blocked") for e in entries) else "",
        )
        return entries


def _fill_entry(path, step, dry_run, break_hardlinks, continue_on_fix_error):
    """Run one fill_missing_time_steps step (fixes.time_fill.fill_file) and return its fixlog entry."""
    entry = dict(rule=step.rule, fix=step.fix, params=step.params, changed=False, write_mode="none")
    with step_scope(step):
        notify("fix", "start")
        try:
            details = fixes.time_fill.fill_file(path, step.params["var"], step.params.get("max_fraction", 0.01),
                                                dry_run=dry_run, break_hardlinks=break_hardlinks)
        except Exception as exc:
            if not continue_on_fix_error:
                raise
            entry.update(status="failed", error=str(exc), traceback=traceback.format_exc())
            return entry
    entry.update(details, changed=details["time_steps_filled"] > 0, data_verified=not dry_run)
    if entry["changed"] and not dry_run:
        entry["write_mode"] = "time-fill-rewrite"
    return entry


def _apply_file(
    path: str | Path,
    steps: list[Step],
    *,
    dry_run: bool = False,
    break_hardlinks: bool = False,
    continue_on_fix_error: bool = False,
) -> list[dict]:
    unknown = [s.fix for s in steps if s.fix not in fixes.REGISTRY]
    if unknown and not continue_on_fix_error:
        raise KeyError(f"{Path(path).name}: fixes not implemented: {unknown}")
    from .execute import execute

    return execute(path, steps, dry_run=dry_run, break_hardlinks=break_hardlinks,
                   continue_on_fix_error=continue_on_fix_error)


def check_under(plan: FixPlan, root: str | Path) -> None:
    """Refuse the whole plan if any file is outside ``root`` (e.g. a source path left in a plan meant for staging)."""
    root = Path(root).resolve()
    outside = [p for p in plan.files if not Path(p).resolve().is_relative_to(root)]
    if outside:
        raise PermissionError(
            f"{len(outside)} file(s) outside {root}, e.g. {outside[0]}: nothing applied"
        )


def parse_shard(text: str) -> tuple[int, int]:
    """'k/K' -> (k, K), 1 <= k <= K."""
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", text or "")
    if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
        raise ValueError(f"--shard expects k/K with 1 <= k <= K, got {text!r}")
    return int(m.group(1)), int(m.group(2))


def shard_files(files, shard: tuple[int, int]) -> set[str]:
    """Share k of K of the plan's files: every K-th file in path order, from the k-th.

    Computed on the whole plan, never on what is left, so jobs started at different times
    (or resumed) always get disjoint shares. Balanced by count: sizes change as files are fixed.
    """
    k, total = shard
    return {path for i, path in enumerate(sorted(files)) if i % total == k - 1}


def recorded_results(logs) -> tuple[set[str], set[str]]:
    """Plan paths that earlier fixlogs record as done / failed; the latest record of a file wins.

    Lines without a ``file`` and ``status`` (events journals, deduplication logs) and dry-run lines are
    ignored, so a shell glob such as ``fixlog*.jsonl`` is fine.
    """
    latest = {}
    for log in logs:
        for line in Path(log).read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (not isinstance(record, dict) or "file" not in record or record.get("dry_run")
                    or record.get("status") not in ("done", "failed")):
                continue
            key = (record.get("time") or "", record["status"] == "done")
            if record["file"] not in latest or key >= latest[record["file"]][0]:
                latest[record["file"]] = (key, record["status"])
    done = {f for f, (_, status) in latest.items() if status == "done"}
    return done, set(latest) - done


def remove_leftovers(paths, dry_run: bool = False) -> list[str]:
    """Temporary files of interrupted writes of these files (``.<name>.*.fixtmp`` / ``.linkbreak``).

    A killed rewrite never replaced its file, so the original is intact and the temporary file is
    debris. Only the files this run is about to fix are looked at: no other run writes them.
    """
    found = []
    for path in map(Path, paths):
        for leftover in path.parent.glob(f".{glob.escape(path.name)}.*"):
            if leftover.suffix in (".fixtmp", ".linkbreak") and leftover.is_file():
                found.append(str(leftover))
                if not dry_run:
                    leftover.unlink()
    return found


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
    workers: int = 1,
    resume: list[str | Path] | None = None,
    skip_failed: bool = False,
    shard: tuple[int, int] | None = None,
) -> dict[str, int]:
    """Apply every file of the plan. ``progress(done, total, path, changed, elapsed_s)`` is called after each file;
    each fixlog line is flushed at once, so the log can be followed while a long run goes on.

    The Python API stays fail-fast unless continue_on_error is requested. In that
    mode ordinary per-file exceptions are recorded and printed, while cancellation
    and global plan validation still abort. A failed progress callback uses changed=None.

    ``shard=(k, K)`` keeps share k of K of the plan's files (several jobs, one plan); the automatic ATM/SRF
    deduplication is then skipped, since shards run concurrently. ``resume`` lists fixlogs of earlier runs of
    this plan: the files they record as done are skipped, failed ones retried unless ``skip_failed``.
    Leftover temporary files of interrupted rewrites of the remaining files are removed first.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if only_under is not None:
        check_under(plan, only_under)
    from .deduplicate import (
        apply_root,
        completed_discard,
        deduplicate,
        unused_product,
        validate_unused,
    )

    active_files = dict(plan.files)
    selection = {}
    if shard is not None:
        if shard[1] > 1:
            # Shards never coordinate: refuse a plan whose entries share a file or a target, as --workers does.
            whole = {}
            for path, steps in plan.files.items():
                try:
                    whole[path] = _rename_request(path, steps)
                except Exception:  # noqa: BLE001, S112 - reported by the shard that owns the file
                    continue
            from .parallel import check_independent

            check_independent(whole)
        mine = shard_files(plan.files, shard)
        active_files = {p: s for p, s in active_files.items() if p in mine}
        selection["shard"] = f"{shard[0]}/{shard[1]}"
    if resume:
        done, failed = recorded_results(resume)
        selection["skipped_done"] = len(active_files.keys() & done)
        if skip_failed:
            selection["skipped_failed"] = len(active_files.keys() & failed)
        skip = done | failed if skip_failed else done
        active_files = {p: s for p, s in active_files.items() if p not in skip}
    leftovers = remove_leftovers(active_files, dry_run)
    if leftovers:
        selection["leftovers_found" if dry_run else "leftovers_removed"] = len(leftovers)

    # Resolve the simulation boundary before modifying any planned file. The
    # deduplication itself runs after the workers have closed all NetCDF handles.
    deduplicate_root = None if shard is not None else apply_root(plan, only_under)
    removed_duplicates = {}
    removed_unused = []
    if deduplicate_root is not None:
        for path in list(active_files):
            if unused_product(deduplicate_root, Path(path)):
                candidate = Path(path)
                if not candidate.exists() and not candidate.is_symlink():
                    removed_unused.append(path)
                    del active_files[path]
                    continue
                try:
                    validate_unused(deduplicate_root, candidate)
                except (OSError, ValueError):
                    pass  # Invalid identity follows normal error handling; never delete by basename alone.
                else:
                    # Do not reconstruct or rewrite a product that is about to
                    # be deleted by the explicit cleanup policy after workers close.
                    del active_files[path]
                    continue
            keep = completed_discard(deduplicate_root, path)
            if keep is not None:
                removed_duplicates[path] = str(keep)
                del active_files[path]
    # Preserve plan-wide target checks, but defer individual validation errors to
    # their file so continuation also handles bad renames and missing inputs.
    effective, preflight_errors = {}, {}
    for path, steps in active_files.items():
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
    stats = {"files": 0, "changed": 0, "unchanged": 0, **selection}
    if continue_on_error:
        stats["failed"] = 0
    total, start = len(active_files), time.monotonic()
    # Set by `etb run` in the esgf-toolbox image: links each fixlog line to the run that wrote it.
    etb_run_id = os.environ.get("ETB_RUN_ID") or None
    from .parallel import file_jobs

    first_error = None
    failed_paths = set()
    with open(fixlog, "a") as out, file_jobs(
        active_files, workers, dry_run, events, preflight_errors, break_hardlinks,
        stop_on_error=not continue_on_error, continue_on_fix_error=continue_on_error,
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
                    continue_on_fix_error=continue_on_error,
                    events=events,
                    file_index=stats["files"] + 1,
                    file_total=total,
                    break_hardlinks=break_hardlinks,
                )
            except Exception as exc:
                failed_paths.add(path)
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
                    "rules_digest": plan.rules_digest,
                    "etb_run_id": etb_run_id,
                    **({"shard": selection["shard"]} if shard is not None else {}),
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
            partial = [e for e in entries if e.get("status") in ("failed", "blocked")]
            if partial:
                stats["failed"] = stats.get("failed", 0) + 1
                stats["partial"] = stats.get("partial", 0) + 1
                failed_paths.add(path)
                for entry in partial:
                    if entry.get("traceback"):
                        print(entry["traceback"], file=sys.stderr, flush=True)
            stats["files"] += 1
            stats["changed" if changed else "unchanged"] += 1
            record = {
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                "file": path,
                "tool_version": __version__,
                "rules_version": plan.rules_version,
                "rules_digest": plan.rules_digest,
                "etb_run_id": etb_run_id,
                **({"shard": selection["shard"]} if shard is not None else {}),
                "dry_run": dry_run,
                "status": "partial" if partial else "done",
                "steps": entries,
                "write_mode": entries[0]["write_mode"] if entries else "none",
                "rewrite_count": max(
                    (e.get("rewrite_count", 0) for e in entries), default=0
                ),
                "elapsed_seconds": time.monotonic() - started,
            }
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
                progress(stats["files"], total, path, None if partial else changed, time.monotonic() - start)
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
                            status="already_removed", discard=discard, keep=[keep], dry_run=dry_run,
                        )) + "\n")
                        notify("deduplication", "result", message=f"already_removed: {Path(discard).name}")
            result = deduplicate(
                deduplicate_root, dedup_log, dry_run=dry_run,
                only_under=only_under or deduplicate_root,
                blocked_paths=failed_paths,
                emit=lambda message: notify("deduplication", "result", message=message),
            )
            summary = (
                f"Doublons ATM/SRF : {result['deleted']} supprimés, "
                f"{result['would_delete']} à supprimer (simulation), "
                f"{len(removed_duplicates)} déjà supprimés, {result['failed']} échecs"
            )
            if any(key.startswith('obsolete_') for key in result) or removed_unused:
                with dedup_log.open('a', encoding='utf-8') as log:
                    for path in removed_unused:
                        log.write(json.dumps(dict(time=datetime.now(UTC).isoformat(),
                            operation='unused-product-removal', status='already_removed',
                            discard=path, dry_run=dry_run)) + '\n')
                summary += (
                    f" | Produits inutiles : {result.get('obsolete_deleted', 0)} supprimés, "
                    f"{result.get('obsolete_would_delete', 0)} à supprimer (simulation), "
                    f"{len(removed_unused)} déjà supprimés, {result.get('obsolete_failed', 0)} échecs"
                )
            notify('deduplication', 'summary', message=summary)
        stats.update({key if key.startswith('obsolete_') else f"duplicates_{key}": value
                      for key, value in result.items()})
        if removed_unused:
            stats['obsolete_already_removed'] = len(removed_unused)
        stats["duplicates_already_removed"] = len(removed_duplicates)
    return stats
