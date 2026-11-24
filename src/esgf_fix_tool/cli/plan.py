"""Write the fix plan (JSON) for a report."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer

from ..classify import Outcome
from ..plan import FixPlan
from .catalogues import resolve_catalogues
from .common import Final, Previous, Report, classification

NAME = "plan"


def _print_diagnostics(classified):
    """Count distinct non-automatic diagnostics per file across the full report."""
    groups = defaultdict(set)
    for item in classified.items:
        if item.outcome is not Outcome.FIX:
            f = item.finding
            groups[item.outcome.value].add((f.path, f.check, f.message))
    for finding, _ in classified.known_errors:
        groups[Outcome.WAIVED.value].add((finding.path, finding.check, finding.message))
    for finding in classified.unknown_errors:
        groups['checker_error'].add((finding.path, finding.check, finding.message))

    ignored = groups.pop(Outcome.WAIVED.value, set())
    pending = set().union(*groups.values()) if groups else set()
    details = ', '.join(f'{status}={len(records)}' for status, records in sorted(groups.items()) if records)
    print(f"Rapport QA : {len(ignored)} diagnostics ignorés (waive) sur "
          f"{len({r[0] for r in ignored})} fichiers ; "
          f"{len(pending)} diagnostics en attente ou à traiter ailleurs sur "
          f"{len({r[0] for r in pending})} fichiers"
          + (f" ({details})" if details else ""))


def run(
    report: Report,
    rules: Annotated[list[str] | None, typer.Option('--rules', '-r',
        help='catalogue path or rule ID, repeatable; default: all project catalogues')] = None,
    previous: Previous = None,
    final: Final = False,
    output: Annotated[Path, typer.Option("--output", "-o", help="plan file")] = Path("fixplan.json"),
    map_path: Annotated[str | None, typer.Option(
        help="OLD=NEW path prefix, e.g. the report was made on the source, the fixes go to staging")] = None,
    only_rule: Annotated[list[str] | None, typer.Option(
        "--only-rule", help="keep fixes from this rule only (repeatable, e.g. --only-rule C001)")] = None,
    exclude_rule: Annotated[list[str] | None, typer.Option(
        '--exclude-rule', help='exclude fixes by exact rule ID (repeatable, e.g. --exclude-rule C103c)')] = None,
    report_only: Annotated[bool, typer.Option(
        '--report-only', help='offline candidates from QA only; do not open NetCDFs or claim confirmed changes')] = False,
    break_hardlinks: Annotated[bool, typer.Option(
        '--break-hardlinks', help='prepare with the same option as apply; no links are broken during plan')] = False,
):
    """Write the fix plan (JSON) for a report."""
    paths, requested = resolve_catalogues(rules)
    classified = classification(report, paths, previous, final)
    p = FixPlan.from_classification(classified)
    known = {r.id for r in classified.rules.rules}
    for values, option in ((requested, '--rules'), (set(only_rule or ()), '--only-rule'),
                           (set(exclude_rule or ()), '--exclude-rule')):
        if unknown := values - known:
            raise typer.BadParameter(f"unknown rule ids: {', '.join(sorted(unknown))}", param_hint=option)
    selected = requested or known
    if only_rule:
        selected &= set(only_rule)
    selected -= set(exclude_rule or ())
    # Keep excluded automatic steps visible: a filtered empty plan is not a clean QA.
    excluded = {path: [s for s in steps if s.rule not in selected] for path, steps in p.files.items()}
    excluded = {path: steps for path, steps in excluded.items() if steps}
    # Filter planned steps, never the matching catalogue: an excluded finding must
    # not fall through to a broader fallback rule and reappear under another identifier.
    p.files = {path: [s for s in steps if s.rule in selected] for path, steps in p.files.items()}
    p.files = {path: steps for path, steps in p.files.items() if steps}
    if map_path:
        old, sep, new = map_path.partition("=")
        if not sep or not old or not new:
            raise typer.BadParameter("expected OLD=NEW", param_hint="--map-path")
        print(f"{p.map_paths(old, new)} of {len(p.files)} paths mapped {old} → {new}")
    if report_only:
        p.preflight = dict(mode='report-only')
        print(f"{sum(len(s) for s in p.files.values())} fixes candidats sur {len(p.files)} fichiers "
              f"(non vérifiés) → {output}")
    else:
        from ..plan_preflight import verify_plan

        with typer.progressbar(list(p.files.items()), label='Vérification des NetCDF sans écriture') as files:
            counts = verify_plan(p, files, break_hardlinks=break_hardlinks)
        print(f"{counts['changed_steps']} fixes à appliquer sur {counts['changed_files']} fichiers → {output}")
        print(f"Déjà conformes : {counts['conforming_files']} fichiers retirés du plan ; "
              f"{counts['noop_steps']} étapes sans changement")
        if counts.get('cleanup_files'):
            print(f"Produits inutiles : {counts['cleanup_files']} fichiers réservés au nettoyage "
                  "lors de apply/deduplicate (aucune suppression pendant plan)")
        if counts['unverified_files']:
            print(f"À vérifier : {counts['unverified_steps']} fixes sur {counts['unverified_files']} fichiers "
                  "non vérifiés ou en échec, conservés dans le plan")
            for path, record in p.preflight['files'].items():
                if record['status'] == 'unverified':
                    print(f"  {Path(path).name} : {record['error']}")
                elif record['status'] == 'partial':
                    for step in record['steps']:
                        if step.get('status') in ('failed', 'blocked'):
                            print(f"  {Path(path).name} — {step['rule']} : {step.get('error', step['status'])}")
    p.dump(output)
    _print_diagnostics(classified)
    if excluded:
        print(f"Filtres : {sum(len(s) for s in excluded.values())} fixes non sélectionnés "
              f"sur {len(excluded)} fichiers")
    if p.deduplicate_root:
        print(f"Automatic ATM/SRF deduplication + unused CHM product cleanup during apply → {p.deduplicate_root}")
