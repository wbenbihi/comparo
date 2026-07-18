"""Validate an execution's response against a request's declared expectations.

Checks are pure functions over an already-materialized response, so they never
touch the network and stay testable with a hand-built execution.
"""

import dataclasses
import json

import jsonschema

from comparo.core.execute import Execution
from comparo.core.loader import LoadedProject
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.refs import ref_id as _ref_id


@dataclasses.dataclass(frozen=True, slots=True)
class Check:
    """One validation over a response: a name, a verdict, and a detail."""

    name: str
    ok: bool
    detail: str


def run_checks(project: LoadedProject, request: Request, execution: Execution) -> list[Check]:
    """Validate *execution* against *request*'s expected status and schema.

    Args:
        project: The loaded project, used to resolve the schema reference.
        request: The request whose expectations apply.
        execution: The execution to validate.

    Returns:
        One :class:`Check` per applicable expectation; ``reachable`` always first.
    """
    response = execution.response
    if response is None:
        return [Check("reachable", ok=False, detail=execution.error or "no response")]
    checks = [Check("reachable", ok=True, detail=str(response.status))]
    expected = request.spec.response
    if expected is None:
        return checks
    if expected.status is not None:
        matched = response.status == expected.status
        arrow = "==" if matched else "≠"
        checks.append(
            Check("status", ok=matched, detail=f"{response.status} {arrow} {expected.status}")
        )
    schema_body = _schema_body(project, expected.schema)
    if schema_body is not None:
        checks.append(_schema_check(response.body, schema_body))
    return checks


def _schema_body(project: LoadedProject, expected: object) -> object:
    """Resolve a ``response.schema`` slot — a ``{$ref}`` or an inline dict — to a schema.

    Mirrors :func:`comparo.core.assertions._schema_body` so the TUI Run tab and the
    CLI/execution assertion engine agree on inline schemas (a divergence otherwise
    passes in one and fails in the other).
    """
    schema_id = _ref_id(expected)
    if schema_id is not None:
        obj = project.objects.get(schema_id)
        return obj.spec if isinstance(obj, Schema) else None
    return expected if isinstance(expected, dict) else None


def passed(checks: list[Check]) -> bool:
    """Return whether every check passed.

    Args:
        checks: The checks to summarize.

    Returns:
        ``True`` when the list is non-empty and every check is ``ok``.
    """
    return bool(checks) and all(check.ok for check in checks)


def _schema_check(body: bytes, schema: object) -> Check:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return Check("schema", ok=False, detail="response body is not JSON")
    try:
        jsonschema.validate(payload, schema)  # type: ignore[arg-type]
    except jsonschema.ValidationError as error:
        return Check("schema", ok=False, detail=error.message)
    except jsonschema.SchemaError as error:
        return Check("schema", ok=False, detail=f"invalid schema: {error.message}")
    except Exception as error:
        # An unresolvable $ref (or any schema-machinery error) fails the check,
        # never the whole run — parity with the assertions engine.
        return Check("schema", ok=False, detail=f"schema check failed: {error}")
    return Check("schema", ok=True, detail="valid")
