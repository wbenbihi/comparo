"""Exercise bounded JSON pointer, projection, exclusion, and shape operations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from comparo.core.diffs.json_transforms import _exclude_paths
from comparo.core.diffs.json_transforms import _excluded_size
from comparo.core.diffs.json_transforms import _project_paths
from comparo.core.diffs.json_transforms import _projected_size
from comparo.core.diffs.json_transforms import _require_depth
from comparo.core.diffs.json_transforms import encode_pointer_token
from comparo.core.diffs.json_transforms import exclude
from comparo.core.diffs.json_transforms import pointer
from comparo.core.diffs.json_transforms import pointer_tokens
from comparo.core.diffs.json_transforms import project
from comparo.core.diffs.json_transforms import shape
from comparo.core.diffs.json_transforms import shape_size_upper_bound
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.domain.diffs.operations import JsonShapeArrayMode
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from tests.core.diffs.factories import meter


def _document() -> JsonObject:
    return JsonObject(
        members=(
            JsonObjectMember(
                name="a/b",
                value=JsonArray(
                    items=(
                        JsonObject(
                            members=(
                                JsonObjectMember(name="~key", value=JsonInteger(value=1)),
                                JsonObjectMember(name="drop", value=JsonInteger(value=2)),
                            )
                        ),
                        JsonString(value="second"),
                    )
                ),
            ),
            JsonObjectMember(name="keep", value=JsonBoolean(value=True)),
        )
    )


def test_pointer_resolves_root_escaped_members_and_canonical_array_indexes() -> None:
    document = _document()
    assert pointer(document, JsonPointer(value=""), meter()) is document
    assert pointer(document, JsonPointer(value="/a~1b/0/~0key"), meter()) == JsonInteger(value=1)
    assert pointer(document, JsonPointer(value="/a~1b/1"), meter()) == JsonString(value="second")


@pytest.mark.parametrize(
    "authored",
    [
        "/missing",
        "/a~1b/2",
        "/a~1b/00",
        "/a~1b/-",
        "/a~1b/\u0661",
        "/keep/nested",
    ],
)
def test_pointer_returns_none_for_missing_or_noncanonical_steps(authored: str) -> None:
    assert pointer(_document(), JsonPointer(value=authored), meter()) is None


def test_project_retains_authored_nested_paths_and_root_identity() -> None:
    document = _document()
    assert project(document, (JsonPointer(value=""),), meter()) is document
    projected = project(
        document,
        (
            JsonPointer(value="/a~1b/0/~0key"),
            JsonPointer(value="/a~1b/1"),
            JsonPointer(value="/keep"),
        ),
        meter(),
    )
    assert pointer(projected, JsonPointer(value="/a~1b/0/~0key"), meter()) == JsonInteger(value=1)
    assert pointer(projected, JsonPointer(value="/a~1b/0/drop"), meter()) is None


def test_project_rejects_missing_and_scalar_empty_path_sets() -> None:
    with pytest.raises(DiffHaltError) as missing:
        project(_document(), (JsonPointer(value="/missing"),), meter())
    assert missing.value.code is not None
    assert missing.value.code.value == "diff.project.pointer-missing"
    with pytest.raises(DiffHaltError) as invalid:
        project(JsonInteger(value=1), (), meter())
    assert invalid.value.code is not None
    assert invalid.value.code.value == "diff.project.invalid-path"


def test_project_paths_skip_unselected_children_and_scalar_recursion() -> None:
    document = _document()
    projected = _project_paths(document, (("keep",),), meter())
    assert projected == JsonObject(
        members=(JsonObjectMember(name="keep", value=JsonBoolean(value=True)),)
    )
    assert _project_paths(JsonInteger(value=1), (("child",),), meter()) is None
    array = JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2)))
    assert _project_paths(array, (("1",),), meter()) == JsonArray(items=(JsonInteger(value=2),))
    assert _project_paths(array, (("0", "child"),), meter()) == JsonArray(items=())
    scalars = JsonObject(
        members=(
            JsonObjectMember(name="x", value=JsonInteger(value=1)),
            JsonObjectMember(name="y", value=JsonInteger(value=2)),
        )
    )
    assert _project_paths(scalars, (("x", "child"), ("y",)), meter()) == JsonObject(
        members=(JsonObjectMember(name="y", value=JsonInteger(value=2)),)
    )


def test_projection_preflight_measures_skipped_and_unprojectable_children() -> None:
    scalars = JsonObject(
        members=(
            JsonObjectMember(name="x", value=JsonInteger(value=1)),
            JsonObjectMember(name="y", value=JsonInteger(value=2)),
        )
    )
    assert _projected_size(scalars, (("missing",),), meter()) == 2
    assert _projected_size(scalars, (("x", "child"),), meter()) == 2
    array = JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2)))
    assert _projected_size(array, (("2",),), meter()) == 2
    assert _projected_size(array, (("0", "child"),), meter()) == 2


def test_exclude_removes_nested_paths_and_supports_explicit_root_removal() -> None:
    document = _document()
    assert exclude(document, (JsonPointer(value=""),), meter()) is None
    result = exclude(
        document,
        (
            JsonPointer(value="/a~1b/0/drop"),
            JsonPointer(value="/a~1b/1"),
        ),
        meter(),
    )
    assert result is not None
    assert pointer(result, JsonPointer(value="/a~1b/0/drop"), meter()) is None
    assert pointer(result, JsonPointer(value="/a~1b/0/~0key"), meter()) == JsonInteger(value=1)


def test_exclude_rejects_missing_paths_and_preserves_unselected_scalars() -> None:
    with pytest.raises(DiffHaltError) as caught:
        exclude(_document(), (JsonPointer(value="/missing"),), meter())
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.exclude.pointer-missing"
    scalar = JsonInteger(value=1)
    assert _exclude_paths(scalar, (("unused",),), meter()) is scalar
    assert _excluded_size(scalar, (("unused",),), meter()) == 1


def test_exclude_paths_cover_direct_and_recursive_array_and_object_children() -> None:
    document = _document()
    result = _exclude_paths(document, (("keep",), ("a/b", "0", "~key")), meter())
    assert pointer(result, JsonPointer(value="/keep"), meter()) is None
    assert pointer(result, JsonPointer(value="/a~1b/0/~0key"), meter()) is None
    assert pointer(result, JsonPointer(value="/a~1b/0/drop"), meter()) == JsonInteger(value=2)


def test_shape_describes_objects_and_positional_or_union_array_items() -> None:
    value = JsonObject(
        members=(
            JsonObjectMember(
                name="items",
                value=JsonArray(
                    items=(
                        JsonInteger(value=1),
                        JsonInteger(value=2),
                        JsonString(value="x"),
                    )
                ),
            ),
        )
    )
    positional = shape(value, JsonShapeArrayMode.POSITIONAL, meter())
    union = shape(value, JsonShapeArrayMode.UNION, meter())
    positional_items = pointer(positional, JsonPointer(value="/members/items/items"), meter())
    union_items = pointer(union, JsonPointer(value="/members/items/items"), meter())
    assert type(positional_items) is JsonArray
    assert type(union_items) is JsonArray
    assert len(positional_items.items) == 3
    assert len(union_items.items) == 2
    assert shape_size_upper_bound(value, JsonShapeArrayMode.POSITIONAL, meter()) > 0


@pytest.mark.parametrize(
    "value",
    [
        JsonNull(),
        JsonBoolean(value=False),
        JsonInteger(value=1),
        JsonDecimal(value=Decimal("1.5")),
        JsonString(value="x"),
        JsonArray(items=()),
        JsonObject(members=()),
    ],
)
def test_shape_supports_every_closed_json_kind(value: JsonValue) -> None:
    result = shape(value, JsonShapeArrayMode.POSITIONAL, meter())
    assert result.members[0].name == "type"


def test_pointer_token_helpers_are_exact_in_both_directions() -> None:
    assert pointer_tokens(JsonPointer(value=""), meter()) == ()
    assert pointer_tokens(JsonPointer(value="/a~1b/~0key"), meter()) == ("a/b", "~key")
    assert encode_pointer_token("a/b~c") == "a~1b~0c"


def test_json_tree_depth_and_work_budgets_fail_closed() -> None:
    _require_depth(256)
    with pytest.raises(DiffHaltError):
        _require_depth(257)
    with pytest.raises(DiffHaltError):
        pointer(_document(), JsonPointer(value="/keep"), meter(work=0))


def test_pointer_work_is_value_proportional_for_long_tokens_and_member_scans() -> None:
    long_name = "x" * 1_000
    document = JsonObject(members=(JsonObjectMember(name=long_name, value=JsonInteger(value=1)),))
    with pytest.raises(DiffHaltError) as caught:
        pointer(document, JsonPointer(value=f"/{long_name}"), meter(work=100))
    assert caught.value.exhaustion is not None
    with pytest.raises(DiffHaltError) as caught:
        pointer_tokens(JsonPointer(value=f"/{long_name}"), meter(work=100))
    assert caught.value.exhaustion is not None
    assert (
        pointer(
            JsonArray(items=(JsonInteger(value=1),)),
            JsonPointer(value="/" + "9" * 5_000),
            meter(),
        )
        is None
    )
