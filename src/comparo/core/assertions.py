"""Evaluate an ``AssertionProfile`` against one execution — the assertion sink.

Where :mod:`comparo.core.diff` compares two responses, this checks a single
response against declared expectations. A rule addresses a *target* (status,
latency, a header, a JSON-path in the body, …) with an *op* (equals, matches,
lt/lte/gt/gte, between, oneOf, exists, contains, schema). ``error`` rules fail
the gate; ``warn`` rules are advisory. Like the rest of the engine it is a pure
function over an already-materialized response and never touches the network.
"""

import dataclasses
import json
import re
from collections.abc import Callable

import jsonschema

from comparo.core.execute import Execution
from comparo.core.loader import LoadedProject
from comparo.core.models import AssertionProfile
from comparo.core.models import AssertionRule
from comparo.core.models import Request
from comparo.core.models import Schema


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionResult:
    """The outcome of one assertion: which rule, whether it held, and why."""

    target: str
    op: str
    ok: bool
    severity: str
    detail: str


#: A bound constructor for the current rule's result: ``(ok, detail) -> result``.
_Result = Callable[[bool, str], AssertionResult]


def run_assertions(
    project: LoadedProject, profile: AssertionProfile, execution: Execution
) -> list[AssertionResult]:
    """Evaluate *profile* (and everything it includes) against *execution*.

    Args:
        project: The loaded project, used to resolve schema and include references.
        profile: The assertion profile to evaluate.
        execution: The execution whose response is checked.

    Returns:
        One :class:`AssertionResult` per resolved rule, in evaluation order.
    """
    return evaluate_rules(project, _resolve_rules(project, profile, set()), execution)


def evaluate_rules(
    project: LoadedProject, rules: list[AssertionRule], execution: Execution
) -> list[AssertionResult]:
    """Evaluate an already-composed list of rules against *execution*.

    Args:
        project: The loaded project (for schema references).
        rules: The assertion rules to evaluate.
        execution: The execution whose response is checked.

    Returns:
        One result per rule.
    """
    return [_evaluate(project, rule, execution) for rule in rules]


def passed(results: list[AssertionResult]) -> bool:
    """Whether every ``error``-severity assertion held (``warn`` never fails).

    Args:
        results: The assertion results to summarize.

    Returns:
        ``True`` when no ``error`` rule failed.
    """
    return all(result.ok for result in results if result.severity == "error")


def request_rules(request: Request) -> list[AssertionRule]:
    """Compile a request's ``response.status`` / ``response.schema`` sugar to rules.

    These inline shortcuts are exactly equivalent to explicit assertion rules, so
    the assertion engine is the single place that decides pass/fail.

    Args:
        request: The request whose response expectations are compiled.

    Returns:
        The implicit assertion rules (possibly empty).
    """
    response = request.spec.response
    if response is None:
        return []
    rules: list[AssertionRule] = []
    if response.status is not None:
        rules.append(AssertionRule(target="status", op="equals", value=response.status))
    if response.schema is not None:
        rules.append(AssertionRule(target="body", op="schema", value=response.schema))
    return rules


def compose_rules(project: LoadedProject, profile: AssertionProfile) -> list[AssertionRule]:
    """Return *profile*'s rules flattened with everything it includes.

    Args:
        project: The loaded project (to resolve ``include`` references).
        profile: The assertion profile to flatten.

    Returns:
        The composed rule list, includes first.
    """
    return _resolve_rules(project, profile, set())


def _resolve_rules(
    project: LoadedProject, profile: AssertionProfile, seen: set[str]
) -> list[AssertionRule]:
    identifier = profile.metadata.id
    if identifier is not None:
        if identifier in seen:  # guard against an include cycle
            return []
        seen = seen | {identifier}
    rules: list[AssertionRule] = []
    for reference in profile.spec.include or []:
        target = _ref_id(reference)
        included = project.objects.get(target) if target is not None else None
        if isinstance(included, AssertionProfile):
            rules.extend(_resolve_rules(project, included, seen))
    rules.extend(profile.spec.rules or [])
    return rules


def _evaluate(project: LoadedProject, rule: AssertionRule, execution: Execution) -> AssertionResult:
    response = execution.response
    if response is None:
        detail = execution.error or "no response"
        return AssertionResult(rule.target, rule.op, False, rule.severity, detail)
    actual, present = _target(rule.target, response, execution)
    return _apply(project, rule, actual, present)


def _target(target: str, response: object, execution: Execution) -> tuple[object, bool]:
    status = getattr(response, "status", None)
    headers = getattr(response, "headers", []) or []
    body = getattr(response, "body", b"") or b""
    if target == "status":
        return status, True
    if target == "latency":
        return getattr(response, "elapsed_ms", None), True
    if target == "contentType":
        return _header(headers, "content-type")
    if target == "bodyRaw":
        return _decode(body), True
    if target.startswith("header:"):
        return _header(headers, target.split(":", 1)[1])
    if target == "body" or target.startswith("body:"):
        parsed = _json(body)
        if parsed is _MISSING:
            return None, False
        if target == "body":
            return parsed, True
        return _at_path(parsed, target.split(":", 1)[1])
    return None, False


def _apply(
    project: LoadedProject, rule: AssertionRule, actual: object, present: bool
) -> AssertionResult:
    op, expected = rule.op, rule.value

    def result(ok: bool, detail: str) -> AssertionResult:
        return AssertionResult(rule.target, op, ok, rule.severity, detail)

    if op == "exists":
        return result(present and actual is not None, "present" if present else "missing")
    if not present:
        return result(op == "exists", f"{rule.target} missing")
    if op == "equals":
        return result(actual == expected, f"{_short(actual)} == {_short(expected)}")
    if op == "matches":
        ok = re.search(str(expected), str(actual)) is not None
        return result(ok, f"{_short(actual)} ~ /{expected}/")
    if op in ("lt", "lte", "gt", "gte"):
        return _numeric(op, actual, expected, rule.target, result)
    if op == "between":
        return _between(actual, expected, rule.target, result)
    if op == "oneOf":
        options = expected if isinstance(expected, list) else [expected]
        return result(actual in options, f"{_short(actual)} in {_short(options)}")
    if op == "contains":
        return result(_contains(actual, expected), f"{_short(actual)} contains {_short(expected)}")
    if op == "schema":
        return _schema(project, actual, expected, result)
    return result(False, f"unknown op '{op}'")


def _numeric(
    op: str, actual: object, expected: object, target: str, result: _Result
) -> AssertionResult:
    left, right = _number(actual, target), _number(expected, target)
    if left is None or right is None:
        return result(False, f"not numeric: {_short(actual)} {op} {_short(expected)}")
    ok = {"lt": left < right, "lte": left <= right, "gt": left > right, "gte": left >= right}[op]
    return result(ok, f"{_short(actual)} {op} {_short(expected)}")


def _between(actual: object, expected: object, target: str, result: _Result) -> AssertionResult:
    if not isinstance(expected, list) or len(expected) != 2:
        return result(False, "between wants [min, max]")
    value = _number(actual, target)
    low, high = _number(expected[0], target), _number(expected[1], target)
    if value is None or low is None or high is None:
        return result(False, f"not numeric: {_short(actual)}")
    return result(low <= value <= high, f"{_short(low)} ≤ {_short(actual)} ≤ {_short(high)}")


def _schema(
    project: LoadedProject, actual: object, expected: object, result: _Result
) -> AssertionResult:
    schema = _schema_body(project, expected)
    if schema is None:
        return result(False, "no schema to validate against")
    try:
        jsonschema.validate(actual, schema)  # type: ignore[arg-type]
    except jsonschema.ValidationError as error:
        return result(False, error.message)
    except jsonschema.SchemaError as error:
        return result(False, f"invalid schema: {error.message}")
    return result(True, "schema valid")


def _schema_body(project: LoadedProject, expected: object) -> object:
    identifier = _ref_id(expected)
    if identifier is not None:
        obj = project.objects.get(identifier)
        return obj.spec if isinstance(obj, Schema) else None
    return expected if isinstance(expected, dict) else None


_MISSING = object()


def _json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return _MISSING


def _decode(body: bytes) -> str:
    try:
        return body.decode()
    except (UnicodeDecodeError, AttributeError):
        return str(body)


def _header(headers: object, name: str) -> tuple[object, bool]:
    if isinstance(headers, list):
        for key, value in headers:
            if str(key).lower() == name.lower():
                return value, True
    return None, False


def _at_path(body: object, path: str) -> tuple[object, bool]:
    current = body
    for segment in _segments(path):
        if segment.startswith("[") and segment.endswith("]"):
            index = int(segment[1:-1])
            if isinstance(current, list) and -len(current) <= index < len(current):
                current = current[index]
            else:
                return None, False
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None, False
    return current, True


def _segments(path: str) -> list[str]:
    trimmed = path.removeprefix("$").removeprefix(".")
    normalized = trimmed.replace("[", ".[")
    return [segment for segment in normalized.split(".") if segment]


def _number(value: object, target: str) -> float | None:
    if target == "latency" and isinstance(value, str):
        return _duration_ms(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _duration_ms(text: str) -> float | None:
    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(ms|s|m)?\s*", text)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or "ms"
    return {"ms": amount, "s": amount * 1000, "m": amount * 60000}[unit]


def _contains(actual: object, expected: object) -> bool:
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, list | dict):
        return expected in actual
    return False


def _ref_id(reference: object) -> str | None:
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None


def _short(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    return rendered if len(rendered) <= 40 else f"{rendered[:37]}..."
