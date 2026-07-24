"""Enforce purity, immutability, and secret-safety constraints in the domain."""

from __future__ import annotations

import ast
import importlib
import inspect
import pickle
import pkgutil
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import MutableSequence
from collections.abc import MutableSet
from collections.abc import Sequence
from collections.abc import Set
from pathlib import Path
from types import ModuleType
from typing import Any
from typing import TypeAliasType
from typing import TypeVar
from typing import get_args
from typing import get_origin
from typing import get_type_hints
from typing import override

import msgspec
import pytest

import comparo.core.domain
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId

_DOMAIN_PREFIX = "comparo.core.domain"
_PRIVATE_STRUCT_BASE_ALLOWLIST = frozenset(
    {
        "comparo.core.domain.identity._SlugIdentifier",
        "comparo.core.domain.identity._UuidIdentifier",
    }
)

_FORBIDDEN_IMPORTS = frozenset(
    {
        "aiohttp",
        "anyio",
        "asyncio",
        "dataclasses",
        "ftplib",
        "http.client",
        "httpcore",
        "httpx",
        "importlib",
        "io",
        "json",
        "jsonschema",
        "logging",
        "marshal",
        "multiprocessing",
        "os",
        "pathlib",
        "pickle",
        "requests",
        "ruamel",
        "shelve",
        "shutil",
        "signal",
        "smtplib",
        "socket",
        "sqlite3",
        "ssl",
        "subprocess",
        "tempfile",
        "textual",
        "threading",
        "typer",
        "urllib.request",
        "urllib3",
    }
)

_FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "builtins.__import__",
        "builtins.compile",
        "builtins.delattr",
        "builtins.eval",
        "builtins.exec",
        "builtins.input",
        "builtins.open",
        "builtins.print",
        "builtins.setattr",
        "compile",
        "dataclasses.asdict",
        "dataclasses.astuple",
        "dataclasses.replace",
        "datetime.date.today",
        "datetime.datetime.now",
        "datetime.datetime.today",
        "datetime.datetime.utcnow",
        "delattr",
        "eval",
        "exec",
        "importlib.import_module",
        "input",
        "msgspec.convert",
        "msgspec.json.Decoder",
        "msgspec.json.Encoder",
        "msgspec.json.decode",
        "msgspec.json.encode",
        "msgspec.msgpack.Decoder",
        "msgspec.msgpack.Encoder",
        "msgspec.msgpack.decode",
        "msgspec.msgpack.encode",
        "msgspec.structs.asdict",
        "msgspec.structs.astuple",
        "msgspec.structs.force_setattr",
        "msgspec.structs.replace",
        "msgspec.to_builtins",
        "object.__setattr__",
        "open",
        "print",
        "setattr",
        "uuid.uuid1",
        "uuid.uuid4",
    }
)

_FORBIDDEN_CALL_PREFIXES = (
    "random.",
    "secrets.",
    "time.",
)

_MUTABLE_OR_DYNAMIC_ORIGINS = frozenset(
    {
        list,
        dict,
        set,
        frozenset,
        bytearray,
        memoryview,
        Collection,
        Iterable,
        Mapping,
        MutableMapping,
        MutableSequence,
        MutableSet,
        Sequence,
        Set,
    }
)

_MUTABLE_RUNTIME_TYPES = (list, dict, set, bytearray, memoryview)
_SENSITIVE_SENTINEL = "architecture-secret-9b66f054"
_MAX_DOMAIN_SOURCE_LINES = 450


def _domain_modules() -> tuple[ModuleType, ...]:
    package = comparo.core.domain
    discovered = [package]
    module_infos = pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}.")
    discovered.extend(importlib.import_module(info.name) for info in module_infos)
    return tuple(sorted(discovered, key=lambda module: module.__name__))


def _domain_sources() -> tuple[tuple[str, Path, ast.Module], ...]:
    sources: list[tuple[str, Path, ast.Module]] = []
    for module in _domain_modules():
        source_file = inspect.getsourcefile(module)
        assert source_file is not None, f"domain module {module.__name__} has no Python source"
        path = Path(source_file)
        sources.append((module.__name__, path, ast.parse(path.read_text(encoding="utf-8"))))
    return tuple(sources)


def _domain_structs() -> tuple[type[msgspec.Struct], ...]:
    structs: set[type[msgspec.Struct]] = set()
    for module in _domain_modules():
        for candidate in vars(module).values():
            if (
                inspect.isclass(candidate)
                and candidate.__module__ == module.__name__
                and issubclass(candidate, msgspec.Struct)
            ):
                structs.add(candidate)
    return tuple(sorted(structs, key=lambda struct: f"{struct.__module__}.{struct.__qualname__}"))


def test_domain_modules_remain_small_enough_to_have_one_cohesive_responsibility() -> None:
    oversized = {
        module_name: len(path.read_text(encoding="utf-8").splitlines())
        for module_name, path, _ in _domain_sources()
        if len(path.read_text(encoding="utf-8").splitlines()) > _MAX_DOMAIN_SOURCE_LINES
    }
    assert not oversized, f"split oversized domain modules by responsibility: {oversized}"


def _qualified_name(candidate: type[object]) -> str:
    return f"{candidate.__module__}.{candidate.__qualname__}"


def _is_true_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _class_options(tree: ast.Module) -> dict[str, dict[str, ast.expr]]:
    return {
        node.name: {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _annotation_violations(annotation: object, *, seen: set[int] | None = None) -> set[str]:
    active = set() if seen is None else seen
    identity = id(annotation)
    if identity in active:
        return set()
    active.add(identity)

    atomic_violation = _atomic_annotation_violation(annotation)
    if atomic_violation is not None:
        return {atomic_violation}
    if isinstance(annotation, TypeVar):
        violations: set[str] = set()
        if annotation.__bound__ is not None:
            violations.update(_annotation_violations(annotation.__bound__, seen=active))
        for constraint in annotation.__constraints__:
            violations.update(_annotation_violations(constraint, seen=active))
        return violations
    if isinstance(annotation, TypeAliasType):
        return _annotation_violations(annotation.__value__, seen=active)

    origin = get_origin(annotation)
    violations = set()
    if origin in _MUTABLE_OR_DYNAMIC_ORIGINS:
        violations.add(str(origin))
    for argument in get_args(annotation):
        violations.update(_annotation_violations(argument, seen=active))
    return violations


def _atomic_annotation_violation(annotation: object) -> str | None:
    for forbidden, label in ((Any, "Any"), (object, "object"), (msgspec.Raw, "msgspec.Raw")):
        if annotation is forbidden:
            return label
    return None


def _mutable_default_paths(value: object, *, path: str = "default") -> tuple[str, ...]:
    if isinstance(value, _MUTABLE_RUNTIME_TYPES):
        return (path,)
    if isinstance(value, tuple):
        paths: list[str] = []
        for index, item in enumerate(value):
            paths.extend(_mutable_default_paths(item, path=f"{path}[{index}]"))
        return tuple(paths)
    if isinstance(value, msgspec.Struct):
        paths = []
        for field_name in value.__struct_fields__:
            paths.extend(
                _mutable_default_paths(getattr(value, field_name), path=f"{path}.{field_name}")
            )
        return tuple(paths)
    return ()


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name.split(".", maxsplit=1)[0]] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for imported in node.names:
                aliases[imported.asname or imported.name] = f"{node.module}.{imported.name}"
    return aliases


def _call_name(node: ast.expr, aliases: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value, aliases)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _matches_module(candidate: str, forbidden: str) -> bool:
    return candidate == forbidden or candidate.startswith(f"{forbidden}.")


class _DomainAstGuard(ast.NodeVisitor):
    """Collect forbidden effects and escape hatches from one domain module."""

    def __init__(self, *, module_name: str, aliases: Mapping[str, str]) -> None:
        self.module_name = module_name
        self.aliases = aliases
        self.problems: list[str] = []
        self._classes: list[str] = []
        self._functions: list[str] = []

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions.append(node.name)
        if node.args.kwarg is not None:
            self.problems.append(
                f"line {node.lineno}: variadic keyword bag **{node.args.kwarg.arg}"
            )
        self.generic_visit(node)
        self._functions.pop()

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        if node.args.kwarg is not None:
            self.problems.append(
                f"line {node.lineno}: variadic keyword bag **{node.args.kwarg.arg}"
            )
        self.generic_visit(node)
        self._functions.pop()

    @override
    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg == "default_factory":
                self.problems.append(f"line {node.lineno}: unreviewed default_factory")

        called = _call_name(node.func, self.aliases)
        if called is not None and self._is_forbidden_call(called):
            self.problems.append(f"line {node.lineno}: forbidden call {called}")
        self.generic_visit(node)

    def _is_forbidden_call(self, called: str) -> bool:
        if called == "object.__setattr__" and self._is_sensitive_initializer():
            return False
        return called in _FORBIDDEN_CALLS or called.startswith(_FORBIDDEN_CALL_PREFIXES)

    def _is_sensitive_initializer(self) -> bool:
        return (
            self.module_name == "comparo.core.domain.sensitivity"
            and self._classes[-1:] in (["SensitiveValue"], ["SensitiveValueAccess"])
            and self._functions[-1:] == ["__init__"]
        )


def test_every_domain_struct_is_explicitly_frozen_keyword_only_and_final() -> None:
    options_by_module = {
        module_name: _class_options(tree) for module_name, _, tree in _domain_sources()
    }
    violations: list[str] = []

    for struct in _domain_structs():
        qualified = _qualified_name(struct)
        config = struct.__struct_config__
        if not config.frozen:
            violations.append(f"{qualified}: not frozen")
        if struct.__match_args__:
            violations.append(f"{qualified}: positional fields {struct.__match_args__!r}")
        if any(
            parameter.kind is not inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(struct).parameters.values()
        ):
            violations.append(f"{qualified}: constructor is not keyword-only")
        if (
            not getattr(struct, "__final__", False)
            and qualified not in _PRIVATE_STRUCT_BASE_ALLOWLIST
        ):
            violations.append(f"{qualified}: concrete/public Struct is not @final")

        options = options_by_module[struct.__module__].get(struct.__name__, {})
        if not _is_true_constant(options.get("frozen", ast.Constant(value=False))):
            violations.append(f"{qualified}: class declaration must repeat frozen=True")
        if not _is_true_constant(options.get("kw_only", ast.Constant(value=False))):
            violations.append(f"{qualified}: class declaration must repeat kw_only=True")

    assert not violations, "\n".join(violations)


def test_private_struct_base_allowances_remain_narrow() -> None:
    structs = {_qualified_name(struct): struct for struct in _domain_structs()}
    violations: list[str] = []
    for qualified in _PRIVATE_STRUCT_BASE_ALLOWLIST:
        base = structs.get(qualified)
        if base is None:
            violations.append(f"allowlisted private Struct no longer exists: {qualified}")
            continue
        if not base.__name__.startswith("_"):
            violations.append(f"allowlisted Struct is no longer private: {qualified}")
        concrete_children = [
            child for child in base.__subclasses__() if child.__module__.startswith(_DOMAIN_PREFIX)
        ]
        if not concrete_children or any(
            not getattr(child, "__final__", False) for child in concrete_children
        ):
            violations.append(f"private Struct base lacks final domain children: {qualified}")
    assert not violations, "\n".join(violations)


def test_domain_struct_state_has_no_mutable_or_dynamic_escape_hatches() -> None:
    violations: list[str] = []
    for struct in _domain_structs():
        qualified = _qualified_name(struct)
        hints = get_type_hints(struct, include_extras=True)
        for field_name in struct.__struct_fields__:
            annotation = hints[field_name]
            forbidden = _annotation_violations(annotation)
            if forbidden:
                violations.append(
                    f"{qualified}.{field_name}: forbidden annotation parts {sorted(forbidden)!r}"
                )
        for default in struct.__struct_defaults__:
            violations.extend(
                f"{qualified}: mutable nested {mutable_path}"
                for mutable_path in _mutable_default_paths(default)
            )
    assert not violations, "\n".join(violations)


def test_domain_source_has_no_io_framework_mutation_or_serialization_effects() -> None:
    violations: list[str] = []
    for module_name, _, tree in _domain_sources():
        aliases = _import_aliases(tree)
        violations.extend(
            f"{module_name}: forbidden import {imported}"
            for imported in aliases.values()
            if any(_matches_module(imported, forbidden) for forbidden in _FORBIDDEN_IMPORTS)
        )
        guard = _DomainAstGuard(module_name=module_name, aliases=aliases)
        guard.visit(tree)
        violations.extend(f"{module_name}: {problem}" for problem in guard.problems)
    assert not violations, "\n".join(violations)


def test_domain_source_has_no_explicit_any_annotations() -> None:
    violations: list[str] = []
    for module_name, _, tree in _domain_sources():
        aliases = _import_aliases(tree)
        any_names = {name for name, target in aliases.items() if target == "typing.Any"}
        for node in ast.walk(tree):
            annotation: ast.expr | None = None
            if isinstance(node, (ast.AnnAssign, ast.arg)):
                annotation = node.annotation
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotation = node.returns
            if annotation is None:
                continue
            for part in ast.walk(annotation):
                is_imported_any = isinstance(part, ast.Name) and part.id in any_names
                is_qualified_any = (
                    isinstance(part, ast.Attribute)
                    and part.attr == "Any"
                    and isinstance(part.value, ast.Name)
                    and aliases.get(part.value.id, part.value.id) == "typing"
                )
                if is_imported_any or is_qualified_any:
                    line_number = part.lineno if isinstance(part, ast.expr) else "unknown"
                    violations.append(f"{module_name}: line {line_number}: explicit Any annotation")
    assert not violations, "\n".join(violations)


def test_sensitive_values_are_sealed_after_construction() -> None:
    sensitive = SensitiveValue(
        _SENSITIVE_SENTINEL,
        origins=(TaintOriginId(value="architecture-test-origin"),),
    )

    with pytest.raises(AttributeError):
        _set_attribute(sensitive, "_origins", ())
    with pytest.raises(AttributeError):
        _set_attribute(sensitive, "_SensitiveValue__value", "replacement")
    with pytest.raises(AttributeError):
        delattr(sensitive, "_origins")


@pytest.mark.parametrize(
    "serialize",
    [msgspec.json.encode, msgspec.msgpack.encode, msgspec.to_builtins, pickle.dumps],
)
def test_sensitive_serialization_fails_without_echoing_payload(
    serialize: Callable[[object], object],
) -> None:
    sensitive = SensitiveValue(
        _SENSITIVE_SENTINEL,
        origins=(TaintOriginId(value="architecture-test-origin"),),
    )

    with pytest.raises((TypeError, ValueError)) as captured:
        serialize(sensitive)

    assert _SENSITIVE_SENTINEL not in str(captured.value)
    assert _SENSITIVE_SENTINEL not in repr(captured.value)


def test_sensitive_rendering_and_enclosing_struct_never_echo_payload() -> None:
    class SensitiveEnvelope(msgspec.Struct, frozen=True, kw_only=True):
        payload: object

    sensitive = SensitiveValue(
        _SENSITIVE_SENTINEL,
        origins=(TaintOriginId(value="architecture-test-origin"),),
    )
    envelope = SensitiveEnvelope(payload=sensitive)

    for rendered in (str(sensitive), repr(sensitive), f"{sensitive:any-spec}", repr(envelope)):
        assert _SENSITIVE_SENTINEL not in rendered
    with pytest.raises(TypeError) as captured:
        msgspec.json.encode(envelope)
    assert _SENSITIVE_SENTINEL not in str(captured.value)


def _set_attribute(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


def test_sensitive_construction_errors_never_echo_payload() -> None:
    with pytest.raises(ValueError, match="at least one") as captured:
        SensitiveValue(_SENSITIVE_SENTINEL, origins=())
    assert _SENSITIVE_SENTINEL not in str(captured.value)
    assert _SENSITIVE_SENTINEL not in repr(captured.value)
