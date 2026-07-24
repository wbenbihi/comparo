"""Tests for deterministic typed override operations."""

import pytest

from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import ClearBase
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import KeyedSet
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue


def test_scalar_override_states_distinguish_inherit_clear_and_falsy_set() -> None:
    states: tuple[object, object] = (Inherit(), Clear())
    assert states[0] != states[1]
    assert SetValue(value="").value == ""


def test_replace_base_requires_an_immutable_tuple() -> None:
    assert ReplaceBase(items=()).items == ()
    with pytest.raises(TypeError, match="immutable tuple"):
        ReplaceBase(items=[])  # type: ignore[arg-type]


def test_collection_plan_retains_fixed_operation_groups() -> None:
    operation = KeyedSet(identity="accept", item=("accept", "application/json"))
    plan = CollectionOverridePlan(
        base=ClearBase(),
        remove=("cookie",),
        set_items=(operation,),
        add=(("x-trace", "enabled"),),
    )
    assert plan.base == ClearBase()
    assert plan.set_items == (operation,)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("remove", ["cookie"], "removed identities"),
        ("set_items", [], "set operations"),
        ("add", [], "added items"),
    ],
)
def test_collection_plan_rejects_mutable_operation_groups(
    field: str,
    value: object,
    message: str,
) -> None:
    arguments: dict[str, object] = {"base": KeepBase(), field: value}
    with pytest.raises(TypeError, match=message):
        CollectionOverridePlan(**arguments)  # type: ignore[arg-type]


def test_collection_plan_rejects_unknown_base_and_set_operation() -> None:
    with pytest.raises(TypeError, match="supported base"):
        CollectionOverridePlan(base="keep")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="KeyedSet"):
        CollectionOverridePlan(base=KeepBase(), set_items=("accept",))  # type: ignore[arg-type]


def test_collection_plan_rejects_duplicate_remove_or_set_identities() -> None:
    with pytest.raises(ValueError, match="removed identities"):
        CollectionOverridePlan(base=KeepBase(), remove=("cookie", "cookie"))
    with pytest.raises(ValueError, match="set identities"):
        CollectionOverridePlan(
            base=KeepBase(),
            set_items=(
                KeyedSet(identity="accept", item="json"),
                KeyedSet(identity="accept", item="text"),
            ),
        )
