"""Tests for the tri-state diff engine."""

from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.diff import diff
from comparo.core.diff import source_rules
from comparo.core.models import DiffRule


def _states(fields: list[FieldDiff]) -> set[State]:
    return {field.state for field in fields}


def test_exact_identical_is_same() -> None:
    fields = diff({"a": 1, "b": "x"}, {"a": 1, "b": "x"}, "exact", [])
    assert _states(fields) == {State.SAME}


def test_exact_value_change_is_drift() -> None:
    assert any(field.state is State.DRIFT for field in diff({"a": 1}, {"a": 2}, "exact", []))


def test_ignored_key_present_on_one_side_is_skip_not_drift() -> None:
    # A key the profile ignores must not count as drift just because it appears on
    # only one side — the missing-key branch must honor the ignore rule.
    rules = [DiffRule(path="$.trace", mode="ignore")]
    fields = diff({"trace": "abc"}, {}, "exact", source_rules(rules))
    trace = next(field for field in fields if field.path == "$.trace")
    assert trace.state is State.SKIP


def test_unignored_key_present_on_one_side_is_still_drift() -> None:
    fields = diff({"id": "abc"}, {}, "exact", [])
    ident = next(field for field in fields if field.path == "$.id")
    assert ident.state is State.DRIFT


def test_leaf_drift_carries_structured_values_and_the_catch_all_ref() -> None:
    # A drifted leaf exposes the raw before/after (for a structured report) and,
    # having matched no explicit rule, records the default catch-all ref.
    fields = diff({"a": 1}, {"a": 2}, "exact", [])
    a = next(field for field in fields if field.path == "$.a")
    assert a.state is State.DRIFT
    assert a.baseline == 1
    assert a.candidate == 2
    assert a.rule is not None
    assert a.rule.origin == "default"
    assert a.rule.path == "$"


def test_field_surfaces_the_matched_rule_path() -> None:
    rules = [DiffRule(path="$.trace", mode="ignore")]
    fields = diff({"trace": "abc"}, {"trace": "xyz"}, "exact", source_rules(rules))
    trace = next(field for field in fields if field.path == "$.trace")
    assert trace.state is State.SKIP
    assert trace.rule is not None
    assert trace.rule.path == "$.trace"  # the DiffRule.path that governed this field
    assert trace.rule.origin == "profile"


def test_missing_key_drift_carries_the_present_side() -> None:
    fields = diff({"id": "abc"}, {}, "exact", [])
    ident = next(field for field in fields if field.path == "$.id")
    assert ident.baseline == "abc"
    assert ident.candidate is None


def test_shape_ignores_scalar_values() -> None:
    fields = diff({"x": 1, "y": "a"}, {"x": 999, "y": "z"}, "shape", [])
    assert State.DRIFT not in _states(fields)


def test_shape_detects_type_change() -> None:
    assert any(field.state is State.DRIFT for field in diff({"x": 1}, {"x": "1"}, "shape", []))


def test_shape_tolerates_array_length() -> None:
    fields = diff({"a": [1, 2, 3]}, {"a": [1]}, "shape", [])
    assert State.DRIFT not in _states(fields)


def test_exact_array_length_is_strict() -> None:
    assert any(field.state is State.DRIFT for field in diff({"a": [1, 2]}, {"a": [1]}, "exact", []))


def test_ignore_rule_carves_hole_out_of_exact() -> None:
    rules = [DiffRule(path="$.uuid", mode="ignore")]
    fields = diff({"uuid": "a", "n": 1}, {"uuid": "b", "n": 1}, "exact", source_rules(rules))
    assert State.DRIFT not in _states(fields)
    assert State.SKIP in _states(fields)


def test_exact_rule_overrides_shape_default() -> None:
    rules = [DiffRule(path="$.args", mode="exact")]
    fields = diff({"args": {"q": 1}}, {"args": {"q": 2}}, "shape", source_rules(rules))
    assert any(field.state is State.DRIFT and "args" in field.path for field in fields)


def test_tolerance_within_limit_is_same() -> None:
    rules = [DiffRule(path="$.score", mode="tolerance", tolerance=0.5)]
    fields = diff({"score": 1.0}, {"score": 1.4}, "exact", source_rules(rules))
    assert State.DRIFT not in _states(fields)


def test_missing_key_is_drift() -> None:
    assert any(
        field.state is State.DRIFT for field in diff({"a": 1}, {"a": 1, "b": 2}, "shape", [])
    )


def test_a_deeply_nested_body_does_not_raise_recursionerror() -> None:
    from comparo.core.diff import diff

    def nest(n: int) -> dict[str, object]:
        node: dict[str, object] = {"v": 1}
        for _ in range(n):
            node = {"k": node}
        return node

    # Well beyond the old ~330 crash threshold — must compare, not overflow.
    result = diff(nest(600), nest(600), "exact", [])
    assert result  # produced field diffs instead of crashing


def test_a_later_rule_overrides_an_earlier_rule_on_the_same_path() -> None:
    from comparo.core.diff import State
    from comparo.core.diff import diff
    from comparo.core.models import DiffRule

    # An execution override (appended last) re-checks a path the request ignored.
    rules = [
        DiffRule(path="$.a", mode="ignore"),
        DiffRule(path="$.a", mode="exact"),
    ]
    result = diff({"a": 1}, {"a": 2}, "exact", source_rules(rules))
    a = next(field for field in result if field.path == "$.a")
    assert a.state is State.DRIFT  # the later 'exact' rule won, so the change is seen
