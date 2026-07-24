"""Cover direct-construction invariants across the selector contract."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.locations import AttemptLocation
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ExecutionLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import ScenarioLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import is_singular_selector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.paths import is_selector_path
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.results import is_selected_value
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from tests.core.domain.budget_origin_factories import default_budget_origin


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="4" * 64))


def _field() -> StaticFieldPath:
    return StaticFieldPath(segments=("timings", "elapsed"))


def _capabilities() -> SelectorCapabilities:
    return SelectorCapabilities(
        cardinality=SelectorCardinality.EXACTLY_ONE,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )


def _output(
    cardinality: SelectorCardinality = SelectorCardinality.EXACTLY_ONE,
    kind: ValueKind = ValueKind.UNKNOWN,
) -> ValueContract:
    return ValueContract(kinds=(kind,), cardinality=cardinality)


@pytest.mark.parametrize("segments", [(), ("bad segment",)])
def test_static_field_path_rejects_invalid_segments(segments: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="segment"):
        StaticFieldPath(segments=segments)


def test_path_types_reject_invalid_direct_values() -> None:
    with pytest.raises(TypeError):
        StaticFieldPath(segments=cast("tuple[str, ...]", ["field"]))
    with pytest.raises(TypeError):
        JsonPointer(value=cast("str", 1))
    with pytest.raises(ValueError, match="begin"):
        JsonPointer(value="not-rooted")
    with pytest.raises(TypeError):
        JsonPointerPath(pointer=cast("JsonPointer", ""))
    with pytest.raises(ValueError, match="begin"):
        JsonPathExpression(
            canonical="users",
            fingerprint=_fingerprint(),
            capabilities=_capabilities(),
        )
    with pytest.raises(TypeError):
        JsonPathExpression(
            canonical="$",
            fingerprint=cast("Fingerprint", "bad"),
            capabilities=_capabilities(),
        )
    assert is_selector_path(JsonPointerPath(pointer=JsonPointer(value="")))
    assert JsonPointer(value="/escaped~0tilde~1slash").value.endswith("slash")
    assert not is_selector_path("$")


def test_selector_capabilities_reject_untyped_or_ambiguous_metadata() -> None:
    with pytest.raises(TypeError):
        SelectorCapabilities(
            cardinality=cast("SelectorCardinality", "one"),
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        )
    with pytest.raises(TypeError):
        SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=cast("SelectionAccess", "stream"),
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        )
    with pytest.raises(TypeError):
        SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=cast("bool", 0),
        )
    with pytest.raises(TypeError):
        SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
            function_dependencies=cast("tuple[str, ...]", ["count"]),
        )
    with pytest.raises(ValueError, match="invalid name"):
        SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
            function_dependencies=("bad name",),
        )
    with pytest.raises(ValueError, match="unique"):
        SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
            function_dependencies=("count", "count"),
        )


def test_compiled_selector_rejects_untyped_components_and_missing_body_view() -> None:
    with pytest.raises(TypeError):
        CompiledSelector(
            fingerprint=cast("Fingerprint", "bad"),
            source=SelectorSource.RESPONSE_STATUS,
            path=NoPath(),
            capabilities=_capabilities(),
            output=_output(kind=ValueKind.INTEGER),
        )
    with pytest.raises(TypeError):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=cast("SelectorSource", "bad"),
            path=NoPath(),
            capabilities=_capabilities(),
            output=_output(kind=ValueKind.INTEGER),
        )
    with pytest.raises(TypeError):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_STATUS,
            path=cast("NoPath", "bad"),
            capabilities=_capabilities(),
            output=_output(kind=ValueKind.INTEGER),
        )
    with pytest.raises(TypeError):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_STATUS,
            path=NoPath(),
            capabilities=cast("SelectorCapabilities", "bad"),
            output=_output(kind=ValueKind.INTEGER),
        )
    with pytest.raises(ValueError, match="body selectors"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_BODY,
            path=NoPath(),
            capabilities=_capabilities(),
            output=_output(),
        )
    with pytest.raises(TypeError, match="body view"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_BODY,
            path=NoPath(),
            capabilities=_capabilities(),
            output=_output(),
            body_view=cast("BodyViewKind", "json"),
        )


def test_selector_source_path_view_and_capabilities_cannot_contradict() -> None:
    pointer_capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_ONE,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    pointer_path = JsonPointerPath(pointer=JsonPointer(value="/id"))
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=pointer_path,
        capabilities=pointer_capabilities,
        output=_output(SelectorCardinality.ZERO_OR_ONE),
        body_view=BodyViewKind.JSON,
    )
    assert is_singular_selector(selector)
    with pytest.raises(ValueError, match="scalar HTTP"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_STATUS,
            path=pointer_path,
            capabilities=pointer_capabilities,
            output=_output(SelectorCardinality.ZERO_OR_ONE),
        )
    with pytest.raises(ValueError, match="explicit JSON"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_BODY,
            path=pointer_path,
            capabilities=pointer_capabilities,
            output=_output(SelectorCardinality.ZERO_OR_ONE),
            body_view=BodyViewKind.ENTITY_BYTES,
        )
    with pytest.raises(ValueError, match="contradict"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_BODY,
            path=pointer_path,
            capabilities=_capabilities(),
            output=_output(),
            body_view=BodyViewKind.JSON,
        )
    with pytest.raises(ValueError, match="JSON-compatible source"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_HEADERS,
            path=pointer_path,
            capabilities=pointer_capabilities,
            output=_output(SelectorCardinality.ZERO_OR_ONE),
        )
    with pytest.raises(ValueError, match="whole-view or JSON"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_BODY,
            path=StaticFieldPath(segments=("field",)),
            capabilities=_capabilities(),
            output=_output(),
            body_view=BodyViewKind.JSON,
        )
    path_capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_MORE,
        access=SelectionAccess.INDEXED,
        may_duplicate=True,
        uses_descendant=True,
        uses_filter=False,
    )
    json_path = JsonPathExpression(
        canonical="$..id",
        fingerprint=_fingerprint(),
        capabilities=path_capabilities,
    )
    with pytest.raises(TypeError, match="JSONPath capabilities"):
        JsonPathExpression(
            canonical="$.id",
            fingerprint=_fingerprint(),
            capabilities=cast("SelectorCapabilities", "bad"),
        )
    with pytest.raises(ValueError, match="contradict"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_BODY,
            path=json_path,
            capabilities=pointer_capabilities,
            output=_output(SelectorCardinality.ZERO_OR_ONE),
            body_view=BodyViewKind.JSON,
        )


def test_every_location_variant_is_concrete() -> None:
    field = _field()
    view_id = ViewId(value=UUID(int=30))
    locations = (
        ExecutionLocation(execution_id=EnvironmentExecutionId(value=UUID(int=31)), field=field),
        AttemptLocation(attempt_id=AttemptId(value=UUID(int=32)), field=field),
        ExchangeLocation(exchange_id=ExchangeId(value=UUID(int=33)), field=field),
        HeaderLocation(
            exchange_id=ExchangeId(value=UUID(int=34)),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            canonical_name="content-type",
        ),
        JsonLocation(view_id=view_id, pointer=JsonPointer(value="/answer")),
        BodyLocation(body_id=BodyId(value=UUID(int=35))),
        StreamLocation(
            view_id=view_id,
            connection_segment=Count(value=0),
            record_index=Count(value=1),
            nested_pointer=JsonPointer(value="/delta"),
        ),
        ScenarioLocation(step_execution_id=StepExecutionId(value=UUID(int=36)), field=field),
    )

    assert all(is_concrete_location(location) for location in locations)
    assert not is_concrete_location("/answer")


def test_location_direct_construction_rejects_invalid_coordinates() -> None:
    field = _field()
    with pytest.raises(TypeError):
        ExecutionLocation(execution_id=cast("EnvironmentExecutionId", "bad"), field=field)
    with pytest.raises(TypeError):
        AttemptLocation(
            attempt_id=AttemptId(value=UUID(int=37)), field=cast("StaticFieldPath", "bad")
        )
    with pytest.raises(TypeError):
        HeaderLocation(
            exchange_id=cast("ExchangeId", "bad"),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            canonical_name="name",
        )
    with pytest.raises(TypeError, match="occurrence"):
        HeaderLocation(
            exchange_id=ExchangeId(value=UUID(int=381)),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=cast("Count", 0),
            canonical_name="name",
        )
    with pytest.raises(TypeError):
        HeaderLocation(
            exchange_id=ExchangeId(value=UUID(int=38)),
            section=cast("HttpMessageSection", "bad"),
            occurrence=Count(value=0),
            canonical_name="name",
        )
    with pytest.raises(ValueError, match="lowercase"):
        HeaderLocation(
            exchange_id=ExchangeId(value=UUID(int=39)),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            canonical_name="Content-Type",
        )
    with pytest.raises(ValueError, match="both"):
        BodyLocation(body_id=BodyId(value=UUID(int=40)), start=ByteCount(value=0))
    with pytest.raises(TypeError, match="BodyId"):
        BodyLocation(body_id=cast("BodyId", "bad"))
    with pytest.raises(TypeError):
        BodyLocation(
            body_id=BodyId(value=UUID(int=41)),
            start=cast("ByteCount", "bad"),
            end=ByteCount(value=1),
        )
    with pytest.raises(ValueError, match="greater"):
        BodyLocation(
            body_id=BodyId(value=UUID(int=42)),
            start=ByteCount(value=2),
            end=ByteCount(value=1),
        )
    with pytest.raises(TypeError, match="end"):
        BodyLocation(
            body_id=BodyId(value=UUID(int=421)),
            start=ByteCount(value=0),
            end=cast("ByteCount", "bad"),
        )
    with pytest.raises(TypeError, match="ViewId"):
        JsonLocation(view_id=cast("ViewId", "bad"), pointer=JsonPointer(value=""))
    with pytest.raises(TypeError, match="ViewId"):
        StreamLocation(
            view_id=cast("ViewId", "bad"),
            connection_segment=Count(value=0),
            record_index=Count(value=0),
        )
    with pytest.raises(TypeError, match="indices"):
        StreamLocation(
            view_id=ViewId(value=UUID(int=422)),
            connection_segment=cast("Count", 0),
            record_index=Count(value=0),
        )
    with pytest.raises(TypeError, match="nested"):
        StreamLocation(
            view_id=ViewId(value=UUID(int=423)),
            connection_segment=Count(value=0),
            record_index=Count(value=0),
            nested_pointer=cast("JsonPointer", "bad"),
        )


def test_selection_result_variants_remain_distinct_and_validate_payloads() -> None:
    code = DiagnosticCode(value="selector.runtime-state")
    variants = (
        SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY),
        SelectionDecodeFailure(code=code, view=BodyViewKind.JSON),
        SelectionIncomplete(reason=SelectionIncompleteReason.PARTIAL_CAPTURE),
        SelectionUnsupported(code=code),
        SelectionCancelled(code=code),
    )
    assert len({type(variant) for variant in variants}) == len(variants)
    for constructor in (SelectionUnsupported, SelectionCancelled):
        with pytest.raises(TypeError):
            constructor(code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="source reason"):
        SelectionSourceUnavailable(reason=cast("SourceUnavailableReason", "bad"))
    with pytest.raises(TypeError, match="DiagnosticCode"):
        SelectionDecodeFailure(
            code=cast("DiagnosticCode", "bad"),
            view=BodyViewKind.JSON,
        )
    with pytest.raises(TypeError, match="incomplete reason"):
        SelectionIncomplete(reason=cast("SelectionIncompleteReason", "bad"))
    zero = Count(value=0)
    limit = Count(value=8)
    budget = ResourceBudget(
        resource=BudgetResource.SELECTION_WORK,
        scope_kind=BudgetScopeKind.RUN,
        stage=BudgetStage.SELECTION,
        operation=BudgetOperation(value="selector.evaluate"),
        requested=limit,
        effective=limit,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=limit,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=RunScope(run_id=RunId(value=UUID(int=424))),
    )
    exhaustion = BudgetExceeded(
        budget=budget,
        attempted=Count(value=9),
        effect=BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
    )
    assert SelectionBudgetExceeded(exhaustion=exhaustion).exhaustion == exhaustion
    decoding_budget = msgspec.structs.replace(
        budget,
        resource=BudgetResource.JSON_NODES,
        stage=BudgetStage.DECODING,
    )
    with pytest.raises(ValueError, match="selection stage"):
        SelectionBudgetExceeded(
            exhaustion=BudgetExceeded(
                budget=decoding_budget,
                attempted=Count(value=9),
                effect=BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
            ),
        )
    evaluation_budget = msgspec.structs.replace(
        budget,
        resource=BudgetResource.DERIVED_VALUES,
        stage=BudgetStage.EVALUATION,
    )
    with pytest.raises(ValueError, match="selection stage"):
        SelectionBudgetExceeded(
            exhaustion=BudgetExceeded(
                budget=evaluation_budget,
                attempted=Count(value=9),
                effect=BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
            ),
        )


def test_selection_matches_reject_invalid_public_and_sensitive_values() -> None:
    location = JsonLocation(view_id=ViewId(value=UUID(int=43)), pointer=JsonPointer(value=""))
    with pytest.raises(TypeError):
        SelectionMatch(value=cast("JsonInteger", "bad"), location=location)
    with pytest.raises(TypeError):
        SelectionMatch(value=JsonInteger(value=1), location=cast("JsonLocation", "bad"))
    invalid_sensitive = SensitiveValue(
        "not-a-selected-value",
        origins=(TaintOriginId(value="invalid-selected-type"),),
    )
    with pytest.raises(TypeError):
        SelectionMatch(
            value=cast("SensitiveValue[JsonInteger]", invalid_sensitive),
            location=location,
        )
    match = SelectionMatch(value=JsonInteger(value=1), location=location)
    assert is_selected_value(match.value)
    assert match.sensitivity.value == "public"
    assert match.taint_origins == ()
    assert SelectionReady(matches=(match,)).matches == (match,)
    with pytest.raises(TypeError):
        SelectionReady(matches=cast("tuple[SelectionMatch, ...]", [match]))
    with pytest.raises(TypeError, match="unsupported match"):
        SelectionReady(matches=(cast("SelectionMatch", "bad"),))


def test_budget_variant_rejects_non_budget_evidence() -> None:
    with pytest.raises(TypeError):
        SelectionBudgetExceeded(exhaustion=cast("BudgetExceeded", "bad"))
