"""Bounded explicit-file adapter for operator-owned ExecutionPolicy documents."""

from __future__ import annotations

from pathlib import Path
from typing import final

import msgspec

from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import SourceSpan
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import SourceLocation
from comparo.core.policy.compiler import compile_execution_policy
from comparo.core.policy.models import PolicyCompilationResult

_POLICY_SOURCE = SourceId(value="execution-policy")
_OPERATOR_NAMESPACE = NamespaceId(value="operator")


@final
class PolicyLoadLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite caller-owned limits for one explicitly selected policy source."""

    yaml: YamlParseLimits = YamlParseLimits(
        maximum_bytes=1024 * 1024,
        maximum_documents=1,
        maximum_depth=128,
        maximum_nodes=100_000,
    )

    def __post_init__(self) -> None:
        """Require the exact reusable YAML admission contract."""
        if type(self.yaml) is not YamlParseLimits:
            raise TypeError("policy load limits require YamlParseLimits")


DEFAULT_POLICY_LOAD_LIMITS = PolicyLoadLimits()


@final
class PolicySource(msgspec.Struct, frozen=True, kw_only=True):
    """Parsed policy documents and diagnostics from one explicit canonical file."""

    path: Path
    documents: tuple[ParsedDocument, ...] = ()
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require an absolute file coordinate and immutable syntax products."""
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise TypeError("policy source path must be absolute")
        if type(self.documents) is not tuple or any(
            type(item) is not ParsedDocument for item in self.documents
        ):
            raise TypeError("policy source documents must be immutable and typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ConfigurationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("policy source diagnostics must be immutable and typed")


@final
class LoadedPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Compiled policy outcome paired only with its explicit adapter-local source path."""

    compilation: PolicyCompilationResult
    path: Path

    def __post_init__(self) -> None:
        """Require an exact compilation and absolute canonical path."""
        if type(self.compilation) is not PolicyCompilationResult:
            raise TypeError("loaded policy requires PolicyCompilationResult")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise TypeError("loaded policy path must be absolute")


def load_policy_path(
    path: Path,
    *,
    limits: PolicyLoadLimits = DEFAULT_POLICY_LOAD_LIMITS,
) -> PolicySource:
    """Read and parse exactly one explicitly named policy file without discovery."""
    if not isinstance(path, Path):
        raise TypeError("policy path must be a pathlib.Path")
    if type(limits) is not PolicyLoadLimits:
        raise TypeError("policy limits must be PolicyLoadLimits")
    try:
        canonical = path.resolve()
    except (OSError, RuntimeError):
        canonical = path.absolute()
        return PolicySource(
            path=canonical,
            diagnostics=(_diagnostic("policy.source.read-failed"),),
        )
    if not canonical.is_file():
        return PolicySource(
            path=canonical,
            diagnostics=(_diagnostic("policy.source.missing"),),
        )
    try:
        with canonical.open("rb") as stream:
            payload = stream.read(limits.yaml.maximum_bytes + 1)
    except OSError:
        return PolicySource(
            path=canonical,
            diagnostics=(_diagnostic("policy.source.read-failed"),),
        )
    if len(payload) > limits.yaml.maximum_bytes:
        return PolicySource(
            path=canonical,
            diagnostics=(_diagnostic("policy.source.limit-exceeded"),),
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        return PolicySource(
            path=canonical,
            diagnostics=(_diagnostic("policy.source.read-failed"),),
        )
    parsed = parse_yaml_source(source_id=_POLICY_SOURCE, text=text, limits=limits.yaml)
    return PolicySource(
        path=canonical,
        documents=parsed.documents,
        diagnostics=parsed.diagnostics,
    )


def compile_policy_path(
    path: Path,
    *,
    namespace: NamespaceId = _OPERATOR_NAMESPACE,
    limits: PolicyLoadLimits = DEFAULT_POLICY_LOAD_LIMITS,
) -> LoadedPolicy:
    """Load and compile one explicit policy file without consulting Project discovery."""
    if type(namespace) is not NamespaceId:
        raise TypeError("policy namespace must be a NamespaceId")
    source = load_policy_path(path, limits=limits)
    return LoadedPolicy(
        compilation=compile_execution_policy(
            source.documents,
            namespace=namespace,
            source_id=_POLICY_SOURCE,
            diagnostics=source.diagnostics,
        ),
        path=source.path,
    )


def _diagnostic(code: str) -> ConfigurationDiagnostic:
    location = SourceLocation(
        source_id=_POLICY_SOURCE,
        document=PositiveCount(value=1),
        line=PositiveCount(value=1),
        column=PositiveCount(value=1),
    )
    return ConfigurationDiagnostic(
        code=DiagnosticCode(value=code),
        severity=DiagnosticSeverity.ERROR,
        primary=SourceSpan(start=location, end=location),
    )


__all__ = (
    "DEFAULT_POLICY_LOAD_LIMITS",
    "LoadedPolicy",
    "PolicyLoadLimits",
    "PolicySource",
    "compile_policy_path",
    "load_policy_path",
)
