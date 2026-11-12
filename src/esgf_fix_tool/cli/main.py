"""esgf-fix-tool: fix CMIP7 netCDF files from an esgqa report and a set of rules."""

from __future__ import annotations

from typing import Annotated

import typer

from .. import __version__
from . import apply, classify, plan, seed_rules, deduplicate

app = typer.Typer(help=__doc__, no_args_is_help=True, add_completion=False)


def _version(value: bool):
    if value:
        print(__version__)
        raise typer.Exit()


@app.callback()
def main(version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True)] = False):
    pass


for module in (classify, plan, apply, seed_rules, deduplicate):
    app.command(module.NAME)(module.run)


if __name__ == "__main__":
    app()
