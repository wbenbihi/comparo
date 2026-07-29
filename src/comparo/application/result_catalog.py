"""Authenticated page-oriented access to completed local run results."""

from __future__ import annotations

from pathlib import Path

from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import read_artifact_manifest
from comparo.application.results import ResultPage
from comparo.application.results import ResultPageQuery
from comparo.application.results import project_result_page


def read_archived_result_page(
    root: Path,
    bundle_id: str,
    limits: ArtifactReadLimits,
    query: ResultPageQuery,
) -> ResultPage:
    """Authenticate one archived manifest and project only the requested collection page."""
    if type(query) is not ResultPageQuery:
        raise TypeError("archived result paging requires ResultPageQuery")
    return project_result_page(
        read_artifact_manifest(root, bundle_id, limits),
        query,
    )


__all__ = ("read_archived_result_page",)
