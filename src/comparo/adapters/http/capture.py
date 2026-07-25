"""Bounded streaming response decoding and opaque body capture."""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from math import gcd
from typing import Protocol
from typing import cast
from typing import final
from uuid import uuid5

import httpcore
import msgspec

from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyWriter
from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budgets import BudgetAmount
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyCaptureState
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import MediaTypeMetadata
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId

_BODY_READ_FAILED = DiagnosticCode(value="http.body-read-failed")
_IDLE_TIMEOUT = DiagnosticCode(value="http.idle-timeout")
_REMOTE_TRUNCATED = DiagnosticCode(value="http.remote-body-truncated")
_CONTENT_DECODING_FAILED = DiagnosticCode(value="http.content-decoding-failed")
_CAPTURE_BUDGET_EXCEEDED = DiagnosticCode(value="http.capture-budget-exceeded")
_CAPTURE_STORAGE_FAILED = DiagnosticCode(value="http.capture-storage-failed")
_CAPTURE_CANCELLED = DiagnosticCode(value="http.capture-cancelled")


@final
class ResponseCaptureLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned independent response-capture limits with no application maxima."""

    maximum_encoded_body_bytes: ByteCount
    maximum_entity_body_bytes: ByteCount
    maximum_expansion_ratio: Ratio

    def __post_init__(self) -> None:
        """Require exact finite units for each independent capture dimension."""
        if type(self.maximum_encoded_body_bytes) is not ByteCount:
            raise TypeError("encoded response limit must be ByteCount")
        if type(self.maximum_entity_body_bytes) is not ByteCount:
            raise TypeError("entity response limit must be ByteCount")
        if type(self.maximum_expansion_ratio) is not Ratio:
            raise TypeError("response expansion limit must be Ratio")


class CaptureBudgetProvider(Protocol):
    """Bind one exceeded capture limit to canonical scoped budget evidence."""

    def exceeded(
        self,
        *,
        resource: BudgetResource,
        request: AuthorizedExchangeRequest,
        body_id: BodyId,
        attempted: BudgetAmount,
    ) -> BudgetExceeded:
        """Return exact derivation and exhaustion evidence for a retained prefix."""
        ...


class DefaultCaptureBudgetProvider:
    """Build capture budgets sourced from one explicit versioned default set."""

    __slots__ = ("_limits", "_origin")

    def __init__(
        self,
        limits: ResponseCaptureLimits,
        origin: DefaultBudgetOrigin,
    ) -> None:
        """Bind caller-supplied limits to their exact default-set identity."""
        if type(limits) is not ResponseCaptureLimits:
            raise TypeError("default capture budgets require ResponseCaptureLimits")
        if type(origin) is not DefaultBudgetOrigin:
            raise TypeError("default capture budgets require DefaultBudgetOrigin")
        self._limits = limits
        self._origin = origin

    def exceeded(
        self,
        *,
        resource: BudgetResource,
        request: AuthorizedExchangeRequest,
        body_id: BodyId,
        attempted: BudgetAmount,
    ) -> BudgetExceeded:
        """Create one body-scoped partial-observation exhaustion outcome."""
        if type(request) is not AuthorizedExchangeRequest or type(body_id) is not BodyId:
            raise TypeError("capture exhaustion requires authorized request and BodyId")
        effective, stage, operation = {
            BudgetResource.ENCODED_BODY_BYTES: (
                self._limits.maximum_encoded_body_bytes,
                BudgetStage.CAPTURE,
                "capture.encoded-body",
            ),
            BudgetResource.ENTITY_BODY_BYTES: (
                self._limits.maximum_entity_body_bytes,
                BudgetStage.DECODING,
                "capture.entity-body",
            ),
            BudgetResource.DECOMPRESSION_RATIO: (
                self._limits.maximum_expansion_ratio,
                BudgetStage.DECODING,
                "capture.expansion-ratio",
            ),
        }.get(resource, (None, None, None))
        if effective is None or stage is None or operation is None:
            raise ValueError("unsupported response capture budget resource")
        if type(attempted) is not type(effective):
            raise TypeError("capture exhaustion attempted amount has the wrong unit")
        zero = _zero_like(effective)
        return BudgetExceeded(
            budget=ResourceBudget(
                resource=resource,
                scope_kind=BudgetScopeKind.BODY,
                stage=stage,
                operation=BudgetOperation(value=operation),
                requested=effective,
                effective=effective,
                cap=None,
                contributions=(
                    BudgetContribution(
                        kind=BudgetContributionKind.DEFAULT,
                        amount=effective,
                        origin=self._origin,
                    ),
                ),
                usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
                scope=BodyBudgetScope(
                    attempt=request.occurrence.attempt,
                    body_id=body_id,
                ),
            ),
            attempted=attempted,
            effect=BudgetExhaustionEffect.OBSERVATION_PARTIAL,
        )


@dataclass(frozen=True, slots=True)
class CapturedEntity:
    """Captured response entity and any exact failure that interrupted it."""

    body: ResponseEntityBody
    failure: TransportFailure | None


@dataclass(frozen=True, slots=True)
class _ConsumeResult:
    capture: BodyCaptureState | _BudgetStop
    failure: TransportFailure | None
    encoded_count: int


class ResponseBodyCapturer:
    """Decode supported codings incrementally into one bounded transient body handle."""

    __slots__ = ("_budgets", "_limits", "_store")

    def __init__(
        self,
        *,
        store: TransientBodyStore,
        limits: ResponseCaptureLimits,
        budgets: CaptureBudgetProvider,
    ) -> None:
        """Compose storage, enforcement amounts, and canonical accounting evidence."""
        if type(store) is not TransientBodyStore:
            raise TypeError("response capturer requires TransientBodyStore")
        if type(limits) is not ResponseCaptureLimits:
            raise TypeError("response capturer requires ResponseCaptureLimits")
        if not hasattr(budgets, "exceeded"):
            raise TypeError("response capturer requires CaptureBudgetProvider")
        self._store = store
        self._limits = limits
        self._budgets = budgets

    async def capture(
        self,
        response: httpcore.Response,
        *,
        request: AuthorizedExchangeRequest,
        media: MediaTypeMetadata,
        encodings: tuple[ContentCoding, ...],
        encoding_valid: bool,
        sensitivity: Sensitivity,
        origins: tuple[TaintOriginId, ...],
        first_byte: Callable[[], None],
    ) -> CapturedEntity:
        """Capture one semantic entity, retaining a prefix for every post-header failure."""
        if type(response) is not httpcore.Response:
            raise TypeError("response capturer requires httpcore.Response")
        if type(request) is not AuthorizedExchangeRequest:
            raise TypeError("response capturer requires AuthorizedExchangeRequest")
        body_id = BodyId(value=uuid5(request.occurrence.exchange_id.value, "response-entity"))
        try:
            writer = await self._store.begin(
                body_id,
                maximum_body_bytes=self._limits.maximum_entity_body_bytes,
                sensitivity=sensitivity,
                origins=origins,
            )
            async with writer:
                result = await self._consume(
                    response,
                    writer=writer,
                    encodings=encodings,
                    encoding_valid=encoding_valid,
                    first_byte=first_byte,
                )
                complete = type(result.capture) is CompleteCapture
                handle = await writer.commit_response(
                    media=media,
                    extent=(BodyCaptureExtent.COMPLETE if complete else BodyCaptureExtent.PARTIAL),
                )
        except BodyStoreError:
            return CapturedEntity(
                body=ResponseEntityBody(
                    capture=StorageFailedCapture(),
                    handle=None,
                    encoded_byte_count=None,
                ),
                failure=_failure(FailurePhase.STORAGE, _CAPTURE_STORAGE_FAILED),
            )
        capture = result.capture
        if type(capture) is _BudgetStop:
            exhaustion = self._budgets.exceeded(
                resource=capture.resource,
                request=request,
                body_id=body_id,
                attempted=capture.attempted,
            )
            capture = BudgetExceededCapture(exhaustion=exhaustion)
        return CapturedEntity(
            body=ResponseEntityBody(
                capture=cast(BodyCaptureState, capture),
                handle=handle,
                encoded_byte_count=ByteCount(value=result.encoded_count),
            ),
            failure=result.failure,
        )

    async def _consume(  # noqa: C901 - closed mapping of stream terminal outcomes
        self,
        response: httpcore.Response,
        *,
        writer: TransientBodyWriter,
        encodings: tuple[ContentCoding, ...],
        encoding_valid: bool,
        first_byte: Callable[[], None],
    ) -> _ConsumeResult:
        if not encoding_valid or any(
            coding.value not in {"gzip", "identity"} for coding in encodings
        ):
            return _ConsumeResult(
                capture=ContentDecodingFailedCapture(),
                failure=_failure(FailurePhase.CONTENT_DECODING, _CONTENT_DECODING_FAILED),
                encoded_count=0,
            )
        decoder = _DecoderChain(encodings)
        encoded_count = 0
        try:
            async for chunk in response.aiter_stream():
                if not chunk:
                    continue
                first_byte()
                attempted_encoded = encoded_count + len(chunk)
                encoded_remaining = self._limits.maximum_encoded_body_bytes.value - encoded_count
                entity_remaining = (
                    self._limits.maximum_entity_body_bytes.value - writer.byte_count.value
                )
                allowed = (
                    min(encoded_remaining, entity_remaining)
                    if decoder.identity
                    else encoded_remaining
                )
                accepted = chunk[: max(0, allowed)]
                encoded_count += len(accepted)
                if accepted:
                    stop = await self._append_decoded(
                        writer,
                        decoder.feed(accepted),
                        encoded_count=encoded_count,
                    )
                    if stop is not None:
                        return _ConsumeResult(
                            capture=stop,
                            failure=_failure(
                                FailurePhase.RESOURCE_BUDGET,
                                _CAPTURE_BUDGET_EXCEEDED,
                            ),
                            encoded_count=encoded_count,
                        )
                if (
                    decoder.identity
                    and len(chunk) > entity_remaining
                    and entity_remaining < encoded_remaining
                ):
                    return _ConsumeResult(
                        capture=_BudgetStop(
                            resource=BudgetResource.ENTITY_BODY_BYTES,
                            attempted=ByteCount(
                                value=writer.byte_count.value + len(chunk) - len(accepted)
                            ),
                        ),
                        failure=_failure(
                            FailurePhase.RESOURCE_BUDGET,
                            _CAPTURE_BUDGET_EXCEEDED,
                        ),
                        encoded_count=encoded_count,
                    )
                if attempted_encoded > self._limits.maximum_encoded_body_bytes.value:
                    return _ConsumeResult(
                        capture=_BudgetStop(
                            resource=BudgetResource.ENCODED_BODY_BYTES,
                            attempted=ByteCount(value=attempted_encoded),
                        ),
                        failure=_failure(
                            FailurePhase.RESOURCE_BUDGET,
                            _CAPTURE_BUDGET_EXCEEDED,
                        ),
                        encoded_count=encoded_count,
                    )
            stop = await self._append_decoded(
                writer,
                decoder.finish(),
                encoded_count=encoded_count,
            )
            if stop is not None:
                return _ConsumeResult(
                    capture=stop,
                    failure=_failure(FailurePhase.RESOURCE_BUDGET, _CAPTURE_BUDGET_EXCEEDED),
                    encoded_count=encoded_count,
                )
            if not decoder.complete:
                raise zlib.error
        except httpcore.ReadTimeout:
            return _ConsumeResult(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.IDLE_TIMEOUT),
                failure=_failure(FailurePhase.IDLE_TIMEOUT, _IDLE_TIMEOUT, retryable=True),
                encoded_count=encoded_count,
            )
        except httpcore.RemoteProtocolError:
            return _ConsumeResult(
                capture=RemoteTruncatedCapture(),
                failure=_failure(FailurePhase.PROTOCOL, _REMOTE_TRUNCATED),
                encoded_count=encoded_count,
            )
        except (httpcore.ReadError, OSError):
            return _ConsumeResult(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
                failure=_failure(FailurePhase.BODY_READ, _BODY_READ_FAILED),
                encoded_count=encoded_count,
            )
        except zlib.error:
            return _ConsumeResult(
                capture=ContentDecodingFailedCapture(),
                failure=_failure(FailurePhase.CONTENT_DECODING, _CONTENT_DECODING_FAILED),
                encoded_count=encoded_count,
            )
        except asyncio.CancelledError:
            return _ConsumeResult(
                capture=CancelledCapture(),
                failure=_failure(FailurePhase.CANCELLATION, _CAPTURE_CANCELLED),
                encoded_count=encoded_count,
            )
        return _ConsumeResult(
            capture=CompleteCapture(),
            failure=None,
            encoded_count=encoded_count,
        )

    async def _append_decoded(
        self,
        writer: TransientBodyWriter,
        content: bytes,
        *,
        encoded_count: int,
    ) -> _BudgetStop | None:
        if not content:
            return None
        current = writer.byte_count.value
        attempted_entity = current + len(content)
        allowed = self._limits.maximum_entity_body_bytes.value - current
        accepted = content[: max(0, allowed)]
        if accepted:
            await writer.append(accepted)
        if attempted_entity > self._limits.maximum_entity_body_bytes.value:
            return _BudgetStop(
                resource=BudgetResource.ENTITY_BODY_BYTES,
                attempted=ByteCount(value=attempted_entity),
            )
        ratio = _ratio(attempted_entity, encoded_count)
        if _ratio_greater(ratio, self._limits.maximum_expansion_ratio):
            return _BudgetStop(
                resource=BudgetResource.DECOMPRESSION_RATIO,
                attempted=ratio,
            )
        return None


@dataclass(frozen=True, slots=True)
class _BudgetStop:
    resource: BudgetResource
    attempted: BudgetAmount


class _DecoderChain:
    """Incrementally reverse a supported ordered HTTP content-coding chain."""

    __slots__ = ("_decoders",)

    def __init__(self, encodings: tuple[ContentCoding, ...]) -> None:
        self._decoders = [
            zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            for coding in reversed(encodings)
            if coding.value == "gzip"
        ]

    def feed(self, content: bytes) -> bytes:
        for decoder in self._decoders:
            content = decoder.decompress(content)
        return content

    def finish(self) -> bytes:
        output = bytearray()
        for index, decoder in enumerate(self._decoders):
            content = decoder.flush()
            for remaining in self._decoders[index + 1 :]:
                content = remaining.decompress(content)
            output.extend(content)
        return bytes(output)

    @property
    def complete(self) -> bool:
        return all(decoder.eof for decoder in self._decoders)

    @property
    def identity(self) -> bool:
        return not self._decoders


def _failure(
    phase: FailurePhase,
    code: DiagnosticCode,
    *,
    retryable: bool = False,
) -> TransportFailure:
    return TransportFailure(phase=phase, code=code, retryable=retryable)


def _ratio(entity: int, encoded: int) -> Ratio:
    if encoded == 0:
        return Ratio.from_decimal(Decimal(0))
    divisor = gcd(entity, encoded)
    return Ratio(
        numerator=entity // divisor,
        denominator=PositiveCount(value=encoded // divisor),
    )


def _ratio_greater(left: Ratio, right: Ratio) -> bool:
    return left.numerator * right.denominator.value > (right.numerator * left.denominator.value)


def _zero_like(amount: BudgetAmount) -> BudgetAmount:
    if type(amount) is ByteCount:
        return ByteCount(value=0)
    if type(amount) is Ratio:
        return Ratio.from_decimal(Decimal(0))
    raise TypeError("response capture budgets use only byte and ratio amounts")
