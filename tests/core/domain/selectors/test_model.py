"""Exercise selector compilation metadata, locations, and runtime states."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString


def _fingerprint() -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value="0" * 64),
    )


def _capabilities(cardinality: SelectorCardinality) -> SelectorCapabilities:
    return SelectorCapabilities(
        cardinality=cardinality,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )


def test_json_pointer_rejects_ambiguous_escapes() -> None:
    with pytest.raises(ValueError, match="escape"):
        JsonPointer(value="/users/~2")


def test_jsonpath_retains_compiled_identity_and_capabilities() -> None:
    capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_MORE,
        access=SelectionAccess.INDEXED,
        may_duplicate=True,
        uses_descendant=False,
        uses_filter=False,
        function_dependencies=("count",),
    )
    path = JsonPathExpression(
        canonical="$.users[*].id",
        fingerprint=_fingerprint(),
        capabilities=capabilities,
    )
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=SelectorCardinality.ZERO_OR_MORE,
        ),
        body_view=BodyViewKind.JSON,
    )

    assert selector.path == path
    assert selector.capabilities.cardinality is SelectorCardinality.ZERO_OR_MORE


def test_only_body_selectors_accept_explicit_body_views() -> None:
    with pytest.raises(ValueError, match="body selectors"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_STATUS,
            path=NoPath(),
            capabilities=_capabilities(SelectorCardinality.EXACTLY_ONE),
            output=ValueContract(
                kinds=(ValueKind.INTEGER,),
                cardinality=SelectorCardinality.EXACTLY_ONE,
            ),
            body_view=BodyViewKind.JSON,
        )


def test_ready_empty_is_distinct_from_decode_failure() -> None:
    ready = SelectionReady(matches=())
    failure = SelectionDecodeFailure(
        code=DiagnosticCode(value="selector.decode-failed"),
        view=BodyViewKind.JSON,
    )

    assert ready.matches == ()
    assert type(failure) is SelectionDecodeFailure


def test_match_retains_canonical_concrete_location() -> None:
    pointer = JsonPointer(value="/users/0/id")
    location = JsonLocation(view_id=ViewId(value=UUID(int=1)), pointer=pointer)
    match = SelectionMatch(value=JsonInteger(value=42), location=location)

    assert match.location == location
    assert location.pointer.value == "/users/0/id"


def test_sensitive_match_propagates_taint_and_fails_closed_at_serialization() -> None:
    secret = "selector-secret-payload"
    origin = TaintOriginId(value="secret-provider.api-token")
    location = JsonLocation(view_id=ViewId(value=UUID(int=2)), pointer=JsonPointer(value="/token"))
    match = SelectionMatch(
        value=SensitiveValue(JsonString(value=secret), origins=(origin,)),
        location=location,
    )

    assert match.sensitivity is Sensitivity.SENSITIVE
    assert match.taint_origins == (origin,)
    with pytest.raises(TypeError) as captured:
        msgspec.json.encode(SelectionReady(matches=(match,)))
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
