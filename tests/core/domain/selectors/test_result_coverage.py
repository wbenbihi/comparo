"""Close branch coverage for selector result and view-provenance contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderIdentity
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.body_view_semantics import validate_requested_body_decoder
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.result_values import require_match_contract
from comparo.core.domain.selectors.result_values import require_view_provenance
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionItemBudgetIssue
from comparo.core.domain.selectors.results import SelectionItemDecodeIssue
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SelectionViewFailureProvenance
from comparo.core.domain.selectors.results import SelectionViewProvenance
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.results import _require_partial_reason_evidence
from comparo.core.domain.selectors.results import is_selection_result
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonString
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.stream_factories import decoder
from tests.core.selection.json_evaluator_factories import selection_meter


def _selector(
    source: SelectorSource,
    *,
    body_view: BodyViewKind | None = None,
) -> CompiledSelector:
    path = NoPath()
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="7" * 64),
        ),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=capabilities.cardinality,
        ),
        body_view=body_view,
    )


def _view_provenance(
    *,
    source: SelectorSource = SelectorSource.RESPONSE_BODY,
    requested: BodyViewKind = BodyViewKind.TEXT,
    view_id: int = 11,
) -> SelectionViewProvenance:
    view_kind = {
        BodyViewKind.TEXT: ViewKind.TEXT,
        BodyViewKind.JSON: ViewKind.JSON,
        BodyViewKind.SSE_EVENTS: ViewKind.SSE,
    }[requested]
    return SelectionViewProvenance(
        view_id=ViewId(value=UUID(int=view_id)),
        source=source,
        requested=requested,
        decoder=decoder(view_kind),
        auto_selection=None,
        media=None,
        completeness=ViewCompleteness.FULL,
    )


def _failure() -> SelectionViewFailureProvenance:
    return SelectionViewFailureProvenance(
        body_id=BodyId(value=UUID(int=12)),
        source=SelectorSource.RESPONSE_BODY,
        requested=BodyViewKind.JSON,
        decoder=decoder(ViewKind.JSON),
        auto_selection=None,
        media=None,
    )


def _stream_partial() -> SelectionPartial:
    selector = _selector(SelectorSource.STREAM_RECORDS)
    owner = EnvironmentExecutionId(value=UUID(int=13))
    provenance = _view_provenance(
        source=selector.source,
        requested=BodyViewKind.SSE_EVENTS,
    )
    issue = SelectionItemDecodeIssue(
        location=StreamLocation(
            environment_execution_id=owner,
            source=selector.source,
            view_id=provenance.view_id,
            connection_segment=Count(value=0),
            item_index=Count(value=0),
            category_index=Count(value=0),
        ),
        decoder=decoder(ViewKind.SSE),
        code=DecodeErrorCode.INVALID_SYNTAX,
    )
    return SelectionPartial(
        selector=selector,
        environment_execution_id=owner,
        matches=(),
        reason=SelectionPartialReason.ITEM_FAILURE,
        issues=(issue,),
        views=(provenance,),
        stream_extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
    )


def _body_partial() -> SelectionPartial:
    selector = _selector(SelectorSource.RESPONSE_BODY, body_view=BodyViewKind.TEXT)
    owner = EnvironmentExecutionId(value=UUID(int=14))
    provenance = _view_provenance()
    match = SelectionMatch(
        value=JsonString(value="prefix"),
        location=ViewLocation(
            environment_execution_id=owner,
            source=selector.source,
            view_id=provenance.view_id,
            body_view=BodyViewKind.TEXT,
        ),
    )
    return SelectionPartial(
        selector=selector,
        environment_execution_id=owner,
        matches=(match,),
        reason=SelectionPartialReason.VIEW_PREFIX,
        views=(provenance,),
    )


def _selection_exhaustion() -> BudgetExceeded:
    exhaustion = selection_meter(work=0).charge_work()
    assert exhaustion is not None
    return exhaustion


def test_body_view_semantics_reject_every_contradiction() -> None:
    json_decoder = decoder(ViewKind.JSON)
    automatic = AutoViewSelection(
        registry=DecoderRegistryIdentity(value="comparo/auto@1"),
        decoder=json_decoder,
    )
    validate_requested_body_decoder(BodyViewKind.JSON, json_decoder, None)
    validate_requested_body_decoder(BodyViewKind.AUTO, json_decoder, automatic)

    with pytest.raises(TypeError, match="typed request"):
        validate_requested_body_decoder(cast("BodyViewKind", "json"), json_decoder, None)
    with pytest.raises(TypeError, match="typed request"):
        validate_requested_body_decoder(
            BodyViewKind.JSON,
            cast("DecoderSpec", "json"),
            None,
        )
    for requested in (BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES):
        with pytest.raises(ValueError, match="not decoded"):
            validate_requested_body_decoder(requested, json_decoder, None)
    with pytest.raises(ValueError, match="require automatic"):
        validate_requested_body_decoder(BodyViewKind.AUTO, json_decoder, None)
    with pytest.raises(ValueError, match="contradicts"):
        validate_requested_body_decoder(
            BodyViewKind.AUTO,
            json_decoder,
            cast("AutoViewSelection", "auto"),
        )
    with pytest.raises(ValueError, match="contradicts"):
        validate_requested_body_decoder(
            BodyViewKind.AUTO,
            json_decoder,
            msgspec.structs.replace(automatic, decoder=decoder(ViewKind.TEXT)),
        )
    with pytest.raises(ValueError, match="explicit body views"):
        validate_requested_body_decoder(BodyViewKind.JSON, json_decoder, automatic)
    with pytest.raises(ValueError, match="contradicts"):
        validate_requested_body_decoder(BodyViewKind.TEXT, json_decoder, None)


def test_view_provenance_validates_each_typed_boundary() -> None:
    valid = _view_provenance()
    auto_decoder = decoder(ViewKind.JSON)
    automatic = AutoViewSelection(
        registry=DecoderRegistryIdentity(value="comparo/auto@1"),
        decoder=auto_decoder,
    )

    invalid_fields = (
        ("view_id", cast("ViewId", "bad"), "ViewId"),
        ("source", cast("SelectorSource", "bad"), "SelectorSource"),
        ("requested", cast("BodyViewKind", "bad"), "requested body view"),
        ("decoder", cast("DecoderSpec", "bad"), "DecoderSpec"),
        ("auto_selection", cast("AutoViewSelection", "bad"), "auto provenance"),
        ("media", cast("object", "bad"), "media"),
        ("completeness", cast("ViewCompleteness", "bad"), "completeness"),
    )
    for field, value, message in invalid_fields:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(valid, **{field: value})

    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(
            valid,
            requested=BodyViewKind.AUTO,
            decoder=auto_decoder,
            auto_selection=msgspec.structs.replace(
                automatic,
                decoder=decoder(ViewKind.TEXT),
            ),
        )


def test_failure_provenance_validates_owner_source_and_media() -> None:
    valid = _failure()
    with pytest.raises(TypeError, match="BodyId"):
        msgspec.structs.replace(valid, body_id=cast("BodyId", "bad"))
    with pytest.raises(ValueError, match="HTTP body"):
        msgspec.structs.replace(valid, source=SelectorSource.RESPONSE_STATUS)
    with pytest.raises(TypeError, match="media"):
        msgspec.structs.replace(valid, media=cast("object", "bad"))


def test_view_collection_requires_unique_exact_provenance() -> None:
    selector = _selector(SelectorSource.RESPONSE_BODY, body_view=BodyViewKind.TEXT)
    provenance = _view_provenance()
    location = ViewLocation(
        environment_execution_id=EnvironmentExecutionId(value=UUID(int=15)),
        source=selector.source,
        view_id=provenance.view_id,
        body_view=BodyViewKind.TEXT,
    )
    require_view_provenance((provenance,), selector, (location,))

    with pytest.raises(TypeError, match="immutable provenance tuple"):
        require_view_provenance([], selector, (location,))
    with pytest.raises(TypeError, match="immutable provenance tuple"):
        require_view_provenance((cast("SelectionViewProvenance", "bad"),), selector, (location,))
    with pytest.raises(ValueError, match="unique"):
        require_view_provenance((provenance, provenance), selector, (location,))
    with pytest.raises(ValueError, match="source"):
        require_view_provenance(
            (msgspec.structs.replace(provenance, source=SelectorSource.REQUEST_BODY),),
            selector,
            (location,),
        )
    with pytest.raises(ValueError, match="exact view provenance"):
        require_view_provenance((), selector, (location,))


def test_item_issue_contracts_reject_untyped_or_non_decoding_evidence() -> None:
    partial = _stream_partial()
    issue = partial.issues[0]
    assert type(issue) is SelectionItemDecodeIssue
    with pytest.raises(TypeError, match="StreamLocation"):
        msgspec.structs.replace(issue, location=cast("StreamLocation", "bad"))
    with pytest.raises(TypeError, match="DecoderSpec"):
        msgspec.structs.replace(issue, decoder=cast("DecoderSpec", "bad"))
    with pytest.raises(TypeError, match="DecodeErrorCode"):
        msgspec.structs.replace(issue, code=cast("DecodeErrorCode", "bad"))

    exhaustion = view_exhaustion(ViewBudgetKind.JSON_NODES)
    budget_issue = SelectionItemBudgetIssue(
        location=issue.location,
        decoder=issue.decoder,
        exhaustion=exhaustion,
    )
    with pytest.raises(TypeError, match="StreamLocation"):
        msgspec.structs.replace(budget_issue, location=cast("StreamLocation", "bad"))
    with pytest.raises(TypeError, match="DecoderSpec"):
        msgspec.structs.replace(budget_issue, decoder=cast("DecoderSpec", "bad"))
    with pytest.raises(TypeError, match="BudgetExceeded"):
        msgspec.structs.replace(budget_issue, exhaustion=cast("object", "bad"))
    with pytest.raises(ValueError, match="decoding evidence"):
        msgspec.structs.replace(
            budget_issue,
            exhaustion=_selection_exhaustion(),
        )


def test_partial_components_and_reason_evidence_are_exhaustive() -> None:
    stream = _stream_partial()
    body = _body_partial()
    assert body.matches[0].value == JsonString(value="prefix")

    invalid_components = (
        (stream, "selector", cast("CompiledSelector", "bad"), TypeError),
        (stream, "environment_execution_id", cast("EnvironmentExecutionId", "bad"), TypeError),
        (stream, "matches", cast("tuple[SelectionMatch, ...]", []), TypeError),
        (stream, "matches", (cast("SelectionMatch", "bad"),), TypeError),
        (stream, "reason", cast("SelectionPartialReason", "bad"), TypeError),
        (stream, "issues", cast("tuple[object, ...]", []), TypeError),
        (stream, "issues", (cast("SelectionItemDecodeIssue", "bad"),), TypeError),
        (stream, "stream_extent", cast("StreamExtent", "bad"), TypeError),
    )
    for result, field, value, error in invalid_components:
        with pytest.raises(error):
            msgspec.structs.replace(result, **{field: value})

    with pytest.raises(ValueError, match="complete match or located issue"):
        msgspec.structs.replace(body, matches=())
    with pytest.raises(ValueError, match="exactly native stream"):
        msgspec.structs.replace(stream, stream_extent=None)
    with pytest.raises(ValueError, match="exactly native stream"):
        msgspec.structs.replace(
            body,
            stream_extent=StreamExtent(
                completeness=StreamCompleteness.FULL,
                termination=StreamTerminationReason.REMOTE_EOF,
            ),
        )
    with pytest.raises(ValueError, match="HTTP body source"):
        msgspec.structs.replace(stream, reason=SelectionPartialReason.VIEW_PREFIX)
    with pytest.raises(ValueError, match="cannot retain stream evidence"):
        msgspec.structs.replace(body, issues=stream.issues)
    with pytest.raises(ValueError, match="located stream issues"):
        msgspec.structs.replace(body, reason=SelectionPartialReason.ITEM_FAILURE)

    stream_match = SelectionMatch(
        value=JsonString(value="record"),
        location=stream.issues[0].location,
    )
    with pytest.raises(ValueError, match="located stream issues"):
        msgspec.structs.replace(stream, matches=(stream_match,), issues=())
    window = SelectionPartial(
        selector=stream.selector,
        environment_execution_id=stream.environment_execution_id,
        matches=(stream_match,),
        reason=SelectionPartialReason.STREAM_WINDOW_INCOMPLETE,
        views=stream.views,
        stream_extent=StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.DECODE_FAILURE,
        ),
    )
    with pytest.raises(ValueError, match="incomplete extent"):
        msgspec.structs.replace(window, issues=stream.issues)
    with pytest.raises(ValueError, match="incomplete extent"):
        msgspec.structs.replace(window, stream_extent=stream.stream_extent)


def test_ready_native_stream_requires_exact_full_or_window_extent() -> None:
    selector = _selector(SelectorSource.STREAM_RECORDS)
    owner = EnvironmentExecutionId(value=UUID(int=130))
    full = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    ready = SelectionReady(
        selector=selector,
        environment_execution_id=owner,
        matches=(),
        stream_extent=full,
    )
    assert ready.stream_extent is full

    window = StreamExtent(
        completeness=StreamCompleteness.WINDOW,
        termination=StreamTerminationReason.EVENT_COUNT_WINDOW,
    )
    assert msgspec.structs.replace(ready, stream_extent=window).stream_extent is window

    with pytest.raises(TypeError, match="ready stream extent"):
        msgspec.structs.replace(ready, stream_extent=cast("StreamExtent", "bad"))
    with pytest.raises(ValueError, match="exactly native stream ready"):
        msgspec.structs.replace(ready, stream_extent=None)
    with pytest.raises(ValueError, match="full or window"):
        msgspec.structs.replace(
            ready,
            stream_extent=StreamExtent(
                completeness=StreamCompleteness.INCOMPLETE,
                termination=StreamTerminationReason.DECODE_FAILURE,
            ),
        )

    body = _body_partial()
    body_ready = SelectionReady(
        selector=body.selector,
        environment_execution_id=body.environment_execution_id,
        matches=body.matches,
        views=body.views,
    )
    with pytest.raises(ValueError, match="exactly native stream ready"):
        msgspec.structs.replace(body_ready, stream_extent=full)


def test_partial_issues_are_bound_to_execution_source_and_unique_location() -> None:
    result = _stream_partial()
    issue = result.issues[0]
    with pytest.raises(ValueError, match="another execution"):
        msgspec.structs.replace(
            result,
            issues=(
                msgspec.structs.replace(
                    issue,
                    location=msgspec.structs.replace(
                        issue.location,
                        environment_execution_id=EnvironmentExecutionId(value=UUID(int=99)),
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="source contradicts"):
        msgspec.structs.replace(
            result,
            issues=(
                msgspec.structs.replace(
                    issue,
                    location=msgspec.structs.replace(
                        issue.location,
                        source=SelectorSource.STREAM_EVENTS,
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(result, issues=(issue, issue))


def test_failure_results_validate_all_optional_provenance_edges() -> None:
    failure = _failure()
    code = DiagnosticCode(value="selection.test")
    decode = SelectionDecodeFailure(code=code, provenance=failure)
    assert decode.view is BodyViewKind.JSON

    with pytest.raises(TypeError, match="exact view provenance"):
        msgspec.structs.replace(decode, provenance=cast("SelectionViewFailureProvenance", "bad"))
    with pytest.raises(TypeError, match="SourceUnavailableReason"):
        SelectionSourceUnavailable(reason=cast("SourceUnavailableReason", "bad"))
    with pytest.raises(TypeError, match="view failure provenance"):
        SelectionSourceUnavailable(
            reason=SourceUnavailableReason.NO_BODY,
            view_failure=cast("SelectionViewFailureProvenance", "bad"),
        )
    with pytest.raises(TypeError, match="DiagnosticCode"):
        SelectionFactUnavailable(code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="DiagnosticCode"):
        SelectionObservedInvalid(code=cast("DiagnosticCode", "bad"))

    partial_view = SelectionIncomplete(
        reason=SelectionIncompleteReason.PARTIAL_VIEW,
        view_failure=failure,
        view_reason=IncompleteViewReason.ENTITY_PARTIAL,
    )
    assert partial_view.view_reason is IncompleteViewReason.ENTITY_PARTIAL
    with pytest.raises(TypeError, match="typed"):
        msgspec.structs.replace(
            partial_view,
            view_reason=cast("IncompleteViewReason", "bad"),
        )
    with pytest.raises(ValueError, match="requires view provenance"):
        SelectionIncomplete(reason=SelectionIncompleteReason.PARTIAL_VIEW)
    with pytest.raises(ValueError, match="requires view provenance"):
        SelectionIncomplete(
            reason=SelectionIncompleteReason.PARTIAL_CAPTURE,
            view_failure=failure,
        )
    with pytest.raises(ValueError, match="view incompleteness reason"):
        SelectionIncomplete(
            reason=SelectionIncompleteReason.PARTIAL_CAPTURE,
            view_reason=IncompleteViewReason.ENTITY_PARTIAL,
        )

    exhaustion = view_exhaustion(ViewBudgetKind.JSON_NODES)
    SelectionBudgetExceeded(exhaustion=exhaustion, view_failure=failure)
    with pytest.raises(ValueError, match="exactly decoding"):
        SelectionBudgetExceeded(exhaustion=exhaustion)
    with pytest.raises(ValueError, match="exactly decoding"):
        SelectionBudgetExceeded(
            exhaustion=_selection_exhaustion(),
            view_failure=failure,
        )

    for value in (
        SelectionUnsupported(code=code),
        SelectionCancelled(code=code),
    ):
        with pytest.raises(TypeError, match="view failure provenance"):
            msgspec.structs.replace(
                value,
                view_failure=cast("SelectionViewFailureProvenance", "bad"),
            )

    variants = (
        SelectionReady(
            selector=_body_partial().selector,
            environment_execution_id=_body_partial().environment_execution_id,
            matches=_body_partial().matches,
            views=_body_partial().views,
        ),
        _body_partial(),
        SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY),
        SelectionFactUnavailable(code=code),
        SelectionObservedInvalid(code=code),
        decode,
        partial_view,
        SelectionBudgetExceeded(exhaustion=exhaustion, view_failure=failure),
        SelectionUnsupported(code=code),
        SelectionCancelled(code=code),
    )
    assert all(is_selection_result(value) for value in variants)
    assert not is_selection_result("bad")


def test_auto_provenance_must_retain_the_exact_decoder() -> None:
    json_decoder = DecoderSpec(
        identity=DecoderIdentity(value="comparo/json@1"),
        view=ViewKind.JSON,
    )
    automatic = AutoViewSelection(
        registry=DecoderRegistryIdentity(value="comparo/auto@1"),
        decoder=json_decoder,
    )
    provenance = SelectionViewProvenance(
        view_id=ViewId(value=UUID(int=77)),
        source=SelectorSource.RESPONSE_BODY,
        requested=BodyViewKind.AUTO,
        decoder=json_decoder,
        auto_selection=automatic,
        media=None,
        completeness=ViewCompleteness.FULL,
    )
    assert provenance.auto_selection is automatic
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(provenance, decoder=decoder(ViewKind.TEXT))


def test_sensitive_match_properties_cover_public_and_sensitive_values() -> None:
    body = _body_partial()
    public = body.matches[0]
    origin = TaintOriginId(value="test.secret")
    sensitive = SelectionMatch(
        value=SensitiveValue(JsonString(value="secret"), origins=(origin,)),
        location=public.location,
    )
    assert public.taint_origins == ()
    assert sensitive.taint_origins == (origin,)


def test_bare_query_contract_is_limited_to_public_query_values() -> None:
    owner = EnvironmentExecutionId(value=UUID(int=88))

    def field_selector(source: SelectorSource, name: str) -> CompiledSelector:
        path = HttpFieldPath(name=name)
        capabilities = derive_selector_capabilities(source, path)
        return CompiledSelector(
            fingerprint=Fingerprint(
                algorithm="sha256",
                digest=Sha256Digest(hex_value="8" * 64),
            ),
            source=source,
            path=path,
            capabilities=capabilities,
            output=ValueContract(
                kinds=(ValueKind.HTTP_FIELD_VALUE,),
                cardinality=capabilities.cardinality,
            ),
        )

    cookie_selector = field_selector(SelectorSource.REQUEST_COOKIES, "session")
    cookie_match = SelectionMatch(
        value=BareQueryFieldValue(),
        location=NamedFieldLocation(
            environment_execution_id=owner,
            source=cookie_selector.source,
            exchange_id=ExchangeId(value=UUID(int=89)),
            name=CookieName(value="session"),
            occurrence=Count(value=0),
        ),
    )
    with pytest.raises(ValueError, match="request-query"):
        require_match_contract(cookie_match, cookie_selector, owner)

    query_selector = field_selector(SelectorSource.REQUEST_QUERY, "flag")
    query_match = SelectionMatch(
        value=SensitiveValue(
            BareQueryFieldValue(),
            origins=(TaintOriginId(value="test.query"),),
        ),
        location=NamedFieldLocation(
            environment_execution_id=owner,
            source=query_selector.source,
            exchange_id=ExchangeId(value=UUID(int=90)),
            name=QueryName(value="flag"),
            occurrence=Count(value=0),
        ),
    )
    with pytest.raises(ValueError, match="cannot carry sensitive"):
        require_match_contract(query_match, query_selector, owner)


def test_partial_reason_helper_has_a_total_defensive_fallthrough() -> None:
    fabricated = SimpleNamespace(reason=None)
    _require_partial_reason_evidence(
        cast("SelectionPartial", fabricated),
        stream_source=False,
    )
