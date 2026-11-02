"""Shared exact-origin builders for budget-domain tests."""

from uuid import UUID

from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import DefinitionBudgetOrigin
from comparo.core.domain.budget_origins import InvocationBudgetOrigin
from comparo.core.domain.budget_origins import VersionedDefaultSetRef
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest

_DEFAULT_INVOCATION_ID = UUID("00000000-0000-0000-0000-000000000001")


def _fingerprint(character: str) -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=character * 64),
    )


def default_budget_origin(
    *,
    identifier: str = "comparo.core-defaults",
    version: str = "1",
    fingerprint_character: str = "0",
) -> DefaultBudgetOrigin:
    """Build an exact versioned built-in default-set origin."""
    return DefaultBudgetOrigin(
        default_set=VersionedDefaultSetRef(
            identifier=identifier,
            version=version,
            fingerprint=_fingerprint(fingerprint_character),
        )
    )


def definition_budget_origin(
    kind: ResourceKind,
    *,
    resource_id: str = "budget-source",
    fingerprint_character: str = "b",
) -> DefinitionBudgetOrigin:
    """Build an exact fingerprinted definition origin of the requested kind."""
    return DefinitionBudgetOrigin(
        definition=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="local"),
                kind=kind,
                resource_id=ResourceId(value=resource_id),
            ),
            fingerprint=_fingerprint(fingerprint_character),
        )
    )


def invocation_budget_origin(
    invocation_id: UUID = _DEFAULT_INVOCATION_ID,
) -> InvocationBudgetOrigin:
    """Build an exact invocation occurrence origin."""
    return InvocationBudgetOrigin(invocation_id=InvocationId(value=invocation_id))
