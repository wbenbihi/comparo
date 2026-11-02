"""Adapt an authenticated resolved batch to the Explorer's masked detail model."""

from comparo.application import ResourceCoordinate
from comparo.tui.backend import ResolvedFieldBatch
from comparo.tui.models.resource import ResolvedMatrixSelection
from comparo.tui.models.resource import ResolvedResourceView
from comparo.tui.models.resource_presenters import resolved_fields


def resolved_view(
    result: ResolvedFieldBatch, environment: ResourceCoordinate
) -> ResolvedResourceView:
    """Build a complete view only after the worker verifies its selection context."""
    if result.values is None or result.occurrence is None:
        raise ValueError("Resolved view requires values and an occurrence.")
    projected = resolved_fields(result.values)
    occurrence = result.occurrence
    return ResolvedResourceView(
        environment=environment,
        subject=occurrence.subject,
        execution_ordinal=occurrence.ordinal,
        total_occurrences=result.total_occurrences or 1,
        cell_id=occurrence.cell_id,
        trial_id=occurrence.trial_id,
        fields=projected.fields,
        matrices=tuple(
            ResolvedMatrixSelection(
                binding=item.binding,
                matrix=item.matrix,
                case_id=item.case_id,
            )
            for item in occurrence.matrices
        ),
        request_target=result.request_target,
        matrix_plan=result.matrix_plan,
        diagnostics=projected.diagnostics,
        incomplete=projected.incomplete or result.selection_incomplete,
    )
