"""Tests for stable and occurrence domain identities."""

from uuid import UUID

import pytest

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleId
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest


@pytest.mark.parametrize("value", ["", "UPPER", "-start", "end-", "two--hyphens", "a" * 64, 1])
def test_slug_identifiers_reject_noncanonical_values(value: object) -> None:
    with pytest.raises(ValueError, match="lowercase alphanumeric slug"):
        RuleId(value=value)  # type: ignore[arg-type]


def test_resource_identity_is_kind_qualified() -> None:
    namespace = NamespaceId(value="local")
    resource_id = ResourceId(value="prod")
    environment = ResourceIdentity(
        namespace=namespace,
        kind=ResourceKind.ENVIRONMENT,
        resource_id=resource_id,
    )
    value = ResourceIdentity(
        namespace=namespace,
        kind=ResourceKind.VALUE,
        resource_id=resource_id,
    )
    assert environment != value


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("namespace", "local"), ("kind", "Environment"), ("resource_id", "prod")],
)
def test_resource_identity_rejects_untyped_components(field: str, replacement: object) -> None:
    values: dict[str, object] = {
        "namespace": NamespaceId(value="local"),
        "kind": ResourceKind.ENVIRONMENT,
        "resource_id": ResourceId(value="prod"),
    }
    values[field] = replacement
    with pytest.raises(TypeError):
        ResourceIdentity(**values)  # type: ignore[arg-type]


def test_definition_reference_validates_components() -> None:
    identity = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.REQUEST,
        resource_id=ResourceId(value="health"),
    )
    fingerprint = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value="a" * 64),
    )
    assert DefinitionRef(identity=identity, fingerprint=fingerprint).identity == identity
    with pytest.raises(TypeError):
        DefinitionRef(identity="Request/health", fingerprint=fingerprint)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        DefinitionRef(identity=identity, fingerprint="a" * 64)  # type: ignore[arg-type]


def test_inline_origin_requires_hash_fragment_and_typed_owner() -> None:
    owner = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.EXECUTION_PROFILE,
        resource_id=ResourceId(value="release"),
    )
    assert InlineDefinitionOrigin(owner=owner, fragment="#safety").fragment == "#safety"
    with pytest.raises(ValueError, match="beginning with '#'"):
        InlineDefinitionOrigin(owner=owner, fragment="safety")
    with pytest.raises(TypeError):
        InlineDefinitionOrigin(owner="release", fragment="#safety")  # type: ignore[arg-type]


def test_occurrence_identifiers_are_type_distinct() -> None:
    value = UUID("12345678-1234-5678-1234-567812345678")
    run: object = RunId(value=value)
    attempt: object = AttemptId(value=value)
    assert run != attempt
    with pytest.raises(TypeError):
        RunId(value=str(value))  # type: ignore[arg-type]
