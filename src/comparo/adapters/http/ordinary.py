"""Bounded storage-backed snapshots for ordinary captured HTTP response entities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import cast
from typing import final
from uuid import uuid5

import msgspec

from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.streaming import StoredStreamSnapshotProvider
from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import VersionedDefaultSetRef
from comparo.core.domain.budgets import BudgetAmount
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import InvalidMediaType
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderIdentity
from comparo.core.domain.http.views import DecoderOption
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.http.views import UnsupportedViewReason
from comparo.core.domain.http.views import ViewBudgetExceeded
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewIncomplete
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewStorageToken
from comparo.core.domain.http.views import ViewUnsupported
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyBytes
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot

_REGISTRY = DecoderRegistryIdentity(value="comparo/auto@1")
_VIEW_LOCATOR_ORIGIN = TaintOriginId(value="runtime/ordinary-view-locator")
_DIGEST_ACCESS = SensitiveValueAccess(purpose="ordinary-response-digest")
_UTF8_BOM = b"\xef\xbb\xbf"


@final
class OrdinaryResponseDecodeLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite per-body admission and semantic decoding limits."""

    maximum_body_bytes: ByteCount
    maximum_text_characters: Count
    maximum_json_depth: Count
    maximum_json_nodes: Count
    maximum_json_tokens: Count

    def __post_init__(self) -> None:
        """Require exact finite amount types for every independently reported limit."""
        if type(self.maximum_body_bytes) is not ByteCount:
            raise TypeError("ordinary body byte limit must be ByteCount")
        if any(
            type(value) is not Count
            for value in (
                self.maximum_text_characters,
                self.maximum_json_depth,
                self.maximum_json_nodes,
                self.maximum_json_tokens,
            )
        ):
            raise TypeError("ordinary semantic limits must be Count values")


@dataclass(frozen=True, slots=True)
class _Target:
    handle: BodyHandle
    attempt: AttemptScope


@dataclass(frozen=True, slots=True)
class _ReadResult:
    data: bytes | None = None
    budget_attempted: ByteCount | None = None


@dataclass(frozen=True, slots=True)
class _JsonBudgetFailure:
    resource: BudgetResource
    effective: Count
    attempted: Count


@dataclass(frozen=True, slots=True)
class _DecodedJson:
    value: JsonValue | None = None
    code: DecodeErrorCode | None = None
    budget: _JsonBudgetFailure | None = None
    bom: bool = False


@dataclass(frozen=True, slots=True)
class _DecodedText:
    value: JsonString | None = None
    code: DecodeErrorCode | None = None
    attempted_characters: Count | None = None
    bom: bool = False


class StoredOrdinaryResponseSnapshotProvider:
    """Resolve response bytes plus explicit and media-selected semantic views."""

    __slots__ = ("_limits", "_store", "_streams")

    def __init__(
        self,
        *,
        store: TransientBodyStore,
        limits: OrdinaryResponseDecodeLimits,
        streams: StoredStreamSnapshotProvider | None = None,
    ) -> None:
        """Compose the shared transient store, finite limits, and optional stream decoder."""
        if type(store) is not TransientBodyStore:
            raise TypeError("ordinary response snapshot provider requires TransientBodyStore")
        if type(limits) is not OrdinaryResponseDecodeLimits:
            raise TypeError("ordinary response snapshot provider requires decode limits")
        if streams is not None and type(streams) is not StoredStreamSnapshotProvider:
            raise TypeError(
                "ordinary response stream delegate must be StoredStreamSnapshotProvider"
            )
        self._store = store
        self._limits = limits
        self._streams = streams

    async def snapshot(
        self,
        observation: EnvironmentExecutionObservation,
    ) -> SelectionSnapshot:
        """Return one closed selection snapshot without exposing store or decoder exceptions."""
        if type(observation) is not EnvironmentExecutionObservation:
            raise TypeError("ordinary response snapshot requires EnvironmentExecutionObservation")
        delegated = await self._delegated_snapshot(observation)
        delegated_auto = {
            view.handle.body_id
            for view in delegated.body_views
            if type(view) is ResolvedBodyView and view.requested_view is BodyViewKind.AUTO
        }
        body_bytes = list(delegated.body_bytes)
        body_views = list(delegated.body_views)
        ready_handles: list[DecodedViewHandle] = []
        for target in _targets(observation):
            read = await self._read(target.handle)
            if read.data is None:
                unavailable = self._unavailable_views(
                    target,
                    delegated_auto,
                    budget_attempted=read.budget_attempted,
                )
                body_views.extend(unavailable)
                continue
            body_bytes.append(_resolved_bytes(target.handle, read.data))
            explicit = self._explicit_views(target, read.data)
            produced: list[ResolvedBodyView | FailedBodyView] = list(explicit)
            if target.handle.body_id not in delegated_auto:
                produced.append(self._auto_view(target, read.data, explicit=explicit))
            body_views.extend(produced)
            ready_handles.extend(view.handle for view in produced if type(view) is ResolvedBodyView)
        enriched = msgspec.structs.replace(
            delegated.observation,
            views=(*delegated.observation.views, *ready_handles),
        )
        stream = delegated.stream
        if stream is not None:
            stream = msgspec.structs.replace(
                stream,
                records=msgspec.structs.replace(stream.records, owner=enriched),
            )
        return SelectionSnapshot(
            observation=enriched,
            body_bytes=tuple(body_bytes),
            body_views=tuple(body_views),
            stream=stream,
            stream_items=delegated.stream_items,
        )

    async def close(self) -> None:
        """Close only the optional delegate; the transport owns the shared body store."""
        if self._streams is not None:
            await self._streams.close()

    async def _delegated_snapshot(
        self,
        observation: EnvironmentExecutionObservation,
    ) -> SelectionSnapshot:
        if self._streams is None:
            return SelectionSnapshot(observation=observation)
        try:
            return await self._streams.snapshot(observation)
        except BodyStoreError:
            return SelectionSnapshot(observation=observation)

    async def _read(self, handle: BodyHandle) -> _ReadResult:
        if handle.byte_count.value > self._limits.maximum_body_bytes.value:
            return _ReadResult(budget_attempted=handle.byte_count)
        chunks: list[bytes] = []
        count = 0
        try:
            stream = self._store.open_response(handle)
            async for chunk in stream:
                count += len(chunk)
                if count > self._limits.maximum_body_bytes.value:
                    return _ReadResult(budget_attempted=ByteCount(value=count))
                chunks.append(chunk)
        except BodyStoreError:
            return _ReadResult()
        data = b"".join(chunks)
        return _ReadResult(data=data) if len(data) == handle.byte_count.value else _ReadResult()

    def _unavailable_views(
        self,
        target: _Target,
        delegated_auto: set[BodyId],
        *,
        budget_attempted: ByteCount | None,
    ) -> tuple[FailedBodyView, ...]:
        charset = _charset(target.handle)
        text = _decoder(ViewKind.TEXT, charset=charset)
        json_decoder = _decoder(ViewKind.JSON, charset=charset)
        failures = [
            self._read_failure(target, BodyViewKind.TEXT, text, budget_attempted),
            self._read_failure(target, BodyViewKind.JSON, json_decoder, budget_attempted),
        ]
        if target.handle.body_id not in delegated_auto:
            decoder, auto = _auto_decoder(target.handle)
            failures.append(
                self._read_failure(
                    target,
                    BodyViewKind.AUTO,
                    decoder,
                    budget_attempted,
                    auto=auto,
                )
            )
        return tuple(failures)

    def _read_failure(
        self,
        target: _Target,
        requested: BodyViewKind,
        decoder: DecoderSpec,
        budget_attempted: ByteCount | None,
        *,
        auto: AutoViewSelection | None = None,
    ) -> FailedBodyView:
        if budget_attempted is not None:
            return _memory_failure(
                target,
                requested,
                decoder,
                self._limits.maximum_body_bytes,
                budget_attempted,
                auto=auto,
            )
        return _failure(
            target.handle,
            requested,
            ViewUnsupported(
                decoder=decoder,
                reason=UnsupportedViewReason.DECODER_UNAVAILABLE,
                auto_selection=auto,
            ),
        )

    def _explicit_views(
        self,
        target: _Target,
        data: bytes,
    ) -> tuple[ResolvedBodyView | FailedBodyView, ResolvedBodyView | FailedBodyView]:
        return (
            self._text_view(target, data, BodyViewKind.TEXT, auto=None),
            self._json_view(target, data, BodyViewKind.JSON, auto=None),
        )

    def _auto_view(
        self,
        target: _Target,
        data: bytes,
        *,
        explicit: tuple[
            ResolvedBodyView | FailedBodyView,
            ResolvedBodyView | FailedBodyView,
        ],
    ) -> ResolvedBodyView | FailedBodyView:
        decoder, auto = _auto_decoder(target.handle)
        if type(target.handle.media) is InvalidMediaType:
            return _failure(
                target.handle,
                BodyViewKind.AUTO,
                ViewDecodeError(
                    decoder=decoder,
                    code=DecodeErrorCode.INVALID_MEDIA_METADATA,
                    auto_selection=auto,
                ),
            )
        if decoder.view in {ViewKind.SSE, ViewKind.NDJSON, ViewKind.JSON_SEQUENCE}:
            return _failure(
                target.handle,
                BodyViewKind.AUTO,
                ViewUnsupported(
                    decoder=decoder,
                    reason=UnsupportedViewReason.DECODER_UNAVAILABLE,
                    auto_selection=auto,
                ),
            )
        if decoder.view is ViewKind.JSON:
            return _auto_from_explicit(target.handle, explicit[1], auto)
        if decoder.view is ViewKind.TEXT:
            return _auto_from_explicit(target.handle, explicit[0], auto)
        return _ready_view(
            target.handle,
            BodyViewKind.AUTO,
            _bytes_payload(target.handle, data),
            decoder,
            auto=auto,
        )

    def _text_view(
        self,
        target: _Target,
        data: bytes,
        requested: BodyViewKind,
        *,
        auto: AutoViewSelection | None,
    ) -> ResolvedBodyView | FailedBodyView:
        charset = _charset(target.handle)
        decoded = _decode_text(data, charset, self._limits.maximum_text_characters)
        decoder = _decoder(ViewKind.TEXT, charset=charset, bom=decoded.bom)
        selection = _selection(auto, decoder)
        if decoded.code is not None:
            return _failure(
                target.handle,
                requested,
                ViewDecodeError(
                    decoder=decoder,
                    code=decoded.code,
                    auto_selection=selection,
                ),
            )
        if decoded.attempted_characters is not None:
            return _failure(
                target.handle,
                requested,
                ViewBudgetExceeded(
                    decoder=decoder,
                    exhaustion=_exhaustion(
                        target,
                        BudgetResource.DECODED_CHARACTERS,
                        self._limits.maximum_text_characters,
                        decoded.attempted_characters,
                    ),
                    auto_selection=selection,
                ),
            )
        return _ready_view(
            target.handle,
            requested,
            _sensitive_payload(target.handle, cast(JsonString, decoded.value)),
            decoder,
            auto=selection,
        )

    def _json_view(
        self,
        target: _Target,
        data: bytes,
        requested: BodyViewKind,
        *,
        auto: AutoViewSelection | None,
    ) -> ResolvedBodyView | FailedBodyView:
        charset = _charset(target.handle)
        decoder = _decoder(ViewKind.JSON, charset=charset, bom=data.startswith(_UTF8_BOM))
        selection = _selection(auto, decoder)
        if target.handle.extent is BodyCaptureExtent.PARTIAL:
            return _failure(
                target.handle,
                requested,
                ViewIncomplete(
                    decoder=decoder,
                    reason=IncompleteViewReason.ENTITY_PARTIAL,
                    auto_selection=selection,
                ),
            )
        decoded = _decode_json(data, charset, self._limits)
        decoder = _decoder(ViewKind.JSON, charset=charset, bom=decoded.bom)
        selection = _selection(auto, decoder)
        if decoded.budget is not None:
            return _failure(
                target.handle,
                requested,
                ViewBudgetExceeded(
                    decoder=decoder,
                    exhaustion=_exhaustion(
                        target,
                        decoded.budget.resource,
                        decoded.budget.effective,
                        decoded.budget.attempted,
                    ),
                    auto_selection=selection,
                ),
            )
        if decoded.code is not None:
            return _failure(
                target.handle,
                requested,
                ViewDecodeError(
                    decoder=decoder,
                    code=decoded.code,
                    auto_selection=selection,
                ),
            )
        return _ready_view(
            target.handle,
            requested,
            _sensitive_payload(target.handle, cast(JsonValue, decoded.value)),
            decoder,
            auto=selection,
        )


def _targets(observation: EnvironmentExecutionObservation) -> tuple[_Target, ...]:
    targets: list[_Target] = []
    seen: set[BodyId] = set()
    for attempt in observation.attempts:
        for exchange in attempt.exchanges:
            response = exchange.response
            if response is None or type(response.body) is not ResponseEntityBody:
                continue
            handle = response.body.handle
            if handle is not None and handle.body_id not in seen:
                seen.add(handle.body_id)
                targets.append(_Target(handle=handle, attempt=exchange.request.occurrence.attempt))
    return tuple(targets)


def _auto_decoder(handle: BodyHandle) -> tuple[DecoderSpec, AutoViewSelection]:
    media = handle.media
    if type(media) in {MissingMediaType, InvalidMediaType}:
        decoder = _decoder(ViewKind.BYTES)
    else:
        value = cast(ParsedMediaType, media).value
        pair = (value.type, value.subtype)
        if pair == ("application", "json") or (
            value.type == "application" and value.subtype.endswith("+json")
        ):
            decoder = _decoder(ViewKind.JSON, charset=_charset(handle))
        elif pair == ("text", "event-stream"):
            decoder = _decoder(ViewKind.SSE)
        elif pair in {("application", "x-ndjson"), ("application", "ndjson")}:
            decoder = _decoder(ViewKind.NDJSON)
        elif pair == ("application", "json-seq"):
            decoder = _decoder(ViewKind.JSON_SEQUENCE)
        elif (
            value.type == "text"
            or pair == ("application", "xml")
            or (value.type == "application" and value.subtype.endswith("+xml"))
        ):
            decoder = _decoder(ViewKind.TEXT, charset=_charset(handle))
        else:
            decoder = _decoder(ViewKind.BYTES)
    return decoder, AutoViewSelection(registry=_REGISTRY, decoder=decoder)


def _decoder(
    kind: ViewKind,
    *,
    charset: str = "utf-8",
    bom: bool = False,
) -> DecoderSpec:
    options: tuple[DecoderOption, ...] = ()
    if kind in {ViewKind.TEXT, ViewKind.JSON}:
        options = (
            DecoderOption(name="bom", value=JsonBoolean(value=bom)),
            DecoderOption(name="charset", value=JsonString(value=charset)),
        )
    return DecoderSpec(
        identity=DecoderIdentity(value=f"comparo/{kind.value}@1"),
        view=kind,
        options=options,
    )


def _selection(
    selection: AutoViewSelection | None,
    decoder: DecoderSpec,
) -> AutoViewSelection | None:
    if selection is None:
        return None
    return AutoViewSelection(registry=selection.registry, decoder=decoder)


def _auto_from_explicit(
    handle: BodyHandle,
    explicit: ResolvedBodyView | FailedBodyView,
    selection: AutoViewSelection,
) -> ResolvedBodyView | FailedBodyView:
    if type(explicit) is ResolvedBodyView:
        decoder = explicit.handle.decoder
        return _ready_view(
            handle,
            BodyViewKind.AUTO,
            explicit.value,
            decoder,
            auto=_selection(selection, decoder),
        )
    decoder = explicit.outcome.decoder
    outcome = msgspec.structs.replace(
        explicit.outcome,
        auto_selection=_selection(selection, decoder),
    )
    return FailedBodyView(
        source=handle,
        requested_view=BodyViewKind.AUTO,
        outcome=outcome,
    )


def _charset(handle: BodyHandle) -> str:
    if type(handle.media) is not ParsedMediaType:
        return "utf-8"
    parameter = next(
        (item for item in handle.media.value.parameters if item.name == "charset"),
        None,
    )
    return "utf-8" if parameter is None else _parameter_value(parameter).lower()


def _parameter_value(parameter: MediaTypeParameter) -> str:
    return parameter.value.value


def _decode_text(data: bytes, charset: str, maximum: Count) -> _DecodedText:
    if charset not in {"utf-8", "utf8"}:
        return _DecodedText(code=DecodeErrorCode.INVALID_ENCODING)
    bom = data.startswith(_UTF8_BOM)
    source = data[len(_UTF8_BOM) :] if bom else data
    try:
        value = source.decode("utf-8")
    except UnicodeError:
        return _DecodedText(code=DecodeErrorCode.INVALID_TEXT, bom=bom)
    if len(value) > maximum.value:
        return _DecodedText(attempted_characters=Count(value=len(value)), bom=bom)
    return _DecodedText(value=JsonString(value=value), bom=bom)


def _decode_json(  # noqa: C901 - exhaustive typed decoder outcome mapping
    data: bytes,
    charset: str,
    limits: OrdinaryResponseDecodeLimits,
) -> _DecodedJson:
    if charset not in {"utf-8", "utf8"}:
        return _DecodedJson(code=DecodeErrorCode.INVALID_ENCODING)
    bom = data.startswith(_UTF8_BOM)
    source = data[len(_UTF8_BOM) :] if bom else data
    try:
        text = source.decode("utf-8")
    except UnicodeError:
        return _DecodedJson(code=DecodeErrorCode.INVALID_TEXT, bom=bom)
    budget = _scan_json_budgets(text, limits)
    if budget is not None:
        return _DecodedJson(budget=budget, bom=bom)
    decoder = json.JSONDecoder(
        object_pairs_hook=_JsonObjectPairs,
        parse_float=Decimal,
        parse_int=int,
        parse_constant=_reject_non_finite,
    )
    start = len(text) - len(text.lstrip())
    try:
        parsed, end = decoder.raw_decode(text, start)
        if text[end:].strip():
            return _DecodedJson(code=DecodeErrorCode.TRAILING_CONTENT, bom=bom)
        value = _json_value(parsed)
    except _DuplicateMemberError:
        return _DecodedJson(code=DecodeErrorCode.DUPLICATE_MEMBER, bom=bom)
    except _NonFiniteNumberError:
        return _DecodedJson(code=DecodeErrorCode.NON_FINITE_NUMBER, bom=bom)
    except _InvalidJsonTextError:
        return _DecodedJson(code=DecodeErrorCode.INVALID_TEXT, bom=bom)
    except (ArithmeticError, RecursionError, TypeError, ValueError, json.JSONDecodeError):
        return _DecodedJson(code=DecodeErrorCode.INVALID_SYNTAX, bom=bom)
    nodes, depth = _json_shape(value)
    if depth > limits.maximum_json_depth.value:
        return _DecodedJson(
            budget=_json_budget(BudgetResource.JSON_DEPTH, limits.maximum_json_depth, depth),
            bom=bom,
        )
    if nodes > limits.maximum_json_nodes.value:
        return _DecodedJson(
            budget=_json_budget(BudgetResource.JSON_NODES, limits.maximum_json_nodes, nodes),
            bom=bom,
        )
    return _DecodedJson(value=value, bom=bom)


def _scan_json_budgets(  # noqa: C901 - explicit bounded lexical state machine
    text: str,
    limits: OrdinaryResponseDecodeLimits,
) -> _JsonBudgetFailure | None:
    depth = 0
    tokens = 0
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character.isspace():
            index += 1
            continue
        tokens += 1
        if tokens > limits.maximum_json_tokens.value:
            return _json_budget(
                BudgetResource.JSON_TOKENS,
                limits.maximum_json_tokens,
                tokens,
            )
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > limits.maximum_json_depth.value:
                return _json_budget(
                    BudgetResource.JSON_DEPTH,
                    limits.maximum_json_depth,
                    depth,
                )
        elif character in "]}":
            depth = max(0, depth - 1)
        elif character not in ",:":
            while index + 1 < len(text) and text[index + 1] not in " \t\r\n,]}:":
                index += 1
        index += 1
    return None


def _json_budget(resource: BudgetResource, effective: Count, attempted: int) -> _JsonBudgetFailure:
    return _JsonBudgetFailure(
        resource=resource,
        effective=effective,
        attempted=Count(value=attempted),
    )


class _JsonObjectPairs:
    __slots__ = ("pairs",)

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        self.pairs = pairs


class _DuplicateMemberError(ValueError):
    pass


class _NonFiniteNumberError(ValueError):
    pass


class _InvalidJsonTextError(ValueError):
    pass


def _reject_non_finite(_value: str) -> None:
    raise _NonFiniteNumberError


def _json_value(value: object) -> JsonValue:  # noqa: C901 - closed JSON carrier conversion
    if value is None:
        return JsonNull()
    if type(value) is bool:
        return JsonBoolean(value=value)
    if type(value) is int:
        return JsonInteger(value=value)
    if type(value) is Decimal:
        if not value.is_finite():
            raise _NonFiniteNumberError
        if value == value.to_integral_value():
            return JsonInteger(value=int(value))
        return JsonDecimal(value=value.normalize())
    if type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise _InvalidJsonTextError
        return JsonString(value=value)
    if type(value) is list:
        return JsonArray(items=tuple(_json_value(item) for item in value))
    if type(value) is _JsonObjectPairs:
        pairs = value.pairs
        names = tuple(name for name, _item in pairs)
        if len(set(names)) != len(names):
            raise _DuplicateMemberError
        return JsonObject(
            members=tuple(
                JsonObjectMember(name=name, value=_json_value(item)) for name, item in pairs
            )
        )
    raise TypeError("JSON decoder returned an unsupported value")


def _json_shape(value: JsonValue) -> tuple[int, int]:
    nodes = 0
    maximum_depth = 0
    stack: list[tuple[JsonValue, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        maximum_depth = max(maximum_depth, depth)
        if type(item) is JsonArray:
            stack.extend((child, depth + 1) for child in item.items)
        elif type(item) is JsonObject:
            stack.extend((member.value, depth + 1) for member in item.members)
    return nodes, maximum_depth


def _resolved_bytes(handle: BodyHandle, data: bytes) -> ResolvedBodyBytes:
    return ResolvedBodyBytes(source=handle, value=_bytes_payload(handle, data))


def _bytes_payload(handle: BodyHandle, data: bytes) -> BytesValue | SensitiveValue[BytesValue]:
    payload = BytesValue(value=data)
    origins = _origins(handle)
    return SensitiveValue(payload, origins=origins) if origins else payload


def _sensitive_payload(
    handle: BodyHandle,
    value: JsonValue,
) -> JsonValue | SensitiveValue[JsonValue]:
    origins = _origins(handle)
    return SensitiveValue(value, origins=origins) if origins else value


def _origins(handle: BodyHandle) -> tuple[TaintOriginId, ...]:
    return handle.digest.origins if is_sensitive_value(handle.digest) else ()


def _fingerprint(handle: BodyHandle) -> Fingerprint | SensitiveValue[Fingerprint]:
    if is_sensitive_value(handle.digest):
        digest = cast("SensitiveValue[Sha256Digest]", handle.digest)
        return SensitiveValue(
            Fingerprint(algorithm="sha256", digest=digest.reveal(access=_DIGEST_ACCESS)),
            origins=digest.origins,
        )
    return Fingerprint(algorithm="sha256", digest=cast(Sha256Digest, handle.digest))


def _ready_view(
    handle: BodyHandle,
    requested: BodyViewKind,
    value: SelectedValue | SensitiveValue[SelectedValue],
    decoder: DecoderSpec,
    *,
    auto: AutoViewSelection | None,
) -> ResolvedBodyView:
    view_id = _view_id(handle, requested, decoder)
    fingerprint = _fingerprint(handle)
    view = DecodedViewHandle(
        view_id=view_id,
        source=handle,
        locator=SensitiveValue(
            ViewStorageToken(value=view_id.value.hex),
            origins=(_VIEW_LOCATOR_ORIGIN,),
        ),
        decoder=decoder,
        fingerprint=fingerprint,
        sensitivity=handle.sensitivity,
        completeness=(
            ViewCompleteness.FULL
            if handle.extent is BodyCaptureExtent.COMPLETE
            else ViewCompleteness.PREFIX
        ),
        auto_selection=auto,
    )
    return ResolvedBodyView(
        handle=view,
        requested_view=requested,
        value=value,
        payload_fingerprint=fingerprint,
    )


def _view_id(handle: BodyHandle, requested: BodyViewKind, decoder: DecoderSpec) -> ViewId:
    options = ";".join(f"{item.name}={_option_value(item)}" for item in decoder.options)
    return ViewId(
        value=uuid5(
            handle.body_id.value,
            f"ordinary:{requested.value}:{decoder.identity.value}:{options}",
        )
    )


def _option_value(option: DecoderOption) -> str:
    if type(option.value) is JsonBoolean:
        return "true" if option.value.value else "false"
    return cast(JsonString, option.value).value


def _failure(
    handle: BodyHandle,
    requested: BodyViewKind,
    outcome: ViewDecodeError | ViewIncomplete | ViewBudgetExceeded | ViewUnsupported,
) -> FailedBodyView:
    return FailedBodyView(source=handle, requested_view=requested, outcome=outcome)


def _memory_failure(
    target: _Target,
    requested: BodyViewKind,
    decoder: DecoderSpec,
    effective: ByteCount,
    attempted: ByteCount,
    *,
    auto: AutoViewSelection | None = None,
) -> FailedBodyView:
    return _failure(
        target.handle,
        requested,
        ViewBudgetExceeded(
            decoder=decoder,
            exhaustion=_exhaustion(
                target,
                BudgetResource.MEMORY_BYTES,
                effective,
                attempted,
            ),
            auto_selection=auto,
        ),
    )


def _exhaustion(
    target: _Target,
    resource: BudgetResource,
    effective: BudgetAmount,
    attempted: BudgetAmount,
) -> BudgetExceeded:
    zero: BudgetAmount = ByteCount(value=0) if type(effective) is ByteCount else Count(value=0)
    default_set = VersionedDefaultSetRef(
        identifier="comparo.ordinary-response-decoding",
        version="1",
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(
                hex_value=sha256(
                    f"{resource.value}:{type(effective).__name__}:"
                    f"{_budget_amount_value(effective)}".encode()
                ).hexdigest()
            ),
        ),
    )
    budget = ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.BODY,
        stage=BudgetStage.DECODING,
        operation=BudgetOperation(value="decode.ordinary-response"),
        requested=effective,
        effective=effective,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=effective,
                origin=DefaultBudgetOrigin(default_set=default_set),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=BodyBudgetScope(attempt=target.attempt, body_id=target.handle.body_id),
    )
    return BudgetExceeded(
        budget=budget,
        attempted=attempted,
        effect=BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
    )


def _budget_amount_value(amount: BudgetAmount) -> int:
    if type(amount) in {Count, ByteCount}:
        return cast("Count | ByteCount", amount).value
    raise TypeError("ordinary response budgets require count or byte amounts")


__all__ = (
    "OrdinaryResponseDecodeLimits",
    "StoredOrdinaryResponseSnapshotProvider",
)
