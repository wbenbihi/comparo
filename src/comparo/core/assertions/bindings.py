"""Canonical identities for externally prepared pure assertion capabilities."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol
from typing import cast

from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString

_ACCESS = SensitiveValueAccess(purpose="assertion.capability.bind")
_DIGEST_IMPLEMENTATION = NamedCapabilityTarget(
    kind=CapabilityTargetKind.PROVIDER,
    identifier="comparo.core.digest@1",
)


class _Digest(Protocol):
    """The incremental hash surface used by canonical value encoding."""

    def update(self, value: bytes) -> object:
        """Consume one canonical chunk."""


class _Updater(Protocol):
    """One incremental hash update method."""

    def __call__(self, value: bytes) -> object:
        """Consume one canonical chunk."""


def operation_fingerprint(operator: AssertionOperator) -> Fingerprint:
    """Fingerprint the complete typed operator semantics, not only its compiled asset."""
    if type(operator) not in {RegexOperator, SchemaOperator, DigestOperator}:
        raise TypeError("only prepared-capability operators have operation fingerprints")
    return _operator_fingerprint(operator)


def capability_implementation(operator: AssertionOperator) -> NamedCapabilityTarget:
    """Return the exact implementation identity required by one prepared capability."""
    if type(operator) is RegexOperator:
        compiler = operator.compiler
        return NamedCapabilityTarget(
            kind=CapabilityTargetKind.PROVIDER,
            identifier=f"{compiler.namespace}.{compiler.name}@{compiler.version}",
        )
    if type(operator) is SchemaOperator:
        return operator.validator
    if type(operator) is DigestOperator:
        return _DIGEST_IMPLEMENTATION
    raise TypeError("operator does not use a prepared capability")


def selected_fingerprint(
    match: SelectionMatch,
) -> Fingerprint | SensitiveValue[Fingerprint]:
    """Bind a prepared result to the exact selected value while preserving sensitivity."""
    if type(match) is not SelectionMatch:
        raise TypeError("selected capability binding requires a SelectionMatch")
    return _fingerprint_values((match.value,))


def operands_fingerprint(
    operands: tuple[object, ...],
) -> Fingerprint | SensitiveValue[Fingerprint]:
    """Bind a prepared result to the exact resolved operands used by the engine."""
    if type(operands) is not tuple:
        raise TypeError("capability operands must be an immutable tuple")
    return _fingerprint_values(operands)


def fingerprints_equal(
    left: Fingerprint | SensitiveValue[Fingerprint],
    right: Fingerprint | SensitiveValue[Fingerprint],
) -> bool:
    """Compare public or sealed fingerprints without exposing them at the API boundary."""
    return _reveal_fingerprint(left) == _reveal_fingerprint(right)


def _fingerprint_values(
    values: tuple[object, ...],
) -> Fingerprint | SensitiveValue[Fingerprint]:
    origin_groups: list[tuple[TaintOriginId, ...]] = []
    revealed: list[object] = []
    for value in values:
        if is_sensitive_value(value):
            origin_groups.append(value.origins)
            revealed.append(value.reveal(access=_ACCESS))
        else:
            revealed.append(value)
    fingerprint = _value_fingerprint(tuple(revealed))
    origins = stable_taint_origin_union(*origin_groups)
    if not origins:
        return fingerprint
    return SensitiveValue(fingerprint, origins=tuple(origins))


def _operator_fingerprint(operator: AssertionOperator) -> Fingerprint:
    digest = sha256()
    digest.update(b"comparo.assertion.operation.v1\x00")
    if type(operator) is RegexOperator:
        for value in (
            operator.pattern,
            operator.mode.value,
            operator.dialect.value,
            operator.compiler.namespace,
            operator.compiler.name,
            operator.compiler.version,
            operator.compiled_fingerprint.digest.hex_value,
            "1" if operator.negate else "0",
        ):
            _update_text(cast("_Updater", digest.update), value)
    elif type(operator) is SchemaOperator:
        _update_schema_operator(digest, operator)
    elif type(operator) is DigestOperator:
        _update_text(cast("_Updater", digest.update), operator.algorithm.value)
    else:  # pragma: no cover - guarded by public entry point
        raise TypeError("operator does not use a prepared capability")
    return _finish(digest.hexdigest())


def _update_schema_operator(digest: _Digest, operator: SchemaOperator) -> None:
    schema = operator.schema
    _update_text(digest.update, schema.fingerprint.digest.hex_value)
    if schema.remote_uri is not None:
        _update_text(digest.update, "remote")
        _update_text(digest.update, schema.remote_uri.identifier)
    else:
        resource = schema.resource
        if resource is None:  # pragma: no cover - CompiledSchema invariant
            raise ValueError("compiled schema lost its source identity")
        if type(resource) is DefinitionRef:
            _update_text(digest.update, "resource")
            _update_text(digest.update, resource.identity.namespace.value)
            _update_text(digest.update, resource.identity.kind.value)
            _update_text(digest.update, resource.identity.resource_id.value)
            _update_text(digest.update, resource.fingerprint.digest.hex_value)
        elif type(resource) is InlineDefinitionRef:
            _update_text(digest.update, "inline")
            _update_text(digest.update, resource.origin.owner.namespace.value)
            _update_text(digest.update, resource.origin.owner.kind.value)
            _update_text(digest.update, resource.origin.owner.resource_id.value)
            _update_text(digest.update, resource.origin.fragment.value)
            _update_text(digest.update, resource.fingerprint.digest.hex_value)
        else:  # pragma: no cover - CompiledSchema invariant
            raise TypeError("compiled schema source has an unsupported identity")
    for value in (
        operator.default_dialect.identifier,
        operator.validator.identifier,
        "1" if operator.assert_formats else "0",
    ):
        _update_text(digest.update, value)


def _value_fingerprint(values: tuple[object, ...]) -> Fingerprint:
    digest = sha256()
    digest.update(b"comparo.assertion.selected-value.v1\x00")
    stack: list[object] = list(reversed(values))
    digest.update(len(values).to_bytes(8, "big"))
    while stack:
        value = stack.pop()
        _update_value(digest, value, stack)
    return _finish(digest.hexdigest())


def _finish(hex_value: str) -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hex_value),
    )


def _update_value(digest: _Digest, value: object, stack: list[object]) -> None:  # noqa: C901
    update = digest.update
    update(f"{type(value).__module__}.{type(value).__qualname__}\x00".encode("ascii"))
    if type(value) is JsonNull or type(value) is BareQueryFieldValue:
        return
    if type(value) is JsonBoolean:
        update(b"1" if value.value else b"0")
    elif type(value) is JsonInteger:
        _update_integer(update, value.value)
    elif type(value) is JsonDecimal:
        sign, digits, exponent = value.value.as_tuple()
        update(bytes((sign,)))
        _update_integer(update, cast("int", exponent))
        update(len(digits).to_bytes(8, "big"))
        update(bytes(digits))
    elif type(value) is JsonString or type(value) is HttpFieldTextValue:
        _update_text(update, value.value)
    elif type(value) is BytesValue:
        update(len(value.value).to_bytes(8, "big"))
        update(value.value)
    elif type(value) is JsonArray:
        update(len(value.items).to_bytes(8, "big"))
        stack.extend(reversed(value.items))
    elif type(value) is JsonObject:
        update(len(value.members).to_bytes(8, "big"))
        for member in reversed(value.members):
            stack.append(member.value)
            stack.append(member.name)
    elif type(value) is str:
        _update_text(update, value)
    elif type(value) is Count or type(value) is ByteCount:
        _update_integer(update, value.value)
    elif type(value) is Duration:
        _update_integer(update, value.nanoseconds)
    elif type(value) is UtcInstant:
        _update_integer(update, value.unix_nanoseconds)
    elif type(value) is Ratio:
        _update_integer(update, value.numerator)
        _update_integer(update, value.denominator.value)
    elif type(value) is BodyId or type(value) is ViewId:
        update(value.value.bytes)
    elif type(value) is HttpFieldItems:
        update(len(value.items).to_bytes(8, "big"))
        stack.extend(reversed(tuple(item.value for item in value.items)))
    elif type(value) is MediaType:
        stack.extend(
            reversed(
                (
                    value.type,
                    value.subtype,
                    *(part for item in value.parameters for part in (item.name, item.value.value)),
                )
            )
        )
    else:
        raise TypeError("unsupported prepared capability binding value")


def _update_text(update: _Updater, value: str) -> None:
    update(len(value).to_bytes(8, "big"))
    for character in value:
        update(character.encode("utf-8"))


def _update_integer(update: _Updater, value: int) -> None:
    update(b"-" if value < 0 else b"+")
    magnitude = abs(value)
    width = max(1, (magnitude.bit_length() + 7) // 8)
    update(width.to_bytes(8, "big"))
    update(magnitude.to_bytes(width, "big"))


def _reveal_fingerprint(
    value: Fingerprint | SensitiveValue[Fingerprint],
) -> Fingerprint:
    if is_sensitive_value(value):
        if value.payload_type is not Fingerprint:
            raise TypeError("sealed capability binding must contain a Fingerprint")
        return cast("Fingerprint", value.reveal(access=_ACCESS))
    if type(value) is not Fingerprint:
        raise TypeError("capability binding must be a Fingerprint")
    return value
