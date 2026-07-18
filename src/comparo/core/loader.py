"""Load and validate a comparo project from a directory of YAML objects.

Loading has three passes, all diagnostics-collecting so one run surfaces every
problem at once: parse + envelope validation, id indexing, and reference
resolution. A dangling ``$ref``/``$val`` is a hard error with a near-miss
suggestion — the loader never silently degrades.
"""

import dataclasses
import datetime
import difflib
from collections.abc import Iterable
from collections.abc import Iterator
from pathlib import Path

import msgspec
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.models import Object
from comparo.core.models import Project

_REF_SIGILS = ("$ref", "$val")


@dataclasses.dataclass(frozen=True, slots=True)
class LoadedProject:
    """A validated project: its manifest and every object indexed by id.

    ``root`` is the project directory (a manifest's parent, or the directory
    itself); ``data_dir`` is where the object YAML lives (``spec.data`` resolved).
    They coincide for the common ``data: .`` shape.
    """

    root: Path
    project: Project | None
    objects: dict[str, Object]
    data_dir: Path | None = None

    @property
    def objects_dir(self) -> Path:
        """The directory the object YAML lives in (``data_dir`` or ``root``)."""
        return self.data_dir if self.data_dir is not None else self.root


@dataclasses.dataclass(frozen=True, slots=True)
class _Entry:
    file: Path
    obj: Object
    raw: object


@dataclasses.dataclass(frozen=True, slots=True)
class _Reference:
    sigil: str
    target: str
    line: int | None


def load_project(source: Path) -> LoadedProject:
    """Load and validate a comparo project from a directory or a manifest file.

    Two shapes are accepted. A **directory** is treated as a self-contained
    project — every ``*.yaml`` under it is loaded. A **manifest file** (e.g.
    ``comparo.yaml``) is the modern shape: the file is the ``Project`` manifest,
    and its objects are loaded from ``spec.data`` resolved relative to the file
    (defaulting to the file's own directory), so comparo's data can live in a
    ``.comparo/`` folder that never collides with the user's own YAML.

    Args:
        source: A project directory or the path to a manifest file.

    Returns:
        The validated project, with all objects indexed by ``metadata.id``.

    Raises:
        LoadError: If any object fails to parse, validate, or resolve a reference.
    """
    root, data_dir, files = _resolve_sources(source)
    yaml = YAML(typ="rt")
    diagnostics: list[Diagnostic] = []
    entries: list[_Entry] = []

    for file in files:
        with file.open() as handle:
            try:
                raw = yaml.load(handle)
            except YAMLError as error:
                diagnostics.append(
                    Diagnostic(file, f"invalid YAML: {_yaml_problem(error)}", _yaml_line(error))
                )
                continue
        if raw is None:
            continue
        try:
            obj = msgspec.convert(_plain(raw), type=Object, strict=True)
        except msgspec.ValidationError as error:
            diagnostics.append(Diagnostic(file, str(error)))
            continue
        entries.append(_Entry(file, obj, raw))

    project, objects = _index(entries, diagnostics)
    _check_references(entries, set(objects), diagnostics)
    loaded = LoadedProject(root=root, project=project, objects=objects, data_dir=data_dir)
    if not diagnostics:  # profile resolution needs a fully-indexed project
        _check_profiles(loaded, entries, diagnostics)

    if diagnostics:
        raise LoadError(diagnostics, root)
    return loaded


def _resolve_sources(source: Path) -> tuple[Path, Path, list[Path]]:
    """Resolve *source* to a project root, data dir, and the object files to load.

    A directory is its own root and data dir. A manifest file's root is its
    parent directory and its data dir is ``spec.data`` resolved against that
    (default: the manifest's own directory); the manifest itself is also loaded.

    Args:
        source: A project directory or a manifest file.

    Returns:
        The project root, the data directory, and the sorted files to parse.
    """
    if source.is_dir():
        return source, source, sorted(source.rglob("*.yaml"))
    root = source.parent
    data_dir = (root / (_manifest_data(source) or ".")).resolve()
    files = sorted(data_dir.rglob("*.yaml")) if data_dir.is_dir() else []
    if source.resolve() not in {file.resolve() for file in files}:
        files = [source, *files]
    return root, data_dir, files


def _plain(node: object) -> object:
    """Reduce ruamel round-trip wrappers to plain Python types for strict convert.

    The round-trip parser yields a ``ScalarFloat`` for every float — a ``float``
    subclass that ``msgspec.convert(strict=True)`` rejects by exact type, so a
    ``tolerance: 0.01`` would fail to load. Unwrapping floats (and recursing
    containers) lets strict conversion see a real ``float``; the untouched
    original tree keeps its line info for reference diagnostics.

    Args:
        node: A parsed value, possibly a ruamel container or scalar.

    Returns:
        The value with any ``ScalarFloat`` reduced to ``float``.
    """
    if isinstance(node, dict):
        return {key: _plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_plain(item) for item in node]
    if isinstance(node, float) and not isinstance(node, bool):
        return float(node)
    if isinstance(node, datetime.date | datetime.time):
        # A YAML-native date/time (``2026-07-18``) has no JSON equivalent; render
        # it as an ISO string so it survives strict decode and json serialization.
        return node.isoformat()
    return node


def _manifest_data(config: Path) -> str | None:
    """Best-effort read of ``spec.data`` from a manifest, for locating objects.

    Parse errors are swallowed here — the main load pass reports them against
    the file with a line number.

    Args:
        config: The manifest file to peek at.

    Returns:
        The declared ``spec.data`` string, or ``None`` if absent or unreadable.
    """
    try:
        with config.open() as handle:
            raw = YAML(typ="safe").load(handle)
    except (OSError, YAMLError):
        return None
    if isinstance(raw, dict):
        spec = raw.get("spec")
        if isinstance(spec, dict) and isinstance(spec.get("data"), str):
            return str(spec["data"])
    return None


def _index(
    entries: list[_Entry], diagnostics: list[Diagnostic]
) -> tuple[Project | None, dict[str, Object]]:
    project: Project | None = None
    objects: dict[str, Object] = {}
    for entry in entries:
        obj = entry.obj
        kind = type(obj).__name__
        if isinstance(obj, Project):
            if project is not None:
                diagnostics.append(
                    Diagnostic(entry.file, "a second Project manifest — only one is allowed")
                )
            else:
                project = obj
            continue
        identifier = obj.metadata.id
        if identifier is None:
            diagnostics.append(Diagnostic(entry.file, f"{kind} is missing metadata.id"))
        elif identifier in objects:
            diagnostics.append(Diagnostic(entry.file, f"duplicate id '{identifier}'"))
        else:
            objects[identifier] = obj
    return project, objects


def _check_references(
    entries: list[_Entry], known: set[str], diagnostics: list[Diagnostic]
) -> None:
    for entry in entries:
        for reference in _find_references(entry.raw):
            # A JSON Schema / OpenAPI ``$ref`` (``#/$defs/…``, ``other.json#/…``)
            # is the user's own payload, not a comparo object id — leave it be.
            if reference.target.startswith("#") or "/" in reference.target:
                continue
            if reference.target not in known:
                diagnostics.append(
                    Diagnostic(
                        entry.file,
                        f"unknown {reference.sigil} target '{reference.target}'",
                        reference.line,
                        _near_miss(reference.target, known),
                    )
                )


def _check_profiles(
    loaded: LoadedProject, entries: list[_Entry], diagnostics: list[Diagnostic]
) -> None:
    """Validate every profile attachment slot at load time.

    A slot that cannot resolve is a load error, never a silent empty rule set —
    an empty rule set passes every gate, so swallowing it would be a false green.
    """
    from comparo.core.models import AssertionProfile
    from comparo.core.models import AssertionProfileSpec
    from comparo.core.models import DiffProfileSpec
    from comparo.core.models import ExecutionProfile
    from comparo.core.models import Instance
    from comparo.core.models import Matrix
    from comparo.core.models import Request
    from comparo.core.refs import SpecResolutionError
    from comparo.core.refs import resolve_specs

    file_by_id = {
        entry.obj.metadata.id: entry.file
        for entry in entries
        if getattr(entry.obj, "metadata", None) is not None and entry.obj.metadata.id is not None
    }

    def check(file: Path, value: object, spec_type: type) -> None:
        if value is None:
            return
        try:
            resolve_specs(loaded, value, spec_type)
        except SpecResolutionError as error:
            diagnostics.append(Diagnostic(file, str(error)))

    def check_includes(file: Path, includes: object) -> None:
        # Each include must be a {$ref: id} pointing at an AssertionProfile; a bare
        # string or wrong-kind ref would be silently dropped at runtime (false green).
        for entry in includes if isinstance(includes, list) else []:
            ref = entry.get("$ref") if isinstance(entry, dict) else None
            target = loaded.objects.get(ref) if isinstance(ref, str) else None
            if not isinstance(ref, str):
                diagnostics.append(
                    Diagnostic(file, f"assertion include is not a {{$ref: id}}: {entry!r}")
                )
            elif not isinstance(target, AssertionProfile):
                what = f"a {type(target).__name__}" if target is not None else "an unknown id"
                diagnostics.append(
                    Diagnostic(
                        file,
                        f"assertion include $ref '{ref}' resolves to {what}, not an "
                        "AssertionProfile",
                    )
                )

    def check_inline_assertions(file: Path, value: object) -> None:
        # Standalone profiles are validated in the object loop; an INLINE assert
        # spec (attached to a request/execution) has no object, so validate its
        # include here too — else its wrong-kind includes load clean and vanish.
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and "$ref" not in item:
                check_includes(file, item.get("include"))

    def check_matrix_refs(file: Path, refs: object) -> None:
        # A request's matrix entry must be a {$ref: id} pointing at a Matrix; a bare
        # string or wrong-kind ref is silently dropped at runtime (coverage vanishes).
        for entry in refs if isinstance(refs, list) else []:
            ref = entry.get("$ref") if isinstance(entry, dict) else None
            target = loaded.objects.get(ref) if isinstance(ref, str) else None
            if not isinstance(ref, str):
                diagnostics.append(
                    Diagnostic(file, f"matrix entry is not a {{$ref: id}}: {entry!r}")
                )
            elif not isinstance(target, Matrix):
                what = f"a {type(target).__name__}" if target is not None else "an unknown id"
                diagnostics.append(
                    Diagnostic(file, f"matrix $ref '{ref}' resolves to {what}, not a Matrix")
                )

    def check_val_kinds(file: Path, raw: object) -> None:
        # A $val must point at an Instance; a wrong-kind target silently resolves to
        # None at runtime (a stripped header or a literal "None" on the wire).
        for reference in _find_references(raw):
            if reference.sigil != "$val" or "/" in reference.target:
                continue
            target = loaded.objects.get(reference.target)
            if target is not None and not isinstance(target, Instance):
                diagnostics.append(
                    Diagnostic(
                        file,
                        f"$val '{reference.target}' resolves to a {type(target).__name__}, "
                        "not an Instance",
                        reference.line,
                    )
                )

    raw_by_id = {
        entry.obj.metadata.id: entry.raw
        for entry in entries
        if getattr(entry.obj, "metadata", None) is not None and entry.obj.metadata.id is not None
    }
    for obj in loaded.objects.values():
        obj_id = getattr(obj.metadata, "id", None)
        file = file_by_id.get(obj_id, loaded.root) if obj_id is not None else loaded.root
        if obj_id is not None and obj_id in raw_by_id:
            check_val_kinds(file, raw_by_id[obj_id])
        if isinstance(obj, Request):
            check_matrix_refs(file, obj.spec.matrix)
            response = obj.spec.response
            if response is not None:
                check(file, response.diff, DiffProfileSpec)
                check(file, response.assertions, AssertionProfileSpec)
                check_inline_assertions(file, response.assertions)
        elif isinstance(obj, ExecutionProfile):
            profiles = obj.spec.profiles
            if profiles is not None:
                check(file, profiles.diff, DiffProfileSpec)
                check(file, profiles.assert_, AssertionProfileSpec)
                check_inline_assertions(file, profiles.assert_)
        elif isinstance(obj, AssertionProfile):
            check_includes(file, obj.spec.include)
    if loaded.project is not None:
        config = loaded.project.spec.diff
        if isinstance(config, dict):
            check(loaded.root, config.get("default"), DiffProfileSpec)
        if loaded.project.spec.plugins:
            # The plugin system does not exist yet; accepting config for it would
            # silently no-op, so a configured plugins block is a hard error.
            diagnostics.append(
                Diagnostic(loaded.root, "spec.plugins is not supported yet — remove it")
            )


def _find_references(node: object) -> Iterator[_Reference]:
    if isinstance(node, dict):
        for sigil in _REF_SIGILS:
            target = node.get(sigil)
            if isinstance(target, str):
                yield _Reference(sigil, target, _key_line(node, sigil))
        for value in node.values():
            yield from _find_references(value)
    elif isinstance(node, list):
        for item in node:
            yield from _find_references(item)


def _near_miss(target: str, known: Iterable[str]) -> str | None:
    pool = sorted(known)
    close = difflib.get_close_matches(target, pool, n=1, cutoff=0.6)
    if close:
        return f"did you mean '{close[0]}'?"
    segments = frozenset(target.split("."))
    for candidate in pool:
        if frozenset(candidate.split(".")) == segments:
            return f"did you mean '{candidate}'? (same segments, different order)"
    return None


def _key_line(node: object, key: str) -> int | None:
    line_col = getattr(node, "lc", None)
    data = getattr(line_col, "data", None)
    if isinstance(data, dict) and key in data:
        return int(data[key][0]) + 1
    line = getattr(line_col, "line", None)
    return int(line) + 1 if isinstance(line, int) else None


def _yaml_problem(error: YAMLError) -> str:
    problem = getattr(error, "problem", None)
    return str(problem) if problem else str(error).splitlines()[0]


def _yaml_line(error: YAMLError) -> int | None:
    mark = getattr(error, "problem_mark", None)
    line = getattr(mark, "line", None)
    return int(line) + 1 if isinstance(line, int) else None
