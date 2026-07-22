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
from comparo.core.models import AssertionProfileSpec
from comparo.core.models import AssertionRule
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.outcomes import Provenance
from comparo.core.refs import ref_id as _ref_id
from comparo.core.refs import resolve_sources


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionResult:
    """The outcome of one assertion: which rule, whether it held, and why."""

    target: str
    op: str
    ok: bool
    severity: str
    detail: str
    #: A human label for the rule, e.g. ``status == 200`` or ``latency <= 800ms``.
    label: str = ""
    #: The rule's declared expectation and the observed value, so a report can show
    #: the structured comparison without re-parsing ``detail``. ``actual`` may hold
    #: a secret echoed into a body field, so a sink MUST redact both before
    #: serializing (the report builder does, via ``redaction.redact_tree``).
    expected: object = None
    actual: object = None
    #: Identity and provenance of the rule that produced this result — the engine
    #: stamps it during evaluation so a rules index can attribute every row
    #: without re-deriving composition. ``None`` only on hand-built values.
    ref: "AssertRef | None" = None


@dataclasses.dataclass(frozen=True, slots=True)
class AssertRef:
    """Identity and provenance of one assertion rule line, as written.

    ``profile`` is the owning AssertionProfile's ``metadata.id`` for
    ``origin == "profile"``; ``request`` is the owning request's id for the
    inline ``response.status`` / ``response.schema`` sugar and ``response.assert``
    inline specs. ``index`` is the rule's position within its owning block —
    deliberately NOT composition-relative, so the same written rule keeps one
    identity however profiles compose around it.
    """

    target: str
    op: str
    severity: str
    label: str
    origin: Provenance
    profile: str | None = None
    request: str | None = None
    index: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class SourcedAssertion:
    """A composed assertion rule together with its provenance."""

    rule: AssertionRule
    ref: AssertRef


def _source(
    rules: list[AssertionRule],
    origin: Provenance,
    profile: str | None = None,
    request: str | None = None,
    start: int = 0,
) -> list[SourcedAssertion]:
    return [
        SourcedAssertion(
            rule,
            AssertRef(
                rule.target,
                rule.op,
                rule.severity,
                rule_label(rule),
                origin,
                profile,
                request,
                start + offset,
            ),
        )
        for offset, rule in enumerate(rules)
    ]


_OP_SYMBOL = {"equals": "==", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "matches": "~"}


def rule_label(rule: AssertionRule) -> str:
    """A compact human label for a rule — ``status == 200``, ``latency <= 800ms``."""
    target = rule.target
    if target.startswith(("body:", "header:")):
        target = target.split(":", 1)[1]
    op, value = rule.op, rule.value
    if op == "exists":
        return f"{target} exists"
    if op == "schema":
        reference = _ref_id(value)
        return f"schema {reference.split('.', 1)[-1]}" if reference else f"{target} schema"
    if op == "oneOf":
        options = value if isinstance(value, list) else [value]
        return f"{target} in [{', '.join(str(option) for option in options)}]"
    if op == "between" and isinstance(value, list) and len(value) == 2:
        return f"{target} in {value[0]}..{value[1]}"
    if op == "contains":
        return f"{target} contains {value}"
    symbol = _OP_SYMBOL.get(op, op)
    return f"{target} {symbol} {value}" if value is not None else f"{target} {op}"


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
    return evaluate_rules(project, compose_rules(project, profile), execution)


def evaluate_rules(
    project: LoadedProject, rules: list[SourcedAssertion], execution: Execution
) -> list[AssertionResult]:
    """Evaluate an already-composed list of rules against *execution*.

    Args:
        project: The loaded project (for schema references).
        rules: The composed assertion rules with their provenance.
        execution: The execution whose response is checked.

    Returns:
        One result per rule, each stamped with its rule's :class:`AssertRef`.
    """
    return [
        dataclasses.replace(_evaluate(project, sourced.rule, execution), ref=sourced.ref)
        for sourced in rules
    ]


def passed(results: list[AssertionResult]) -> bool:
    """Whether every ``error``-severity assertion held (``warn`` never fails).

    Args:
        results: The assertion results to summarize.

    Returns:
        ``True`` when no ``error`` rule failed.
    """
    return all(result.ok for result in results if result.severity == "error")


def request_rules(request: Request) -> list[SourcedAssertion]:
    """Compile a request's ``response.status`` / ``response.schema`` sugar to rules.

    These inline shortcuts are exactly equivalent to explicit assertion rules, so
    the assertion engine is the single place that decides pass/fail.

    Args:
        request: The request whose response expectations are compiled.

    Returns:
        The implicit assertion rules (possibly empty), owned by the request.
    """
    response = request.spec.response
    if response is None:
        return []
    rules: list[AssertionRule] = []
    if response.status is not None:
        rules.append(AssertionRule(target="status", op="equals", value=response.status))
    if response.schema is not None:
        rules.append(AssertionRule(target="body", op="schema", value=response.schema))
    owner = request.metadata.id or request.metadata.name
    return _source(rules, "inline", request=owner)


def profiles_to_rules(
    project: LoadedProject, refs: object, request: str | None = None, start: int = 0
) -> list[SourcedAssertion]:
    """Flatten one or more AssertionProfiles (``$use`` or inline) into rules.

    *refs* is a ``response.assert`` / execution ``profiles.assert`` slot: a single
    reference, an inline spec, or a list of either. Each resolved profile's
    ``include`` chain is composed before its own rules, so a referenced base's
    rules precede the overriding ones. Every rule keeps its provenance: the
    owning profile's id, or *request* (the attaching request) for inline specs.
    Inline rules index continuously from *start* across every inline block, so
    two rules in different blocks of one request can never share an identity.
    """
    rules: list[SourcedAssertion] = []
    inline_index = start
    for profile_id, spec in resolve_sources(project, refs, AssertionProfileSpec):
        for reference in spec.include or []:
            identifier = _ref_id(reference)
            included = project.objects.get(identifier) if identifier is not None else None
            if isinstance(included, AssertionProfile):
                rules.extend(compose_rules(project, included))
        if profile_id is not None:
            rules.extend(_source(spec.rules or [], "profile", profile=profile_id))
        else:
            block = _source(spec.rules or [], "inline", request=request, start=inline_index)
            inline_index += len(block)
            rules.extend(block)
    return rules


def request_response_rules(project: LoadedProject, request: Request) -> list[SourcedAssertion]:
    """Compile a request's whole response contract into assertion rules.

    Combines the ``response.status`` / ``response.schema`` sugar with the
    profiles attached via ``response.assert``, so every sink that gates a single
    response — ``comparo run`` and the execution planner — evaluates the same
    rules. Without the ``assert`` half, a ``response.assert`` block would be
    silently ignored on the run path while the execution path honoured it.
    """
    rules = list(request_rules(request))
    response = request.spec.response
    if response is not None:
        owner = request.metadata.id or request.metadata.name
        rules += profiles_to_rules(project, response.assertions, request=owner, start=len(rules))
    return dedupe_rules(rules)


def dedupe_rules(rules: list[SourcedAssertion]) -> list[SourcedAssertion]:
    """Drop identical rules a layered composition can produce twice, keeping order.

    Identity is what the rule CHECKS (target/op/value/severity) — the first
    occurrence keeps its provenance, so a base profile's rule stays attributed
    to the base even when an including profile (or a diamond include) restates
    it. Shared by the run path and the execution planner, so the two can never
    disagree about how many rules a request carries.
    """
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[SourcedAssertion] = []
    for sourced in rules:
        rule = sourced.rule
        signature = (rule.target, rule.op, repr(rule.value), rule.severity)
        if signature not in seen:
            seen.add(signature)
            unique.append(sourced)
    return unique


def compose_rules(project: LoadedProject, profile: AssertionProfile) -> list[SourcedAssertion]:
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
) -> list[SourcedAssertion]:
    identifier = profile.metadata.id
    if identifier is not None:
        if identifier in seen:  # guard against an include cycle
            return []
        seen = seen | {identifier}
    rules: list[SourcedAssertion] = []
    for reference in profile.spec.include or []:
        target = _ref_id(reference)
        included = project.objects.get(target) if target is not None else None
        if isinstance(included, AssertionProfile):
            rules.extend(_resolve_rules(project, included, seen))
    rules.extend(
        _source(profile.spec.rules or [], "profile", profile=identifier or profile.metadata.name)
    )
    return rules


def _evaluate(project: LoadedProject, rule: AssertionRule, execution: Execution) -> AssertionResult:
    response = execution.response
    if response is None:
        detail = execution.error or "no response"
        return AssertionResult(
            rule.target,
            rule.op,
            False,
            rule.severity,
            detail,
            rule_label(rule),
            expected=rule.value,
            actual=None,
        )
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

    label = rule_label(rule)

    def result(ok: bool, detail: str) -> AssertionResult:
        return AssertionResult(
            rule.target, op, ok, rule.severity, detail, label, expected=expected, actual=actual
        )

    if op == "exists":
        return result(present and actual is not None, "present" if present else "missing")
    if not present:
        return result(op == "exists", f"{rule.target} missing")
    if op == "equals":
        return result(actual == expected, f"{_short(actual)} == {_short(expected)}")
    if op == "matches":
        try:
            ok = re.search(str(expected), str(actual)) is not None
        except re.error as error:
            return result(False, f"invalid regex /{expected}/: {error}")
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
    except Exception as error:
        # An unresolvable $ref (or any schema-machinery error) fails the rule,
        # never the whole run.
        return result(False, f"schema check failed: {error}")
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
            try:
                index = int(segment[1:-1])
            except ValueError:
                # A wildcard or quoted index (``[*]``, ``["key"]``) is not a real
                # subscript — the path simply doesn't resolve, it isn't a crash.
                return None, False
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
    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(ms|s|m|h)?\s*", text)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or "ms"
    return {"ms": amount, "s": amount * 1000, "m": amount * 60000, "h": amount * 3600000}[unit]


def _contains(actual: object, expected: object) -> bool:
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, list | dict):
        return expected in actual
    return False


def _short(value: object) -> str:
    # Full rendering: an assertion detail must not be truncated before a sink can
    # redact it, else a long secret's prefix would survive masking. Sinks truncate
    # after redaction for brevity.
    return json.dumps(value, ensure_ascii=False, default=str)
