"""Tests for bounded descriptor-pinned local artifact enumeration."""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn
from typing import cast

import pytest

from comparo.adapters.artifacts import local as local_module
from comparo.adapters.artifacts.local import LocalArtifactStoreError
from comparo.adapters.artifacts.local import list_local_artifact_bundle_ids
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from tests.adapters.artifacts.test_local_store import _secure_root


def test_listing_is_sorted_bounded_and_returns_only_safe_identifiers(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    for name in ("report-z", "report-a", ".staging-private", "not a bundle"):
        (root / name).mkdir(mode=0o700)

    assert list_local_artifact_bundle_ids(root, maximum_entries=4) == (
        "report-a",
        "report-z",
    )

    with pytest.raises(LocalArtifactStoreError) as exceeded:
        list_local_artifact_bundle_ids(root, maximum_entries=3)
    assert exceeded.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED


@pytest.mark.parametrize("maximum", [0, -1, True, cast("int", object())])
def test_listing_rejects_invalid_budgets(tmp_path: Path, maximum: int) -> None:
    root = _secure_root(tmp_path / "archive")
    with pytest.raises(LocalArtifactStoreError) as failure:
        list_local_artifact_bundle_ids(root, maximum_entries=maximum)
    assert failure.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED


def test_listing_rejects_untrusted_roots_without_path_provenance(tmp_path: Path) -> None:
    missing = tmp_path / "private-project-name" / "archive"
    for root in (Path("relative"), cast("Path", object()), missing):
        with pytest.raises(LocalArtifactStoreError) as failure:
            list_local_artifact_bundle_ids(root, maximum_entries=1)
        assert failure.value.code in {
            ArtifactStoreErrorCode.ROOT_UNSAFE,
            ArtifactStoreErrorCode.IO_FAILED,
        }
        rendered = "".join(traceback.format_exception(failure.value))
        assert "private-project-name" not in rendered
        assert failure.value.__cause__ is None
        assert failure.value.__context__ is None


def test_listing_keeps_using_the_pinned_root_after_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "archive")
    (root / "original").mkdir(mode=0o700)
    moved = tmp_path / "moved"
    validate = local_module._validate_root_fd

    def replace_after_validation(descriptor: int) -> None:
        validate(descriptor)
        root.rename(moved)
        _secure_root(root)
        (root / "replacement").mkdir(mode=0o700)

    monkeypatch.setattr(local_module, "_validate_root_fd", replace_after_validation)

    assert list_local_artifact_bundle_ids(root, maximum_entries=1) == ("original",)


def test_listing_maps_scandir_failures_and_ignores_non_text_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "archive")

    def fail_scan(_descriptor: int) -> NoReturn:
        raise OSError("private-scandir-detail")

    monkeypatch.setattr(os, "scandir", fail_scan)
    with pytest.raises(LocalArtifactStoreError) as failure:
        list_local_artifact_bundle_ids(root, maximum_entries=1)
    assert failure.value.code is ArtifactStoreErrorCode.IO_FAILED
    assert "private-scandir-detail" not in "".join(traceback.format_exception(failure.value))
    monkeypatch.undo()

    class _Entries:
        def __enter__(self) -> _Entries:
            return self

        def __exit__(self, *_error: object) -> None:
            return None

        def __iter__(self) -> object:
            return iter((SimpleNamespace(name=b"bytes-name"),))

    monkeypatch.setattr(os, "scandir", lambda _descriptor: _Entries())
    assert list_local_artifact_bundle_ids(root, maximum_entries=1) == ()
