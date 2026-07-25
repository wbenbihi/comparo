"""Bounded explicit-file tests for the ExecutionPolicy YAML adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Never
from typing import cast

import msgspec
import pytest

from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ParsedDocument
from comparo.core.domain.identity import NamespaceId
from comparo.policy import LoadedPolicy
from comparo.policy import PolicyLoadLimits
from comparo.policy import PolicySource
from comparo.policy import compile_policy_path
from comparo.policy import load_policy_path


def _policy_text() -> str:
    return """apiVersion: comparo.policy/v1alpha1
kind: ExecutionPolicy
metadata: {id: ci, name: CI}
spec:
  planning:
    subjects: []
    environments: []
    allowAnySubjects: true
    allowAnyEnvironments: true
  http:
    allowedSchemes: [https]
    allowedMethods: [GET]
    allowedHosts: [api.example.com]
    allowedPorts: [443]
    allowedAddressClasses: [public]
    allowAnyHost: false
    allowAnyPort: false
    requireMutationApproval: false
    allowProxyDns: false
  values:
    ambientEnvironment: {names: [], allowAny: false}
    allowProjectFiles: false
    remoteSchemas: {sources: [], allowAny: false}
  artifacts:
    allowSanitizedPersistence: false
    roots: []
"""


def test_loader_reads_one_explicit_file_and_compiles_namespace(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(_policy_text())
    source = load_policy_path(path)
    assert source.path == path.resolve()
    assert len(source.documents) == 1
    assert source.diagnostics == ()
    loaded = compile_policy_path(path, namespace=NamespaceId(value="ci"))
    assert loaded.compilation.policy is not None
    assert loaded.compilation.policy.definition.identity.namespace == NamespaceId(value="ci")


@pytest.mark.parametrize(
    ("name", "payload", "code"),
    [
        ("invalid.yaml", "apiVersion: [", "configuration.yaml.syntax"),
        ("binary.yaml", b"\xff", "policy.source.read-failed"),
        ("multiple.yaml", "---\nplain\n---\nplain\n", "configuration.yaml.limit-exceeded"),
    ],
)
def test_loader_returns_safe_diagnostics_for_invalid_sources(
    tmp_path: Path,
    name: str,
    payload: str | bytes,
    code: str,
) -> None:
    path = tmp_path / name
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload)
    source = load_policy_path(path)
    assert source.documents == ()
    assert code in {item.code.value for item in source.diagnostics}


def test_loader_rejects_missing_directory_and_oversized_sources(tmp_path: Path) -> None:
    assert load_policy_path(tmp_path / "missing.yaml").diagnostics[0].code.value == (
        "policy.source.missing"
    )
    assert load_policy_path(tmp_path).diagnostics[0].code.value == "policy.source.missing"
    path = tmp_path / "large.yaml"
    path.write_text("a" * 9)
    limits = PolicyLoadLimits(
        yaml=YamlParseLimits(
            maximum_bytes=8,
            maximum_documents=1,
            maximum_depth=8,
            maximum_nodes=8,
        )
    )
    assert load_policy_path(path, limits=limits).diagnostics[0].code.value == (
        "policy.source.limit-exceeded"
    )


def test_loader_boundaries_require_typed_paths_limits_and_namespace(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(_policy_text())
    with pytest.raises(TypeError, match=r"pathlib\.Path"):
        load_policy_path(cast("Path", "policy.yaml"))
    with pytest.raises(TypeError, match="PolicyLoadLimits"):
        load_policy_path(path, limits=cast("PolicyLoadLimits", object()))
    with pytest.raises(TypeError, match="NamespaceId"):
        compile_policy_path(path, namespace=cast("NamespaceId", object()))
    with pytest.raises(TypeError, match="YamlParseLimits"):
        PolicyLoadLimits(yaml=cast("YamlParseLimits", object()))


def test_adapter_products_reject_relative_or_untyped_components(tmp_path: Path) -> None:
    loaded = compile_policy_path(tmp_path / "missing.yaml")
    with pytest.raises(TypeError, match="source path"):
        PolicySource(path=Path("relative"))
    with pytest.raises(TypeError, match="source documents"):
        PolicySource(
            path=tmp_path,
            documents=cast("tuple[ParsedDocument, ...]", [object()]),
        )
    with pytest.raises(TypeError, match="source diagnostics"):
        PolicySource(
            path=tmp_path,
            diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [object()]),
        )
    with pytest.raises(TypeError, match="PolicyCompilationResult"):
        msgspec.structs.replace(loaded, compilation=object())
    with pytest.raises(TypeError, match="loaded policy path"):
        LoadedPolicy(compilation=loaded.compilation, path=Path("relative"))


def test_loader_normalizes_resolution_and_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(_policy_text())
    original_resolve = Path.resolve

    def fail_resolution(candidate: Path, *_args: object, **_kwargs: object) -> Path:
        if candidate == path:
            raise OSError
        return original_resolve(candidate)

    monkeypatch.setattr(Path, "resolve", fail_resolution)
    assert load_policy_path(path).diagnostics[0].code.value == "policy.source.read-failed"
    monkeypatch.setattr(Path, "resolve", original_resolve)

    def fail_read(_candidate: Path, *_args: object, **_kwargs: object) -> Never:
        raise OSError

    monkeypatch.setattr(Path, "open", fail_read)
    assert load_policy_path(path).diagnostics[0].code.value == "policy.source.read-failed"
