"""The shared verdict and outcome vocabulary for RUN, DIFF, and EXECUTION.

DiffProfile rules and assertion rules grade every check against a cell with the
same five-state outcome and roll up to the same tri-state verdict. This module
is the single home of that vocabulary — the diff engine, the assertion engine,
the report builder, and the TUI all speak it, so the tabs can never drift apart
on what the words mean.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable
from typing import Literal

#: How one rule fared against one cell. ``silenced`` is diff-only (assertions
#: have no ignore mode). An advisory break is ``broke`` on a ``warn``-severity
#: rule and a tolerance absorb is ``held`` within the band — annotations, never
#: extra states. ``error`` means the cell produced nothing comparable, so the
#: rule was never judged; ``absent`` means the rule was in scope but its target
#: path was not present.
CheckOutcome = Literal["held", "broke", "silenced", "absent", "error"]

#: Where a rule came from — one display grammar everywhere:
#: ``profile <name>`` · ``inline · <request>`` · ``default`` · ``synthetic``.
Provenance = Literal["profile", "inline", "default", "synthetic"]


class Verdict(enum.StrEnum):
    """The tri-state verdict — one vocabulary for cells, runs, and gates."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    """Fold verdicts with the locked precedence.

    ``FAIL`` whenever anything broke; ``ERROR`` only when errors are the only
    failure; an empty fold fails closed — judging nothing is never a pass.
    """
    seen = set(verdicts)
    if Verdict.FAIL in seen:
        return Verdict.FAIL
    if Verdict.ERROR in seen:
        return Verdict.ERROR
    return Verdict.PASS if seen else Verdict.FAIL


def provenance_label(origin: Provenance, name: str | None = None) -> str:
    """Render provenance in the one grammar every surface uses.

    ``name`` is the profile name for ``profile`` rules and the owning request
    for ``inline`` rules; ``default`` and ``synthetic`` carry no name.
    """
    if origin == "profile":
        return f"profile {name}" if name else "profile"
    if origin == "inline":
        return f"inline · {name}" if name else "inline"
    return origin


@dataclasses.dataclass(frozen=True, slots=True)
class CheckTally:
    """Per-rule outcome counts across the cells a rule touched.

    ``absorbed`` counts held checks a tolerance band absorbed; ``warn_broke`` /
    ``warn_held`` split warn-severity rules out of ``broke``/``held`` so an
    advisory never reads as a gate failure. "Unused" (the rule matched nothing
    anywhere) is derived — every count zero — and never stored.
    """

    broke: int = 0
    held: int = 0
    silenced: int = 0
    absent: int = 0
    error: int = 0
    absorbed: int = 0
    warn_broke: int = 0
    warn_held: int = 0
