"""Event-driven terminal view; no timer, threads, or extra log files."""

import shutil
import sys
import time
from pathlib import Path


def _fit(value, width):
    text = str(value).replace("\n", " ").replace("\r", " ").replace("\x1b", "")
    if len(text) <= width:
        return text
    left = max(0, (width - 1) // 2)
    return text[:left] + "…" + text[-(width - left - 1):] if width > 1 else "…"


class Dashboard:
    def __init__(self):
        self.active = {}
        self.finished = set()
        self.total = self.changed = self.unchanged = self.failed = 0
        self.lines = 0
        self.last_render = 0.
        self.maintenance = None

    def clear(self):
        if self.lines:
            sys.stdout.write(f"\x1b[{self.lines}A\r\x1b[J")
            sys.stdout.flush()
            self.lines = 0

    def update(self, event, io_text):
        if event.kind == "deduplication":
            self.maintenance = event.details["message"]
        elif event.kind == "phase" and event.phase == "Dédoublonnage ATM/SRF":
            if event.status == "start":
                self.maintenance = event.phase + " — En cours"
            elif event.status in ("failed", "cancelled"):
                self.maintenance = event.phase + " — " + event.details.get("error", event.status)
        if event.kind == "file":
            if event.status == "start":
                self.total = event.details["total"]
                self.active[event.path] = "Ouverture / lecture du schéma"
            elif event.path not in self.finished:
                self.finished.add(event.path)
                self.active.pop(event.path, None)
                if event.status == "done":
                    if event.details.get("changed"):
                        self.changed += 1
                    else:
                        self.unchanged += 1
                else:
                    self.failed += 1
        elif event.path in self.active:
            if event.kind == "io":
                self.active[event.path] = io_text(event)
            elif event.kind == "phase":
                self.active[event.path] = event.phase
            elif event.kind == "fix":
                suffix = " — Done" if event.status == "done" else ""
                self.active[event.path] = f"{event.rule} — {event.title}{suffix}"
        self.render(force=event.kind == "file")

    def render(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_render < .1:
            return
        self.last_render = now
        size = shutil.get_terminal_size(fallback=(120, 24))
        width = max(1, size.columns - 1)  # Never wrap into the next terminal row.
        lines = [f"Terminés : {len(self.finished)}/{self.total} — Modifiés : {self.changed}"
                 f" — Déjà conformes : {self.unchanged} — Échecs : {self.failed}"]
        if self.maintenance:
            lines.append(self.maintenance)
        visible = max(1, size.lines - 4)
        filename_width = max(1, (width - 3) // 2)
        phase_width = max(1, width - filename_width - 3)
        for path, phase in list(self.active.items())[:visible]:
            lines.append(f"{_fit(Path(path).name, filename_width):<{filename_width}} | {_fit(phase, phase_width)}")
        if len(self.active) > visible:
            lines.append(f"… {len(self.active) - visible} autres fichiers en cours")
        self.clear()
        sys.stdout.write("\n".join(_fit(line, width) for line in lines) + "\n")
        sys.stdout.flush()
        self.lines = len(lines)


class DashboardStderr:
    """Clear the live view before a traceback, then let stderr work normally."""
    def __init__(self, stream, dashboard):
        self.stream, self.dashboard = stream, dashboard

    def write(self, text):
        if text:
            self.dashboard.clear()
        return self.stream.write(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)
