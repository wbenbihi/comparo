"""Artifact-root discovery and initialization edges for the production TUI backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.tui.test_backend import _HANDLE
from tests.tui.test_backend import _controls

from comparo.application import ComparePreparationHandle
from comparo.application import CompareService
from comparo.application import ProductionExecutionSettings
from comparo.application import persisted_production_execution_defaults_v1
from comparo.tui import backend as backend_module
from comparo.tui.backend import ProductionTuiBackend
from comparo.tui.contracts import TuiBackendError
from comparo.tui.contracts import TuiBackendErrorCode
from comparo.tui.contracts import TuiLaunchRequest


def _relative_backend(
    tmp_path: Path,
    *,
    artifact_root: Path = Path("reports"),
) -> ProductionTuiBackend:
    return ProductionTuiBackend(
        TuiLaunchRequest(
            config=tmp_path / "project" / "comparo.yaml",
            artifact_root=artifact_root,
        )
    )


def test_artifact_root_discovery_is_idempotent_and_validates_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        project_root = tmp_path / "project"
        candidate = project_root / "reports"
        candidate.mkdir(parents=True)
        backend = _relative_backend(tmp_path)
        validated: list[tuple[Path, Path]] = []
        monkeypatch.setattr(backend_module, "resolve_project_root", lambda _config: project_root)

        def validate(root: Path, selected: Path) -> Path:
            validated.append((root, selected))
            return candidate

        monkeypatch.setattr(backend_module, "validate_project_artifact_root", validate)
        await backend._discover_artifact_root()
        await backend._discover_artifact_root()

        assert backend._project_root == project_root
        assert backend._artifact_candidate == candidate
        assert backend._artifact_root == candidate
        assert validated == [(project_root, Path("reports"))]
        await backend.close()

    asyncio.run(scenario())


def test_artifact_root_discovery_contains_validation_and_filesystem_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        project_root = tmp_path / "project"
        candidate = project_root / "reports"
        candidate.mkdir(parents=True)
        backend = _relative_backend(tmp_path)
        monkeypatch.setattr(backend_module, "resolve_project_root", lambda _config: project_root)

        def invalid(*_args: object) -> Path:
            raise TuiBackendError(TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE)

        monkeypatch.setattr(backend_module, "validate_project_artifact_root", invalid)
        await backend._discover_artifact_root()
        assert backend._artifact_candidate is None
        assert backend._artifact_root is None
        await backend.close()

        failing = _relative_backend(tmp_path)
        original_resolve = Path.resolve

        def fail_resolve(path: Path, *, strict: bool = False) -> Path:
            if path == candidate:
                raise OSError("private path detail")
            return original_resolve(path, strict=strict)

        monkeypatch.setattr(Path, "resolve", fail_resolve)
        await failing._discover_artifact_root()
        assert failing._artifact_candidate is None
        await failing.close()

    asyncio.run(scenario())


def test_discovery_defensively_rejects_invalid_selected_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _relative_backend(tmp_path)
        backend._launch = cast(
            "TuiLaunchRequest",
            SimpleNamespace(config=tmp_path / "comparo.yaml", artifact_root=Path("..")),
        )
        monkeypatch.setattr(backend_module, "resolve_project_root", lambda _config: tmp_path)
        await backend._discover_artifact_root()
        assert backend._artifact_candidate is None
        await backend.close()

    asyncio.run(scenario())


def test_authorized_artifact_initialization_requires_discovery_and_exact_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = persisted_production_execution_defaults_v1()
        backend = _relative_backend(tmp_path)
        with pytest.raises(TuiBackendError) as missing:
            await backend._initialize_authorized_artifact_root(settings)
        assert missing.value.code is TuiBackendErrorCode.COMPARE_START_FAILED

        project_root = tmp_path / "project"
        candidate = project_root / "reports"
        backend._project_root = project_root
        backend._artifact_candidate = candidate

        monkeypatch.setattr(
            backend_module,
            "initialize_project_artifact_root",
            lambda *_args: project_root / "different",
        )
        with pytest.raises(TuiBackendError) as mismatch:
            await backend._initialize_authorized_artifact_root(settings)
        assert mismatch.value.code is TuiBackendErrorCode.COMPARE_START_FAILED

        monkeypatch.setattr(
            backend_module,
            "initialize_project_artifact_root",
            lambda *_args: candidate,
        )
        await backend._initialize_authorized_artifact_root(settings)
        assert backend._artifact_root == candidate
        await backend._initialize_authorized_artifact_root(settings)
        await backend.close()

    asyncio.run(scenario())


def test_start_discards_handle_when_artifact_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _relative_backend(tmp_path)
        discarded: list[ComparePreparationHandle] = []

        monkeypatch.setattr(CompareService, "validate_start", lambda *_args: None)

        async def fail_initialize(
            _backend: ProductionTuiBackend,
            _settings: ProductionExecutionSettings,
        ) -> None:
            raise TuiBackendError(TuiBackendErrorCode.COMPARE_START_FAILED)

        def discard(
            _service: CompareService,
            handle: ComparePreparationHandle,
        ) -> bool:
            discarded.append(handle)
            raise RuntimeError("discard detail is suppressed")

        monkeypatch.setattr(
            ProductionTuiBackend,
            "_initialize_authorized_artifact_root",
            fail_initialize,
        )
        monkeypatch.setattr(CompareService, "discard", discard)
        with pytest.raises(TuiBackendError) as caught:
            await backend.start_compare(_HANDLE, _controls())
        assert caught.value.code is TuiBackendErrorCode.COMPARE_START_FAILED
        assert discarded == [_HANDLE]
        await backend.close()

    asyncio.run(scenario())
