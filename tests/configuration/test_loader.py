"""Tests for deterministic local project discovery and compilation adapters."""

from pathlib import Path
from typing import BinaryIO
from typing import cast

import msgspec
import pytest

from comparo.configuration import loader as loader_module
from comparo.configuration.loader import DEFAULT_PROJECT_LOAD_LIMITS
from comparo.configuration.loader import LoadedCompilation
from comparo.configuration.loader import ProjectLoadLimits
from comparo.configuration.loader import ProjectSourceSet
from comparo.configuration.loader import SourceCatalogEntry
from comparo.configuration.loader import compile_project_path
from comparo.configuration.loader import load_project_sources
from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.canonical import CompilationResult
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ParsedDocument
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId


def _entry(*, data: str = ".comparo", sources: str = "") -> str:
    source_line = f"  sources: {sources}\n" if sources else ""
    return (
        "apiVersion: comparo/v1alpha1\nkind: Project\n"
        "metadata: {id: demo, name: Demo}\nspec:\n"
        f"  data: {data}\n{source_line}"
    )


def _resource(kind: str, resource_id: str, spec: str) -> str:
    return (
        "apiVersion: comparo/v1alpha1\n"
        f"kind: {kind}\nmetadata: {{id: {resource_id}, name: {resource_id}}}\n"
        f"spec: {spec}\n"
    )


def test_loader_discovers_yaml_deterministically_deduplicates_and_compiles(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(), encoding="utf-8")
    data = tmp_path / ".comparo"
    data.mkdir()
    (data / "b.yml").write_text(
        _resource("Environment", "prod", "{baseUrl: https://example.com}"),
        encoding="utf-8",
    )
    (data / "a.yaml").write_text(
        _resource("Request", "get", "{request: {method: GET, endpoint: /}}"),
        encoding="utf-8",
    )
    (data / "ignored.txt").write_text("not yaml", encoding="utf-8")
    loaded = load_project_sources(tmp_path)
    assert loaded.diagnostics == ()
    assert len(loaded.catalog) == 3
    assert tuple(item.path.name for item in loaded.catalog) == (
        "comparo.yaml",
        "a.yaml",
        "b.yml",
    )
    compiled = compile_project_path(tmp_path)
    assert compiled.compilation.project is not None
    assert (
        compiled.compilation.project.find(
            ResourceKind.REQUEST,
            ResourceId(value="get"),
        )
        is not None
    )


def test_loader_supports_long_discovery_config_include_exclude_and_explicit_sources(
    tmp_path: Path,
) -> None:
    entry = tmp_path / "custom.yaml"
    entry.write_text(
        _entry(
            data="{root: resources, include: ['**/*.yaml'], exclude: ['skip.yaml']}",
            sources="[extra.yml, resources]",
        ),
        encoding="utf-8",
    )
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "keep.yaml").write_text(
        _resource("Environment", "keep", "{baseUrl: keep}"),
        encoding="utf-8",
    )
    (resources / "skip.yaml").write_text(
        _resource("Environment", "skip", "{baseUrl: skip}"),
        encoding="utf-8",
    )
    (tmp_path / "extra.yml").write_text(
        _resource("Environment", "extra", "{baseUrl: extra}"),
        encoding="utf-8",
    )
    loaded = load_project_sources(entry)
    assert loaded.diagnostics == ()
    assert {item.path.name for item in loaded.catalog} == {
        "custom.yaml",
        "keep.yaml",
        "skip.yaml",
        "extra.yml",
    }
    assert len({entry.source_id for entry in loaded.catalog}) == len(loaded.catalog)


def test_loader_requires_operator_authorization_for_external_sources(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        _resource("Environment", "outside", "{baseUrl: outside}"),
        encoding="utf-8",
    )
    (project / "comparo.yaml").write_text(_entry(sources="[../outside.yaml]"), encoding="utf-8")
    blocked = load_project_sources(project)
    assert "configuration.source.external-forbidden" in {
        item.code.value for item in blocked.diagnostics
    }
    allowed = load_project_sources(project, authorize_external=lambda path: path == outside)
    assert allowed.diagnostics == ()
    assert outside.resolve() in {item.path for item in allowed.catalog}


def test_loader_reports_missing_entry_explicit_source_invalid_discovery_and_yaml(
    tmp_path: Path,
) -> None:
    missing = load_project_sources(tmp_path / "missing.yaml")
    assert missing.diagnostics[0].code.value == "configuration.source.entry-missing"
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(sources="[missing.yaml]"), encoding="utf-8")
    explicit = load_project_sources(entry)
    assert "configuration.source.explicit-missing" in {
        item.code.value for item in explicit.diagnostics
    }
    entry.write_text(_entry(data="{root: 2}"), encoding="utf-8")
    invalid = load_project_sources(entry)
    assert "configuration.source.discovery-invalid" in {
        item.code.value for item in invalid.diagnostics
    }
    entry.write_text("broken: [", encoding="utf-8")
    broken = load_project_sources(entry)
    assert broken.diagnostics[0].code.value == "configuration.yaml.syntax"


@pytest.mark.parametrize(
    "spec",
    [
        "{}",
        "{data: 2}",
        "{data: {root: resources, unknown: true}}",
        "{data: {root: resources, include: 2}}",
        "{sources: 2}",
        "{sources: [valid.yaml, 2]}",
    ],
)
def test_loader_validates_every_discovery_shape(tmp_path: Path, spec: str) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(
        "apiVersion: comparo/v1alpha1\nkind: Project\n"
        f"metadata: {{id: demo, name: Demo}}\nspec: {spec}\n",
        encoding="utf-8",
    )
    result = load_project_sources(entry)
    if spec == "{}":
        assert result.diagnostics == ()
    else:
        assert "configuration.source.discovery-invalid" in {
            item.code.value for item in result.diagnostics
        }


def test_loader_accepts_scalar_explicit_source_and_reports_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(data=".", sources="extra.yaml"), encoding="utf-8")
    extra = tmp_path / "extra.yaml"
    extra.write_text(_resource("Environment", "extra", "{baseUrl: extra}"), encoding="utf-8")
    original = Path.open

    def fail_extra(self: Path, mode: str) -> BinaryIO:
        if self == extra:
            raise OSError
        return cast("BinaryIO", original(self, mode))

    monkeypatch.setattr(Path, "open", fail_extra)
    result = load_project_sources(entry)
    assert "configuration.source.read-failed" in {item.code.value for item in result.diagnostics}


def test_loader_requires_authorization_for_external_data_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "environment.yaml").write_text(
        _resource("Environment", "outside", "{baseUrl: outside}"),
        encoding="utf-8",
    )
    entry = project / "comparo.yaml"
    entry.write_text(_entry(data="../outside"), encoding="utf-8")
    blocked = load_project_sources(entry)
    assert "configuration.source.external-forbidden" in {
        item.code.value for item in blocked.diagnostics
    }
    allowed = load_project_sources(entry, authorize_external=lambda path: path == outside)
    assert allowed.diagnostics == ()


def test_loader_handles_project_with_non_mapping_spec_without_crashing(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(
        "apiVersion: comparo/v1alpha1\nkind: Project\nmetadata: {id: demo, name: Demo}\nspec: []\n",
        encoding="utf-8",
    )
    result = load_project_sources(entry)
    assert result.documents


def test_loader_ignores_symlinked_files_and_directories(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(), encoding="utf-8")
    data = tmp_path / ".comparo"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "external.yaml"
    target.write_text(_resource("Environment", "external", "{baseUrl: x}"), encoding="utf-8")
    (data / "linked.yaml").symlink_to(target)
    (data / "linked-dir").symlink_to(outside, target_is_directory=True)
    loaded = load_project_sources(entry)
    assert tuple(item.path.name for item in loaded.catalog) == ("comparo.yaml",)


def test_loader_boundary_models_reject_untyped_mutable_values(tmp_path: Path) -> None:
    source_id = SourceId(value="entry-config")
    path = tmp_path.resolve()
    with pytest.raises(TypeError, match="SourceId"):
        SourceCatalogEntry(source_id=cast("SourceId", "entry"), path=path)
    with pytest.raises(TypeError, match="absolute Path"):
        SourceCatalogEntry(source_id=source_id, path=Path("relative"))
    catalog = (SourceCatalogEntry(source_id=source_id, path=path),)
    with pytest.raises(TypeError, match="entry"):
        ProjectSourceSet(entry_source=cast("SourceId", "entry"))
    with pytest.raises(TypeError, match="documents"):
        ProjectSourceSet(
            entry_source=source_id,
            documents=cast("tuple[ParsedDocument, ...]", [object()]),
        )
    with pytest.raises(TypeError, match="diagnostics"):
        ProjectSourceSet(
            entry_source=source_id,
            diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [object()]),
        )
    with pytest.raises(TypeError, match="catalog"):
        ProjectSourceSet(
            entry_source=source_id,
            catalog=cast("tuple[SourceCatalogEntry, ...]", [catalog[0]]),
        )
    with pytest.raises(TypeError, match="CompilationResult"):
        LoadedCompilation(compilation=cast("CompilationResult", object()), catalog=catalog)
    with pytest.raises(TypeError, match="catalog"):
        LoadedCompilation(
            compilation=CompilationResult(),
            catalog=cast("tuple[SourceCatalogEntry, ...]", [catalog[0]]),
        )
    with pytest.raises(TypeError, match="must be a Path"):
        load_project_sources(cast("Path", "config"))
    with pytest.raises(TypeError, match="tuple of Paths"):
        load_project_sources(path, additional_sources=cast("tuple[Path, ...]", [path]))
    with pytest.raises(TypeError, match="load limits"):
        load_project_sources(path, limits=cast("ProjectLoadLimits", object()))
    with pytest.raises(ValueError, match="positive exact integer"):
        ProjectLoadLimits(maximum_sources=0)
    with pytest.raises(TypeError, match="YAML limits"):
        ProjectLoadLimits(yaml=cast("YamlParseLimits", object()))
    result = compile_project_path(
        tmp_path / "missing.yaml",
        namespace=NamespaceId(value="custom"),
    )
    assert result.compilation.project is None


def test_loader_enforces_source_byte_count_and_discovery_limits(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(), encoding="utf-8")
    too_small = ProjectLoadLimits(
        yaml=YamlParseLimits(maximum_bytes=2),
    )
    oversized = load_project_sources(entry, limits=too_small)
    assert oversized.diagnostics[0].code.value == "configuration.source.limit-exceeded"

    entry.write_bytes(b"\xff")
    invalid_text = load_project_sources(entry)
    assert invalid_text.diagnostics[0].code.value == "configuration.source.read-failed"

    entry.write_text(_entry(sources="[extras]"), encoding="utf-8")
    data = tmp_path / ".comparo"
    data.mkdir()
    (data / "one.yaml").write_text(
        _resource("Environment", "one", "{baseUrl: one}"),
        encoding="utf-8",
    )
    (data / "zero.yaml").write_text(
        _resource("Environment", "zero", "{baseUrl: zero}"),
        encoding="utf-8",
    )
    extras = tmp_path / "extras"
    extras.mkdir()
    for name in ("two", "three"):
        (extras / f"{name}.yaml").write_text(
            _resource("Environment", name, f"{{baseUrl: {name}}}"),
            encoding="utf-8",
        )
    bounded = load_project_sources(entry, limits=ProjectLoadLimits(maximum_sources=2))
    assert "configuration.source.limit-exceeded" in {
        diagnostic.code.value for diagnostic in bounded.diagnostics
    }


def test_excluded_files_do_not_consume_the_source_admission_budget(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(
        _entry(data=("{root: resources, include: ['**/*.yaml'], exclude: ['a-excluded.yaml']}")),
        encoding="utf-8",
    )
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "a-excluded.yaml").write_text(
        _resource("Environment", "excluded", "{baseUrl: excluded}"),
        encoding="utf-8",
    )
    (resources / "z-admitted.yaml").write_text(
        _resource("Environment", "admitted", "{baseUrl: admitted}"),
        encoding="utf-8",
    )

    loaded = load_project_sources(entry, limits=ProjectLoadLimits(maximum_sources=2))

    assert tuple(item.path.name for item in loaded.catalog) == (
        "comparo.yaml",
        "z-admitted.yaml",
    )
    assert "configuration.source.limit-exceeded" not in {
        diagnostic.code.value for diagnostic in loaded.diagnostics
    }


def test_automatic_and_explicit_duplicates_do_not_consume_unique_source_capacity(
    tmp_path: Path,
) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(
        _entry(sources="[.comparo/one.yaml, extra.yaml]"),
        encoding="utf-8",
    )
    data = tmp_path / ".comparo"
    data.mkdir()
    (data / "one.yaml").write_text(
        _resource("Environment", "one", "{baseUrl: one}"),
        encoding="utf-8",
    )
    (tmp_path / "extra.yaml").write_text(
        _resource("Environment", "extra", "{baseUrl: extra}"),
        encoding="utf-8",
    )

    loaded = load_project_sources(entry, limits=ProjectLoadLimits(maximum_sources=3))

    assert {item.path.name for item in loaded.catalog} == {
        "comparo.yaml",
        "one.yaml",
        "extra.yaml",
    }
    assert len(loaded.catalog) == 3
    assert "configuration.source.limit-exceeded" not in {
        diagnostic.code.value for diagnostic in loaded.diagnostics
    }


def test_irrelevant_entries_exhaust_bounded_discovery_and_fail_closed(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(), encoding="utf-8")
    data = tmp_path / ".comparo"
    data.mkdir()
    for name in ("one.txt", "two.txt", "three.txt"):
        (data / name).write_text("irrelevant", encoding="utf-8")

    loaded = load_project_sources(
        entry,
        limits=ProjectLoadLimits(maximum_discovery_entries=2),
    )

    assert tuple(item.path.name for item in loaded.catalog) == ("comparo.yaml",)
    assert "configuration.source.discovery-limit-exceeded" in {
        diagnostic.code.value for diagnostic in loaded.diagnostics
    }


def test_loader_enforces_aggregate_project_budgets_across_individually_valid_files(
    tmp_path: Path,
) -> None:
    entry = tmp_path / "comparo.yaml"
    entry_text = _entry()
    entry.write_text(entry_text, encoding="utf-8")
    data = tmp_path / ".comparo"
    data.mkdir()
    resource_text = _resource("Environment", "one", "{baseUrl: one}")
    (data / "one.yaml").write_text(resource_text, encoding="utf-8")
    (data / "two.yaml").write_text(
        _resource("Environment", "two", "{baseUrl: two}"),
        encoding="utf-8",
    )
    limits = ProjectLoadLimits(
        maximum_total_bytes=len(entry_text.encode()) + len(resource_text.encode()),
        maximum_total_documents=2,
        maximum_total_nodes=1_000_000,
        maximum_resources=2,
    )

    loaded = load_project_sources(entry, limits=limits)

    assert "configuration.source.aggregate-limit-exceeded" in {
        diagnostic.code.value for diagnostic in loaded.diagnostics
    }
    assert len(loaded.documents) == 2
    assert len(loaded.catalog) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_total_bytes": 0},
        {"maximum_total_documents": 0},
        {"maximum_total_nodes": 0},
        {"maximum_resources": 0},
    ],
)
def test_project_aggregate_budgets_are_positive_caller_owned_values(
    changes: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="aggregate limits"):
        msgspec.structs.replace(DEFAULT_PROJECT_LOAD_LIMITS, **changes)


def test_aggregate_node_unit_charges_mapping_entries_and_value_nodes() -> None:
    parsed = parse_yaml_source(
        source_id=SourceId(value="mapping-count"),
        text="{a: 1, b: [2, 3]}",
    )

    assert loader_module._document_node_count(parsed.documents) == 7


def test_loader_bounds_explicit_directories_before_reading_every_file(tmp_path: Path) -> None:
    entry = tmp_path / "comparo.yaml"
    entry.write_text(_entry(data="missing", sources="[extras]"), encoding="utf-8")
    extras = tmp_path / "extras"
    extras.mkdir()
    for name in ("one", "two"):
        (extras / f"{name}.yaml").write_text(
            _resource("Environment", name, f"{{baseUrl: {name}}}"),
            encoding="utf-8",
        )
    bounded = load_project_sources(entry, limits=ProjectLoadLimits(maximum_sources=2))
    assert "configuration.source.limit-exceeded" in {
        diagnostic.code.value for diagnostic in bounded.diagnostics
    }
