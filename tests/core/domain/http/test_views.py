"""Tests for portable timing facts and storage-backed decoded-view outcomes."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderIdentity
from comparo.core.domain.http.views import DecoderOption
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.http.views import ReadyView
from comparo.core.domain.http.views import UnsupportedViewReason
from comparo.core.domain.http.views import ViewBudgetExceeded
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewCancelled
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewIncomplete
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewNoBody
from comparo.core.domain.http.views import ViewStorageToken
from comparo.core.domain.http.views import ViewUnsupported
from comparo.core.domain.http.views import is_decoded_view_outcome
from comparo.core.domain.identity import ViewId
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonValue
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import multipart_body
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import view_handle


def test_decoder_specs_retain_registry_options_and_reject_duplicates() -> None:
    option = DecoderOption(name="strict-mode", value=JsonBoolean(value=True))
    spec = DecoderSpec(
        identity=DecoderIdentity(value="comparo/json@1"),
        view=ViewKind.JSON,
        options=(option,),
    )
    selection = AutoViewSelection(
        registry=DecoderRegistryIdentity(value="comparo/auto@1"),
        decoder=spec,
    )
    assert selection.decoder.options == (option,)

    with pytest.raises(ValueError, match="unique"):
        DecoderSpec(identity=spec.identity, view=spec.view, options=(option, option))
    with pytest.raises(ValueError, match="option name"):
        DecoderOption(name="Not_Canonical", value=JsonBoolean(value=True))
    with pytest.raises(TypeError, match="JsonValue"):
        DecoderOption(name="strict-mode", value=cast("JsonValue", "true"))
    with pytest.raises(ValueError, match="decoder identity"):
        DecoderIdentity(value="json")
    with pytest.raises(ValueError, match="decoder registry"):
        DecoderRegistryIdentity(value="auto")
    with pytest.raises(TypeError, match="spec identity"):
        DecoderSpec(identity=cast("DecoderIdentity", "json"), view=ViewKind.JSON)
    with pytest.raises(TypeError, match="spec view"):
        DecoderSpec(identity=spec.identity, view=cast("ViewKind", "json"))
    with pytest.raises(TypeError, match="immutable tuple"):
        DecoderSpec(
            identity=spec.identity, view=spec.view, options=cast("tuple[DecoderOption, ...]", [])
        )
    with pytest.raises(TypeError, match="auto-view registry"):
        AutoViewSelection(registry=cast("DecoderRegistryIdentity", "auto"), decoder=spec)
    with pytest.raises(TypeError, match="auto-view decoder"):
        AutoViewSelection(
            registry=DecoderRegistryIdentity(value="comparo/auto@1"),
            decoder=cast("DecoderSpec", "json"),
        )
    with pytest.raises(ValueError, match="storage locator"):
        ViewStorageToken(value="")


def test_ready_prefixes_are_limited_to_prefix_safe_decoders() -> None:
    json_handle = view_handle()
    full = ReadyView(handle=json_handle, completeness=ViewCompleteness.FULL)
    assert full.handle.decoder.view is ViewKind.JSON
    with pytest.raises(ValueError, match="prefix-safe"):
        msgspec.structs.replace(json_handle, completeness=ViewCompleteness.PREFIX)

    for kind in (
        ViewKind.BYTES,
        ViewKind.TEXT,
        ViewKind.SSE,
        ViewKind.NDJSON,
        ViewKind.JSON_SEQUENCE,
    ):
        prefix = ReadyView(
            handle=view_handle(kind, completeness=ViewCompleteness.PREFIX),
            completeness=ViewCompleteness.PREFIX,
        )
        assert prefix.completeness is ViewCompleteness.PREFIX


def test_view_failures_are_typed_and_retain_decoder_identity() -> None:
    spec = decoder()
    outcomes = (
        ViewNoBody(decoder=spec),
        ViewDecodeError(decoder=spec, code=DecodeErrorCode.INVALID_SYNTAX),
        ViewIncomplete(decoder=spec, reason=IncompleteViewReason.ENTITY_PARTIAL),
        ViewBudgetExceeded(
            decoder=spec,
            exhaustion=view_exhaustion(ViewBudgetKind.JSON_NODES),
        ),
        ViewUnsupported(decoder=spec, reason=UnsupportedViewReason.DECODER_UNAVAILABLE),
        ViewCancelled(decoder=spec),
    )
    assert all(outcome.decoder is spec for outcome in outcomes)


def test_automatic_view_selection_is_retained_on_ready_and_failed_outcomes() -> None:
    spec = decoder()
    selection = AutoViewSelection(
        registry=DecoderRegistryIdentity(value="comparo/auto@1"),
        decoder=spec,
    )
    original = view_handle()
    selected_handle = DecodedViewHandle(
        view_id=original.view_id,
        source=original.source,
        locator=original.locator,
        decoder=spec,
        fingerprint=original.fingerprint,
        sensitivity=original.sensitivity,
        completeness=original.completeness,
        auto_selection=selection,
    )
    assert selected_handle.auto_selection is selection
    assert ViewNoBody(decoder=spec, auto_selection=selection).auto_selection is selection
    assert ViewBudgetKind.JSON_TOKENS.value == "json-tokens"

    mismatched = decoder(ViewKind.TEXT)
    with pytest.raises(ValueError, match="resolved decoder"):
        ViewDecodeError(
            decoder=mismatched,
            code=DecodeErrorCode.INVALID_TEXT,
            auto_selection=selection,
        )


def test_view_storage_locator_is_opaque_and_not_serializable() -> None:
    handle = view_handle()
    rendered = repr(handle)
    assert "private/view/path" not in rendered
    with pytest.raises(TypeError) as captured:
        msgspec.json.encode(handle)
    assert "private/view/path" not in str(captured.value)


def test_sensitive_view_content_requires_a_sealed_content_fingerprint() -> None:
    locator = SensitiveValue(
        ViewStorageToken(value="private/view/path"),
        origins=(TaintOriginId(value="view-store"),),
    )
    with pytest.raises(TypeError, match="must remain sealed"):
        DecodedViewHandle(
            view_id=ViewId(value=UUID(int=3)),
            source=body_handle(size=4),
            locator=locator,
            decoder=decoder(),
            fingerprint=fingerprint("f"),
            sensitivity=Sensitivity.SENSITIVE,
            completeness=ViewCompleteness.FULL,
        )


def test_request_snapshot_is_a_complete_decoded_view_source() -> None:
    materialized, _ = multipart_body()
    handle = view_handle(source=materialized.source)
    assert handle.source is materialized.source
    assert handle.body_id == materialized.source.source_id
    with pytest.raises(TypeError, match="request body handle"):
        msgspec.structs.replace(handle, source=cast("BodyHandle", object()))


def test_view_handle_rejects_wrong_components_and_sensitivity_containment() -> None:
    locator = SensitiveValue(
        ViewStorageToken(value="private/view/path"),
        origins=(TaintOriginId(value="view-store"),),
    )
    public_source = msgspec.structs.replace(
        body_handle(size=4),
        digest=digest("a"),
        sensitivity=Sensitivity.PUBLIC,
    )
    public = DecodedViewHandle(
        view_id=ViewId(value=UUID(int=5)),
        source=public_source,
        locator=locator,
        decoder=decoder(),
        fingerprint=fingerprint("a"),
        sensitivity=Sensitivity.PUBLIC,
        completeness=ViewCompleteness.FULL,
    )
    assert public.sensitivity is Sensitivity.PUBLIC

    wrong_locator = SensitiveValue(
        JsonBoolean(value=True),
        origins=(TaintOriginId(value="wrong-locator"),),
    )
    with pytest.raises(TypeError, match="payload"):
        DecodedViewHandle(
            view_id=public.view_id,
            source=public.source,
            locator=cast("SensitiveValue[ViewStorageToken]", wrong_locator),
            decoder=public.decoder,
            fingerprint=public.fingerprint,
            sensitivity=public.sensitivity,
            completeness=public.completeness,
        )
    with pytest.raises(TypeError, match="public decoded-view fingerprint"):
        DecodedViewHandle(
            view_id=public.view_id,
            source=public.source,
            locator=locator,
            decoder=public.decoder,
            fingerprint=SensitiveValue(
                fingerprint("a"),
                origins=(TaintOriginId(value="unneeded-taint"),),
            ),
            sensitivity=Sensitivity.PUBLIC,
            completeness=public.completeness,
        )


def test_view_outcomes_reject_untyped_components() -> None:
    spec = decoder()
    bad_decoder = cast("DecoderSpec", "json")
    with pytest.raises(TypeError, match="ready view handle"):
        ReadyView(handle=cast("DecodedViewHandle", "view"), completeness=ViewCompleteness.FULL)
    with pytest.raises(TypeError, match="completeness"):
        ReadyView(handle=view_handle(), completeness=cast("ViewCompleteness", "full"))
    with pytest.raises(TypeError, match="no-body"):
        ViewNoBody(decoder=bad_decoder)
    with pytest.raises(TypeError, match="decode-error decoder"):
        ViewDecodeError(decoder=bad_decoder, code=DecodeErrorCode.INVALID_TEXT)
    with pytest.raises(TypeError, match="decode-error code"):
        ViewDecodeError(decoder=spec, code=cast("DecodeErrorCode", "syntax"))
    with pytest.raises(TypeError, match="incomplete-view decoder"):
        ViewIncomplete(decoder=bad_decoder, reason=IncompleteViewReason.RECORD_PARTIAL)
    with pytest.raises(TypeError, match="incomplete-view reason"):
        ViewIncomplete(decoder=spec, reason=cast("IncompleteViewReason", "partial"))
    with pytest.raises(TypeError, match="budget-exceeded decoder"):
        ViewBudgetExceeded(
            decoder=bad_decoder,
            exhaustion=view_exhaustion(ViewBudgetKind.MEMORY),
        )
    with pytest.raises(TypeError, match="view budget evidence"):
        ViewBudgetExceeded(decoder=spec, exhaustion=cast("BudgetExceeded", "memory"))
    with pytest.raises(TypeError, match="unsupported-view decoder"):
        ViewUnsupported(decoder=bad_decoder, reason=UnsupportedViewReason.VIEW_NOT_APPLICABLE)
    with pytest.raises(TypeError, match="unsupported-view reason"):
        ViewUnsupported(decoder=spec, reason=cast("UnsupportedViewReason", "missing"))
    with pytest.raises(TypeError, match="cancelled-view decoder"):
        ViewCancelled(decoder=bad_decoder)


def test_decoded_view_handle_rejects_every_untyped_identity_boundary() -> None:
    valid = view_handle()
    with pytest.raises(TypeError, match="decoded-view ID"):
        msgspec.structs.replace(valid, view_id=cast("ViewId", "view"))
    with pytest.raises(TypeError, match="decoded-view source"):
        msgspec.structs.replace(valid, source=cast("BodyHandle", "body"))
    with pytest.raises(TypeError, match="sealed ViewStorageLocator"):
        msgspec.structs.replace(
            valid,
            locator=cast("SensitiveValue[ViewStorageToken]", ViewStorageToken(value="raw")),
        )
    with pytest.raises(TypeError, match="decoded-view decoder"):
        msgspec.structs.replace(valid, decoder=cast("DecoderSpec", "decoder"))
    with pytest.raises(TypeError, match="decoded-view sensitivity"):
        msgspec.structs.replace(valid, sensitivity=cast("Sensitivity", "sensitive"))
    with pytest.raises(TypeError, match="decoded-view completeness"):
        msgspec.structs.replace(valid, completeness=cast("ViewCompleteness", "full"))
    assert type(valid.source) is BodyHandle
    assert valid.body_id == valid.source.body_id
    with pytest.raises(TypeError, match="automatic view selection"):
        msgspec.structs.replace(
            valid,
            auto_selection=cast("AutoViewSelection", "selection"),
        )
    assert is_decoded_view_outcome(ReadyView(handle=valid, completeness=ViewCompleteness.FULL))
    assert not is_decoded_view_outcome("ready")


def test_decoded_views_cannot_upgrade_source_sensitivity_or_completeness() -> None:
    sensitive = view_handle()
    with pytest.raises(ValueError, match="lower source-body sensitivity"):
        msgspec.structs.replace(
            sensitive,
            sensitivity=Sensitivity.PUBLIC,
            fingerprint=fingerprint("a"),
        )
    with pytest.raises(ValueError, match="complete source body"):
        msgspec.structs.replace(
            sensitive,
            source=msgspec.structs.replace(
                sensitive.source,
                extent=BodyCaptureExtent.PARTIAL,
            ),
        )
    prefix = view_handle(ViewKind.TEXT, completeness=ViewCompleteness.PREFIX)
    with pytest.raises(ValueError, match="must match its stored handle"):
        ReadyView(handle=prefix, completeness=ViewCompleteness.FULL)
