"""Load and validate a comparo project from a directory of YAML objects.

Loading has three passes, all diagnostics-collecting so one run surfaces every
problem at once: parse + envelope validation, id indexing, and reference
resolution. A dangling ``$use``/``$val`` is a hard error with a near-miss
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

_REF_SIGILS = ("$use", "$val")


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
        _check_val_cycles(entries, objects, diagnostics)
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
        return source, source, _yaml_files(source)
    root = source.parent
    data_dir = (root / (_manifest_data(source) or ".")).resolve()
    # Confine spec.data to the project root: a ``../..`` or absolute path would make
    # comparo scan and parse YAML from anywhere on disk (S-2). Refuse it up front,
    # before globbing, so nothing outside the project is ever read.
    if not data_dir.is_relative_to(root.resolve()):
        message = f"spec.data escapes the project root: {_manifest_data(source)!r}"
        raise LoadError([Diagnostic(source, message)], root)
    files = _yaml_files(data_dir) if data_dir.is_dir() else []
    if source.resolve() not in {file.resolve() for file in files}:
        files = [source, *files]
    return root, data_dir, files


def _yaml_files(directory: Path) -> list[Path]:
    """Every YAML object file under *directory* — both ``.yaml`` and ``.yml``."""
    return sorted([*directory.rglob("*.yaml"), *directory.rglob("*.yml")])


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


def _check_val_cycles(
    entries: list[_Entry], objects: dict[str, Object], diagnostics: list[Diagnostic]
) -> None:
    """Reject an Instance ``$val`` reference cycle at load time.

    The resolver already fails closed on a cycle at run time, but ``validate`` never
    resolves — so without this static check a project with an ``A → B → A`` ``$val``
    cycle would validate clean and only blow up on the first ``run``/``diff``.
    """
    from comparo.core.models import Instance

    graph: dict[str, tuple[Path, list[str]]] = {}
    for entry in entries:
        if not isinstance(entry.obj, Instance) or entry.obj.metadata.id is None:
            continue
        targets = [
            reference.target
            for reference in _find_references(entry.raw)
            if reference.sigil == "$val" and isinstance(objects.get(reference.target), Instance)
        ]
        graph[entry.obj.metadata.id] = (entry.file, targets)

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str, stack: list[str]) -> bool:
        visiting.add(node)
        stack.append(node)
        for target in graph.get(node, (None, []))[1]:
            if target in visiting:  # a back edge closes a cycle
                cycle = [*stack[stack.index(target) :], target]
                diagnostics.append(Diagnostic(graph[node][0], f"$val cycle: {' → '.join(cycle)}"))
                return True
            if target not in done and target in graph and walk(target, stack):
                return True
        stack.pop()
        visiting.discard(node)
        done.add(node)
        return False

    for node in graph:
        if node not in done and walk(node, []):
            return  # one named cycle is enough to fail validation


def _check_references(
    entries: list[_Entry], known: set[str], diagnostics: list[Diagnostic]
) -> None:
    for entry in entries:
        for reference in _find_references(entry.raw):
            # A path-like ``$use``/``$val`` target (a ``#/…`` or ``/`` JSON pointer)
            # is never a comparo object id — leave it for the user's own payload.
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


def _check_spec(
    loaded: LoadedProject, diagnostics: list[Diagnostic], file: Path, value: object, spec_type: type
) -> None:
    """A profile attachment slot must resolve, or it is a load error."""
    from comparo.core.refs import SpecResolutionError
    from comparo.core.refs import resolve_specs

    if value is None:
        return
    try:
        resolve_specs(loaded, value, spec_type)
    except SpecResolutionError as error:
        diagnostics.append(Diagnostic(file, str(error)))


def _check_includes(
    loaded: LoadedProject, diagnostics: list[Diagnostic], file: Path, includes: object
) -> None:
    # Each include must be a {$use: id} pointing at an AssertionProfile; a bare
    # string or wrong-kind ref would be silently dropped at runtime (false green).
    from comparo.core.models import AssertionProfile

    for entry in includes if isinstance(includes, list) else []:
        ref = entry.get("$use") if isinstance(entry, dict) else None
        target = loaded.objects.get(ref) if isinstance(ref, str) else None
        if not isinstance(ref, str):
            diagnostics.append(
                Diagnostic(file, f"assertion include is not a {{$use: id}}: {entry!r}")
            )
        elif not isinstance(target, AssertionProfile):
            what = f"a {type(target).__name__}" if target is not None else "an unknown id"
            diagnostics.append(
                Diagnostic(
                    file,
                    f"assertion include $use '{ref}' resolves to {what}, not an AssertionProfile",
                )
            )


def _check_inline_assertions(
    loaded: LoadedProject, diagnostics: list[Diagnostic], file: Path, value: object
) -> None:
    # Standalone profiles are validated in the object loop; an INLINE assert spec
    # (attached to a request/execution) has no object, so validate its include here
    # too — else its wrong-kind includes load clean and vanish.
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict) and "$use" not in item:
            _check_includes(loaded, diagnostics, file, item.get("include"))


def _check_matrix_refs(
    loaded: LoadedProject, diagnostics: list[Diagnostic], file: Path, refs: object
) -> None:
    # A request's matrix entry must be a {$use: id} pointing at a Matrix; a bare
    # string or wrong-kind ref is silently dropped at runtime (coverage vanishes).
    from comparo.core.models import Matrix

    for entry in refs if isinstance(refs, list) else []:
        ref = entry.get("$use") if isinstance(entry, dict) else None
        target = loaded.objects.get(ref) if isinstance(ref, str) else None
        if not isinstance(ref, str):
            diagnostics.append(Diagnostic(file, f"matrix entry is not a {{$use: id}}: {entry!r}"))
        elif not isinstance(target, Matrix):
            what = f"a {type(target).__name__}" if target is not None else "an unknown id"
            diagnostics.append(
                Diagnostic(file, f"matrix $use '{ref}' resolves to {what}, not a Matrix")
            )


def _check_val_kinds(
    loaded: LoadedProject, diagnostics: list[Diagnostic], file: Path, raw: object
) -> None:
    # A $val must point at an Instance; a wrong-kind target silently resolves to None
    # at runtime (a stripped header or a literal "None" on the wire).
    from comparo.core.models import Instance

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
    from comparo.core.models import Request

    file_by_id: dict[str, Path] = {}
    raw_by_id: dict[str, object] = {}
    for entry in entries:
        ident = getattr(getattr(entry.obj, "metadata", None), "id", None)
        if ident is not None:
            file_by_id[ident] = entry.file
            raw_by_id[ident] = entry.raw

    for obj in loaded.objects.values():
        obj_id = getattr(obj.metadata, "id", None)
        file = file_by_id.get(obj_id, loaded.root) if obj_id is not None else loaded.root
        if obj_id is not None and obj_id in raw_by_id:
            _check_val_kinds(loaded, diagnostics, file, raw_by_id[obj_id])
        if isinstance(obj, Request):
            _check_matrix_refs(loaded, diagnostics, file, obj.spec.matrix)
            response = obj.spec.response
            if response is not None:
                _check_spec(loaded, diagnostics, file, response.diff, DiffProfileSpec)
                _check_spec(loaded, diagnostics, file, response.assertions, AssertionProfileSpec)
                _check_inline_assertions(loaded, diagnostics, file, response.assertions)
        elif isinstance(obj, ExecutionProfile):
            profiles = obj.spec.profiles
            if profiles is not None:
                _check_spec(loaded, diagnostics, file, profiles.diff, DiffProfileSpec)
                _check_spec(loaded, diagnostics, file, profiles.assert_, AssertionProfileSpec)
                _check_inline_assertions(loaded, diagnostics, file, profiles.assert_)
        elif isinstance(obj, AssertionProfile):
            _check_includes(loaded, diagnostics, file, obj.spec.include)
    if loaded.project is not None:
        config = loaded.project.spec.diff
        if isinstance(config, dict):
            _check_spec(loaded, diagnostics, loaded.root, config.get("default"), DiffProfileSpec)
        if loaded.project.spec.plugins:
            # The plugin system does not exist yet; accepting config for it would
            # silently no-op, so a configured plugins block is a hard error.
            diagnostics.append(
                Diagnostic(loaded.root, "spec.plugins is not supported yet — remove it")
            )
        report = loaded.project.spec.report
        if report is not None:
            # A report dir/output must stay within the project — an absolute or
            # ``..`` path would let a config write report files anywhere on disk.
            root = loaded.root.resolve()
            for label, value in (("dir", report.dir), ("output", report.output)):
                if (
                    isinstance(value, str)
                    and value
                    and not (root / value).resolve().is_relative_to(root)
                ):
                    diagnostics.append(
                        Diagnostic(
                            loaded.root, f"spec.report.{label} '{value}' escapes the project root"
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
