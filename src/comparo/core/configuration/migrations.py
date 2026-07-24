"""Explicit migration classification for known configuration lineages."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity

_PROTOTYPE = DiagnosticCode(value="configuration.migration.prototype-unsupported")
_UNKNOWN = DiagnosticCode(value="configuration.migration.source-unsupported")


@final
class MigrationResult(msgspec.Struct, frozen=True, kw_only=True):
    """A conservative migration result that never executes or resolves values."""

    documents: tuple[ParsedDocument, ...] = ()
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable typed documents and diagnostics."""
        if type(self.documents) is not tuple or any(
            type(document) is not ParsedDocument for document in self.documents
        ):
            raise TypeError("migration documents must be an immutable typed tuple")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not ConfigurationDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("migration diagnostics must be an immutable typed tuple")
        if self.documents and self.diagnostics:
            raise ValueError("migration cannot return documents alongside diagnostics")


def _version(document: ParsedDocument) -> str | None:
    if type(document.root) is not MappingNode:
        return None
    entry = next(
        (entry for entry in document.root.entries if entry.name == "apiVersion"),
        None,
    )
    if entry is None or type(entry.value) is not ScalarNode:
        return None
    return entry.value.value if type(entry.value.value) is str else None


def classify_migration(documents: tuple[ParsedDocument, ...]) -> MigrationResult:
    """Return identity for v1alpha1 and explicit refusal for every other lineage."""
    if type(documents) is not tuple or any(
        type(document) is not ParsedDocument for document in documents
    ):
        raise TypeError("migration classification requires ParsedDocument values")
    if not documents:
        return MigrationResult()
    versions = tuple(_version(document) for document in documents)
    if all(version == "comparo/v1alpha1" for version in versions):
        return MigrationResult(documents=documents)
    code = _PROTOTYPE if "comparo/v1" in versions else _UNKNOWN
    return MigrationResult(
        diagnostics=(
            ConfigurationDiagnostic(
                code=code,
                severity=DiagnosticSeverity.ERROR,
                primary=documents[0].root.span,
            ),
        )
    )
