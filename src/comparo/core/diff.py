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

Every emitted field carries the :class:`RuleRef` that governed it — the rule's
declared path, mode, and provenance (which profile, or the default catch-all) —
so rule ↔ field ↔ cell traceability is in the data, not reconstructed later.
"""

import dataclasses
import enum
import json
from collections.abc import Sequence

from comparo.core.models import DiffRule
from comparo.core.outcomes import Provenance


class State(enum.Enum):
    """The comparison outcome for one path."""

    SAME = "same"
    DRIFT = "drift"
    SKIP = "skip"


@dataclasses.dataclass(frozen=True, slots=True)
class RuleRef:
    """Identity and provenance of one effective rule line.

    ``path`` is the rule's declared path exactly as a profile states it
    (``$.quote``, ``$status``, ``$headers.date``) — for the catch-all it is the
    tree root (``$`` / ``$headers``). ``profile`` is the owning DiffProfile's
    ``metadata.id`` for ``origin == "profile"``; inline specs, defaults, and
    synthetics carry ``None``. ``index`` is the rule's position in the composed
    rule list, so two profiles declaring the same path stay distinguishable.
    """

    path: str
    mode: str
    origin: Provenance
    profile: str | None = None
    index: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class SourcedRule:
    """A composed rule together with its provenance — what the composer produces."""

    rule: DiffRule
    ref: RuleRef


def default_ref(mode: str, root: str = "$") -> RuleRef:
    """The catch-all ref :func:`diff` stamps on fields no rule matched."""
    return RuleRef(root, mode, "default")


def source_rules(
    rules: Sequence[DiffRule],
    origin: Provenance = "profile",
    profile: str | None = None,
    start: int = 0,
) -> list[SourcedRule]:
    """Tag a homogeneous rule list with provenance (profile, inline, synthetic)."""
    return [
        SourcedRule(rule, RuleRef(rule.path, rule.mode, origin, profile, start + offset))
        for offset, rule in enumerate(rules)
    ]


@dataclasses.dataclass(frozen=True, slots=True)
class FieldDiff:
    """The comparison outcome at one path.

    ``baseline``/``candidate`` are the raw values compared at this path (``None``
    when the field was not compared — an ``ignore`` skip or a max-depth cut), so a
    report can show the structured "was → now" without re-parsing ``detail``. They
    may hold a secret a server echoed back, so a sink MUST redact them before
    serializing (the report builder does, via ``redaction.redact_tree``).
    ``rule`` is the :class:`RuleRef` that governed the field — the engine always
    stamps one (the catch-all when no rule matched); ``None`` appears only on
    values reconstructed from a saved record that predates rule provenance.
    """

    path: str
    state: State
    mode: str
    detail: str = ""
    baseline: object = None
    candidate: object = None
    rule: RuleRef | None = None


#: A recursion cap so a pathologically deep body compares as a leaf instead of
#: overflowing the stack; far beyond any realistic API payload nesting.
_MAX_DEPTH = 200


@dataclasses.dataclass(frozen=True, slots=True)
class _Rule:
    segments: tuple[str, ...]
    mode: str
    array_length: str | None
    tolerance: float | None
    ref: RuleRef  # surfaced onto matched FieldDiffs


def diff(
    baseline: object,
    candidate: object,
    default_mode: str,
    rules: list[SourcedRule],
    *,
    root: str = "$",
) -> list[FieldDiff]:
    """Compare *baseline* against *candidate* under a default mode and rules.

    Args:
        baseline: The baseline response tree (parsed JSON).
        candidate: The candidate response tree.
        default_mode: The mode for paths no rule matches.
        rules: The composed path-scoped rules with their provenance. Rule paths
            must be relative to *root* (the composer strips a synthetic prefix
            such as ``$headers`` before calling).
        root: The rendered prefix for emitted paths — ``$`` for a body tree,
            ``$headers`` for the response-header tree.

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
    return _walk(baseline, candidate, (), compiled, default_ref(default_mode, root), root)


def _walk(
    baseline: object,
    candidate: object,
    path: tuple[str, ...],
    rules: list[_Rule],
    default: RuleRef,
    root: str,
    depth: int = 0,
) -> list[FieldDiff]:
    rule = _match(path, rules)
    mode = rule.mode if rule is not None else default.mode
    ref = rule.ref if rule is not None else default
    rendered = _render(path, root)
    if mode == "ignore":
        return [FieldDiff(rendered, State.SKIP, mode, rule=ref)]
    if mode == "type":
        return [
            _leaf(rendered, _type(baseline) == _type(candidate), mode, baseline, candidate, ref)
        ]
    if mode == "tolerance":
        limit = rule.tolerance if rule is not None and rule.tolerance is not None else 0.0
        return [_tolerance(rendered, baseline, candidate, limit, ref)]
    if depth >= _MAX_DEPTH:
        # Too deep to recurse safely, AND too deep to compare/render as a leaf
        # (``==`` and ``json.dumps`` would recurse just as far) — mark it uncompared.
        return [FieldDiff(rendered, State.SKIP, mode, "not compared: max depth exceeded", rule=ref)]
    return _structural(baseline, candidate, path, rules, default, root, mode, rule, ref, depth)


def _structural(
    baseline: object,
    candidate: object,
    path: tuple[str, ...],
    rules: list[_Rule],
    default: RuleRef,
    root: str,
    mode: str,
    rule: _Rule | None,
    ref: RuleRef,
    depth: int,
) -> list[FieldDiff]:
    rendered = _render(path, root)
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
                rule=ref,
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
                child_ref = child_rule.ref if child_rule is not None else ref
                if child_mode == "ignore":
                    results.append(
                        FieldDiff(_render(child, root), State.SKIP, "ignore", rule=child_ref)
                    )
                else:
                    results.append(
                        FieldDiff(
                            _render(child, root),
                            State.DRIFT,
                            child_mode,
                            "missing on one side",
                            baseline=baseline.get(key),
                            candidate=candidate.get(key),
                            rule=child_ref,
                        )
                    )
            else:
                results.extend(
                    _walk(baseline[key], candidate[key], child, rules, default, root, depth + 1)
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
                    rule=ref,
                )
            )
        for index in range(min(len(baseline), len(candidate))):
            child = (*path, f"[{index}]")
            results.extend(
                _walk(baseline[index], candidate[index], child, rules, default, root, depth + 1)
            )
        return results
    if mode == "exact":
        return [_leaf(rendered, baseline == candidate, "exact", baseline, candidate, ref)]
    return [FieldDiff(rendered, State.SAME, mode, baseline=baseline, candidate=candidate, rule=ref)]


def _leaf(
    path: str,
    equal: bool,
    mode: str,
    baseline: object,
    candidate: object,
    rule: RuleRef,
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


def _tolerance(
    path: str, baseline: object, candidate: object, limit: float, rule: RuleRef
) -> FieldDiff:
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
                rule=rule,
            )
        return FieldDiff(
            path,
            State.DRIFT,
            "tolerance",
            f"{baseline} → {candidate} (±{limit})",
            baseline=baseline,
            candidate=candidate,
            rule=rule,
        )
    return _leaf(path, baseline == candidate, "tolerance", baseline, candidate, rule)


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


def _compile(sourced: SourcedRule) -> _Rule:
    rule = sourced.rule
    return _Rule(_parse_path(rule.path), rule.mode, rule.array_length, rule.tolerance, sourced.ref)


def _parse_path(path: str) -> tuple[str, ...]:
    trimmed = path.removeprefix("$").removeprefix(".")
    if not trimmed:
        return ()
    normalized = trimmed.replace("[", ".[")
    return tuple(segment for segment in normalized.split(".") if segment)


def _render(path: tuple[str, ...], root: str = "$") -> str:
    rendered = root
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
