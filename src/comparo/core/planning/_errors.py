"""Internal value-free control flow for fail-closed planning diagnostics."""

from __future__ import annotations

from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.primitives import Count


class PlanningError(Exception):
    """Carry only structured, value-free information across planner helpers."""

    def __init__(
        self,
        code: str,
        *,
        resource: CompiledResource | None = None,
        field: str | None = None,
        occurrences: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = DiagnosticCode(value=code)
        self.resource = resource
        self.field = field
        self.occurrences = occurrences

    def diagnostic(self) -> Diagnostic:
        """Convert the internal stop into the public safe diagnostic vocabulary."""
        resource = self.resource
        return Diagnostic(
            code=self.code,
            severity=DiagnosticSeverity.ERROR,
            stage=DiagnosticStage.PLANNING,
            context=DiagnosticContext(
                resource=None if resource is None else resource.definition.identity,
                location=None if resource is None else resource.location,
                field_path=() if self.field is None else (FieldPathSegment(name=self.field),),
                occurrences=None if self.occurrences is None else Count(value=self.occurrences),
            ),
        )
