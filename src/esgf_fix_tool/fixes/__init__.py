"""Registry of fix functions.

A metadata fix receives an open, writable ``netCDF4.Dataset`` (a temp copy, never the original)
and the rule parameters. It must be idempotent and return True if it changed something.

    @fix("drop_var_attr")
    def drop_var_attr(ds, var, attr): ...
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Kind = Literal["metadata", "structural"]


@dataclass(frozen=True)
class FixFunction:
    name: str
    kind: Kind
    func: Callable[..., bool]


REGISTRY: dict[str, FixFunction] = {}


def fix(name: str, kind: Kind = "metadata"):
    def register(func):
        if name in REGISTRY:
            raise ValueError(f"fix {name!r} registered twice")
        REGISTRY[name] = FixFunction(name, kind, func)
        return func

    return register


from . import attributes  # noqa: F401  (registers the fixes)
