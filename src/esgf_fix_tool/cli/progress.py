"""Flush file, recipe and actual I/O progress; no timer or periodic message."""
import json
import shutil
import sys
import time
from contextlib import redirect_stderr
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .dashboard import Dashboard, DashboardStderr
from .text_log import TextLog


def hms(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}'


class ConsoleProgress:
    def __init__(self, events_log, *, quiet=False, parallel=False, label=''):
        self.interactive = not quiet and not label and sys.stdout.isatty()
        self.dashboard = Dashboard() if self.interactive else None
        self.stderr_scope = None
        self.rewrite_io = {}
        self.io_log_state = {}
        self.parallel = parallel
        self.label = label
        self.children = {}
        self.events_log = Path(events_log)
        self.text_log = None
        self.quiet = quiet
        self.file = None
        self.phase = None
        self.phase_stack = []
        self.io = None
        self.transient = False
        self.stream = None
        self.log_error = False

    def __enter__(self):
        self.text_log = TextLog(self.events_log.with_suffix('.log'))
        self.text_log.open()
        try:
            self.stream = self.events_log.open('a')
        except OSError as exc:
            self.log_error = True
            print(f'Journal de progression indisponible : {exc}', file=sys.stderr, flush=True)
        if self.dashboard is not None:
            self.stderr_scope = redirect_stderr(DashboardStderr(sys.stderr, self.dashboard))
            self.stderr_scope.__enter__()
        return self

    def __exit__(self, *exc):
        if self.dashboard is not None:
            self.dashboard.render(force=True)
            self.dashboard.lines = 0  # Leave the final view above the shell prompt.
            self.stderr_scope.__exit__(*exc)
        if self.transient and not self.quiet:
            print(flush=True)
        if self.text_log is not None:
            self.text_log.close()
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError as error:
                if not self.log_error:
                    print(f'Journal de progression indisponible : {error}', file=sys.stderr, flush=True)

    def _write(self, text, *, transient=False):
        if self.quiet:
            return
        label = self.label or (Path(self.file.path).name if self.file else '')
        if label:
            text = f'[{Path(label).name}] {text}'
            transient = False
        tty = sys.stdout.isatty()
        if self.transient:
            print('\r\033[K', end='', flush=True)
        # A wrapped line cannot be replaced with carriage-return + clear-to-EOL.
        # Keep long parameter lists readable as ordinary lines instead.
        width = shutil.get_terminal_size(fallback=(120,24)).columns
        self.transient = transient and tty and len(text) < width
        print(text, end='' if self.transient else '\n', flush=True)

    def _log(self, record):
        if self.log_error:
            return
        try:
            self.stream.write(json.dumps(dict(time=datetime.now(UTC).isoformat(), **record), ensure_ascii=False)+'\n')
            self.stream.flush()
        except OSError as exc:
            # A progress-log error must not interrupt a verified scientific correction.
            self.log_error = True
            print(f'Journal de progression indisponible : {exc}', file=sys.stderr, flush=True)

    def _log_event(self, event):
        """Persist coarse I/O milestones, independently of live screen updates.

        Keep every phase/fix/error event. For each large array operation retain
        at most the initial value and four observed quarter-progress buckets.
        Small auxiliaries need only their completion event. Never invent a
        percentage when a single block crosses several milestones.
        """
        if event.kind == 'io':
            d = event.details
            key = (event.path, event.phase, event.rule, event.fix,
                   d['variable'], d['operation'])
            completed, total = d['completed'], d['total']
            bucket = min(4, max(0, 4 * completed // total)) if total > 0 else 4
            previous = self.io_log_state.get(key)
            # The same operation can legitimately run again during verification.
            if previous and (completed < previous[1] or total != previous[2]):
                previous = None
            self.io_log_state[key] = (bucket, completed, total)
            if total * d['itemsize'] < 8 * 1024 * 1024 and completed < total:
                return False
            if previous and bucket == previous[0]:
                return False
        self._log(asdict(event))
        if self.text_log is not None:
            self.text_log.write(event, self._io_text)
        if event.kind == 'file' and event.status in ('done', 'failed', 'cancelled'):
            self.io_log_state = {k: v for k, v in self.io_log_state.items()
                                 if k[0] != event.path}
        return True

    @staticmethod
    def _prefix(event):
        if event.rule:
            params = json.dumps(event.params, ensure_ascii=False, sort_keys=True)
            return f'  Fix: {event.rule} - {event.title} - {params}'
        return '  Phase'

    @staticmethod
    def _io_text(event):
        d = event.details
        total, completed = d['total'], d['completed']
        percent = 100.*completed/total if total else 100.
        text = f"{d['operation']} : {d['variable']} — {percent:.1f}% ({completed}/{total} valeurs)"
        if total*d['itemsize'] >= 1024*1024:
            text += f" [{completed*d['itemsize']/1024**3:.2f}/{total*d['itemsize']/1024**3:.2f} Gio bruts]"
        return text

    def __call__(self, event):
        if self.dashboard is not None:
            self._log_event(event)
            self.dashboard.update(event, self._io_text)
            return
        if self.parallel:
            child = self.children.get(event.path)
            if child is None:
                child = ConsoleProgress(self.events_log, quiet=self.quiet,
                                        label=Path(event.path).name)
                child.stream, child.log_error = self.stream, self.log_error
                child.text_log = self.text_log
                self.children[event.path] = child
            child(event)
            if (event.kind == 'file' and event.status in ('done', 'failed', 'cancelled')
                    or event.kind == 'error'):
                self.children.pop(event.path, None)
            return
        now = time.monotonic()
        prefix = self._prefix(event)

        # Keep current counters for the final summary, but print only the same
        # observed quarter milestones retained by the human/JSON journals.
        if event.kind == 'io':
            self.io = event
            retained = self._log_event(event)
            if ('réécriture' in event.details['operation'].casefold()
                    or any('réécriture' in phase.phase.casefold() for phase, _ in self.phase_stack)):
                self.rewrite_io[event.details['variable']] = event.details
            if not retained:
                return
        if event.kind != 'io':
            self._log_event(event)

        if event.kind == 'file':
            if event.status == 'start':
                self.file, self.file_started = event, now
                self.phase, self.io = None, None
                self.phase_stack = []
                self._write(f"[{event.details['index']}/{event.details['total']}] En cours")
            elif event.status in ['failed','cancelled']:
                self._write(f"  {'Interrompu' if event.status=='cancelled' else 'Échec'} : {event.details['error']}")
                self.file, self.phase, self.io = None, None, None
                self.phase_stack = []
            else:
                self.file, self.phase, self.io = None, None, None
                self.phase_stack = []
        elif event.kind == 'phase':
            rewrite = 'réécriture' in event.phase.casefold()
            if event.status == 'start':
                self.phase, self.phase_started, self.io = event, now, None
                self.phase_stack.append((event,now))
                if rewrite:
                    self.rewrite_io = {}
                else:
                    self._write(f'{prefix} - {event.phase} - En cours', transient=True)
            else:
                state = 'Étape terminée' if event.status=='done' else 'Interrompu' if event.status=='cancelled' else 'Échec'
                error = f" - {event.details['error']}" if 'error' in event.details else ''
                summary = ''
                if rewrite and self.rewrite_io:
                    size = sum(d['total'] * d['itemsize'] for d in self.rewrite_io.values())
                    summary = f' — {len(self.rewrite_io)} variables — {size / 1024**3:.2f} Gio bruts'
                self._write(f'{prefix} - {event.phase} - {state}{summary} ({event.duration:.2f} s){error}')
                self.phase_stack.pop()
                self.phase, self.phase_started = self.phase_stack[-1] if self.phase_stack else (None,0.)
                self.io = None
        elif event.kind == 'deduplication':
            self._write(f"  {event.details['message']}")
        elif event.kind == 'fix':
            if event.status == 'done':
                suffix = ' (simulation)' if event.details['dry_run'] else ' (déjà conforme)' if not event.details['changed'] else ''
                status = 'Done'+suffix
            elif event.status in ('failed', 'blocked'):
                status = ('Échec' if event.status == 'failed' else 'Bloqué par un prérequis') + ': ' + event.details.get('error', '')
            elif event.status == 'prepared':
                status = 'Préparé — vérifications en attente'
            else:
                status = 'En cours'
            self._write(f'{prefix} - {status}', transient=event.status=='start')
        elif event.kind == 'io' and event.details['total']*event.details['itemsize'] >= 8*1024*1024:
            # Small coordinates are still recorded in the event log.
            # Reserve console block counters for substantial arrays.
            self._write(f'{prefix} - {self._io_text(event)}', transient=True)

