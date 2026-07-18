"""Compare two response trees under a diff profile — the tri-state core.

Every path resolves to one of three states: ``same`` (compared, identical),
``drift`` (compared, different — a regression), or ``skip`` (a path the profile
deliberately ignores). A skipped field is never "same": the tool says out loud
what it chose not to check.

Modes: ``ignore`` (skip), ``exact`` (recurse; leaf values must be equal, arrays
same length), ``shape`` (recurse; leaf values ignored, arrays length-tolerant),
``type`` (same JSON type, no recursion), and ``tolerance`` (numbers within ±).
Both ``exact`` and ``shape`` recurse, so a more specific ``ignore`` rule can
carve a hole out of an otherwise-exact tree. The most-specific rule whose path
is a prefix of the current path wins; unlisted paths fall to the default.
"""

import dataclasses
import enum
import json

from comparo.core.models import DiffRule


class State(enum.Enum):
    """The comparison outcome for one path."""

    SAME = "same"
    DRIFT = "drift"
    SKIP = "skip"


@dataclasses.dataclass(frozen=True, slots=True)
class FieldDiff:
    """The comparison outcome at one path.

    ``baseline``/``candidate`` are the raw values compared at this path (``None``
    when the field was not compared — an ``ignore`` skip or a max-depth cut), so a
    report can show the structured "was → now" without re-parsing ``detail``. They
    may hold a secret a server echoed back, so a sink MUST redact them before
    serializing (the report builder does, via ``redaction.redact_tree``).
    ``rule`` is the ``DiffRule.path`` of the profile rule that governed the field,
    or ``None`` when it fell to the default mode.
    """

    path: str
    state: State
    mode: str
    detail: str = ""
    baseline: object = None
    candidate: object = None
    rule: str | None = None


#: A recursion cap so a pathologically deep body compares as a leaf instead of
#: overflowing the stack; far beyond any realistic API payload nesting.
_MAX_DEPTH = 200


@dataclasses.dataclass(frozen=True, slots=True)
class _Rule:
    segments: tuple[str, ...]
    mode: str
    array_length: str | None
    tolerance: float | None
    path: str  # the rule's original DiffRule.path, surfaced onto matched FieldDiffs


def diff(
    baseline: object, candidate: object, default_mode: str, rules: list[DiffRule]
) -> list[FieldDiff]:
    """Compare *baseline* against *candidate* under a default mode and rules.

    Args:
        baseline: The baseline response tree (parsed JSON).
        candidate: The candidate response tree.
        default_mode: The mode for paths no rule matches.
        rules: The profile's path-scoped rules.

    Returns:
        One :class:`FieldDiff` per compared or skipped leaf, in tree order.
    """
    # Most specific (longest path) first; among equal-length paths the later-loaded
    # rule wins, so an execution-level override can re-check what a request ignored.
    compiled = [
        rule
        for _, rule in sorted(
            ((index, _compile(rule)) for index, rule in enumerate(rules)),
            key=lambda pair: (len(pair[1].segments), pair[0]),
            reverse=True,
        )
    ]
    return _walk(baseline, candidate, (), compiled, default_mode)


def _walk(
    baseline: object,
    candidate: object,
    path: tuple[str, ...],
    rules: list[_Rule],
    default_mode: str,
    depth: int = 0,
) -> list[FieldDiff]:
    rule = _match(path, rules)
    mode = rule.mode if rule is not None else default_mode
    rule_path = rule.path if rule is not None else None
    rendered = _render(path)
    if mode == "ignore":
        return [FieldDiff(rendered, State.SKIP, mode, rule=rule_path)]
    if mode == "type":
        return [
            _leaf(
                rendered, _type(baseline) == _type(candidate), mode, baseline, candidate, rule_path
            )
        ]
    if mode == "tolerance":
        return [_tolerance(rendered, baseline, candidate, rule)]
    if depth >= _MAX_DEPTH:
        # Too deep to recurse safely, AND too deep to compare/render as a leaf
        # (``==`` and ``json.dumps`` would recurse just as far) — mark it uncompared.
        return [
            FieldDiff(
                rendered, State.SKIP, mode, "not compared: max depth exceeded", rule=rule_path
            )
        ]
    return _structural(baseline, candidate, path, rules, default_mode, mode, rule, depth)


def _structural(
    baseline: object,
    candidate: object,
    path: tuple[str, ...],
    rules: list[_Rule],
    default_mode: str,
    mode: str,
    rule: _Rule | None,
    depth: int,
) -> list[FieldDiff]:
    rendered = _render(path)
    rule_path = rule.path if rule is not None else None
    baseline_type, candidate_type = _type(baseline), _type(candidate)
    if baseline_type != candidate_type:
        return [
            FieldDiff(
                rendered,
                State.DRIFT,
                mode,
                f"type {baseline_type} → {candidate_type}",
                baseline=baseline,
                candidate=candidate,
                rule=rule_path,
            )
        ]
    if isinstance(baseline, dict) and isinstance(candidate, dict):
        results: list[FieldDiff] = []
        for key in sorted(set(baseline) | set(candidate)):
            child = (*path, str(key))
            if key not in baseline or key not in candidate:
                # Honor the child path's rule: a key the profile ignores must not
                # count as drift just because it is present on only one side.
                child_rule = _match(child, rules)
                child_mode = child_rule.mode if child_rule is not None else mode
                child_rule_path = child_rule.path if child_rule is not None else None
                if child_mode == "ignore":
                    results.append(
                        FieldDiff(_render(child), State.SKIP, "ignore", rule=child_rule_path)
                    )
                else:
                    results.append(
                        FieldDiff(
                            _render(child),
                            State.DRIFT,
                            child_mode,
                            "missing on one side",
                            baseline=baseline.get(key),
                            candidate=candidate.get(key),
                            rule=child_rule_path,
                        )
                    )
            else:
                results.extend(
                    _walk(baseline[key], candidate[key], child, rules, default_mode, depth + 1)
                )
        return results
    if isinstance(baseline, list) and isinstance(candidate, list):
        results = []
        strict = mode == "exact" or (rule is not None and rule.array_length == "exact")
        if strict and len(baseline) != len(candidate):
            results.append(
                FieldDiff(
                    rendered,
                    State.DRIFT,
                    mode,
                    f"length {len(baseline)} → {len(candidate)}",
                    baseline=baseline,
                    candidate=candidate,
                    rule=rule_path,
                )
            )
        for index in range(min(len(baseline), len(candidate))):
            child = (*path, f"[{index}]")
            results.extend(
                _walk(baseline[index], candidate[index], child, rules, default_mode, depth + 1)
            )
        return results
    if mode == "exact":
        return [_leaf(rendered, baseline == candidate, "exact", baseline, candidate, rule_path)]
    return [
        FieldDiff(
            rendered, State.SAME, mode, baseline=baseline, candidate=candidate, rule=rule_path
        )
    ]


def _leaf(
    path: str,
    equal: bool,
    mode: str,
    baseline: object,
    candidate: object,
    rule: str | None = None,
) -> FieldDiff:
    if equal:
        return FieldDiff(path, State.SAME, mode, baseline=baseline, candidate=candidate, rule=rule)
    return FieldDiff(
        path,
        State.DRIFT,
        mode,
        f"{_short(baseline)} → {_short(candidate)}",
        baseline=baseline,
        candidate=candidate,
        rule=rule,
    )


def _tolerance(path: str, baseline: object, candidate: object, rule: _Rule | None) -> FieldDiff:
    limit = rule.tolerance if rule is not None and rule.tolerance is not None else 0.0
    rule_path = rule.path if rule is not None else None
    if (
        isinstance(baseline, int | float)
        and isinstance(candidate, int | float)
        and not isinstance(baseline, bool)
        and not isinstance(candidate, bool)
    ):
        if abs(baseline - candidate) <= limit:
            return FieldDiff(
                path,
                State.SAME,
                "tolerance",
                baseline=baseline,
                candidate=candidate,
                rule=rule_path,
            )
        return FieldDiff(
            path,
            State.DRIFT,
            "tolerance",
            f"{baseline} → {candidate} (±{limit})",
            baseline=baseline,
            candidate=candidate,
            rule=rule_path,
        )
    return _leaf(path, baseline == candidate, "tolerance", baseline, candidate, rule_path)


def _match(path: tuple[str, ...], rules: list[_Rule]) -> _Rule | None:
    for rule in rules:
        if _is_prefix(rule.segments, path):
            return rule
    return None


def _is_prefix(segments: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if len(segments) > len(path):
        return False
    return all(_segment_matches(segment, path[index]) for index, segment in enumerate(segments))


def _segment_matches(rule_segment: str, path_segment: str) -> bool:
    return rule_segment in ("[*]", "*") or rule_segment == path_segment


def _compile(rule: DiffRule) -> _Rule:
    return _Rule(_parse_path(rule.path), rule.mode, rule.array_length, rule.tolerance, rule.path)


def _parse_path(path: str) -> tuple[str, ...]:
    trimmed = path.removeprefix("$").removeprefix(".")
    if not trimmed:
        return ()
    normalized = trimmed.replace("[", ".[")
    return tuple(segment for segment in normalized.split(".") if segment)


def _render(path: tuple[str, ...]) -> str:
    rendered = "$"
    for segment in path:
        rendered += segment if segment.startswith("[") else f".{segment}"
    return rendered


def _type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    return "null"


def _short(value: object) -> str:
    # The FULL rendering — a detail string must never be truncated before a sink
    # gets to redact it, or a long secret's prefix would survive masking. Sinks
    # truncate for brevity only after redaction (see report/archive/display).
    # ``default=str`` keeps a non-JSON value (e.g. a stray date) from crashing.
    return json.dumps(value, ensure_ascii=False, default=str)
