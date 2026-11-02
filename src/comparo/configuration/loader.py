"""Deterministic, policy-aware local configuration source discovery."""

from __future__ import annotations

import hashlib
import os
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
_DISCOVERY_LIMIT = DiagnosticCode(value="configuration.source.discovery-limit-exceeded")
_SOURCE_LIMIT = DiagnosticCode(value="configuration.source.limit-exceeded")
_SOURCE_AGGREGATE_LIMIT = DiagnosticCode(value="configuration.source.aggregate-limit-exceeded")
_LOCAL_NAMESPACE = NamespaceId(value="local")


@final
class ProjectLoadLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite caller-overridable admission limits for project source discovery."""

    maximum_sources: int = 4096
    maximum_discovery_entries: int = 100_000
    maximum_total_bytes: int = 64 * 1024 * 1024
    maximum_total_documents: int = 4096
    maximum_total_nodes: int = 4_000_000
    maximum_resources: int = 4096
    yaml: YamlParseLimits = DEFAULT_YAML_PARSE_LIMITS

    def __post_init__(self) -> None:
        """Require an exact positive source count and typed YAML policy."""
        aggregate = (
            self.maximum_sources,
            self.maximum_discovery_entries,
            self.maximum_total_bytes,
            self.maximum_total_documents,
            self.maximum_total_nodes,
            self.maximum_resources,
        )
        if any(type(value) is not int or value < 1 for value in aggregate):
            raise ValueError("project aggregate limits must be positive exact integers")
        if type(self.yaml) is not YamlParseLimits:
            raise TypeError("project YAML limits must be YamlParseLimits")


DEFAULT_PROJECT_LOAD_LIMITS = ProjectLoadLimits()


@final
class _DiscoveryBudget:
    """Caller-bounded count of filesystem entries examined during recursive discovery."""

    __slots__ = ("examined", "maximum")

    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.examined = 0

    def admit(self) -> bool:
        """Charge one directory entry, returning false before exceeding the ceiling."""
        if self.examined >= self.maximum:
            return False
        self.examined += 1
        return True


@final
class _DirectoryDiscovery(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded directory traversal result with closed failure categories."""

    files: tuple[Path, ...] = ()
    source_exceeded: bool = False
    work_exceeded: bool = False
    invalid: bool = False


@final
class _ScannedDirectory(msgspec.Struct, frozen=True, kw_only=True):
    """One caller-bounded immediate directory scan before deterministic sorting."""

    entries: tuple[tuple[str, Path, bool, bool], ...] = ()
    work_exceeded: bool = False
    invalid: bool = False


def _scan_directory(current: Path, budget: _DiscoveryBudget) -> _ScannedDirectory:
    examined: list[tuple[str, Path, bool, bool]] = []
    try:
        with os.scandir(current) as entries:
            for entry in entries:
                if not budget.admit():
                    return _ScannedDirectory(work_exceeded=True)
                is_symlink = entry.is_symlink()
                examined.append(
                    (
                        entry.name,
                        Path(entry.path),
                        not is_symlink and entry.is_dir(follow_symlinks=False),
                        not is_symlink and entry.is_file(follow_symlinks=False),
                    )
                )
    except OSError:
        return _ScannedDirectory(invalid=True)
    return _ScannedDirectory(entries=tuple(sorted(examined, key=lambda item: item[0])))


@final
class SourceCatalogEntry(msgspec.Struct, frozen=True, kw_only=True):
    """Adapter-local mapping from safe logical IDs to filesystem paths."""

    source_id: SourceId
    path: Path
    sha256: str | None = None
    byte_count: int | None = None

    def __post_init__(self) -> None:
        """Require a logical identifier and absolute canonical path."""
        if type(self.source_id) is not SourceId:
            raise TypeError("source catalog ID must be a SourceId")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise TypeError("source catalog path must be an absolute Path")
        if self.sha256 is not None and (
            len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("source catalog digest must be lowercase SHA-256 or absent")
        if self.byte_count is not None and (
            type(self.byte_count) is not int or self.byte_count < 0
        ):
            raise ValueError("source catalog byte count must be non-negative or absent")
        if (self.sha256 is None) != (self.byte_count is None):
            raise ValueError("source catalog revision metadata must be complete or absent")


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
    documents: tuple[ParsedDocument, ...] = ()

    def __post_init__(self) -> None:
        """Require exact compilation and catalog values."""
        if type(self.compilation) is not CompilationResult:
            raise TypeError("loaded compilation must contain a CompilationResult")
        if type(self.catalog) is not tuple or any(
            type(entry) is not SourceCatalogEntry for entry in self.catalog
        ):
            raise TypeError("loaded compilation catalog must be an immutable typed tuple")
        if type(self.documents) is not tuple or any(
            type(document) is not ParsedDocument for document in self.documents
        ):
            raise TypeError("loaded compilation documents must be an immutable typed tuple")


@final
class _SourceLoad(msgspec.Struct, frozen=True, kw_only=True):
    """One parsed source plus aggregate work retained by the project loader."""

    documents: tuple[ParsedDocument, ...]
    diagnostics: tuple[ConfigurationDiagnostic, ...]
    byte_count: int
    node_count: int
    resource_count: int
    sha256: str | None = None


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
    *,
    remaining_bytes: int,
    remaining_documents: int,
    remaining_nodes: int,
    remaining_resources: int,
) -> _SourceLoad:
    if (
        min(
            remaining_bytes,
            remaining_documents,
            remaining_nodes,
            remaining_resources,
        )
        < 1
    ):
        return _SourceLoad(
            documents=(),
            diagnostics=(_issue(_SOURCE_AGGREGATE_LIMIT, source_id),),
            byte_count=0,
            node_count=0,
            resource_count=0,
        )
    effective = YamlParseLimits(
        maximum_bytes=min(limits.maximum_bytes, remaining_bytes),
        maximum_documents=min(
            limits.maximum_documents,
            remaining_documents,
            remaining_resources,
        ),
        maximum_depth=limits.maximum_depth,
        maximum_nodes=min(limits.maximum_nodes, remaining_nodes),
    )
    try:
        with path.open("rb") as stream:
            content = stream.read(effective.maximum_bytes + 1)
    except (OSError, UnicodeError):
        return _SourceLoad(
            documents=(),
            diagnostics=(_issue(_SOURCE_READ, source_id),),
            byte_count=0,
            node_count=0,
            resource_count=0,
        )
    if len(content) > effective.maximum_bytes:
        code = (
            _SOURCE_AGGREGATE_LIMIT
            if effective.maximum_bytes < limits.maximum_bytes
            else _SOURCE_LIMIT
        )
        return _SourceLoad(
            documents=(),
            diagnostics=(_issue(code, source_id),),
            byte_count=0,
            node_count=0,
            resource_count=0,
        )
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        return _SourceLoad(
            documents=(),
            diagnostics=(_issue(_SOURCE_READ, source_id),),
            byte_count=len(content),
            node_count=0,
            resource_count=0,
        )
    parsed = parse_yaml_source(source_id=source_id, text=text, limits=effective)
    constrained_parse = (
        effective.maximum_documents < limits.maximum_documents
        or effective.maximum_nodes < limits.maximum_nodes
    )
    if constrained_parse and any(
        item.code.value == "configuration.yaml.limit-exceeded" for item in parsed.diagnostics
    ):
        return _SourceLoad(
            documents=(),
            diagnostics=(_issue(_SOURCE_AGGREGATE_LIMIT, source_id),),
            byte_count=len(content),
            node_count=0,
            resource_count=0,
        )
    node_count = _document_node_count(parsed.documents)
    resource_count = len(parsed.documents)
    if (
        len(parsed.documents) > remaining_documents
        or node_count > remaining_nodes
        or resource_count > remaining_resources
    ):
        return _SourceLoad(
            documents=(),
            diagnostics=(_issue(_SOURCE_AGGREGATE_LIMIT, source_id),),
            byte_count=len(content),
            node_count=0,
            resource_count=0,
        )
    return _SourceLoad(
        documents=parsed.documents,
        diagnostics=parsed.diagnostics,
        byte_count=len(content),
        node_count=node_count,
        resource_count=resource_count,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _document_node_count(documents: tuple[ParsedDocument, ...]) -> int:
    """Count retained configuration nodes iteratively without recursion."""
    count = 0
    pending = [document.root for document in documents]
    while pending:
        node = pending.pop()
        count += 1
        if type(node) is MappingNode:
            count += len(node.entries)
            pending.extend(entry.value for entry in node.entries)
        elif type(node) is SequenceNode:
            pending.extend(node.items)
    return count


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


def _directory_files(
    directory: Path,
    maximum: int,
    budget: _DiscoveryBudget,
    seen: set[Path],
    *,
    root: Path | None = None,
    includes: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
) -> _DirectoryDiscovery:
    """Discover matching YAML files with bounded traversal and bounded retention."""
    files: list[Path] = []
    pending = [directory]
    while pending:
        current = pending.pop()
        scanned = _scan_directory(current, budget)
        if scanned.work_exceeded:
            return _DirectoryDiscovery(work_exceeded=True)
        if scanned.invalid:
            return _DirectoryDiscovery(invalid=True)
        directories: list[Path] = []
        for _name, path, is_directory, is_file in scanned.entries:
            if is_directory:
                directories.append(path)
                continue
            if not is_file or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            if root is not None and not _matches(path, root, includes, excludes):
                continue
            if path in seen:
                continue
            if len(files) >= maximum:
                return _DirectoryDiscovery(files=tuple(files), source_exceeded=True)
            seen.add(path)
            files.append(path)
        pending.extend(reversed(directories))
    return _DirectoryDiscovery(files=tuple(files))


def _admit_directory_discovery(
    result: _DirectoryDiscovery,
    candidates: list[Path],
    diagnostics: list[ConfigurationDiagnostic],
    source_id: SourceId,
) -> bool:
    """Admit one traversal result, discarding every partial path on hard failure."""
    if result.work_exceeded:
        diagnostics.append(_issue(_DISCOVERY_LIMIT, source_id))
        return False
    if result.invalid:
        diagnostics.append(_issue(_DISCOVERY_INVALID, source_id))
        return False
    candidates.extend(result.files)
    if result.source_exceeded:
        diagnostics.append(_issue(_SOURCE_LIMIT, source_id))
    return True


def _admit_explicit_source(
    source: Path,
    *,
    root: Path,
    authorizer: ExternalSourceAuthorizer | None,
    candidates: list[Path],
    seen: set[Path],
    diagnostics: list[ConfigurationDiagnostic],
    source_id: SourceId,
    maximum: int,
    budget: _DiscoveryBudget,
) -> bool | None:
    """Admit one explicit file/directory; false stops capacity work, None fails closed."""
    canonical = source.resolve()
    if not _authorized(canonical, root, authorizer):
        diagnostics.append(_issue(_SOURCE_EXTERNAL, source_id))
        return True
    if canonical.is_file():
        if canonical in seen:
            return True
        if len(candidates) >= maximum:
            diagnostics.append(_issue(_SOURCE_LIMIT, source_id))
            return False
        seen.add(canonical)
        candidates.append(canonical)
        return True
    if canonical.is_dir():
        discovered = _directory_files(
            canonical,
            maximum - len(candidates),
            budget,
            seen,
        )
        if not _admit_directory_discovery(
            discovered,
            candidates,
            diagnostics,
            source_id,
        ):
            return None
        return not discovered.source_exceeded
    diagnostics.append(_issue(_SOURCE_MISSING, source_id))
    return True


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
    maximum_discovery_entries: int,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    seen = {entry}
    budget = _DiscoveryBudget(maximum_discovery_entries)
    data_root = (root / data_root_name).resolve()
    if _authorized(data_root, root, authorizer) and data_root.is_dir():
        discovered = _directory_files(
            data_root,
            maximum,
            budget,
            seen,
            root=data_root,
            includes=includes,
            excludes=excludes,
        )
        if not _admit_directory_discovery(
            discovered,
            candidates,
            diagnostics,
            source_id,
        ):
            return ()
    elif not _authorized(data_root, root, authorizer):
        diagnostics.append(_issue(_SOURCE_EXTERNAL, source_id))
    for source in explicit:
        admitted = _admit_explicit_source(
            source,
            root=root,
            authorizer=authorizer,
            candidates=candidates,
            seen=seen,
            diagnostics=diagnostics,
            source_id=source_id,
            maximum=maximum,
            budget=budget,
        )
        if admitted is None:
            return ()
        if not admitted:
            break
    return tuple(sorted(candidates, key=str))


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
    entry_load = _read(
        entry,
        entry_id,
        limits.yaml,
        remaining_bytes=limits.maximum_total_bytes,
        remaining_documents=limits.maximum_total_documents,
        remaining_nodes=limits.maximum_total_nodes,
        remaining_resources=limits.maximum_resources,
    )
    project = _project_from_entry(entry_load.documents)
    if project is None:
        return ProjectSourceSet(
            entry_source=entry_id,
            documents=entry_load.documents,
            diagnostics=entry_load.diagnostics,
            catalog=(
                SourceCatalogEntry(
                    source_id=entry_id,
                    path=entry,
                    sha256=entry_load.sha256,
                    byte_count=entry_load.byte_count if entry_load.sha256 is not None else None,
                ),
            ),
        )
    discovery = _discovery_config(project)
    if discovery is None:
        return ProjectSourceSet(
            entry_source=entry_id,
            documents=entry_load.documents,
            diagnostics=(*entry_load.diagnostics, _issue(_DISCOVERY_INVALID, entry_id)),
            catalog=(
                SourceCatalogEntry(
                    source_id=entry_id,
                    path=entry,
                    sha256=entry_load.sha256,
                    byte_count=entry_load.byte_count if entry_load.sha256 is not None else None,
                ),
            ),
        )
    data_root_name, includes, excludes, declared_sources = discovery
    diagnostics = list(entry_load.diagnostics)
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
        maximum_discovery_entries=limits.maximum_discovery_entries,
    )
    documents = list(entry_load.documents)
    catalog = [
        SourceCatalogEntry(
            source_id=entry_id,
            path=entry,
            sha256=entry_load.sha256,
            byte_count=entry_load.byte_count if entry_load.sha256 is not None else None,
        )
    ]
    used_bytes = entry_load.byte_count
    used_documents = len(entry_load.documents)
    used_nodes = entry_load.node_count
    used_resources = entry_load.resource_count
    for path in ordered:
        source_id = _source_id(path)
        loaded = _read(
            path,
            source_id,
            limits.yaml,
            remaining_bytes=limits.maximum_total_bytes - used_bytes,
            remaining_documents=limits.maximum_total_documents - used_documents,
            remaining_nodes=limits.maximum_total_nodes - used_nodes,
            remaining_resources=limits.maximum_resources - used_resources,
        )
        diagnostics.extend(loaded.diagnostics)
        if any(item.code == _SOURCE_AGGREGATE_LIMIT for item in loaded.diagnostics):
            break
        documents.extend(loaded.documents)
        catalog.append(
            SourceCatalogEntry(
                source_id=source_id,
                path=path,
                sha256=loaded.sha256,
                byte_count=loaded.byte_count if loaded.sha256 is not None else None,
            )
        )
        used_bytes += loaded.byte_count
        used_documents += len(loaded.documents)
        used_nodes += loaded.node_count
        used_resources += loaded.resource_count
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
        documents=sources.documents,
    )
