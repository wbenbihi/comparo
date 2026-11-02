"""Sensitivity-safe structural evidence sizing across value carriers."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions.evidence import _match_outcome
from comparo.core.assertions.evidence_size import evidence_fact_size
from comparo.core.assertions.evidence_size import selected_value_size
from comparo.core.assertions.predicates import PredicateError
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.selection import select
from tests.core.assertions.factories import control
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot


def test_evidence_value_sizing_covers_json_http_scalar_and_media_variants() -> None:
    values = (
        JsonNull(),
        JsonBoolean(value=True),
        JsonBoolean(value=False),
        JsonInteger(value=-10),
        JsonDecimal(value=Decimal("0.01")),
        JsonString(value='"\\\n\ud800'),
        JsonArray(items=(JsonInteger(value=1), JsonString(value="x"))),
        JsonObject(members=(JsonObjectMember(name="x", value=JsonInteger(value=1)),)),
        BytesValue(value=b"abc"),
        HttpFieldTextValue(value="é"),
        BareQueryFieldValue(),
        HttpFieldItems(items=(MediaTypeTokenValue(value="gzip"), MediaTypeTokenValue(value="br"))),
        Count(value=12),
        ByteCount(value=12),
        Duration(nanoseconds=12),
        Ratio(numerator=1, denominator=PositiveCount(value=2)),
        UtcInstant(unix_nanoseconds=12),
        MediaType(
            type="text",
            subtype="plain",
            parameters=(
                MediaTypeParameter(
                    name="note",
                    value=MediaTypeQuotedValue(value='a "quote"'),
                ),
            ),
        ),
    )
    assert all(selected_value_size(value) >= 0 for value in values)
    sensitive = SensitiveValue(
        JsonString(value="secret"), origins=(TaintOriginId(value="env.secret"),)
    )
    assert selected_value_size(sensitive) > selected_value_size(JsonString(value="secret"))
    with pytest.raises(TypeError):
        selected_value_size(object())
    selected = select(snapshot(), rule().selector, control().selection)
    assert type(selected.result) is SelectionReady
    match = selected.result.matches[0]
    fact = (
        msgspec.structs.replace(
            _match_outcome(PredicateError(code=DiagnosticCode(value="bad"))),
        )
        if False
        else None
    )
    del fact
    from comparo.core.domain.assertions.results import MatchFact
    from comparo.core.domain.identity import FactId

    evidence = MatchFact(
        fact_id=FactId(value=UUID(int=10)),
        match=match,
        outcome=MatchOutcome.ERROR,
        diagnostic=DiagnosticCode(value="assertion.error"),
    )
    assert evidence_fact_size(evidence) > 0
    with pytest.raises(TypeError):
        evidence_fact_size(cast("MatchFact", object()))
