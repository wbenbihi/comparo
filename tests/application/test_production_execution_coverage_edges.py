"""Remaining production composition contracts and safe failure branches."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import msgspec
import pytest

from comparo.application import production_execution as production
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.profile_binding import bind_execution_profiles
from comparo.application.remote_schemas import RemoteSchemaFailureCode
from comparo.application.remote_schemas import RemoteSchemaLoadError
from comparo.application.schema_validation import RemoteSchemaDocuments
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.planning.models import PreparedRun
from tests.application.test_production_execution import _project
from tests.application.test_production_execution import _request
from tests.core.artifacts.test_sanitization import _limits


def test_settings_reject_request_body_larger_than_transient_store() -> None:
    settings = production.production_execution_defaults_v1()
    oversized = msgspec.structs.replace(
        settings.request_body,
        maximum_body_bytes=ByteCount(value=settings.body_store.maximum_transient_bytes.value + 1),
    )
    with pytest.raises(ValueError, match="request body limit"):
        msgspec.structs.replace(settings, request_body=oversized)


def test_annotation_models_validate_all_optional_and_bounded_fields() -> None:
    vcs = production.ProductionVcsProvenance(
        provider="git",
        repository="org/repo",
        revision="abc123",
    )
    ci = production.ProductionCiProvenance(provider="github", run_id="1")
    assert vcs.branch is None
    assert ci.job is None
    assert ci.url is None
    assert (
        production.ProductionCiProvenance(
            provider="github",
            run_id="1",
            job="test",
            url="https://ci.example.test/runs/1",
        ).url
        is not None
    )

    for url in ("ftp://ci.example.test/run", "https:///run"):
        with pytest.raises(ValueError, match="must not contain"):
            production.ProductionCiProvenance(provider="ci", run_id="1", url=url)

    for value in (cast("object", 1), "", "x" * 64):
        with pytest.raises(ValueError, match="bounded and non-empty"):
            production._annotation_text(value, maximum=63)
    with pytest.raises(ValueError, match="Unicode scalars") as invalid_unicode:
        production._annotation_text("\ud800", maximum=63)
    assert invalid_unicode.value.__cause__ is None
    assert invalid_unicode.value.__context__ is None

    for tags in (
        cast("tuple[str, ...]", ["release"]),
        ("",),
        ("x" * 128,),
        ("duplicate", "duplicate"),
        tuple(f"tag-{index}" for index in range(129)),
    ):
        with pytest.raises(ValueError, match="tags"):
            production.ProductionRunAnnotations(tags=tags)
    with pytest.raises(TypeError, match="VCS provenance"):
        production.ProductionRunAnnotations(
            vcs=cast("production.ProductionVcsProvenance", object())
        )
    with pytest.raises(TypeError, match="CI provenance"):
        production.ProductionRunAnnotations(ci=cast("production.ProductionCiProvenance", object()))


def test_execution_request_rejects_untyped_annotations(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="annotations"):
        msgspec.structs.replace(
            _request(tmp_path),
            annotations=cast("production.ProductionRunAnnotations", object()),
        )


def test_artifact_annotations_cover_absent_and_nonredacted_optional_values() -> None:
    boundary = SanitizationBoundary(_limits())
    boundary.seal()
    try:
        assert production._artifact_annotations(None, boundary) is None
        empty = production._artifact_annotations(production.ProductionRunAnnotations(), boundary)
        assert empty is not None
        assert empty.title is None
        assert empty.vcs is None
        assert empty.ci is None

        full = production._artifact_annotations(
            production.ProductionRunAnnotations(
                title="Release",
                vcs=production.ProductionVcsProvenance(
                    provider="git",
                    repository="org/repo",
                    revision="abc",
                ),
                ci=production.ProductionCiProvenance(
                    provider="github",
                    run_id="1",
                    url="https://ci.example.test/runs/1",
                ),
            ),
            boundary,
        )
        assert full is not None
        assert full.ci is not None
        assert full.ci.url == "https://ci.example.test/runs/1"
    finally:
        boundary.close()


def test_profile_binding_failure_is_returned_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    safe = ApplicationDiagnostic(code="profile.invalid", severity="error", stage="planning")
    monkeypatch.setattr(
        production,
        "bind_execution_profiles",
        lambda *_args, **_kwargs: SimpleNamespace(profiles=None, diagnostics=(object(),)),
    )
    monkeypatch.setattr(production, "inspect_diagnostic", lambda _value: safe)
    resources = production._ExecutionResources()

    result = asyncio.run(production._execute_with_resources(request, resources))

    assert result.diagnostics == (safe,)
    assert asyncio.run(resources.close()) is False


def test_remote_schema_loader_failure_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        binding = bind_execution_profiles(
            request.project.prepared,
            artifact=request.settings.artifact,
            response=request.settings.transport.classification,
        )
        assert binding.profiles is not None
        profiles = binding.profiles
        schema = RemoteSchema(uri="https://schemas.example.test/public.json")
        monkeypatch.setattr(production, "_remote_schema_bindings", lambda _prepared: (schema,))

        class Loader:
            def __init__(self, _limits: object) -> None:
                pass

            async def load(self, *_args: object) -> None:
                raise RemoteSchemaLoadError(RemoteSchemaFailureCode.NETWORK_FAILED)

        monkeypatch.setattr(production, "RemoteSchemaLoader", Loader)
        documents, code = await production._load_remote_schemas(request, profiles)
        assert documents is None
        assert code == "schema.remote.network-failed"

        expected = cast("RemoteSchemaDocuments", object())

        class SuccessfulLoader:
            def __init__(self, _limits: object) -> None:
                pass

            async def load(self, *_args: object) -> RemoteSchemaDocuments:
                return expected

        monkeypatch.setattr(production, "RemoteSchemaLoader", SuccessfulLoader)
        documents, code = await production._load_remote_schemas(request, profiles)
        assert documents is expected
        assert code is None

    asyncio.run(scenario())


def test_remote_schema_collection_skips_non_request_and_missing_resources(
    tmp_path: Path,
) -> None:
    prepared = _project(tmp_path).prepared
    subject = prepared.effective.subjects[0]

    non_request = cast(
        "PreparedRun",
        SimpleNamespace(
            effective=SimpleNamespace(
                subjects=(
                    SimpleNamespace(
                        definition=SimpleNamespace(
                            identity=SimpleNamespace(kind=ResourceKind.VALUE)
                        )
                    ),
                )
            ),
            project=prepared.project,
        ),
    )
    assert production._remote_schema_bindings(non_request) == ()

    absent = cast(
        "PreparedRun",
        SimpleNamespace(
            effective=SimpleNamespace(subjects=(subject,)),
            project=SimpleNamespace(find=lambda *_args: None),
        ),
    )
    assert production._remote_schema_bindings(absent) == ()
