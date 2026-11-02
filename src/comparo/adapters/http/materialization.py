"""Deterministic request-body serialization into repeatable transient snapshots."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Protocol
from typing import cast
from typing import final
from urllib.parse import quote
from urllib.parse import quote_plus

import msgspec

from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import BodyStoreFailureCode
from comparo.adapters.http.storage import StoredBodyStream
from comparo.adapters.http.storage import TransientBodyStore
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundaryValidation
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.bodies import multipart_content_type
from comparo.core.domain.http.body_lineage import request_body_taint_origins
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue

_BODY_ACCESS = SensitiveValueAccess(purpose="http.request-body.materialization")


class RepeatableSourceReader(Protocol):
    """Open independently positioned streams for already-snapshotted byte sources."""

    def open_request(self, source: RepeatableByteSource) -> StoredBodyStream:
        """Return one asynchronous reader after descriptor validation."""
        ...


@final
class BodyMaterializationLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite caller-owned request serialization limits."""

    maximum_body_bytes: ByteCount
    maximum_json_depth: PositiveCount
    maximum_json_nodes: PositiveCount

    def __post_init__(self) -> None:
        """Require exact finite amount types without imposing application maxima."""
        if type(self.maximum_body_bytes) is not ByteCount:
            raise TypeError("materialization body limit must be ByteCount")
        if type(self.maximum_json_depth) is not PositiveCount:
            raise TypeError("materialization JSON depth limit must be PositiveCount")
        if type(self.maximum_json_nodes) is not PositiveCount:
            raise TypeError("materialization JSON node limit must be PositiveCount")


class BodyMaterializationFailureCode(DomainStrEnum):
    """Stable request-body materialization failures."""

    INVALID_TEXT = "invalid-text"
    JSON_LIMIT_EXCEEDED = "json-limit-exceeded"
    MULTIPART_BOUNDARY_COLLISION = "multipart-boundary-collision"
    SOURCE_FAILED = "source-failed"
    STORE_FAILED = "store-failed"


@final
class BodyMaterializationError(Exception):
    """Value-free serialization failure with optional nested store classification."""

    __slots__ = ("code", "store_code")

    def __init__(
        self,
        code: BodyMaterializationFailureCode,
        *,
        store_code: BodyStoreFailureCode | None = None,
    ) -> None:
        """Retain only stable classifications and never a body value or filename."""
        if type(code) is not BodyMaterializationFailureCode:
            raise TypeError("materialization failure code must be typed")
        if store_code is not None and type(store_code) is not BodyStoreFailureCode:
            raise TypeError("nested body-store failure code must be typed")
        if (code is BodyMaterializationFailureCode.STORE_FAILED) is (store_code is None):
            raise ValueError("store materialization failures require one store code")
        self.code = code
        self.store_code = store_code
        super().__init__(code.value)


class RequestBodyMaterializer:
    """Serialize typed bodies once and commit exact retry-safe bytes to a run-local store."""

    __slots__ = ("_limits", "_reader", "_store")

    def __init__(
        self,
        store: TransientBodyStore,
        limits: BodyMaterializationLimits,
        *,
        source_reader: RepeatableSourceReader | None = None,
    ) -> None:
        """Compose storage and existing-source reading behind narrow interfaces."""
        if type(store) is not TransientBodyStore:
            raise TypeError("request body materializer requires TransientBodyStore")
        if type(limits) is not BodyMaterializationLimits:
            raise TypeError("request body materializer requires BodyMaterializationLimits")
        self._store = store
        self._limits = limits
        self._reader = source_reader or store

    async def materialize(
        self,
        body: RequestBody,
        *,
        body_id: BodyId,
        provenance: ValueProvenance,
    ) -> NoMaterializedBody | MaterializedBody:
        """Return absence or one complete repeatable snapshot with exact digest lineage."""
        if type(body_id) is not BodyId or type(provenance) is not ValueProvenance:
            raise TypeError("body materialization requires BodyId and ValueProvenance")
        if type(body) is NoBody:
            return NoMaterializedBody()
        origins = request_body_taint_origins(body)
        sensitivity = Sensitivity.SENSITIVE if origins else Sensitivity.PUBLIC
        source: RepeatableByteSource | None = None
        store_failure: BodyStoreFailureCode | None = None
        try:
            writer = await self._store.begin(
                body_id,
                maximum_body_bytes=self._limits.maximum_body_bytes,
                sensitivity=sensitivity,
                origins=origins,
            )
            async with writer:
                async for chunk in self._serialize(body):
                    await writer.append(chunk)
                source = await writer.commit_request(provenance)
        except BodyStoreError as error:
            store_failure = error.code
        if store_failure is not None or source is None:
            raise BodyMaterializationError(
                BodyMaterializationFailureCode.STORE_FAILED,
                store_code=store_failure or BodyStoreFailureCode.IO_FAILED,
            )
        multipart = None
        if type(body) is MultipartBody:
            multipart = MultipartBoundaryValidation(
                boundary=body.boundary,
                source_id=source.source_id,
                digest=source.digest,
                encoded_content_type=multipart_content_type(body.boundary),
            )
        return MaterializedBody(source=source, multipart=multipart)

    async def _serialize(self, body: RequestBody) -> AsyncIterator[bytes]:
        if type(body) is JsonBody:
            value = _reveal_json(body.value)
            yield _encode_json(value, self._limits)
            return
        if type(body) is TextBody:
            yield _encode_text(_reveal_text(body.value))
            return
        if type(body) is BytesBody:
            async for chunk in self._bytes(body.value):
                yield chunk
            return
        if type(body) is FormBody:
            yield _encode_form(body)
            return
        if type(body) is MultipartBody:
            async for chunk in self._multipart(body):
                yield chunk
            return
        raise TypeError("body materializer requires a supported RequestBody")

    async def _bytes(
        self,
        value: BytesValue | SensitiveValue[BytesValue] | RepeatableByteSource,
    ) -> AsyncIterator[bytes]:
        if type(value) is RepeatableByteSource:
            failure = False
            try:
                async for chunk in self._reader.open_request(value):
                    yield chunk
            except BodyStoreError:
                failure = True
            if failure:
                raise BodyMaterializationError(BodyMaterializationFailureCode.SOURCE_FAILED)
            return
        exact = (
            cast(BytesValue, value.reveal(access=_BODY_ACCESS))
            if is_sensitive_value(value)
            else value
        )
        if type(exact) is not BytesValue:  # pragma: no cover - domain union invariant
            raise TypeError("bytes body contains unsupported content")
        yield exact.value

    async def _multipart(self, body: MultipartBody) -> AsyncIterator[bytes]:
        boundary = body.boundary.value.encode("ascii")
        for part in body.parts:
            if type(part) is MultipartTextPart:
                content = _encode_text(_reveal_text(part.value))
                _reject_boundary(content, boundary)
            else:
                await self._scan_file_part(part, boundary)
        for part in body.parts:
            yield b"--" + boundary + b"\r\n"
            if type(part) is MultipartTextPart:
                yield _text_part_headers(part)
                yield _encode_text(_reveal_text(part.value))
            else:
                yield _file_part_headers(part)
                async for chunk in self._bytes(part.value):
                    yield chunk
            yield b"\r\n"
        yield b"--" + boundary + b"--\r\n"

    async def _scan_file_part(
        self,
        part: MultipartFilePart,
        boundary: bytes,
    ) -> None:
        overlap = b""
        async for chunk in self._bytes(part.value):
            candidate = overlap + chunk
            _reject_boundary(candidate, boundary)
            retained = len(boundary) - 1
            overlap = candidate[-retained:] if retained else b""


def _reveal_text(value: BodyText | SensitiveValue[BodyText]) -> BodyText:
    if type(value) is BodyText:
        return value
    return value.reveal(access=_BODY_ACCESS)


def _reveal_json(value: JsonValue | SensitiveValue[JsonValue]) -> JsonValue:
    if not is_sensitive_value(value):
        return cast(JsonValue, value)
    return cast(JsonValue, value.reveal(access=_BODY_ACCESS))


def _encode_text(value: BodyText) -> bytes:
    encoded: bytes | None = None
    with suppress(UnicodeEncodeError):
        encoded = value.value.encode("utf-8")
    if encoded is None:
        raise BodyMaterializationError(BodyMaterializationFailureCode.INVALID_TEXT)
    return encoded


def _encode_form(body: FormBody) -> bytes:
    encoded: bytes | None = None
    with suppress(UnicodeEncodeError):
        encoded = "&".join(
            f"{quote_plus(field.name.value, safe='')}="
            f"{quote_plus(_reveal_text(field.value).value, safe='')}"
            for field in body.fields
        ).encode("ascii")
    if encoded is None:  # pragma: no cover - quote_plus emits ASCII
        raise BodyMaterializationError(BodyMaterializationFailureCode.INVALID_TEXT)
    return encoded


def _text_part_headers(part: MultipartTextPart) -> bytes:
    result = f'Content-Disposition: form-data; name="{part.name.value}"\r\n'
    if part.media_type is not None:
        result += f"Content-Type: {render_media_type(part.media_type).value}\r\n"
    return (result + "\r\n").encode("latin-1")


def _file_part_headers(part: MultipartFilePart) -> bytes:
    filename = part.filename
    exact = (
        filename
        if type(filename) is MultipartDispositionFilename
        else filename.reveal(access=_BODY_ACCESS)
    )
    encoded: bytes | None = None
    try:
        encoded_filename = quote(exact.value, safe="", encoding="utf-8", errors="strict")
        result = (
            f'Content-Disposition: form-data; name="{part.name.value}"; '
            f"filename*=UTF-8''{encoded_filename}\r\n"
            f"Content-Type: {render_media_type(part.media_type).value}\r\n\r\n"
        )
        encoded = result.encode("latin-1")
    except UnicodeEncodeError:
        pass
    if encoded is None:
        raise BodyMaterializationError(BodyMaterializationFailureCode.INVALID_TEXT)
    return encoded


def _reject_boundary(content: bytes, boundary: bytes) -> None:
    if boundary in content:
        raise BodyMaterializationError(BodyMaterializationFailureCode.MULTIPART_BOUNDARY_COLLISION)


def _encode_json(value: JsonValue, limits: BodyMaterializationLimits) -> bytes:
    output = bytearray()
    nodes = 0
    stack: list[tuple[JsonValue | bytes, int]] = [(value, 1)]
    invalid_text = False
    try:
        while stack:
            current, depth = stack.pop()
            if type(current) is bytes:
                output.extend(current)
                continue
            json_value = cast(JsonValue, current)
            nodes += 1
            if nodes > limits.maximum_json_nodes.value or depth > limits.maximum_json_depth.value:
                raise BodyMaterializationError(BodyMaterializationFailureCode.JSON_LIMIT_EXCEEDED)
            scalar = _json_scalar(json_value)
            if scalar is not None:
                output.extend(scalar)
            else:
                _push_json_container(json_value, depth, output, stack)
    except UnicodeEncodeError:
        invalid_text = True
    if invalid_text:
        raise BodyMaterializationError(BodyMaterializationFailureCode.INVALID_TEXT)
    return bytes(output)


def _json_string(value: str) -> bytes:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return rendered.encode("utf-8")


def _json_scalar(value: JsonValue) -> bytes | None:
    if type(value) is JsonNull:
        return b"null"
    if type(value) is JsonBoolean:
        return b"true" if value.value else b"false"
    if type(value) is JsonInteger:
        return str(value.value).encode("ascii")
    if type(value) is JsonDecimal:
        return str(value.value).replace("E", "e").replace("e+", "e").encode("ascii")
    if type(value) is JsonString:
        return _json_string(value.value)
    return None


def _push_json_container(
    value: JsonValue,
    depth: int,
    output: bytearray,
    stack: list[tuple[JsonValue | bytes, int]],
) -> None:
    if type(value) is JsonArray:
        output.extend(b"[")
        stack.append((b"]", depth))
        for index, item in reversed(tuple(enumerate(value.items))):
            stack.append((item, depth + 1))
            if index:
                stack.append((b",", depth))
        return
    if type(value) is JsonObject:
        output.extend(b"{")
        stack.append((b"}", depth))
        for index, member in reversed(tuple(enumerate(value.members))):
            stack.append((member.value, depth + 1))
            stack.append((b":", depth))
            stack.append((_json_string(member.name), depth))
            if index:
                stack.append((b",", depth))
        return
    raise TypeError("unsupported JSON value")  # pragma: no cover - closed union


__all__ = (
    "BodyMaterializationError",
    "BodyMaterializationFailureCode",
    "BodyMaterializationLimits",
    "RepeatableSourceReader",
    "RequestBodyMaterializer",
)
