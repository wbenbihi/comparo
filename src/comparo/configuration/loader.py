"""Deterministic, policy-aware local configuration source discovery."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import final

import msgspec

from comparo.configuration.yaml_parser import DEFAULT_YAML_PARSE_LIMITS
from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.canonical import CompilationResult
from comparo.core.configuration.compiler import compile_v1alpha1
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Resource
from comparo.core.configuration.v1alpha1.frontend import parse_v1alpha1_documents
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import SourceLocation

type ExternalSourceAuthorizer = Callable[[Path], bool]

_ENTRY_MISSING = DiagnosticCode(value="configuration.source.entry-missing")
_SOURCE_MISSING = DiagnosticCode(value="configuration.source.explicit-missing")
_SOURCE_EXTERNAL = DiagnosticCode(value="configuration.source.external-forbidden")
_SOURCE_READ = DiagnosticCode(value="configuration.source.read-failed")
_DISCOVERY_INVALID = DiagnosticCode(value="configuration.source.discovery-invalid")
_SOURCE_LIMIT = DiagnosticCode(value="configuration.source.limit-exceeded")
_LOCAL_NAMESPACE = NamespaceId(value="local")


@final
class ProjectLoadLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite caller-overridable admission limits for project source discovery."""

    maximum_sources: int = 4096
    yaml: YamlParseLimits = DEFAULT_YAML_PARSE_LIMITS

    def __post_init__(self) -> None:
        """Require an exact positive source count and typed YAML policy."""
        if type(self.maximum_sources) is not int or self.maximum_sources < 1:
            raise ValueError("project source limit must be a positive exact integer")
        if type(self.yaml) is not YamlParseLimits:
            raise TypeError("project YAML limits must be YamlParseLimits")


DEFAULT_PROJECT_LOAD_LIMITS = ProjectLoadLimits()


@final
class SourceCatalogEntry(msgspec.Struct, frozen=True, kw_only=True):
    """Adapter-local mapping from safe logical IDs to filesystem paths."""

    source_id: SourceId
    path: Path

    def __post_init__(self) -> None:
        """Require a logical identifier and absolute canonical path."""
        if type(self.source_id) is not SourceId:
            raise TypeError("source catalog ID must be a SourceId")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise TypeError("source catalog path must be an absolute Path")


@final
class ProjectSourceSet(msgspec.Struct, frozen=True, kw_only=True):
    """All parsed documents, diagnostics, and path mappings for one project load."""

    entry_source: SourceId
    documents: tuple[ParsedDocument, ...] = ()
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()
    catalog: tuple[SourceCatalogEntry, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable typed source-load components."""
        if type(self.entry_source) is not SourceId:
            raise TypeError("project source set entry must be a SourceId")
        if type(self.documents) is not tuple or any(
            type(document) is not ParsedDocument for document in self.documents
        ):
            raise TypeError("project source documents must be an immutable typed tuple")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not ConfigurationDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("project source diagnostics must be an immutable typed tuple")
        if type(self.catalog) is not tuple or any(
            type(entry) is not SourceCatalogEntry for entry in self.catalog
        ):
            raise TypeError("project source catalog must be an immutable typed tuple")


@final
class LoadedCompilation(msgspec.Struct, frozen=True, kw_only=True):
    """A compiled result paired with the adapter-local source catalog."""

    compilation: CompilationResult
    catalog: tuple[SourceCatalogEntry, ...]

    def __post_init__(self) -> None:
        """Require exact compilation and catalog values."""
        if type(self.compilation) is not CompilationResult:
            raise TypeError("loaded compilation must contain a CompilationResult")
        if type(self.catalog) is not tuple or any(
            type(entry) is not SourceCatalogEntry for entry in self.catalog
        ):
            raise TypeError("loaded compilation catalog must be an immutable typed tuple")


def _span(source_id: SourceId) -> SourceSpan:
    location = SourceLocation(
        source_id=source_id,
        document=PositiveCount(value=1),
        line=PositiveCount(value=1),
        column=PositiveCount(value=1),
    )
    return SourceSpan(start=location, end=location)


def _issue(code: DiagnosticCode, source_id: SourceId) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        primary=_span(source_id),
    )


def _source_id(path: Path) -> SourceId:
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    return SourceId(value=f"source-{digest}")


def _read(
    path: Path,
    source_id: SourceId,
    limits: YamlParseLimits,
) -> tuple[tuple[ParsedDocument, ...], tuple[ConfigurationDiagnostic, ...]]:
    try:
        with path.open("rb") as stream:
            content = stream.read(limits.maximum_bytes + 1)
    except (OSError, UnicodeError):
        return (), (_issue(_SOURCE_READ, source_id),)
    if len(content) > limits.maximum_bytes:
        return (), (_issue(_SOURCE_LIMIT, source_id),)
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        return (), (_issue(_SOURCE_READ, source_id),)
    parsed = parse_yaml_source(source_id=source_id, text=text, limits=limits)
    return parsed.documents, parsed.diagnostics


def _project_from_entry(documents: tuple[ParsedDocument, ...]) -> V1Alpha1Resource | None:
    frontend = parse_v1alpha1_documents(documents)
    return next(
        (resource for resource in frontend.resources if resource.kind is ResourceKind.PROJECT),
        None,
    )


def _project_fields(project: V1Alpha1Resource) -> dict[str, object]:
    if type(project.spec) is not MappingNode:
        return {}
    return {entry.name: entry.value for entry in project.spec.entries}


def _scalar_paths(node: object) -> tuple[str, ...] | None:
    if type(node) is ScalarNode and type(node.value) is str:
        return (node.value,)
    if type(node) is not SequenceNode:
        return None
    values: list[str] = []
    for item in node.items:
        if type(item) is not ScalarNode or type(item.value) is not str:
            return None
        values.append(item.value)
    return tuple(values)


def _discovery_config(
    project: V1Alpha1Resource,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None:
    fields = _project_fields(project)
    data = fields.get("data")
    sources = _scalar_paths(fields.get("sources")) if "sources" in fields else ()
    if sources is None:
        return None
    if data is None:
        return ".comparo", ("**/*.yaml", "**/*.yml"), (), sources
    if type(data) is ScalarNode and type(data.value) is str:
        return data.value, ("**/*.yaml", "**/*.yml"), (), sources
    if type(data) is not MappingNode:
        return None
    parts = {entry.name: entry.value for entry in data.entries}
    if not set(parts) <= {"root", "include", "exclude"}:
        return None
    root = parts.get("root")
    if type(root) is not ScalarNode or type(root.value) is not str:
        return None
    include = (
        _scalar_paths(parts.get("include")) if "include" in parts else ("**/*.yaml", "**/*.yml")
    )
    exclude = _scalar_paths(parts.get("exclude")) if "exclude" in parts else ()
    if include is None or exclude is None:
        return None
    return root.value, include, exclude, sources


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _authorized(
    path: Path,
    root: Path,
    authorizer: ExternalSourceAuthorizer | None,
) -> bool:
    return _inside(path, root) or (authorizer is not None and authorizer(path))


def _directory_files(directory: Path, maximum: int) -> tuple[tuple[Path, ...], bool]:
    files: list[Path] = []
    for current, directories, names in directory.walk(top_down=True, follow_symlinks=False):
        directories[:] = sorted(name for name in directories if not (current / name).is_symlink())
        for name in sorted(names):
            path = current / name
            if path.suffix.lower() not in {".yaml", ".yml"} or path.is_symlink():
                continue
            if len(files) >= maximum:
                return tuple(files), True
            files.append(path)
    return tuple(files), False


def _matches(path: Path, root: Path, includes: tuple[str, ...], excludes: tuple[str, ...]) -> bool:
    relative = path.relative_to(root)
    return any(_path_matches(relative, pattern) for pattern in includes) and not any(
        _path_matches(relative, pattern) for pattern in excludes
    )


def _path_matches(relative: Path, pattern: str) -> bool:
    return relative.match(pattern) or (
        pattern.startswith("**/") and relative.match(pattern.removeprefix("**/"))
    )


def _resolve_entry(config: Path) -> Path:
    return config / "comparo.yaml" if config.is_dir() else config


def _discover_candidates(
    *,
    root: Path,
    entry: Path,
    data_root_name: str,
    includes: tuple[str, ...],
    excludes: tuple[str, ...],
    explicit: tuple[Path, ...],
    authorizer: ExternalSourceAuthorizer | None,
    diagnostics: list[ConfigurationDiagnostic],
    source_id: SourceId,
    maximum: int,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    data_root = (root / data_root_name).resolve()
    if _authorized(data_root, root, authorizer) and data_root.is_dir():
        discovered, exceeded = _directory_files(data_root, maximum)
        candidates.extend(
            path for path in discovered if _matches(path, data_root, includes, excludes)
        )
        if exceeded:
            diagnostics.append(_issue(_SOURCE_LIMIT, source_id))
    elif not _authorized(data_root, root, authorizer):
        diagnostics.append(_issue(_SOURCE_EXTERNAL, source_id))
    for source in explicit:
        if len(candidates) >= maximum:
            diagnostics.append(_issue(_SOURCE_LIMIT, source_id))
            break
        canonical = source.resolve()
        if not _authorized(canonical, root, authorizer):
            diagnostics.append(_issue(_SOURCE_EXTERNAL, source_id))
        elif canonical.is_file():
            candidates.append(canonical)
        elif canonical.is_dir():
            discovered, exceeded = _directory_files(canonical, maximum - len(candidates))
            candidates.extend(discovered)
            if exceeded:
                diagnostics.append(_issue(_SOURCE_LIMIT, source_id))
                break
        else:
            diagnostics.append(_issue(_SOURCE_MISSING, source_id))
    return tuple(sorted(set(candidates) - {entry}, key=str))


def load_project_sources(
    config: Path,
    *,
    additional_sources: tuple[Path, ...] = (),
    authorize_external: ExternalSourceAuthorizer | None = None,
    limits: ProjectLoadLimits = DEFAULT_PROJECT_LOAD_LIMITS,
) -> ProjectSourceSet:
    """Load the entry manifest and its deterministic local source graph."""
    if not isinstance(config, Path):
        raise TypeError("project config must be a Path")
    if type(additional_sources) is not tuple or any(
        not isinstance(path, Path) for path in additional_sources
    ):
        raise TypeError("additional sources must be an immutable tuple of Paths")
    if type(limits) is not ProjectLoadLimits:
        raise TypeError("project load limits must be ProjectLoadLimits")
    entry_id = SourceId(value="entry-config")
    entry = _resolve_entry(config).resolve()
    if not entry.is_file():
        return ProjectSourceSet(
            entry_source=entry_id,
            diagnostics=(_issue(_ENTRY_MISSING, entry_id),),
        )
    root = entry.parent
    entry_documents, entry_diagnostics = _read(entry, entry_id, limits.yaml)
    project = _project_from_entry(entry_documents)
    if project is None:
        return ProjectSourceSet(
            entry_source=entry_id,
            documents=entry_documents,
            diagnostics=entry_diagnostics,
            catalog=(SourceCatalogEntry(source_id=entry_id, path=entry),),
        )
    discovery = _discovery_config(project)
    if discovery is None:
        return ProjectSourceSet(
            entry_source=entry_id,
            documents=entry_documents,
            diagnostics=(*entry_diagnostics, _issue(_DISCOVERY_INVALID, entry_id)),
            catalog=(SourceCatalogEntry(source_id=entry_id, path=entry),),
        )
    data_root_name, includes, excludes, declared_sources = discovery
    diagnostics = list(entry_diagnostics)
    explicit = tuple(root / name for name in declared_sources) + additional_sources
    ordered = _discover_candidates(
        root=root,
        entry=entry,
        data_root_name=data_root_name,
        includes=includes,
        excludes=excludes,
        explicit=explicit,
        authorizer=authorize_external,
        diagnostics=diagnostics,
        source_id=entry_id,
        maximum=limits.maximum_sources - 1,
    )
    documents = list(entry_documents)
    catalog = [SourceCatalogEntry(source_id=entry_id, path=entry)]
    for path in ordered:
        source_id = _source_id(path)
        source_documents, source_diagnostics = _read(path, source_id, limits.yaml)
        documents.extend(source_documents)
        diagnostics.extend(source_diagnostics)
        catalog.append(SourceCatalogEntry(source_id=source_id, path=path))
    return ProjectSourceSet(
        entry_source=entry_id,
        documents=tuple(documents),
        diagnostics=tuple(diagnostics),
        catalog=tuple(catalog),
    )


def compile_project_path(
    config: Path,
    *,
    namespace: NamespaceId = _LOCAL_NAMESPACE,
    additional_sources: tuple[Path, ...] = (),
    authorize_external: ExternalSourceAuthorizer | None = None,
    limits: ProjectLoadLimits = DEFAULT_PROJECT_LOAD_LIMITS,
) -> LoadedCompilation:
    """Discover, parse, version, and compile one project without executing it."""
    sources = load_project_sources(
        config,
        additional_sources=additional_sources,
        authorize_external=authorize_external,
        limits=limits,
    )
    frontend = parse_v1alpha1_documents(sources.documents)
    combined = type(frontend)(
        resources=frontend.resources,
        diagnostics=(*sources.diagnostics, *frontend.diagnostics),
    )
    return LoadedCompilation(
        compilation=compile_v1alpha1(
            combined,
            namespace=namespace,
            entry_source=sources.entry_source,
        ),
        catalog=sources.catalog,
    )
