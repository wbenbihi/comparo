"""Tests for deterministic evaluator fact identities."""

from uuid import UUID

import pytest

from comparo.core.domain.identity import RuleApplicationId
from comparo.core.evaluation.fact_ids import derive_fact_id


def test_fact_ids_are_stable_and_structurally_distinct() -> None:
    application = RuleApplicationId(value=UUID(int=41))

    first = derive_fact_id(application, engine="assertion", role="match", ordinal=0)

    assert first == derive_fact_id(application, engine="assertion", role="match", ordinal=0)
    assert first != derive_fact_id(application, engine="assertion", role="match", ordinal=1)
    assert first != derive_fact_id(application, engine="diff", role="match", ordinal=0)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"application_id": object()}, TypeError),
        ({"engine": "Assertion"}, ValueError),
        ({"role": "bad role"}, ValueError),
        ({"ordinal": -1}, ValueError),
        ({"ordinal": True}, ValueError),
    ],
)
def test_fact_id_derivation_rejects_ambiguous_coordinates(
    arguments: dict[str, object],
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "application_id": RuleApplicationId(value=UUID(int=42)),
        "engine": "assertion",
        "role": "parent",
        "ordinal": 0,
    }
    values.update(arguments)

    with pytest.raises(error):
        derive_fact_id(**values)  # type: ignore[arg-type]
