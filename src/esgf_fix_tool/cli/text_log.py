"""Human-readable event journal, written only by the parent process."""

import sys
from datetime import datetime
from pathlib import Path


class TextLog:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None
        self.failed = False

    def _error(self, error):
        if not self.failed:
            print(f'Journal texte indisponible : {error}', file=sys.stderr, flush=True)
        self.failed = True

    def open(self):
        try:
            self.stream = self.path.open('a', encoding='utf-8')
        except OSError as error:
            self._error(error)

    def write(self, event, io_text):
        if self.failed:
            return
        details = event.details
        if event.kind == 'file':
            if event.status == 'start':
                text = f"[{details['index']}/{details['total']}] En cours"
            elif event.status == 'done':
                result = 'modifié' if details.get('changed') else 'déjà conforme'
                text = f"Terminé — {result}" + (' (simulation)' if details.get('dry_run') else '')
            else:
                text = f"{event.status} — {details.get('error', '')}"
        elif event.kind == 'phase':
            state = {'start': 'En cours', 'done': 'Étape terminée',
                     'failed': 'Échec', 'cancelled': 'Interrompu'}.get(event.status, event.status)
            duration = f' ({event.duration:.2f} s)' if event.status != 'start' else ''
            error = f" — {details['error']}" if 'error' in details else ''
            text = f'Phase — {event.phase} — {state}{duration}{error}'
        elif event.kind == 'fix':
            state = {'start': 'En cours', 'prepared': 'Préparé — vérifications en attente',
                     'done': 'Done'}.get(event.status, event.status)
            if event.status == 'done':
                state += ' (simulation)' if details.get('dry_run') else ' (déjà conforme)' if not details.get('changed') else ''
            params = ', '.join(f'{key}={value}' for key, value in sorted(event.params.items()))
            text = f'Fix: {event.rule} — {event.title}' + (f' — {params}' if params else '') + f' — {state}'
        elif event.kind == 'io':
            text = io_text(event)
        elif event.kind == 'error':
            text = details.get('traceback', details.get('error', event.status))
        else:
            text = f'{event.kind} — {event.status}'

        stamp = datetime.now().astimezone().isoformat(timespec='seconds')
        prefix = f'[{stamp}] [{Path(event.path).name}] '
        try:
            # Prefix every traceback line so parallel file output is unambiguous.
            for line in text.splitlines():
                self.stream.write(prefix + line + '\n')
            self.stream.flush()
        except OSError as error:
            self._error(error)

    def close(self):
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError as error:
                self._error(error)
