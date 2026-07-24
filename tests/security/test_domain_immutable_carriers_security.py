"""Adversarial regressions for deeply immutable generic carriers and semantic constants."""

from __future__ import annotations

from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

import comparo.core.domain.analysis_budget_validation as analysis_budget_module
import comparo.core.domain.expressions as expressions_module
import comparo.core.domain.http.bodies as bodies_module
import comparo.core.domain.http.retry_observation_validation as retry_observation_module
import comparo.core.domain.http.timing_validation as timing_module
import comparo.core.domain.selectors.selector_semantics as selector_module
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.identity import RunId
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import KeyedSet
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
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
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Present


def _configuration_provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _provenance() -> ResolutionProvenance:
    provider = ValueProviderIdentity(namespace="comparo.core", name="literal", version="1")
    scope = RunScope(run_id=RunId(value=UUID("00000000-0000-0000-0000-000000000001")))
    source = ResolutionSource(
        namespace=ValueNamespace.VARIABLES,
        symbol=SymbolPath(segments=("value",)),
        provider=provider,
        configuration=_configuration_provenance(),
        scope=scope,
    )
    return ResolutionProvenance(
        namespace=source.namespace,
        symbol=source.symbol,
        provider=provider,
        scope=scope,
        steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
    )


def _text(value: str) -> JsonString:
    return JsonString(value=value)


def test_resolution_provenance_has_no_payload_slot_or_mutable_escape_hatch() -> None:
    provenance = _provenance()

    assert tuple(field.name for field in msgspec.structs.fields(ResolutionProvenance)) == (
        "symbol",
        "provider",
        "scope",
        "steps",
        "namespace",
    )
    assert all(
        "value" not in field.name
        for contract in (ResolutionProvenance, ResolutionSource, ResolutionStep)
        for field in msgspec.structs.fields(contract)
    )
    with pytest.raises(AttributeError):
        provenance.steps = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="immutable tuple"):
        msgspec.structs.replace(provenance, steps=list(provenance.steps))
    with pytest.raises(TypeError, match=r"[Uu]nexpected keyword argument"):
        ResolutionProvenance(
            symbol=provenance.symbol,
            provider=provenance.provider,
            scope=provenance.scope,
            steps=provenance.steps,
            namespace=provenance.namespace,
            value="raw-secret",  # type: ignore[call-arg]
        )


def test_present_rejects_mutable_and_open_ended_payload_graphs() -> None:
    for payload in (
        ["mutable"],
        {"nested": ["mutable"]},
        ("tuple", ["nested-mutable"]),
    ):
        with pytest.raises(TypeError, match="closed resolved value"):
            Present(value=cast("ExactValue", payload), provenance=_provenance())

    sealed_wrong_type = SensitiveValue(
        _provenance(),
        origins=(TaintOriginId(value="security-test"),),
    )
    with pytest.raises(TypeError, match="sensitive payload"):
        Present(
            value=cast("SensitiveValue[ExactValue]", sealed_wrong_type),
            provenance=_provenance(),
        )


def test_present_accepts_closed_non_json_runtime_scalars() -> None:
    """Retain exact duration and byte-size outputs without reopening arbitrary payloads."""
    duration = Duration(nanoseconds=123)
    byte_count = ByteCount(value=456)
    assert Present(value=duration, provenance=_provenance()).value is duration
    assert Present(value=byte_count, provenance=_provenance()).value is byte_count


def test_override_carriers_reject_runtime_type_erasure_and_nested_mutability() -> None:
    mutable = cast(Any, {"nested": ["mutable"]})
    with pytest.raises(TypeError, match="scalar override"):
        SetValue[Any](value=mutable)
    with pytest.raises(TypeError, match="replacement items"):
        ReplaceBase[Any](items=(mutable,))
    with pytest.raises(TypeError, match="keyed-set identity"):
        KeyedSet[Any, Any](identity=mutable, item=_text("value"))
    with pytest.raises(TypeError, match="keyed-set item"):
        KeyedSet[Any, Any](identity=_text("id"), item=mutable)
    with pytest.raises(TypeError, match="removed identities"):
        CollectionOverridePlan[Any, Any](base=KeepBase(), remove=(mutable,))
    with pytest.raises(TypeError, match="added items"):
        CollectionOverridePlan[Any, Any](base=KeepBase(), add=(mutable,))


def test_override_carriers_reject_external_frozen_struct_payloads() -> None:
    """Do not let adapter-owned records become serializable generic payload stores."""

    class AdapterPayload(msgspec.Struct, frozen=True, kw_only=True):
        """A structurally frozen but non-domain payload."""

        token: str

    secret = "external-carrier-secret-7e91d7"
    payload = AdapterPayload(token=secret)
    assert secret in repr(payload)
    assert secret.encode() in msgspec.json.encode(payload)
    with pytest.raises(TypeError, match="deeply immutable domain value"):
        SetValue(value=cast(Any, payload))


def test_override_carriers_reject_external_domain_enum_subclasses() -> None:
    """The common enum behavior alone does not make an adapter token domain-owned."""

    class AdapterSecret(DomainStrEnum):
        """An external string enum whose value must not enter an override."""

        TOKEN = "external-enum-secret-55f0"

    assert "external-enum-secret-55f0" in repr(AdapterSecret.TOKEN)
    assert b"external-enum-secret-55f0" in msgspec.json.encode(AdapterSecret.TOKEN)
    with pytest.raises(TypeError, match="deeply immutable domain value"):
        SetValue(value=cast(Any, AdapterSecret.TOKEN))


def test_supported_nested_payloads_cannot_be_mutated_after_construction() -> None:
    nested = JsonObject(
        members=(
            JsonObjectMember(
                name="items",
                value=JsonArray(items=(_text("stable"),)),
            ),
        )
    )
    present = Present(value=nested, provenance=_provenance())
    override = SetValue(value=nested)

    assert present.value is nested
    assert override.value is nested
    with pytest.raises(AttributeError):
        nested.members = ()  # type: ignore[misc]
    array = cast(JsonArray, nested.members[0].value)
    with pytest.raises(AttributeError):
        array.items = ()  # type: ignore[misc]


def test_override_carriers_accept_self_contained_immutable_domain_structs() -> None:
    """Keep overrides generic enough for field-owned policies, not only wire values."""
    policy = TimeoutPolicy(
        connect=Duration(nanoseconds=1),
        tls_handshake=Duration(nanoseconds=2),
        response_headers=Duration(nanoseconds=3),
        idle=Duration(nanoseconds=4),
        attempt_total=Duration(nanoseconds=5),
        execution_total=Duration(nanoseconds=6),
    )
    assert SetValue(value=policy).value is policy


def test_semantic_lookup_tables_are_immutable_after_import() -> None:
    tuple_tables = (
        analysis_budget_module._ANALYSIS_BUDGET_OPERATIONS,
        expressions_module._NAMESPACE_SCOPES,
        retry_observation_module._TRIGGER_CONDITIONS,
        timing_module._LATER_TIMING_FIELDS,
    )
    frozen_sets = (
        bodies_module._JSON_VALUE_TYPES,
        selector_module.BODY_SELECTOR_SOURCES,
        selector_module._JSON_VALUE_SOURCES,
        selector_module._SCALAR_SOURCES,
        selector_module._PLURAL_SOURCES,
    )
    assert all(type(table) is tuple for table in tuple_tables)
    assert all(type(values) is frozenset for values in frozen_sets)
    with pytest.raises(TypeError):
        cast("list[object]", tuple_tables[0])[0] = ()
    with pytest.raises(AttributeError):
        frozen_sets[0].add(_text("mutation"))  # type: ignore[attr-defined]
