"""Load and validate a comparo project from a directory of YAML objects.

Loading has three passes, all diagnostics-collecting so one run surfaces every
problem at once: parse + envelope validation, id indexing, and reference
resolution. A dangling ``$ref``/``$val`` is a hard error with a near-miss
suggestion — the loader never silently degrades.
"""

import dataclasses
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
    """A validated project: its manifest and every object indexed by id."""

    root: Path
    project: Project | None
    objects: dict[str, Object]


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
    root, files = _resolve_sources(source)
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

    if diagnostics:
        raise LoadError(diagnostics, root)
    return LoadedProject(root=root, project=project, objects=objects)


def _resolve_sources(source: Path) -> tuple[Path, list[Path]]:
    """Resolve *source* to a project root and the object files to load.

    A directory loads every ``*.yaml`` beneath it. A manifest file loads its
    ``spec.data`` directory (default: the manifest's own directory) plus the
    manifest itself.

    Args:
        source: A project directory or a manifest file.

    Returns:
        The project root and the sorted list of files to parse.
    """
    if source.is_dir():
        return source, sorted(source.rglob("*.yaml"))
    data_dir = (source.parent / (_manifest_data(source) or ".")).resolve()
    files = sorted(data_dir.rglob("*.yaml")) if data_dir.is_dir() else []
    if source.resolve() not in {file.resolve() for file in files}:
        files = [source, *files]
    return data_dir, files


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
            if reference.target not in known:
                diagnostics.append(
                    Diagnostic(
                        entry.file,
                        f"unknown {reference.sigil} target '{reference.target}'",
                        reference.line,
                        _near_miss(reference.target, known),
                    )
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
