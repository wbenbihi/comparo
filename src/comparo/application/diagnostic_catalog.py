"""Safe human guidance for closed diagnostics without interpolating live values."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application.inspection import ApplicationDiagnostic


@final
class DiagnosticPresentation(msgspec.Struct, frozen=True, kw_only=True):
    """Static localized-ready copy selected only from trusted catalog entries."""

    title: str
    summary: str
    action: str
    documentation_key: str

    def __post_init__(self) -> None:
        """Require complete bounded static presentation fields."""
        if any(
            type(value) is not str or not value or len(value) > 512
            for value in (self.title, self.summary, self.action, self.documentation_key)
        ):
            raise ValueError("diagnostic presentation fields must be bounded non-empty text")


_CATALOG: tuple[tuple[str, DiagnosticPresentation], ...] = (
    (
        "schema.remote.",
        DiagnosticPresentation(
            title="Remote schema unavailable",
            summary="Comparo could not authorize or load a referenced remote schema.",
            action="Review the SafetyProfile remote-schema authority and the schema source.",
            documentation_key="schemas.remote",
        ),
    ),
    (
        "schema.",
        DiagnosticPresentation(
            title="Schema validation could not complete",
            summary="A schema definition or response did not satisfy the configured contract.",
            action="Inspect the schema binding and the bounded validation locations.",
            documentation_key="schemas.validation",
        ),
    ),
    (
        "planning.",
        DiagnosticPresentation(
            title="Execution plan rejected",
            summary="The requested topology or overrides could not produce an admitted plan.",
            action="Review the selected resources, topology, matrices, and effective limits.",
            documentation_key="planning.admission",
        ),
    ),
    (
        "resolution.",
        DiagnosticPresentation(
            title="A value could not be resolved",
            summary="Comparo could not resolve one declared value through the authorized sources.",
            action="Inspect the field provenance and its explicit value directive.",
            documentation_key="values.resolution",
        ),
    ),
    (
        "policy.",
        DiagnosticPresentation(
            title="Execution policy rejected the operation",
            summary="The requested capability is outside the effective operator authority.",
            action="Review the effective ExecutionPolicy instead of bypassing the check.",
            documentation_key="policy.authority",
        ),
    ),
    (
        "artifact.",
        DiagnosticPresentation(
            title="Safe artifact creation was incomplete",
            summary="Comparo could not create or persist a complete sanitized run artifact.",
            action="Review capture coverage, storage authority, and artifact limits.",
            documentation_key="artifacts.capture",
        ),
    ),
    (
        "execution.",
        DiagnosticPresentation(
            title="Execution did not complete",
            summary="One or more admitted operations ended before a complete result was produced.",
            action=(
                "Inspect the safe failure stage and occurrence outcome, then retry if appropriate."
            ),
            documentation_key="execution.failures",
        ),
    ),
    (
        "render.",
        DiagnosticPresentation(
            title="Request preview unavailable",
            summary="Comparo could not produce a sanitized offline request preview.",
            action="Resolve the reported configuration or value-source issue before previewing.",
            documentation_key="requests.preview",
        ),
    ),
)

_FALLBACK = DiagnosticPresentation(
    title="Comparo could not complete this operation",
    summary="A structured diagnostic is available, but no specific guidance is cataloged yet.",
    action="Inspect its stage, resource coordinate, and field path for the next safe action.",
    documentation_key="diagnostics.general",
)


def present_diagnostic(diagnostic: ApplicationDiagnostic) -> DiagnosticPresentation:
    """Select static guidance without copying diagnostic codes or source values into prose."""
    if type(diagnostic) is not ApplicationDiagnostic:
        raise TypeError("diagnostic presentation requires ApplicationDiagnostic")
    for prefix, presentation in _CATALOG:
        if diagnostic.code.startswith(prefix):
            return presentation
    return _FALLBACK


__all__ = ("DiagnosticPresentation", "present_diagnostic")
