"""Authenticated archived result catalog tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import read_artifact_manifest
from comparo.application.result_catalog import read_archived_result_page
from comparo.application.results import ResultPageKind
from comparo.application.results import ResultPageQuery
from comparo.application.results import project_result_page
from tests.application.test_artifacts import _bundle
from tests.application.test_artifacts import _limits


def test_reads_authenticated_manifest_and_archived_result_pages(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)
    query = ResultPageQuery(kind=ResultPageKind.EXECUTIONS, start=0, maximum_items=1)

    assert read_artifact_manifest(root, "artifact-0", _limits()) == artifact
    archived = read_archived_result_page(root, "artifact-0", _limits(), query)

    assert archived == project_result_page(artifact, query)
    assert len(archived.items) == 1
    assert archived.next_start == 1


def test_archived_result_page_rejects_invalid_inputs_and_missing_bundle(
    tmp_path: Path,
) -> None:
    root, _artifact = _bundle(tmp_path)
    with pytest.raises(TypeError, match="ResultPageQuery"):
        read_archived_result_page(
            root,
            "artifact-0",
            _limits(),
            cast("ResultPageQuery", object()),
        )
    with pytest.raises(ArtifactOperationError) as captured:
        read_artifact_manifest(root, "missing", _limits())
    assert captured.value.code is ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE
