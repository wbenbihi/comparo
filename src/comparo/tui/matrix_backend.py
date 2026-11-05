"""Ephemeral, cancellation-safe Matrix browsing through application inspection."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from comparo.application import CompareExecutionControls
from comparo.application import ComparePreparationHandle
from comparo.application import ComparePreparationResult
from comparo.application import ResolvedValueInspectionBatch
from comparo.application import ResolvedValueInspectionBatchResult
from comparo.application import ResolvedValueInspectionTarget
from comparo.application.matrix_inspection import MatrixBrowseSelection
from comparo.application.matrix_inspection import MatrixCaseWindow
from comparo.application.matrix_inspection import inspect_matrix_cases
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.resolved_inspection import MatrixCaseSelection

if TYPE_CHECKING:
    from comparo.tui.backend import TuiBackend


@dataclass(frozen=True)
class MatrixBrowseResult:
    """A bounded page, context-qualified preview, or closed failure code."""

    window: MatrixCaseWindow | None = None
    occurrence: PlanOccurrenceInspection | None = None
    values: ResolvedValueInspectionBatchResult | None = None
    message: str | None = None


async def inspect_matrix(
    backend: TuiBackend, selection: MatrixBrowseSelection
) -> MatrixBrowseResult:
    """Prepare and inspect without starting Requests or retaining resolved values."""
    async with backend._preparation_lock:
        if backend._closed:
            return MatrixBrowseResult(message="Matrix inspection is closed.")
        try:
            prepared, controls = await backend._join_preparation(
                asyncio.create_task(
                    asyncio.to_thread(
                        backend._prepare_resolved_context,
                        selection.environment.resource_id,
                        "Matrix",
                        selection.matrix.resource_id,
                        selection.subject.resource_id,
                    )
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never expose exception payloads
            return MatrixBrowseResult(message="Matrix preparation failed.")
        handle = prepared.handle
        if handle is None:
            codes = ", ".join(item.code for item in prepared.diagnostics)
            return MatrixBrowseResult(message=f"Matrix preparation unavailable: {codes}")
        try:
            return await _inspect_prepared(backend, selection, prepared, handle, controls)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - only closed messages cross the UI boundary
            return MatrixBrowseResult(message="Matrix inspection failed.")
        finally:
            with suppress(Exception):
                backend.compare.discard(handle)


async def _inspect_prepared(
    backend: TuiBackend,
    selection: MatrixBrowseSelection,
    prepared: ComparePreparationResult,
    handle: ComparePreparationHandle,
    controls: CompareExecutionControls,
) -> MatrixBrowseResult:
    """Authenticate the bounded occurrence before accessing masked values."""
    from comparo.tui.backend import RESOLVED_PROJECTION_LIMITS
    from comparo.tui.backend import RESOLVED_PROJECTION_NODES

    if prepared.review is None:
        return MatrixBrowseResult(message="Matrix plan is unavailable.")
    try:
        window = inspect_matrix_cases(prepared.review.plan, selection)
    except ValueError:
        return MatrixBrowseResult(message="Matrix context changed or selection is unavailable.")
    if window.execution_ordinal is None:
        return MatrixBrowseResult(window=window)
    occurrences = backend.compare.list_occurrences(
        handle, controls, start_ordinal=window.execution_ordinal, maximum_occurrences=1
    )
    if not occurrences.occurrences:
        return MatrixBrowseResult(message="Matrix occurrence is unavailable.")
    occurrence = occurrences.occurrences[0]
    if (
        occurrence.subject != selection.subject
        or occurrence.environment != selection.environment
        or not any(
            item.matrix == selection.matrix
            and item.binding == window.binding
            and item.case_id == window.case_id
            for item in occurrence.matrices
        )
    ):
        return MatrixBrowseResult(message="Matrix occurrence does not match the selection.")
    if window.binding is None or window.case_id is None:
        return MatrixBrowseResult(message="Matrix case selection is incomplete.")
    values = await backend.compare.inspect_resolved_values(
        handle,
        controls,
        ResolvedValueInspectionBatch(
            targets=(
                ResolvedValueInspectionTarget(
                    resource=selection.matrix,
                    environment=selection.environment,
                    field_path=(),
                    execution_ordinal=occurrence.ordinal,
                    matrix_case=MatrixCaseSelection(binding=window.binding, case_id=window.case_id),
                    limits=RESOLVED_PROJECTION_LIMITS,
                    start_node=0,
                    maximum_nodes=RESOLVED_PROJECTION_NODES,
                ),
            ),
            maximum_targets=1,
        ),
    )
    return MatrixBrowseResult(window=window, occurrence=occurrence, values=values)
