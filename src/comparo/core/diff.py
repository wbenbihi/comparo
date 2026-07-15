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

from comparo.core.models import DiffProfile
from comparo.core.models import DiffRule


class State(enum.Enum):
    """The comparison outcome for one path."""

    SAME = "same"
    DRIFT = "drift"
    SKIP = "skip"


@dataclasses.dataclass(frozen=True, slots=True)
class FieldDiff:
    """The comparison outcome at one path."""

    path: str
    state: State
    mode: str
    detail: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class _Rule:
    segments: tuple[str, ...]
    mode: str
    array_length: str | None
    tolerance: float | None


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
    compiled = sorted(
        (_compile(rule) for rule in rules), key=lambda rule: len(rule.segments), reverse=True
    )
    return _walk(baseline, candidate, (), compiled, default_mode)


def profile_rules(profile: DiffProfile | None) -> tuple[str, list[DiffRule]]:
    """Return the ``(default_mode, rules)`` of *profile*, or a strict fallback.

    Args:
        profile: The diff profile, or ``None``.

    Returns:
        The default mode and rules; ``("exact", [])`` when no profile applies.
    """
    if profile is None:
        return "exact", []
    return profile.spec.default, profile.spec.rules or []


def _walk(
    baseline: object,
    candidate: object,
    path: tuple[str, ...],
    rules: list[_Rule],
    default_mode: str,
) -> list[FieldDiff]:
    rule = _match(path, rules)
    mode = rule.mode if rule is not None else default_mode
    rendered = _render(path)
    if mode == "ignore":
        return [FieldDiff(rendered, State.SKIP, mode)]
    if mode == "type":
        return [_leaf(rendered, _type(baseline) == _type(candidate), mode, baseline, candidate)]
    if mode == "tolerance":
        return [_tolerance(rendered, baseline, candidate, rule)]
    return _structural(baseline, candidate, path, rules, default_mode, mode, rule)


def _structural(
    baseline: object,
    candidate: object,
    path: tuple[str, ...],
    rules: list[_Rule],
    default_mode: str,
    mode: str,
    rule: _Rule | None,
) -> list[FieldDiff]:
    rendered = _render(path)
    baseline_type, candidate_type = _type(baseline), _type(candidate)
    if baseline_type != candidate_type:
        return [FieldDiff(rendered, State.DRIFT, mode, f"type {baseline_type} → {candidate_type}")]
    if isinstance(baseline, dict) and isinstance(candidate, dict):
        results: list[FieldDiff] = []
        for key in sorted(set(baseline) | set(candidate)):
            child = (*path, str(key))
            if key not in baseline or key not in candidate:
                results.append(FieldDiff(_render(child), State.DRIFT, mode, "missing on one side"))
            else:
                results.extend(_walk(baseline[key], candidate[key], child, rules, default_mode))
        return results
    if isinstance(baseline, list) and isinstance(candidate, list):
        results = []
        strict = mode == "exact" or (rule is not None and rule.array_length == "exact")
        if strict and len(baseline) != len(candidate):
            results.append(
                FieldDiff(rendered, State.DRIFT, mode, f"length {len(baseline)} → {len(candidate)}")
            )
        for index in range(min(len(baseline), len(candidate))):
            child = (*path, f"[{index}]")
            results.extend(_walk(baseline[index], candidate[index], child, rules, default_mode))
        return results
    if mode == "exact":
        return [_leaf(rendered, baseline == candidate, "exact", baseline, candidate)]
    return [FieldDiff(rendered, State.SAME, mode)]


def _leaf(path: str, equal: bool, mode: str, baseline: object, candidate: object) -> FieldDiff:
    if equal:
        return FieldDiff(path, State.SAME, mode)
    return FieldDiff(path, State.DRIFT, mode, f"{_short(baseline)} → {_short(candidate)}")


def _tolerance(path: str, baseline: object, candidate: object, rule: _Rule | None) -> FieldDiff:
    limit = rule.tolerance if rule is not None and rule.tolerance is not None else 0.0
    if (
        isinstance(baseline, int | float)
        and isinstance(candidate, int | float)
        and not isinstance(baseline, bool)
        and not isinstance(candidate, bool)
    ):
        if abs(baseline - candidate) <= limit:
            return FieldDiff(path, State.SAME, "tolerance")
        return FieldDiff(path, State.DRIFT, "tolerance", f"{baseline} → {candidate} (±{limit})")
    return _leaf(path, baseline == candidate, "tolerance", baseline, candidate)


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
    return _Rule(_parse_path(rule.path), rule.mode, rule.array_length, rule.tolerance)


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
    rendered = json.dumps(value, ensure_ascii=False)
    return rendered if len(rendered) <= 40 else f"{rendered[:37]}..."
