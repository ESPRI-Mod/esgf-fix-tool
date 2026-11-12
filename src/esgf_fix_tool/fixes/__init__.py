"""Registry of fix functions.

A metadata fix receives a Dataset-compatible view and the rule parameters during preflight.
The writer applies its changes directly to an independent target or includes them in one
combined structural output. A fix must be idempotent and return True if it changed something.

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
    verify_data: bool = False
    after_close: Callable | None = None
    layout_change: bool = False
    data_targets: Callable | None = None
    verify_final: Callable | None = None


REGISTRY: dict[str, FixFunction] = {}


def fix(
    name: str,
    kind: Kind = "metadata",
    *,
    verify_data: bool = False,
    after_close: Callable | None = None,
    layout_change: bool = False,
    data_targets: Callable | None = None,
    verify_final: Callable | None = None,
):
    if layout_change and (not verify_data or after_close is None):
        raise ValueError(
            "A layout change requires data verification and an after-close rewrite/verifier"
        )
    if data_targets is not None and (not verify_data or verify_final is None):
        raise ValueError(
            "Changing coordinate values requires an explicit final verifier"
        )

    def register(func):
        if name in REGISTRY:
            raise ValueError(f"fix {name!r} registered twice")
        REGISTRY[name] = FixFunction(
            name,
            kind,
            func,
            verify_data,
            after_close,
            layout_change,
            data_targets,
            verify_final,
        )
        return func

    return register


from . import attributes  # noqa: F401  (registers the fixes)
from . import coordinates  # noqa: F401
from . import layouts  # noqa: F401
from . import bounds  # noqa: F401
from . import horizontal  # noqa: F401
from . import surface  # noqa: F401
from . import hybrid  # noqa: F401
from . import rectilinear  # noqa: F401
from . import qa_metadata  # noqa: F401
from . import grid_labels  # noqa: F401
from . import reviewed_structures  # noqa: F401
from . import final_coordinates  # noqa: F401
from . import climatology  # noqa: F401
from . import heat_transport  # noqa: F401
from . import hybrid_centering  # noqa: F401
from . import basin_latitude_bounds  # noqa: F401

from . import cleanup  # noqa: F401
