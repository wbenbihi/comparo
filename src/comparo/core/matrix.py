"""Expand a request across its matrices into concrete cells.

A request references zero or more matrices; each is a list of atomic cases. The
cartesian product of those lists gives one cell per combination, and each cell
carries a stable key (``locale=ja-JP``) so a report or diff can name exactly which
combination it refers to.
"""

import dataclasses
import itertools

from comparo.core.loader import LoadedProject
from comparo.core.models import Matrix
from comparo.core.models import Request


@dataclasses.dataclass(frozen=True, slots=True)
class Injection:
    """One matrix case to merge into a request at a target path."""

    target: str
    case: dict[str, object]
    mode: str
    create_path: bool


@dataclasses.dataclass(frozen=True, slots=True)
class MatrixCell:
    """One combination across a request's matrices, with a stable key."""

    key: str
    injections: tuple[Injection, ...]


def case_key(case: dict[str, object]) -> str:
    """Render a matrix case as a stable, sorted ``key=value`` string.

    Args:
        case: The matrix case to render.

    Returns:
        A deterministic identity such as ``currency=USD, locale=en-US``.
    """
    return ", ".join(f"{key}={case[key]}" for key in sorted(case))


def expand(project: LoadedProject, request: Request) -> list[MatrixCell]:
    """Expand *request* into one cell per matrix combination.

    Args:
        project: The loaded project (to resolve matrix references).
        request: The request to expand.

    Returns:
        One :class:`MatrixCell` per combination; a single empty cell when the
        request has no matrices.
    """
    matrices = _matrices(project, request)
    if not matrices:
        return [MatrixCell("", ())]
    cells: list[MatrixCell] = []
    for combination in itertools.product(*(matrix.spec.values for matrix in matrices)):
        injections = tuple(
            Injection(matrix.spec.target, case, matrix.spec.mode, matrix.spec.create_path)
            for matrix, case in zip(matrices, combination, strict=True)
        )
        key = " · ".join(case_key(case) for case in combination)
        cells.append(MatrixCell(key, injections))
    return cells


def _matrices(project: LoadedProject, request: Request) -> list[Matrix]:
    matrices: list[Matrix] = []
    for reference in request.spec.matrix or []:
        identifier = _ref_id(reference)
        matrix = project.objects.get(identifier) if identifier is not None else None
        if isinstance(matrix, Matrix):
            matrices.append(matrix)
    return matrices


def _ref_id(reference: object) -> str | None:
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None
