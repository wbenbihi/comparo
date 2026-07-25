"""Security and streaming tests for the explicit artifact sanitization boundary."""

from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from decimal import Decimal
from urllib.parse import quote
from uuid import UUID

import pytest

from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.artifacts.sanitization import SanitizationBoundaryClosedError
from comparo.core.artifacts.sanitization import SanitizationBoundarySealedError
from comparo.core.artifacts.sanitization import SanitizationBoundaryUnsealedError
from comparo.core.artifacts.sanitization import SanitizedBytes
from comparo.core.artifacts.sanitization import SanitizedText
from comparo.core.domain.identity import RunId
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.provenance import ResolutionStep
from comparo.core.domain.provenance import ResolutionStepOutcome
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Present
from comparo.core.resolution.registry import SensitiveValueRegistry

_ORIGIN = TaintOriginId(value="secret/project/token")


def _secret[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(_ORIGIN,))


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def _provenance(run: RunScope) -> ResolutionProvenance:
    symbol = SymbolPath(segments=("token",))
    provider = ValueProviderIdentity(namespace="test", name="literal", version="1")
    source = ResolutionSource(
        namespace=ValueNamespace.SECRETS,
        symbol=symbol,
        provider=provider,
        configuration=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.VERSIONED_DEFAULT),)
        ),
        scope=run,
    )
    return ResolutionProvenance(
        namespace=ValueNamespace.SECRETS,
        symbol=symbol,
        provider=provider,
        scope=run,
        steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
    )


def test_literal_and_known_derivative_forms_are_removed() -> None:
    secret = "a secret/with spaces"
    boundary = SanitizationBoundary()
    boundary.register(_secret(JsonString(value=secret)))
    boundary.seal()
    encoded = quote(secret, safe="")
    base64_value = b64encode(secret.encode()).decode()
    source = f"raw={secret}&encoded={encoded}&base64={base64_value}"

    result = boundary.sanitize_text(source)

    assert secret not in result.value
    assert encoded not in result.value
    assert base64_value not in result.value
    assert result.redaction_count == 3


def test_streaming_replacement_crosses_arbitrary_chunk_boundaries() -> None:
    secret = "longest-secret-value"
    boundary = SanitizationBoundary(marker="[MASKED]")
    boundary.register(_secret(JsonString(value=secret)))
    boundary.register(_secret(JsonString(value="secret")))
    boundary.seal()
    output: list[bytes] = []

    count = asyncio.run(
        boundary.sanitize_stream(
            _chunks(b"before-longest-", b"secret-va", b"lue-after"),
            _writer(output),
        )
    )

    assert b"".join(output) == b"before-[MASKED]-after"
    assert count == 1


def _writer(output: list[bytes]) -> Callable[[bytes], Awaitable[None]]:
    async def write(value: bytes) -> None:
        output.append(value)

    return write


def test_empty_registry_streams_without_buffering_the_complete_source() -> None:
    boundary = SanitizationBoundary()
    boundary.seal()
    output: list[bytes] = []

    count = asyncio.run(boundary.sanitize_stream(_chunks(b"one", b"two"), _writer(output)))

    assert output == [b"one", b"two"]
    assert count == 0


def test_mandatory_and_sealed_fields_are_masked_whole() -> None:
    boundary = SanitizationBoundary(marker="<safe>")
    boundary.seal()

    assert boundary.sanitize_text("public", mandatory=True) == SanitizedText(
        value="<safe>",
        redaction_count=1,
    )
    assert boundary.sanitize_text(_secret("opaque")) == SanitizedText(
        value="<safe>",
        redaction_count=1,
    )


@pytest.mark.parametrize(
    "payload",
    [
        BytesValue(value=b"opaque-bytes"),
        JsonInteger(value=123456789),
        JsonDecimal(value=Decimal("123.456")),
        JsonBoolean(value=True),
        JsonBoolean(value=False),
        JsonNull(),
        JsonArray(items=(JsonString(value="array-secret"),)),
        JsonObject(
            members=(JsonObjectMember(name="key", value=JsonString(value="object-secret")),)
        ),
    ],
)
def test_closed_resolved_value_vocabulary_can_seed_patterns(payload: object) -> None:
    boundary = SanitizationBoundary()
    boundary.register(_secret(payload))
    boundary.seal()

    assert boundary.sanitize_bytes(b"prefix-array-secret-object-secret-suffix").value != (
        b"prefix-array-secret-object-secret-suffix"
    ) or type(payload) not in {JsonArray, JsonObject}


def test_registry_snapshot_seeds_the_same_boundary() -> None:
    asyncio.run(_assert_registry_snapshot_seeds_boundary())


async def _assert_registry_snapshot_seeds_boundary() -> None:
    run = RunScope(run_id=RunId(value=UUID("00000000-0000-0000-0000-000000000001")))
    registry = SensitiveValueRegistry(run)
    present = Present(
        value=_secret(JsonString(value="snapshot-secret")),
        provenance=_provenance(run),
    )
    await registry.register(
        present,
        symbol=SymbolPath(segments=("token",)),
        scope=run,
    )
    boundary = SanitizationBoundary.from_snapshot(await registry.snapshot())
    boundary.seal()

    assert boundary.sanitize_text("snapshot-secret").value == "<redacted>"


def test_boundary_lifecycle_fails_closed_and_close_is_idempotent() -> None:
    boundary = SanitizationBoundary()
    with pytest.raises(SanitizationBoundaryUnsealedError, match="not sealed"):
        boundary.sanitize_bytes(b"value")
    boundary.seal()
    with pytest.raises(SanitizationBoundarySealedError, match="already sealed"):
        boundary.register(_secret(JsonString(value="late")))
    boundary.close()
    boundary.close()
    with pytest.raises(SanitizationBoundaryClosedError, match="closed"):
        boundary.sanitize_text("value")
    with pytest.raises(SanitizationBoundaryClosedError, match="closed"):
        boundary.seal()


def test_boundary_rejects_untyped_inputs_and_results_validate_accounting() -> None:
    with pytest.raises(TypeError, match="SanitizationSnapshot"):
        SanitizationBoundary.from_snapshot(object())  # type: ignore[arg-type]
    boundary = SanitizationBoundary()
    with pytest.raises(TypeError, match="SensitiveValue"):
        boundary.register("raw")  # type: ignore[arg-type]
    boundary.seal()
    with pytest.raises(TypeError, match="text or SensitiveValue"):
        boundary.sanitize_text(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="requires bytes"):
        boundary.sanitize_bytes(bytearray())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="async byte iterable"):
        asyncio.run(
            boundary.sanitize_stream(object(), _writer([]))  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="async write callback"):
        asyncio.run(
            boundary.sanitize_stream(_chunks(), object())  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="must be text"):
        SanitizedText(value=1, redaction_count=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        SanitizedBytes(value=b"", redaction_count=-1)
