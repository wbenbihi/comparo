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

from comparo.core.artifacts import sanitization
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.artifacts.sanitization import SanitizationBoundaryClosedError
from comparo.core.artifacts.sanitization import SanitizationBoundarySealedError
from comparo.core.artifacts.sanitization import SanitizationBoundaryUnsealedError
from comparo.core.artifacts.sanitization import SanitizationBudgetExceededError
from comparo.core.artifacts.sanitization import SanitizedBytes
from comparo.core.artifacts.sanitization import SanitizedText
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
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


def _limits(
    *,
    patterns: int = 4096,
    pattern_bytes: int = 1_048_576,
    value_bytes: int = 262_144,
    value_nodes: int = 4096,
) -> SanitizationLimits:
    return SanitizationLimits(
        maximum_patterns=PositiveCount(value=patterns),
        maximum_pattern_bytes=ByteCount(value=pattern_bytes),
        maximum_value_bytes=ByteCount(value=value_bytes),
        maximum_value_nodes=PositiveCount(value=value_nodes),
    )


def _secret[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(_ORIGIN,))


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


async def _untyped_chunks() -> AsyncIterator[bytes]:
    yield b"safe"
    yield bytearray(b"unsafe")  # type: ignore[misc]


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
    boundary = SanitizationBoundary(_limits())
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


def test_sensitive_json_member_names_are_registered_as_secret_patterns() -> None:
    boundary = SanitizationBoundary(_limits())
    boundary.register(
        _secret(
            JsonObject(
                members=(
                    JsonObjectMember(
                        name="secret-key",
                        value=JsonString(value="secret-value"),
                    ),
                )
            )
        )
    )
    boundary.seal()

    result = boundary.sanitize_text("/secret-key/secret-value")

    assert "secret-key" not in result.value
    assert "secret-value" not in result.value


def test_binary_patterns_cannot_split_valid_utf8_text() -> None:
    boundary = SanitizationBoundary(_limits())
    boundary.register(_secret(BytesValue(value=b"\xa9")))
    boundary.seal()

    assert boundary.sanitize_text("é").value == "é"


def test_pattern_budgets_are_incremental_deduplicated_and_fail_closed() -> None:
    deduplicated = SanitizationBoundary(_limits(patterns=3))
    deduplicated.register(_secret(JsonString(value="x")))
    deduplicated.register(_secret(JsonString(value="x")))
    deduplicated.seal()
    assert deduplicated.sanitize_text("x").value == "<redacted>"

    for limits in (_limits(patterns=1), _limits(pattern_bytes=5)):
        exhausted = SanitizationBoundary(limits)
        with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
            exhausted.register(_secret(JsonString(value="secret")))
        with pytest.raises(SanitizationBoundaryClosedError, match="closed"):
            exhausted.seal()

    composite = SanitizationBoundary(_limits(pattern_bytes=60, value_bytes=10))
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        composite.register(_secret(JsonArray(items=(JsonString(value="x" * 100),))))
    with pytest.raises(SanitizationBoundaryClosedError, match="closed"):
        composite.seal()

    nested: object = JsonString(value="leaf")
    for _index in range(20):
        nested = JsonArray(items=(nested,))  # type: ignore[arg-type]
    deep = SanitizationBoundary(_limits(value_nodes=10))
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        deep.register(_secret(nested))
    with pytest.raises(SanitizationBoundaryClosedError, match="closed"):
        deep.seal()

    direct = SanitizationBoundary(_limits(value_bytes=1))
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        direct._add_pattern_derivatives(b"too-large")
    direct.close()


def test_streaming_replacement_crosses_arbitrary_chunk_boundaries() -> None:
    secret = "longest-secret-value"
    boundary = SanitizationBoundary(_limits(), marker="[MASKED]")
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
    boundary = SanitizationBoundary(_limits())
    boundary.seal()
    output: list[bytes] = []

    count = asyncio.run(boundary.sanitize_stream(_chunks(b"one", b"two"), _writer(output)))

    assert output == [b"one", b"two"]
    assert count == 0


def test_stream_sanitization_closes_its_iterator_when_the_destination_fails() -> None:
    boundary = SanitizationBoundary(_limits())
    boundary.seal()
    closed = False

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"value"
        finally:
            closed = True

    async def fail_write(_value: bytes) -> None:
        raise OSError("destination failed")

    with pytest.raises(OSError, match="destination failed"):
        asyncio.run(boundary.sanitize_stream(chunks(), fail_write))
    assert closed


def test_mandatory_and_sealed_fields_are_masked_whole() -> None:
    boundary = SanitizationBoundary(_limits(), marker="<safe>")
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
    boundary = SanitizationBoundary(_limits())
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
    snapshot = await registry.snapshot()
    boundary = SanitizationBoundary.from_snapshot(snapshot, _limits())
    boundary.seal()

    assert boundary.sanitize_text("snapshot-secret").value == "<redacted>"
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        SanitizationBoundary.from_snapshot(snapshot, _limits(value_bytes=1))


def test_boundary_lifecycle_fails_closed_and_close_is_idempotent() -> None:
    boundary = SanitizationBoundary(_limits())
    with pytest.raises(SanitizationBoundaryUnsealedError, match="not sealed"):
        boundary.sanitize_bytes(b"value")
    boundary.register(_secret(JsonString(value="wipe-me")))
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
    with pytest.raises(TypeError, match="requires SanitizationLimits"):
        SanitizationBoundary(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SanitizationSnapshot"):
        SanitizationBoundary.from_snapshot(object(), _limits())  # type: ignore[arg-type]
    boundary = SanitizationBoundary(_limits())
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
        SanitizedText(value="", redaction_count=True)
    with pytest.raises(TypeError, match="must be bytes"):
        SanitizedBytes(value=bytearray(), redaction_count=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        SanitizedBytes(value=b"", redaction_count=-1)
    with pytest.raises(TypeError, match="chunks must be bytes"):
        asyncio.run(boundary.sanitize_stream(_untyped_chunks(), _writer([])))


def test_all_supported_scalar_and_structural_payloads_seed_exact_patterns() -> None:
    values: tuple[tuple[object, bytes], ...] = (
        ("raw-text", b"raw-text"),
        (ByteCount(value=42), b"42"),
        (Duration(nanoseconds=73), b"73"),
        (Sha256Digest(hex_value="a" * 64), b"a" * 64),
    )
    for value, expected in values:
        boundary = SanitizationBoundary(_limits())
        boundary.register(_secret(value))
        boundary.seal()
        assert boundary.sanitize_bytes(expected).value == b"<redacted>"

    boundary = SanitizationBoundary(_limits())
    boundary.register(_secret(BytesValue(value=b"")))
    boundary.register(_secret(BytesValue(value=b"\xff\xfe")))
    boundary.seal()
    assert boundary.sanitize_bytes(b"before\xff\xfeafter").value == (b"before<redacted>after")

    unsupported = SanitizationBoundary(_limits())
    with pytest.raises(TypeError, match="unsupported sensitive payload"):
        unsupported.register(_secret(("immutable", "tuple")))
    with pytest.raises(SanitizationBoundaryClosedError, match="closed"):
        unsupported.seal()


def test_literal_replacement_prefers_earliest_then_longest_and_flushes_tail() -> None:
    buffer = bytearray(b"x-secret-and-token-tail")
    output, count = sanitization._replace_literals(
        buffer,
        [bytearray(b"secret"), bytearray(b"token"), bytearray(b"sec")],
        b"[x]",
        final=True,
    )
    assert output == b"x-[x]-and-[x]-tail"
    assert count == 2
    assert buffer == b""

    partial = bytearray(b"prefix-secr")
    output, count = sanitization._replace_literals(
        partial,
        [bytearray(b"secret")],
        b"[x]",
        final=False,
    )
    assert output == b"prefix"
    assert count == 0
    assert partial == b"-secr"


def test_exact_json_rendering_covers_every_closed_variant() -> None:
    value = JsonObject(
        members=(
            JsonObjectMember(
                name='a"b',
                value=JsonArray(
                    items=(
                        JsonNull(),
                        JsonBoolean(value=True),
                        JsonBoolean(value=False),
                        JsonInteger(value=7),
                        JsonDecimal(value=Decimal("1.25")),
                        JsonString(value="text"),
                    )
                ),
            ),
            JsonObjectMember(name="second", value=JsonNull()),
        )
    )
    assert sanitization._bounded_json_bytes(value, maximum=100, maximum_nodes=16) == (
        rb'{"a\"b":[null,true,false,7,1.25,"text"],"second":null}'
    )
    with pytest.raises(TypeError, match="only exact JSON"):
        sanitization._bounded_json_bytes(
            BytesValue(value=b"x"),
            maximum=100,
            maximum_nodes=16,
        )


def test_internal_payload_and_json_bounds_fail_before_unbounded_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        tuple(
            sanitization._sensitive_payload_bytes(
                JsonNull(),
                maximum=16,
                maximum_nodes=0,
            )
        )
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        tuple(
            sanitization._sensitive_payload_bytes(
                JsonString(value="xx"),
                maximum=1,
                maximum_nodes=1,
            )
        )
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        tuple(
            sanitization._sensitive_payload_bytes(
                Duration(nanoseconds=73),
                maximum=1,
                maximum_nodes=1,
            )
        )

    def bounded_json_stub(
        _value: object,
        *,
        maximum: int,
        maximum_nodes: int,
    ) -> bytes:
        del maximum, maximum_nodes
        return b"{}"

    monkeypatch.setattr(sanitization, "_bounded_json_bytes", bounded_json_stub)
    named = JsonObject(members=(JsonObjectMember(name="long-name", value=JsonNull()),))
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        tuple(
            sanitization._sensitive_payload_bytes(
                named,
                maximum=1,
                maximum_nodes=2,
            )
        )
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        tuple(
            sanitization._sensitive_payload_bytes(
                JsonArray(items=(JsonNull(),)),
                maximum=16,
                maximum_nodes=1,
            )
        )
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        tuple(
            sanitization._sensitive_payload_bytes(
                named,
                maximum=16,
                maximum_nodes=1,
            )
        )


def test_bounded_json_rendering_rejects_each_independent_limit() -> None:
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        sanitization._bounded_json_bytes(JsonNull(), maximum=3, maximum_nodes=1)
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        sanitization._bounded_json_bytes(JsonNull(), maximum=16, maximum_nodes=0)
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        sanitization._bounded_json_bytes(
            JsonObject(members=(JsonObjectMember(name="name", value=JsonNull()),)),
            maximum=100,
            maximum_nodes=1,
        )
    with pytest.raises(SanitizationBudgetExceededError, match="budget exceeded"):
        sanitization._bounded_json_quote('"', maximum=1)


def test_scalar_fallbacks_are_explicit_and_bounded_to_supported_shapes() -> None:
    class Value:
        def __init__(self, value: object) -> None:
            self.value = value

    assert sanitization._sensitive_scalar_bytes(Value("text")) == b"text"
    assert sanitization._sensitive_scalar_bytes(Value(b"bytes")) == b"bytes"
    assert sanitization._sensitive_scalar_bytes(Value(12)) == b"12"
    assert sanitization._sensitive_scalar_bytes(Value(object())) is None
