"""Isolated file workers; the parent alone owns callbacks and shared journals."""

import multiprocessing
import os
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from queue import Empty

_events = None


def _initialize(events):
    global _events
    _events = events


def _run(path, steps, dry_run, index, total, error, allow_hardlinks=False):
    from .apply import apply_file
    from .progress import notify, reporting

    last_io = {}

    def report(event):
        # Do not flood the transport with every chunk of a large science array.
        if event.kind == "io":
            key = (event.rule, event.phase, event.details.get("variable"))
            now = time.monotonic()
            if now - last_io.get(key, 0) < 1 and event.details.get("completed") != event.details.get("total"):
                return
            last_io[key] = now
        _events.put(event)

    started = time.monotonic()
    try:
        if error is not None:
            with reporting(report, path):
                notify("file", "start", index=index, total=total, dry_run=dry_run)
                notify("file", "failed", error=str(error))
            raise error
        entries = apply_file(path, steps, dry_run=dry_run, events=report,
                             file_index=index, file_total=total,
                             allow_hardlinks=allow_hardlinks)
        return dict(entries=entries, error=None, elapsed=time.monotonic() - started,
                    worker_pid=os.getpid())
    except Exception as exc:
        return dict(entries=None, error=exc, traceback=traceback.format_exc(),
                    elapsed=time.monotonic() - started, worker_pid=os.getpid())


def check_independent(effective):
    """Refuse aliases and hardlinks before workers can touch a shared resource."""
    owners = {}
    for path, (actual, target, _) in effective.items():
        for resource in (actual, target):
            if resource is None:
                continue
            resource = Path(resource)
            keys = [("path", str(resource.resolve()))]
            if resource.exists():
                stat = resource.stat()
                keys.append(("inode", stat.st_dev, stat.st_ino))
            for key in keys:
                if key in owners and owners[key] != path:
                    raise ValueError(
                        f"Parallel plan entries share a physical file or target: "
                        f"{owners[key]} and {path}: nothing applied"
                    )
                owners[key] = path


@contextmanager
def file_jobs(files, workers, dry_run, events, errors, stop_on_error=False, allow_hardlinks=False):
    """Keep at most workers files in flight, yielding in completion order.

    On failure/cancellation stop dispatching. Already running files finish their
    current transaction before shutdown, rather than being killed mid-write.
    """
    if workers == 1 or not files:
        yield ((path, steps, None) for path, steps in files.items())
        return
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    pool = ProcessPoolExecutor(max_workers=min(workers, len(files)),
                               mp_context=context, initializer=_initialize,
                               initargs=(queue,))
    pending = {}
    source = iter(enumerate(files.items(), 1))

    def dispatch():
        try:
            index, (path, steps) = next(source)
        except StopIteration:
            return
        future = pool.submit(
            _run, path, steps, dry_run, index, len(files), errors.get(path), allow_hardlinks
        )
        pending[future] = (path, steps)

    def drain():
        while True:
            try:
                event = queue.get_nowait()
            except Empty:
                break
            if events is not None:
                events(event)

    def results():
        stopped = False
        for _ in range(min(workers, len(files))):
            dispatch()
        while pending:
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            drain()
            for future in done:
                path, steps = pending.pop(future)
                result = future.result()
                stopped = stopped or (stop_on_error and result["error"] is not None)
                yield path, steps, result
                if not stopped:
                    dispatch()

    try:
        yield results()
    finally:
        # Drain while waiting: a child queue feeder must not block shutdown.
        for future in pending:
            future.cancel()
        while any(not f.done() for f in pending):
            drain()
            wait([f for f in pending if not f.done()], timeout=0.1)
        pool.shutdown(wait=True, cancel_futures=True)
        drain()
        queue.close()
        queue.join_thread()
