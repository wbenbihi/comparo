"""Exact identity-to-fingerprint consistency for compiled definition references."""

from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.primitives import Fingerprint


def require_consistent_definitions(definitions: tuple[DefinitionRef, ...]) -> None:
    """Reject one resource identity resolving to multiple effective definitions."""
    fingerprints: dict[ResourceIdentity, Fingerprint] = {}
    for definition in definitions:
        if (known := fingerprints.get(definition.identity)) is not None and (
            known != definition.fingerprint
        ):
            raise ValueError("resource identity maps to multiple plan fingerprints")
        fingerprints[definition.identity] = definition.fingerprint
