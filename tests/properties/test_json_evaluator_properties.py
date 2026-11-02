"""Property checks for RFC pointer escaping and array-slice evaluation."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import evaluate_json
from tests.core.selection.json_evaluator_factories import selection_meter


@given(
    name=st.text(
        alphabet=st.sampled_from(tuple("abc~/é😀")),
        min_size=0,
        max_size=20,
    )
)
def test_pointer_round_trips_arbitrary_escaped_object_names(name: str) -> None:
    value = JsonObject(members=(JsonObjectMember(name=name, value=JsonString(value="selected")),))
    token = name.replace("~", "~0").replace("/", "~1")

    result = evaluate_json(value, compile_json_pointer(f"/{token}"), selection_meter())

    assert type(result) is JsonEvaluationReady
    assert result.matches[0].value == JsonString(value="selected")
    assert result.matches[0].pointer.value == f"/{token}"


@given(
    values=st.lists(st.integers(min_value=-100, max_value=100), max_size=20),
    start=st.one_of(st.none(), st.integers(min_value=-25, max_value=25)),
    stop=st.one_of(st.none(), st.integers(min_value=-25, max_value=25)),
    step=st.one_of(st.none(), st.integers(min_value=-8, max_value=8)),
)
def test_jsonpath_slice_matches_python_equivalent_rfc_algorithm(
    values: list[int],
    start: int | None,
    stop: int | None,
    step: int | None,
) -> None:
    value = JsonArray(items=tuple(JsonInteger(value=item) for item in values))
    fields = ("" if start is None else str(start), "" if stop is None else str(stop))
    suffix = "" if step is None else f":{step}"
    authored = f"$[{fields[0]}:{fields[1]}{suffix}]"

    result = evaluate_json(value, compile_jsonpath(authored), selection_meter())
    expected = [] if step == 0 else values[slice(start, stop, step)]

    assert type(result) is JsonEvaluationReady
    assert [match.value for match in result.matches] == [
        JsonInteger(value=item) for item in expected
    ]
