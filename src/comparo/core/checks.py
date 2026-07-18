"""Adapt the one assertion engine's results into the Run tab's ``Check`` rows.

The TUI Run tab shows a flat ✓/✗ per response — a ``reachable`` row plus one row
per gating expectation. This module owns *only* that presentation shape: it holds
no validation logic. Status, schema, and a request's ``response.assert`` profiles
are all decided by :mod:`comparo.core.assertions` (via
:func:`~comparo.core.assertions.request_response_rules`), the same rules
``comparo run`` and the execution planner evaluate — so the Run tab can never
disagree with the CLI about whether a response passed.

Only ``error``-severity rules become ``Check`` rows: the Run tab's pass/fail model
has no place for advisory ``warn`` rules, which ride the Execution and Report tabs
where full-fidelity assertion results are shown.
"""

import dataclasses

from comparo.core.assertions import AssertionResult
from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import request_response_rules
from comparo.core.execute import Execution
from comparo.core.loader import LoadedProject
from comparo.core.models import Request


@dataclasses.dataclass(frozen=True, slots=True)
class Check:
    """One row in the Run tab's flat verdict: a name, a verdict, and a detail."""

    name: str
    ok: bool
    detail: str


def run_checks(project: LoadedProject, request: Request, execution: Execution) -> list[Check]:
    """Validate *execution* against *request*'s whole response contract.

    Delegates to the assertion engine so ``response.status`` / ``response.schema``
    sugar *and* ``response.assert`` profiles are all honoured, then flattens the
    ``error``-severity results into ``Check`` rows with ``reachable`` always first.

    Args:
        project: The loaded project, used to resolve schema / assertion references.
        request: The request whose expectations apply.
        execution: The execution to validate.

    Returns:
        One :class:`Check` per gating expectation; ``reachable`` always first.
    """
    response = execution.response
    if response is None:
        return [Check("reachable", ok=False, detail=execution.error or "no response")]
    checks = [Check("reachable", ok=True, detail=str(response.status))]
    rules = request_response_rules(project, request)
    for result in evaluate_rules(project, rules, execution):
        if result.severity != "error":
            continue  # advisory warns ride the Execution / Report tabs, not the Run gate
        checks.append(Check(_check_name(result), ok=result.ok, detail=result.detail))
    return checks


def _check_name(result: AssertionResult) -> str:
    """Name a check the way the Run tab labels it: the sugar keeps its short name."""
    if result.target == "status" and result.op == "equals":
        return "status"
    if result.op == "schema":
        return "schema"
    return result.label or f"{result.target} {result.op}"


def passed(checks: list[Check]) -> bool:
    """Return whether every check passed.

    Args:
        checks: The checks to summarize.

    Returns:
        ``True`` when the list is non-empty and every check is ``ok``.
    """
    return bool(checks) and all(check.ok for check in checks)
