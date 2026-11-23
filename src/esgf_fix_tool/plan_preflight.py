"""Refine report candidates using exactly apply's read-only preparation pipeline."""

from .apply import apply_file
from .plan import order_steps


def verify_plan(plan, files=None, *, break_hardlinks=False):
    """Drop fully conforming files, retaining selected prerequisites within changed files.

    Errors are never interpreted as conformity. Keep unverified candidates in the
    plan and inventory them separately from the confirmed change count. Apply
    still prepares and validates every retained file again before writing.
    """
    candidates = list(plan.files.items()) if files is None else files
    retained, records = {}, {}
    counts = dict(changed_steps=0, changed_files=0, noop_steps=0, conforming_files=0,
                  unverified_steps=0, unverified_files=0)
    for path, selected in candidates:
        steps = order_steps(selected)
        try:
            entries = apply_file(path, steps, dry_run=True, break_hardlinks=break_hardlinks,
                                 continue_on_fix_error=True)
        except Exception as error:  # noqa: BLE001 -- recipe failures stay explicit; interrupts propagate
            # An unreadable file or failed guard must remain visible and retryable.
            retained[path] = steps
            records[path] = dict(status='unverified', error=str(error),
                                 error_type=type(error).__name__)
            counts['unverified_files'] += 1
            counts['unverified_steps'] += len(steps)
            continue
        changed = sum(bool(e['changed']) for e in entries)
        unresolved = sum(e.get('status') in ('failed', 'blocked') for e in entries)
        counts['changed_steps'] += changed
        counts['changed_files'] += bool(changed)
        counts['noop_steps'] += len(entries) - changed - unresolved
        counts['unverified_steps'] += unresolved
        counts['unverified_files'] += bool(unresolved)
        records[path] = dict(status='partial' if unresolved else 'changed' if changed else 'conforming',
                             steps=[{k: e[k] for k in ('rule','fix','params','changed','status','error') if k in e}
                                    for e in entries])
        if changed or unresolved:
            # No-op steps may establish names or verification invariants needed
            # by another selected recipe. Prune whole files, never prerequisites.
            retained[path] = steps
        else:
            counts['conforming_files'] += 1
    plan.files = retained
    plan.preflight = dict(mode='read-only', break_hardlinks=break_hardlinks, counts=counts, files=records)
    return counts
