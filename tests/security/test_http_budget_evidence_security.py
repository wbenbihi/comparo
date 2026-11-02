"""Adversarial canonical budget-evidence binding for HTTP capture and views."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_items import SseJsonBudgetExceeded
from comparo.core.domain.http.views import ViewBudgetExceeded
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.scopes import RunScope
from tests.core.domain.http.budget_factories import capture_exhaustion
from tests.core.domain.http.budget_factories import exhaustion
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import decoder


def test_capture_exhaustion_binds_full_budget_derivation_to_body_identity_and_counts() -> None:
    handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    exhaustion = capture_exhaustion(
        handle=handle,
        kind=CaptureBudgetKind.ENTITY_BYTES,
        encoded_byte_count=ByteCount(value=4),
    )
    capture = BudgetExceededCapture(exhaustion=exhaustion)
    body = ResponseEntityBody(
        capture=capture,
        handle=handle,
        encoded_byte_count=ByteCount(value=4),
    )
    assert capture.budget is CaptureBudgetKind.ENTITY_BYTES
    assert body.capture is capture
    assert capture.exhaustion.budget.contributions

    scope = exhaustion.budget.scope
    assert type(scope) is BodyBudgetScope
    foreign_scope = msgspec.structs.replace(scope, body_id=BodyId(value=UUID(int=999)))
    foreign = msgspec.structs.replace(
        capture,
        exhaustion=msgspec.structs.replace(
            exhaustion,
            budget=msgspec.structs.replace(exhaustion.budget, scope=foreign_scope),
        ),
    )
    with pytest.raises(ValueError, match="identify the captured body"):
        ResponseEntityBody(
            capture=foreign,
            handle=handle,
            encoded_byte_count=ByteCount(value=4),
        )

    with pytest.raises(ValueError, match="retained entity extent"):
        ResponseEntityBody(
            capture=capture,
            handle=msgspec.structs.replace(handle, byte_count=ByteCount(value=3)),
            encoded_byte_count=ByteCount(value=3),
        )


def test_capture_and_view_wrappers_reject_wrong_canonical_effect_or_resource() -> None:
    handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    capture_evidence = capture_exhaustion(
        handle=handle,
        kind=CaptureBudgetKind.ENTITY_BYTES,
        encoded_byte_count=ByteCount(value=4),
    )
    unavailable = msgspec.structs.replace(
        capture_evidence,
        effect=BudgetExhaustionEffect.OBSERVATION_UNAVAILABLE,
    )
    with pytest.raises(ValueError, match="observation-partial"):
        BudgetExceededCapture(exhaustion=unavailable)

    view_evidence = view_exhaustion(ViewBudgetKind.JSON_NODES, body_id=handle.body_id)
    view = ViewBudgetExceeded(decoder=decoder(), exhaustion=view_evidence)
    assert view.budget is ViewBudgetKind.JSON_NODES
    with pytest.raises(ValueError, match="semantic-view dimension"):
        ViewBudgetExceeded(decoder=decoder(), exhaustion=capture_evidence)
    with pytest.raises(ValueError, match="body-capture dimension"):
        BudgetExceededCapture(exhaustion=view_evidence)

    wrong_scope = msgspec.structs.replace(
        capture_evidence,
        budget=msgspec.structs.replace(
            capture_evidence.budget,
            scope_kind=BudgetScopeKind.RUN,
            scope=RunScope(run_id=RunId(value=UUID(int=2000))),
        ),
    )
    with pytest.raises(ValueError, match="exact body scope and stage"):
        BudgetExceededCapture(exhaustion=wrong_scope)

    view_wrong_scope = msgspec.structs.replace(
        view_evidence,
        budget=msgspec.structs.replace(
            view_evidence.budget,
            scope_kind=BudgetScopeKind.RUN,
            scope=RunScope(run_id=RunId(value=UUID(int=2001))),
        ),
    )
    with pytest.raises(ValueError, match="exact body scope and decoding stage"):
        ViewBudgetExceeded(decoder=decoder(), exhaustion=view_wrong_scope)
    with pytest.raises(ValueError, match="JSON-decoding resource"):
        SseJsonBudgetExceeded(
            decoder=decoder(),
            exhaustion=view_exhaustion(ViewBudgetKind.INDEX_STORAGE),
        )


def test_capture_count_reconciliation_rejects_every_budget_dimension_mismatch() -> None:
    encoded_handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    encoded_capture = BudgetExceededCapture(
        exhaustion=capture_exhaustion(
            handle=encoded_handle,
            kind=CaptureBudgetKind.ENCODED_BYTES,
            encoded_byte_count=ByteCount(value=4),
        )
    )
    with pytest.raises(ValueError, match="retained encoded extent"):
        ResponseEntityBody(
            capture=encoded_capture,
            handle=encoded_handle,
            encoded_byte_count=ByteCount(value=3),
        )

    ratio_handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    zero_encoded = exhaustion(
        resource=BudgetResource.DECOMPRESSION_RATIO,
        stage=BudgetStage.DECODING,
        effective=Ratio.from_decimal(Decimal(1)),
        attempted=Ratio.from_decimal(Decimal(2)),
        body_id=ratio_handle.body_id,
        operation="decode.expansion-ratio",
    )
    with pytest.raises(ValueError, match="non-zero encoded extent"):
        ResponseEntityBody(
            capture=BudgetExceededCapture(exhaustion=zero_encoded),
            handle=ratio_handle,
            encoded_byte_count=ByteCount(value=0),
        )

    original_handle = body_handle(size=8, extent=BodyCaptureExtent.PARTIAL)
    ratio_capture = BudgetExceededCapture(
        exhaustion=capture_exhaustion(
            handle=original_handle,
            kind=CaptureBudgetKind.EXPANSION_RATIO,
            encoded_byte_count=ByteCount(value=4),
        )
    )
    with pytest.raises(ValueError, match="exact encoded and entity extents"):
        ResponseEntityBody(
            capture=ratio_capture,
            handle=msgspec.structs.replace(original_handle, byte_count=ByteCount(value=6)),
            encoded_byte_count=ByteCount(value=4),
        )


def test_uncoded_response_cannot_report_conflicting_wire_and_entity_extents() -> None:
    with pytest.raises(ValueError, match="encoded and entity byte counts"):
        response(
            body=ResponseEntityBody(
                capture=CompleteCapture(),
                handle=body_handle(size=4),
                encoded_byte_count=ByteCount(value=3),
            )
        )
