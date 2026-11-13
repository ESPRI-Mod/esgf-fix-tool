"""Flush file, recipe and actual I/O progress; no timer or periodic message."""
import json
import shutil
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path


def hms(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}'


class ConsoleProgress:
    def __init__(self, events_log, *, quiet=False):
        self.events_log = Path(events_log)
        self.quiet = quiet
        self.file = None
        self.phase = None
        self.phase_stack = []
        self.io = None
        self.last_io_key = None
        self.last_io_print = 0.
        self.transient = False
        self.stream = None
        self.log_error = False

    def __enter__(self):
        try:
            self.stream = self.events_log.open('a')
        except OSError as exc:
            self.log_error = True
            print(f'Journal de progression indisponible : {exc}', file=sys.stderr, flush=True)
        return self

    def __exit__(self, *exc):
        if self.transient and not self.quiet:
            print(flush=True)
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError as error:
                if not self.log_error:
                    print(f'Journal de progression indisponible : {error}', file=sys.stderr, flush=True)

    def _write(self, text, *, transient=False):
        if self.quiet:
            return
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
        now = time.monotonic()
        prefix = self._prefix(event)

        # Keep the latest real I/O counters, but throttle large block loops.
        if event.kind == 'io':
            self.io = event
            key = (event.path,event.phase,event.rule,event.details['variable'],event.details['operation'])
            if key == self.last_io_key and now-self.last_io_print < 1. and event.details['completed'] != event.details['total']:
                return
            self.last_io_key, self.last_io_print = key, now
        self._log(asdict(event))

        if event.kind == 'file':
            if event.status == 'start':
                self.file, self.file_started = event, now
                self.phase, self.io = None, None
                self.phase_stack = []
                self._write(f"[{event.details['index']}/{event.details['total']}] En cours - {event.path}")
            elif event.status in ['failed','cancelled']:
                self._write(f"  {'Interrompu' if event.status=='cancelled' else 'Échec'} : {event.details['error']}")
                self.file, self.phase, self.io = None, None, None
                self.phase_stack = []
            else:
                self.file, self.phase, self.io = None, None, None
                self.phase_stack = []
        elif event.kind == 'phase':
            if event.status == 'start':
                self.phase, self.phase_started, self.io = event, now, None
                self.phase_stack.append((event,now))
                self._write(f'{prefix} - {event.phase} - En cours', transient=True)
            else:
                state = 'Étape terminée' if event.status=='done' else 'Interrompu' if event.status=='cancelled' else 'Échec'
                error = f" - {event.details['error']}" if 'error' in event.details else ''
                self._write(f'{prefix} - {event.phase} - {state} ({event.duration:.2f} s){error}')
                self.phase_stack.pop()
                self.phase, self.phase_started = self.phase_stack[-1] if self.phase_stack else (None,0.)
                self.io = None
        elif event.kind == 'fix':
            if event.status == 'done':
                suffix = ' (simulation)' if event.details['dry_run'] else ' (déjà conforme)' if not event.details['changed'] else ''
                status = 'Done'+suffix
            elif event.status == 'prepared':
                status = 'Préparé — vérifications en attente'
            else:
                status = 'En cours'
            self._write(f'{prefix} - {status}', transient=event.status=='start')
        elif event.kind == 'io' and event.details['total']*event.details['itemsize'] >= 8*1024*1024:
            # Small coordinates are still recorded in the event log.
            # Reserve console block counters for substantial arrays.
            self._write(f'{prefix} - {self._io_text(event)}', transient=True)

