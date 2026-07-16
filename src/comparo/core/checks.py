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
    schema_id = _ref_id(expected.schema)
    schema = project.objects.get(schema_id) if schema_id else None
    if isinstance(schema, Schema):
        checks.append(_schema_check(response.body, schema))
    return checks


def passed(checks: list[Check]) -> bool:
    """Return whether every check passed.

    Args:
        checks: The checks to summarize.

    Returns:
        ``True`` when the list is non-empty and every check is ``ok``.
    """
    return bool(checks) and all(check.ok for check in checks)


def _schema_check(body: bytes, schema: Schema) -> Check:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return Check("schema", ok=False, detail="response body is not JSON")
    try:
        jsonschema.validate(payload, schema.spec)
    except jsonschema.ValidationError as error:
        return Check("schema", ok=False, detail=error.message)
    except jsonschema.SchemaError as error:
        return Check("schema", ok=False, detail=f"invalid schema: {error.message}")
    return Check("schema", ok=True, detail="valid")


def _ref_id(reference: object) -> str | None:
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None
