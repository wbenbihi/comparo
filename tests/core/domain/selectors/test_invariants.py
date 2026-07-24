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
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
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
from comparo.core.domain.selectors.locations import IndexedLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import ScenarioLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.locations import location_body_view
from comparo.core.domain.selectors.locations import location_environment_execution_id
from comparo.core.domain.selectors.locations import location_selector_source
from comparo.core.domain.selectors.locations import location_static_field
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import is_singular_selector
from comparo.core.domain.selectors.model import require_location_matches_selector
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
from comparo.core.domain.values import JsonString
from tests.core.domain.budget_origin_factories import default_budget_origin


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="4" * 64))


def _field() -> StaticFieldPath:
    return StaticFieldPath(segments=("timings", "elapsed"))


def _owner(value: int = 29) -> EnvironmentExecutionId:
    return EnvironmentExecutionId(value=UUID(int=value))


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
    with pytest.raises(ValueError, match="static field selector path"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_HEADERS,
            path=StaticFieldPath(segments=("name",)),
            capabilities=SelectorCapabilities(
                cardinality=SelectorCardinality.ZERO_OR_MORE,
                access=SelectionAccess.INCREMENTAL,
                may_duplicate=False,
                uses_descendant=False,
                uses_filter=False,
            ),
            output=_output(SelectorCardinality.ZERO_OR_MORE),
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
        ExecutionLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.EXECUTION,
            field=field,
        ),
        AttemptLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.ATTEMPT,
            attempt_id=AttemptId(value=UUID(int=32)),
            field=field,
        ),
        ExchangeLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_STATUS,
            exchange_id=ExchangeId(value=UUID(int=33)),
            field=field,
        ),
        HeaderLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_HEADERS,
            exchange_id=ExchangeId(value=UUID(int=34)),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            name=HeaderName(value="content-type"),
        ),
        HeaderLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_INFORMATIONAL,
            exchange_id=ExchangeId(value=UUID(int=341)),
            section=HttpMessageSection.RESPONSE_INFORMATIONAL_HEADER,
            occurrence=Count(value=0),
            name=HeaderName(value="link"),
            informational_response=Count(value=1),
            informational_status=103,
        ),
        NamedFieldLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.REQUEST_QUERY,
            exchange_id=ExchangeId(value=UUID(int=342)),
            name=QueryName(value="tag"),
            occurrence=Count(value=1),
        ),
        IndexedLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_ENCODINGS,
            exchange_id=ExchangeId(value=UUID(int=343)),
            index=Count(value=0),
        ),
        JsonLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            view_id=view_id,
            pointer=JsonPointer(value="/answer"),
            body_view=BodyViewKind.JSON,
        ),
        ViewLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            view_id=ViewId(value=UUID(int=344)),
            body_view=BodyViewKind.SSE_EVENTS,
        ),
        BodyLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=35)),
            body_view=BodyViewKind.HANDLE,
        ),
        StreamLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.STREAM_RECORDS,
            view_id=view_id,
            connection_segment=Count(value=0),
            record_index=Count(value=1),
            nested_pointer=JsonPointer(value="/delta"),
        ),
        ScenarioLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.SCENARIO_STEP,
            step_execution_id=StepExecutionId(value=UUID(int=36)),
            field=field,
        ),
    )

    assert all(is_concrete_location(location) for location in locations)
    assert not is_concrete_location("/answer")

    for location in locations:
        assert location_environment_execution_id(location) == _owner()
        assert location_selector_source(location) is location.source
    assert location_body_view(locations[5]) is None
    assert location_static_field(locations[0]) == field
    assert location_static_field(locations[3]) is None


def test_location_binding_requires_exact_source_view_and_path() -> None:
    owner = _owner()
    status_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_STATUS,
        path=NoPath(),
        capabilities=_capabilities(),
        output=_output(kind=ValueKind.INTEGER),
    )
    status = ExchangeLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_STATUS,
        exchange_id=ExchangeId(value=UUID(int=350)),
        field=StaticFieldPath(segments=("response", "status")),
    )
    require_location_matches_selector(status, status_selector)

    wrong_source = ExchangeLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_PROTOCOL,
        exchange_id=status.exchange_id,
        field=StaticFieldPath(segments=("response", "protocol")),
    )
    with pytest.raises(ValueError, match="source does not match"):
        require_location_matches_selector(wrong_source, status_selector)
    wrong_field = msgspec.structs.replace(
        status,
        field=StaticFieldPath(segments=("response", "media")),
    )
    with pytest.raises(ValueError, match="selector source"):
        require_location_matches_selector(wrong_field, status_selector)

    pointer = JsonPointer(value="/answer")
    json_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=JsonPointerPath(pointer=pointer),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=_output(SelectorCardinality.ZERO_OR_ONE),
        body_view=BodyViewKind.JSON,
    )
    json_location = JsonLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        view_id=ViewId(value=UUID(int=351)),
        pointer=pointer,
        body_view=BodyViewKind.JSON,
    )
    require_location_matches_selector(json_location, json_selector)
    with pytest.raises(ValueError, match="pointer does not match"):
        require_location_matches_selector(
            msgspec.structs.replace(json_location, pointer=JsonPointer(value="/secret")),
            json_selector,
        )


def test_whole_streaming_body_selector_requires_a_whole_view_location() -> None:
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=NoPath(),
        capabilities=_capabilities(),
        output=_output(kind=ValueKind.ARRAY),
        body_view=BodyViewKind.SSE_EVENTS,
    )
    location = ViewLocation(
        environment_execution_id=_owner(),
        source=SelectorSource.RESPONSE_BODY,
        view_id=ViewId(value=UUID(int=352)),
        body_view=BodyViewKind.SSE_EVENTS,
    )
    require_location_matches_selector(location, selector)

    with pytest.raises(ValueError, match="body view does not match"):
        require_location_matches_selector(
            msgspec.structs.replace(location, body_view=BodyViewKind.JSON),
            selector,
        )


def test_location_path_binding_rejects_narrower_or_inexact_coordinates() -> None:
    owner = _owner()
    pointer = JsonPointer(value="/data/id")
    plural = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_MORE,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    stream_pointer_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.STREAM_EVENTS,
        path=JsonPointerPath(pointer=pointer),
        capabilities=plural,
        output=_output(SelectorCardinality.ZERO_OR_MORE),
    )
    stream = StreamLocation(
        environment_execution_id=owner,
        source=SelectorSource.STREAM_EVENTS,
        view_id=ViewId(value=UUID(int=353)),
        connection_segment=Count(value=0),
        record_index=Count(value=0),
        nested_pointer=pointer,
    )
    require_location_matches_selector(stream, stream_pointer_selector)
    with pytest.raises(ValueError, match="exact concrete pointer"):
        require_location_matches_selector(
            msgspec.structs.replace(stream, nested_pointer=JsonPointer(value="/data/other")),
            stream_pointer_selector,
        )

    whole_stream_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.STREAM_EVENTS,
        path=NoPath(),
        capabilities=plural,
        output=_output(SelectorCardinality.ZERO_OR_MORE),
    )
    with pytest.raises(ValueError, match="output types contradict"):
        msgspec.structs.replace(
            whole_stream_selector,
            output=_output(SelectorCardinality.ZERO_OR_MORE, ValueKind.STRING),
        )
    with pytest.raises(ValueError, match="whole stream-item"):
        require_location_matches_selector(stream, whole_stream_selector)

    json_path_capabilities = msgspec.structs.replace(plural, may_duplicate=True)
    json_path_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.STREAM_EVENTS,
        path=JsonPathExpression(
            canonical="$..id",
            fingerprint=_fingerprint(),
            capabilities=json_path_capabilities,
        ),
        capabilities=json_path_capabilities,
        output=_output(SelectorCardinality.ZERO_OR_MORE),
    )
    require_location_matches_selector(stream, json_path_selector)
    with pytest.raises(ValueError, match="exact nested JSON pointer"):
        require_location_matches_selector(
            msgspec.structs.replace(stream, nested_pointer=None),
            json_path_selector,
        )

    whole_json_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=NoPath(),
        capabilities=_capabilities(),
        output=_output(),
        body_view=BodyViewKind.JSON,
    )
    with pytest.raises(ValueError, match="whole body or decoded-view"):
        require_location_matches_selector(
            JsonLocation(
                environment_execution_id=owner,
                source=SelectorSource.RESPONSE_BODY,
                view_id=ViewId(value=UUID(int=354)),
                pointer=JsonPointer(value=""),
                body_view=BodyViewKind.JSON,
            ),
            whole_json_selector,
        )

    json_path_body_selector = msgspec.structs.replace(
        whole_json_selector,
        path=JsonPathExpression(
            canonical="$.id",
            fingerprint=_fingerprint(),
            capabilities=plural,
        ),
        capabilities=plural,
        output=_output(SelectorCardinality.ZERO_OR_MORE),
    )
    with pytest.raises(ValueError, match="JSON-addressable"):
        require_location_matches_selector(
            ViewLocation(
                environment_execution_id=owner,
                source=SelectorSource.RESPONSE_BODY,
                view_id=ViewId(value=UUID(int=355)),
                body_view=BodyViewKind.JSON,
            ),
            json_path_body_selector,
        )

    scenario_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.SCENARIO_STEP,
        path=StaticFieldPath(segments=("output",)),
        capabilities=_capabilities(),
        output=_output(),
    )
    scenario_location = ScenarioLocation(
        environment_execution_id=owner,
        source=SelectorSource.SCENARIO_STEP,
        step_execution_id=StepExecutionId(value=UUID(int=356)),
        field=cast("StaticFieldPath", scenario_selector.path),
    )
    require_location_matches_selector(scenario_location, scenario_selector)
    with pytest.raises(ValueError, match="compiled selector path"):
        require_location_matches_selector(
            msgspec.structs.replace(
                scenario_location,
                field=StaticFieldPath(segments=("input",)),
            ),
            scenario_selector,
        )

    with pytest.raises(TypeError, match="ConcreteLocation"):
        require_location_matches_selector(cast("JsonLocation", "bad"), whole_json_selector)
    with pytest.raises(TypeError, match="CompiledSelector"):
        require_location_matches_selector(stream, cast("CompiledSelector", "bad"))


def test_whole_scenario_step_rejects_nested_json_coordinates() -> None:
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.SCENARIO_STEP,
        path=NoPath(),
        capabilities=_capabilities(),
        output=_output(),
    )
    whole_step = ScenarioLocation(
        environment_execution_id=_owner(),
        source=SelectorSource.SCENARIO_STEP,
        step_execution_id=StepExecutionId(value=UUID(int=357)),
        field=StaticFieldPath(segments=("scenario", "step")),
    )
    require_location_matches_selector(whole_step, selector)

    with pytest.raises(ValueError, match="selector source"):
        require_location_matches_selector(
            JsonLocation(
                environment_execution_id=_owner(),
                source=SelectorSource.SCENARIO_STEP,
                view_id=ViewId(value=UUID(int=358)),
                pointer=JsonPointer(value="/nested"),
            ),
            selector,
        )


def test_location_direct_construction_rejects_invalid_coordinates() -> None:
    field = _field()
    with pytest.raises(TypeError):
        ExecutionLocation(
            environment_execution_id=cast("EnvironmentExecutionId", "bad"),
            source=SelectorSource.EXECUTION,
            field=field,
        )
    with pytest.raises(TypeError):
        AttemptLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.ATTEMPT,
            attempt_id=AttemptId(value=UUID(int=37)),
            field=cast("StaticFieldPath", "bad"),
        )
    with pytest.raises(TypeError):
        HeaderLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_HEADERS,
            exchange_id=cast("ExchangeId", "bad"),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            name=HeaderName(value="name"),
        )
    with pytest.raises(TypeError, match="occurrence"):
        HeaderLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_HEADERS,
            exchange_id=ExchangeId(value=UUID(int=381)),
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=cast("Count", 0),
            name=HeaderName(value="name"),
        )
    with pytest.raises(TypeError):
        HeaderLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_HEADERS,
            exchange_id=ExchangeId(value=UUID(int=38)),
            section=cast("HttpMessageSection", "bad"),
            occurrence=Count(value=0),
            name=HeaderName(value="name"),
        )
    with pytest.raises(ValueError, match="lowercase"):
        HeaderName(value="Content-Type")
    with pytest.raises(ValueError, match="both"):
        BodyLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=40)),
            body_view=BodyViewKind.HANDLE,
            start=ByteCount(value=0),
        )
    with pytest.raises(TypeError, match="BodyId"):
        BodyLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            body_id=cast("BodyId", "bad"),
            body_view=BodyViewKind.HANDLE,
        )
    with pytest.raises(TypeError):
        BodyLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=41)),
            body_view=BodyViewKind.HANDLE,
            start=cast("ByteCount", "bad"),
            end=ByteCount(value=1),
        )
    with pytest.raises(ValueError, match="greater"):
        BodyLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=42)),
            body_view=BodyViewKind.HANDLE,
            start=ByteCount(value=2),
            end=ByteCount(value=1),
        )
    with pytest.raises(TypeError, match="end"):
        BodyLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=421)),
            body_view=BodyViewKind.HANDLE,
            start=ByteCount(value=0),
            end=cast("ByteCount", "bad"),
        )
    with pytest.raises(TypeError, match="ViewId"):
        JsonLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.RESPONSE_BODY,
            view_id=cast("ViewId", "bad"),
            pointer=JsonPointer(value=""),
            body_view=BodyViewKind.JSON,
        )
    with pytest.raises(TypeError, match="ViewId"):
        StreamLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.STREAM_RECORDS,
            view_id=cast("ViewId", "bad"),
            connection_segment=Count(value=0),
            record_index=Count(value=0),
        )
    with pytest.raises(TypeError, match="indices"):
        StreamLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.STREAM_RECORDS,
            view_id=ViewId(value=UUID(int=422)),
            connection_segment=cast("Count", 0),
            record_index=Count(value=0),
        )
    with pytest.raises(TypeError, match="nested"):
        StreamLocation(
            environment_execution_id=_owner(),
            source=SelectorSource.STREAM_RECORDS,
            view_id=ViewId(value=UUID(int=423)),
            connection_segment=Count(value=0),
            record_index=Count(value=0),
            nested_pointer=cast("JsonPointer", "bad"),
        )


def test_location_variants_reject_contradictory_sources_and_coordinates() -> None:
    owner = _owner()
    field = _field()
    exchange_id = ExchangeId(value=UUID(int=430))
    view_id = ViewId(value=UUID(int=431))

    with pytest.raises(ValueError, match="execution selector source"):
        ExecutionLocation(
            environment_execution_id=owner,
            source=SelectorSource.ATTEMPT,
            field=field,
        )
    with pytest.raises(ValueError, match="attempt selector source"):
        AttemptLocation(
            environment_execution_id=owner,
            source=SelectorSource.EXECUTION,
            attempt_id=AttemptId(value=UUID(int=432)),
            field=field,
        )
    with pytest.raises(ValueError, match="incompatible"):
        ExchangeLocation(
            environment_execution_id=owner,
            source=SelectorSource.REQUEST_HEADERS,
            exchange_id=exchange_id,
            field=field,
        )

    header = HeaderLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_HEADERS,
        exchange_id=exchange_id,
        section=HttpMessageSection.RESPONSE_HEADER,
        occurrence=Count(value=0),
        name=HeaderName(value="x-id"),
    )
    with pytest.raises(TypeError, match="HeaderName"):
        msgspec.structs.replace(header, name=cast("HeaderName", "x-id"))
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(header, source=SelectorSource.REQUEST_HEADERS)
    with pytest.raises(ValueError, match="cannot identify"):
        msgspec.structs.replace(header, informational_status=103)

    informational = HeaderLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_INFORMATIONAL,
        exchange_id=exchange_id,
        section=HttpMessageSection.RESPONSE_INFORMATIONAL_HEADER,
        occurrence=Count(value=0),
        name=HeaderName(value="link"),
        informational_response=Count(value=0),
        informational_status=103,
    )
    with pytest.raises(TypeError, match="response occurrence"):
        msgspec.structs.replace(informational, informational_response=None)
    with pytest.raises(TypeError, match="integer status"):
        msgspec.structs.replace(informational, informational_status=cast("int", "103"))
    with pytest.raises(ValueError, match="between 100 and 199"):
        msgspec.structs.replace(informational, informational_status=200)

    named = NamedFieldLocation(
        environment_execution_id=owner,
        source=SelectorSource.REQUEST_QUERY,
        exchange_id=exchange_id,
        name=QueryName(value="tag"),
        occurrence=Count(value=0),
    )
    with pytest.raises(ValueError, match="query or cookie"):
        msgspec.structs.replace(named, source=SelectorSource.REQUEST_HEADERS)
    with pytest.raises(TypeError, match="ExchangeId"):
        msgspec.structs.replace(named, exchange_id=cast("ExchangeId", "bad"))
    with pytest.raises(TypeError, match="name type contradicts"):
        msgspec.structs.replace(named, name=CookieName(value="tag"))
    with pytest.raises(TypeError, match="occurrence must be a Count"):
        msgspec.structs.replace(named, occurrence=cast("Count", 0))

    indexed = IndexedLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_ENCODINGS,
        exchange_id=exchange_id,
        index=Count(value=0),
    )
    with pytest.raises(ValueError, match="response-encodings"):
        msgspec.structs.replace(indexed, source=SelectorSource.RESPONSE_HEADERS)
    with pytest.raises(TypeError, match="ExchangeId"):
        msgspec.structs.replace(indexed, exchange_id=cast("ExchangeId", "bad"))
    with pytest.raises(TypeError, match="index must be a Count"):
        msgspec.structs.replace(indexed, index=cast("Count", 0))

    body_json = JsonLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        view_id=view_id,
        pointer=JsonPointer(value=""),
        body_view=BodyViewKind.JSON,
    )
    with pytest.raises(ValueError, match="explicit JSON"):
        msgspec.structs.replace(body_json, body_view=None)
    scenario_json = msgspec.structs.replace(
        body_json,
        source=SelectorSource.SCENARIO_STEP,
        body_view=None,
    )
    with pytest.raises(ValueError, match="cannot declare"):
        msgspec.structs.replace(scenario_json, body_view=BodyViewKind.JSON)
    with pytest.raises(ValueError, match="not JSON-view compatible"):
        msgspec.structs.replace(scenario_json, source=SelectorSource.RESPONSE_STATUS)

    view = ViewLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        view_id=view_id,
        body_view=BodyViewKind.TEXT,
    )
    with pytest.raises(ValueError, match="request or response body"):
        msgspec.structs.replace(view, source=SelectorSource.RESPONSE_STATUS)
    with pytest.raises(TypeError, match="ViewId"):
        msgspec.structs.replace(view, view_id=cast("ViewId", "bad"))
    with pytest.raises(TypeError, match="BodyViewKind"):
        msgspec.structs.replace(view, body_view=cast("BodyViewKind", "text"))
    with pytest.raises(ValueError, match="decoded body view"):
        msgspec.structs.replace(view, body_view=BodyViewKind.HANDLE)

    body = BodyLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        body_id=BodyId(value=UUID(int=433)),
        body_view=BodyViewKind.HANDLE,
    )
    with pytest.raises(ValueError, match="request or response body"):
        msgspec.structs.replace(body, source=SelectorSource.RESPONSE_STATUS)
    with pytest.raises(TypeError, match="BodyViewKind"):
        msgspec.structs.replace(body, body_view=cast("BodyViewKind", "handle"))
    with pytest.raises(ValueError, match="handle or entity-byte"):
        msgspec.structs.replace(body, body_view=BodyViewKind.TEXT)

    stream = StreamLocation(
        environment_execution_id=owner,
        source=SelectorSource.STREAM_RECORDS,
        view_id=view_id,
        connection_segment=Count(value=0),
        record_index=Count(value=0),
    )
    with pytest.raises(ValueError, match="not stream compatible"):
        msgspec.structs.replace(stream, source=SelectorSource.RESPONSE_BODY)
    with pytest.raises(ValueError, match="only stream event or record"):
        msgspec.structs.replace(
            stream,
            source=SelectorSource.STREAM_CONTROLS,
            nested_pointer=JsonPointer(value="/data"),
        )
    with pytest.raises(ValueError, match="cannot declare"):
        msgspec.structs.replace(stream, body_view=BodyViewKind.JSON)

    scenario = ScenarioLocation(
        environment_execution_id=owner,
        source=SelectorSource.SCENARIO_STEP,
        step_execution_id=StepExecutionId(value=UUID(int=434)),
        field=field,
    )
    with pytest.raises(ValueError, match="Scenario-step selector source"):
        msgspec.structs.replace(scenario, source=SelectorSource.EXECUTION)
    with pytest.raises(TypeError, match="occurrence type"):
        msgspec.structs.replace(scenario, step_execution_id=cast("StepExecutionId", "bad"))
    with pytest.raises(TypeError, match="SelectorSource"):
        msgspec.structs.replace(body, source=cast("SelectorSource", "bad"))


def test_location_accessors_are_total_only_for_concrete_locations() -> None:
    owner = _owner()
    field = _field()
    attempt = AttemptLocation(
        environment_execution_id=owner,
        source=SelectorSource.ATTEMPT,
        attempt_id=AttemptId(value=UUID(int=435)),
        field=field,
    )
    exchange = ExchangeLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_STATUS,
        exchange_id=ExchangeId(value=UUID(int=436)),
        field=field,
    )
    scenario = ScenarioLocation(
        environment_execution_id=owner,
        source=SelectorSource.SCENARIO_STEP,
        step_execution_id=StepExecutionId(value=UUID(int=437)),
        field=field,
    )
    assert location_static_field(attempt) == field
    assert location_static_field(exchange) == field
    assert location_static_field(scenario) == field

    view_id = ViewId(value=UUID(int=438))
    json = JsonLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        view_id=view_id,
        pointer=JsonPointer(value=""),
        body_view=BodyViewKind.JSON,
    )
    view = ViewLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        view_id=view_id,
        body_view=BodyViewKind.TEXT,
    )
    body = BodyLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        body_id=BodyId(value=UUID(int=439)),
        body_view=BodyViewKind.HANDLE,
    )
    stream = StreamLocation(
        environment_execution_id=owner,
        source=SelectorSource.STREAM_RECORDS,
        view_id=view_id,
        connection_segment=Count(value=0),
        record_index=Count(value=0),
    )
    assert location_body_view(json) is BodyViewKind.JSON
    assert location_body_view(view) is BodyViewKind.TEXT
    assert location_body_view(body) is BodyViewKind.HANDLE
    assert location_body_view(stream) is None

    for accessor in (
        location_environment_execution_id,
        location_selector_source,
        location_body_view,
        location_static_field,
    ):
        with pytest.raises(TypeError, match="location"):
            accessor(cast("object", "bad"))  # type: ignore[arg-type]


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
    location = JsonLocation(
        environment_execution_id=_owner(),
        source=SelectorSource.RESPONSE_BODY,
        view_id=ViewId(value=UUID(int=43)),
        pointer=JsonPointer(value=""),
        body_view=BodyViewKind.JSON,
    )
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
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=JsonPointerPath(pointer=location.pointer),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=_output(SelectorCardinality.ZERO_OR_ONE),
        body_view=BodyViewKind.JSON,
    )
    assert SelectionReady(
        selector=selector,
        environment_execution_id=location.environment_execution_id,
        matches=(match,),
    ).matches == (match,)
    with pytest.raises(ValueError, match="cannot repeat a location"):
        SelectionReady(
            selector=selector,
            environment_execution_id=location.environment_execution_id,
            matches=(match, match),
        )
    with pytest.raises(ValueError, match="different Environment execution"):
        SelectionReady(
            selector=selector,
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=999)),
            matches=(match,),
        )
    duplicate_capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_MORE,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=True,
        uses_descendant=False,
        uses_filter=False,
    )
    duplicate_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=JsonPathExpression(
            canonical="$..value",
            fingerprint=_fingerprint(),
            capabilities=duplicate_capabilities,
        ),
        capabilities=duplicate_capabilities,
        output=_output(SelectorCardinality.ZERO_OR_MORE),
        body_view=BodyViewKind.JSON,
    )
    assert (
        len(
            SelectionReady(
                selector=duplicate_selector,
                environment_execution_id=location.environment_execution_id,
                matches=(match, match),
            ).matches
        )
        == 2
    )
    with pytest.raises(TypeError):
        SelectionReady(
            selector=selector,
            environment_execution_id=location.environment_execution_id,
            matches=cast("tuple[SelectionMatch, ...]", [match]),
        )
    with pytest.raises(TypeError, match="unsupported match"):
        SelectionReady(
            selector=selector,
            environment_execution_id=location.environment_execution_id,
            matches=(cast("SelectionMatch", "bad"),),
        )
    with pytest.raises(TypeError, match="CompiledSelector"):
        SelectionReady(
            selector=cast("CompiledSelector", "bad"),
            environment_execution_id=location.environment_execution_id,
            matches=(),
        )
    with pytest.raises(TypeError, match="EnvironmentExecutionId"):
        SelectionReady(
            selector=selector,
            environment_execution_id=cast("EnvironmentExecutionId", "bad"),
            matches=(),
        )


def test_selection_ready_enforces_cardinality_and_runtime_value_kind() -> None:
    owner = _owner()
    status_location = ExchangeLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_STATUS,
        exchange_id=ExchangeId(value=UUID(int=1240)),
        field=StaticFieldPath(segments=("response", "status")),
    )
    status_match = SelectionMatch(value=JsonInteger(value=200), location=status_location)
    status_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_STATUS,
        path=NoPath(),
        capabilities=_capabilities(),
        output=_output(SelectorCardinality.EXACTLY_ONE, kind=ValueKind.INTEGER),
    )
    assert SelectionReady(
        selector=status_selector,
        environment_execution_id=owner,
        matches=(status_match,),
    ).matches == (status_match,)
    with pytest.raises(ValueError, match="exactly one match"):
        SelectionReady(
            selector=status_selector,
            environment_execution_id=owner,
            matches=(),
        )
    with pytest.raises(ValueError, match="value kind"):
        SelectionReady(
            selector=status_selector,
            environment_execution_id=owner,
            matches=(
                msgspec.structs.replace(
                    status_match,
                    value=JsonString(value="200"),
                ),
            ),
        )

    pointer = JsonPointer(value="/id")
    optional_selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=JsonPointerPath(pointer=pointer),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=_output(SelectorCardinality.ZERO_OR_ONE),
        body_view=BodyViewKind.JSON,
    )
    optional_matches = tuple(
        SelectionMatch(
            value=JsonInteger(value=index),
            location=JsonLocation(
                environment_execution_id=owner,
                source=SelectorSource.RESPONSE_BODY,
                view_id=ViewId(value=UUID(int=1241 + index)),
                pointer=pointer,
                body_view=BodyViewKind.JSON,
            ),
        )
        for index in (1, 2)
    )
    with pytest.raises(ValueError, match="more than one match"):
        SelectionReady(
            selector=optional_selector,
            environment_execution_id=owner,
            matches=optional_matches,
        )


def test_selection_match_binds_body_and_view_handle_identities() -> None:
    owner = _owner()
    body_id = BodyId(value=UUID(int=1250))
    body_location = BodyLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        body_id=body_id,
        body_view=BodyViewKind.HANDLE,
    )
    assert SelectionMatch(value=body_id, location=body_location).value == body_id
    with pytest.raises(ValueError, match="BodyId"):
        SelectionMatch(
            value=BodyId(value=UUID(int=1251)),
            location=body_location,
        )

    view_id = ViewId(value=UUID(int=1252))
    view_location = ViewLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_BODY,
        view_id=view_id,
        body_view=BodyViewKind.TEXT,
    )
    assert SelectionMatch(value=view_id, location=view_location).value == view_id
    with pytest.raises(ValueError, match="ViewId"):
        SelectionMatch(
            value=ViewId(value=UUID(int=1253)),
            location=view_location,
        )
    with pytest.raises(ValueError, match="view location"):
        SelectionMatch(value=view_id, location=body_location)
    with pytest.raises(ValueError, match="cannot be sensitive"):
        SelectionMatch(
            value=SensitiveValue(
                view_id,
                origins=(TaintOriginId(value="selector.sensitive-view-id"),),
            ),
            location=view_location,
        )


def test_budget_variant_rejects_non_budget_evidence() -> None:
    with pytest.raises(TypeError):
        SelectionBudgetExceeded(exhaustion=cast("BudgetExceeded", "bad"))
