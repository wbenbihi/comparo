"""Tests for Comparo's exact values and resolution outcomes."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import msgspec
import pytest

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
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason
from comparo.core.domain.values import is_json_value
from comparo.core.domain.values import json_number


def _configuration_provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.VERSIONED_DEFAULT),))


def _provenance(
    outcome: ResolutionStepOutcome = ResolutionStepOutcome.PRESENT,
    *,
    namespace: ValueNamespace = ValueNamespace.VARIABLES,
    source_namespace: ValueNamespace = ValueNamespace.VARIABLES,
) -> ResolutionProvenance:
    provider = ValueProviderIdentity(namespace="comparo.core", name="literal", version="1")
    scope = RunScope(run_id=RunId(value=UUID("00000000-0000-0000-0000-000000000001")))
    source = ResolutionSource(
        namespace=source_namespace,
        symbol=SymbolPath(segments=("value",)),
        provider=provider,
        configuration=_configuration_provenance(),
        scope=scope,
    )
    return ResolutionProvenance(
        namespace=namespace,
        symbol=source.symbol,
        provider=provider,
        scope=scope,
        steps=(ResolutionStep(source=source, outcome=outcome),),
    )


def test_json_scalars_retain_exact_types() -> None:
    assert JsonNull() == JsonNull()
    assert JsonBoolean(value=False).value is False
    assert JsonInteger(value=10**100).value == 10**100
    assert JsonDecimal(value=Decimal("1.23")).value == Decimal("1.23")
    assert JsonString(value="").value == ""
    with pytest.raises(TypeError, match="must be a str"):
        JsonString(value=1)  # type: ignore[arg-type]


def test_json_boolean_and_integer_cannot_be_confused() -> None:
    with pytest.raises(TypeError, match="must be a bool"):
        JsonBoolean(value=1)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="cannot be a bool"):
        JsonInteger(value=True)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_json_decimal_must_be_finite(value: Decimal) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        JsonDecimal(value=value)


def test_json_decimal_rejects_floats() -> None:
    with pytest.raises(TypeError, match="must be a Decimal"):
        JsonDecimal(value=1.5)  # type: ignore[arg-type]


def test_json_number_factory_produces_one_canonical_representation() -> None:
    assert json_number(7) == JsonInteger(value=7)
    assert json_number(Decimal("1.2300")) == JsonDecimal(value=Decimal("1.23"))
    assert json_number(Decimal("1.00")) == JsonInteger(value=1)
    assert json_number(Decimal("-0.00")) == JsonInteger(value=0)

    with pytest.raises(ValueError, match="canonical coefficient"):
        JsonDecimal(value=Decimal("1.2300"))
    with pytest.raises(ValueError, match="must use JsonInteger"):
        JsonDecimal(value=Decimal("1"))
    with pytest.raises(TypeError, match="cannot be a bool"):
        json_number(True)
    with pytest.raises(ValueError, match="must be finite"):
        json_number(Decimal("NaN"))


def test_json_arrays_are_deeply_immutable_and_typed() -> None:
    array = JsonArray(items=(JsonInteger(value=1), JsonString(value="two")))

    assert array.items == (JsonInteger(value=1), JsonString(value="two"))
    with pytest.raises(AttributeError):
        array.items = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="immutable tuple"):
        JsonArray(items=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact JSON"):
        JsonArray(items=(BytesValue(value=b"not-json"),))  # type: ignore[arg-type]


def test_json_objects_retain_order_and_reject_duplicate_names() -> None:
    first = JsonObjectMember(name="first", value=JsonInteger(value=1))
    second = JsonObjectMember(name="second", value=JsonInteger(value=2))
    value = JsonObject(members=(first, second))

    assert value.members == (first, second)
    assert value != JsonObject(members=(second, first))

    with pytest.raises(ValueError, match="must be unique"):
        JsonObject(members=(first, JsonObjectMember(name="first", value=JsonInteger(value=3))))
    with pytest.raises(TypeError, match="member name"):
        JsonObjectMember(name=1, value=JsonInteger(value=1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="member value"):
        JsonObjectMember(
            name="invalid",
            value=BytesValue(value=b"not-json"),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        JsonObject(members=[first])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="JsonObjectMember"):
        JsonObject(members=(JsonString(value="not-member"),))  # type: ignore[arg-type]


def test_bytes_are_distinct_from_json() -> None:
    opaque = BytesValue(value=b"\x00\xff")

    assert not is_json_value(opaque)
    assert is_json_value(JsonString(value="text"))
    with pytest.raises(TypeError, match="must be bytes"):
        BytesValue(value=bytearray(b"mutable"))  # type: ignore[arg-type]


def test_present_distinguishes_falsy_values_from_other_resolution_outcomes() -> None:
    provenance = _provenance()
    zero = Present(value=JsonInteger(value=0), provenance=provenance)
    null = Present(value=JsonNull(), provenance=provenance)

    assert zero.sensitivity is Sensitivity.PUBLIC
    assert null.sensitivity is Sensitivity.PUBLIC
    assert zero.taint_origins == ()
    unavailable = Unavailable(
        reason=UnavailableReason.NOT_FOUND,
        provenance=_provenance(ResolutionStepOutcome.UNAVAILABLE),
    )
    assert isinstance(unavailable, Unavailable)
    assert isinstance(Missing(provenance=_provenance(ResolutionStepOutcome.MISSING)), Missing)
    assert isinstance(
        Error(
            code=ResolutionErrorCode.CONVERSION_FAILED,
            provenance=_provenance(ResolutionStepOutcome.ERROR),
        ),
        Error,
    )
    assert isinstance(
        Blocked(
            code=ResolutionBlockCode.SOURCE_DENIED,
            provenance=_provenance(ResolutionStepOutcome.BLOCKED),
        ),
        Blocked,
    )


def test_present_derives_taint_and_cannot_serialize_a_sensitive_graph() -> None:
    secret = "do-not-leak"
    origin = TaintOriginId(value="secret/project/api-token")
    sensitive = SensitiveValue(JsonString(value=secret), origins=(origin,))
    present = Present[JsonString](value=sensitive, provenance=_provenance())

    assert present.sensitivity is Sensitivity.SENSITIVE
    assert present.taint_origins == (origin,)
    with pytest.raises(TypeError, match="unsupported") as encoded:
        msgspec.json.encode(present)
    with pytest.raises(TypeError, match="unsupported") as built:
        msgspec.to_builtins(present)
    assert secret not in str(encoded.value)
    assert secret not in str(built.value)


@pytest.mark.parametrize(
    ("namespace", "source_namespace"),
    [
        (ValueNamespace.SECRETS, ValueNamespace.VARIABLES),
        (ValueNamespace.VARIABLES, ValueNamespace.SECRETS),
    ],
)
def test_secret_namespace_present_values_cannot_claim_public_sensitivity(
    namespace: ValueNamespace,
    source_namespace: ValueNamespace,
) -> None:
    provenance = _provenance(namespace=namespace, source_namespace=source_namespace)
    with pytest.raises(ValueError, match="must be SensitiveValue"):
        Present(value=JsonString(value="secret"), provenance=provenance)


def test_secret_namespace_accepts_a_sealed_sensitive_value() -> None:
    origin = TaintOriginId(value="secret/project/api-token")
    value = SensitiveValue(JsonString(value="secret"), origins=(origin,))
    present = Present(
        value=value,
        provenance=_provenance(
            namespace=ValueNamespace.SECRETS,
            source_namespace=ValueNamespace.SECRETS,
        ),
    )

    assert present.sensitivity is Sensitivity.SENSITIVE
    assert present.taint_origins == (origin,)


def test_resolution_outcomes_validate_direct_construction() -> None:
    with pytest.raises(TypeError, match="UnavailableReason"):
        Unavailable(
            reason="not-found",  # type: ignore[arg-type]
            provenance=_provenance(ResolutionStepOutcome.UNAVAILABLE),
        )
    with pytest.raises(TypeError, match="provenance"):
        Present(value=JsonNull(), provenance=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="provenance"):
        Unavailable(
            reason=UnavailableReason.NOT_FOUND,
            provenance=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="provenance"):
        Missing(provenance=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResolutionErrorCode"):
        Error(
            code="provider-failed",  # type: ignore[arg-type]
            provenance=_provenance(ResolutionStepOutcome.ERROR),
        )
    with pytest.raises(TypeError, match="provenance"):
        Error(
            code=ResolutionErrorCode.PROVIDER_FAILED,
            provenance=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="ResolutionBlockCode"):
        Blocked(
            code="source-denied",  # type: ignore[arg-type]
            provenance=_provenance(ResolutionStepOutcome.BLOCKED),
        )
    with pytest.raises(TypeError, match="provenance"):
        Blocked(
            code=ResolutionBlockCode.CAPABILITY_DENIED,
            provenance=object(),  # type: ignore[arg-type]
        )


def test_resolution_outcome_must_match_the_terminal_provenance_step() -> None:
    with pytest.raises(ValueError, match="must end with a present resolution step"):
        Present(
            value=JsonNull(),
            provenance=_provenance(ResolutionStepOutcome.UNAVAILABLE),
        )
