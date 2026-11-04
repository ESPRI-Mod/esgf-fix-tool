"""One-off import: the 2026-10-29 Excel triage → a rules YAML file.

The Excel rows hold a *cluster* template (``'{A}' not found…``, with a "(N occurrences, e.g. …)" suffix), the
team's action (Corriger / Ignorer / Vérifier / Déjà corrigé / À qualifier) and a comment. Each row becomes a
rule; rows that only differ by a quoted name (one per variable) and share the same decision are merged into
one generic rule. Generated rules have ``fix: null``: a human writes the fix function and the parameters.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import yaml

SHEETS = {"Sévérité 3": 3, "Sévérité 2": 2, "Sévérité 1": 1}

ACTIONS = {
    "Corriger": "fix",
    "Déjà corrigé": "fix",
    "Ignorer": "waive",
    "Vérifier": "review",
    "À qualifier": "review",
}

# checks handled by another pipeline step, whatever the Excel says
OVERRIDES = [
    (re.compile(r"\[PATH00[123]\]"), "defer", "checked on the DRS draft tree (final QA)"),
    (re.compile(r"\[FILE004"), "other_step", "fixed by the repack step (cmip7repack)"),
    (re.compile(r"^\[Continuity\]"), "upstream_only", "gap between files: dataset level, not a per-file fix"),
]

SUFFIX = re.compile(r"\s*\(\d+ occurrences?, e\.g\..*$", re.DOTALL)
PLACEHOLDER = re.compile(r"\\\{([A-Z])\\\}")
QUOTED = re.compile(r"'[^'{}]+'")


def _escape(text: str) -> str:
    """re.escape, but keep spaces, '-' and a few others readable (none of them is special outside [])."""
    out = re.escape(text)
    for ch in " -#&~,'\"":
        out = out.replace("\\" + ch, ch)
    return out


def template_to_regex(template: str, generalize_quotes: bool) -> str:
    text = SUFFIX.sub("", template).strip()
    if generalize_quotes:
        parts = QUOTED.split(text)
        quoted = QUOTED.findall(text)
        out = _escape(parts[0])
        for i, (_, part) in enumerate(zip(quoted, parts[1:])):
            out += f"'(?P<q{i}>[^']+)'" + _escape(part)
    else:
        out = _escape(text)
    seen: set[str] = set()

    def group(m: re.Match) -> str:
        name = m.group(1)
        if name in seen:
            return f"(?P={name})"
        seen.add(name)
        return f"(?P<{name}>.+?)"

    out = PLACEHOLDER.sub(group, out)
    return re.sub(r"Standard Name Table v\d+", r"Standard Name Table v\\d+", out)


def read_excel(path: str | Path) -> list[dict]:
    import openpyxl  # optional dependency: esgf-fix-tool[seed]

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for sheet, severity in SHEETS.items():
        for r in list(wb[sheet].iter_rows(values_only=True))[1:]:
            if not r or not r[0] or not r[2]:
                continue
            rows.append({"severity": severity, "check": r[0], "template": r[2],
                         "excel_action": r[5], "comment": r[6]})
    return rows


def build_rules(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["severity"], template_to_regex(row["check"], True), template_to_regex(row["template"], True))
        groups[key].append(row)

    rules = []
    for (severity, check_re, message_re), members in groups.items():
        decisions = {(m["excel_action"], m["comment"]) for m in members}
        if len(decisions) == 1:
            rules.append(_rule(members[0], severity, check_re, message_re, len(members)))
        else:  # same template, different decisions: keep one exact rule per row
            for m in members:
                rules.append(_rule(m, severity, template_to_regex(m["check"], False),
                                   template_to_regex(m["template"], False), 1))

    seen, unique = set(), []
    for r in rules:
        key = (r["severity"], r["check"], r["message"], r["action"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    rules = unique
    rules.sort(key=lambda r: (-r["severity"], r["check"]))
    for i, r in enumerate(rules, 1):
        r["id"] = f"S{i:03d}"
    return [{"id": r.pop("id"), **r} for r in rules]


def _rule(row: dict, severity: int, check_re: str, message_re: str, merged: int) -> dict:
    action = ACTIONS.get(row["excel_action"], "review")
    note = row["comment"]
    for pattern, forced, why in OVERRIDES:
        if pattern.search(row["check"]):
            # handled by another step whatever the message says: match ANY message of that check (the Excel template
            # holds values of the simulation it was made on, e.g. its file path: pilot run #1, 2026-11-04)
            action, note, message_re = forced, why, ".*"
    rule = {"check": check_re, "message": message_re, "action": action, "fix": None,
            "severity": severity, "excel_action": row["excel_action"], "note": note}
    if row["excel_action"] == "Déjà corrigé":
        rule["upstream_status"] = "fixed_upstream"
    if merged > 1:
        rule["merged_rows"] = merged
    return rule


def write_rules(rules: list[dict], path: str | Path, source: str) -> None:
    header = (f"# Generated by `esgf-fix-tool seed-rules` from {source}.\n"
              "# Do not edit by hand: copy a rule to a curated file, write its fix, and put that file first.\n")
    Path(path).write_text(header + yaml.safe_dump(rules, sort_keys=False, allow_unicode=True, width=200))
