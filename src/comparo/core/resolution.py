"""The one place comparo resolves configuration values.

Every value directive lives here so resolution is never scattered: the
``${...}`` string grammar, the ``$val``/``$var``/``$secret``/``$literal``/
``$env``/``$file``/``$from`` dict directives, and the ``$env``/``$file`` source
backends. :mod:`comparo.core.resolve` keeps only the request-shaped tree-walk
(header merge, matrix injection, auth override) and delegates every hole and
string here; :mod:`comparo.core.loader` shares the hole detector; the redactor
collects declared-secret values through :class:`ExecuteSecrets`.

Two sinks, distinguished by one flag on :class:`Context`. The **display** sink
(``mask_secrets=True``) masks a value the moment it is known to be secret and
never consults the lazy real-secret backend, so an unused, unavailable secret
never fails a render. The **execute** sink (``mask_secrets=False``) injects the
real value. Masking is keyed off the ``secrets:`` declaration — a value is
secret because its name is declared (secret-first) or its value matches a
declared secret (the redactor's substring floor), never because of the directive
that produced it. So ``$env``/``$file``/``$literal``/``$from`` resolve their real
value everywhere; whether that value is then masked is the project's call.
"""

import dataclasses
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from comparo.core.provenance import Origin
from comparo.core.provenance import Trail

# ── errors ──────────────────────────────────────────────────────────────────


class ResolutionError(Exception):
    """Base for every resolution failure — an unset variable or an unusable source."""


class InterpolationError(ResolutionError):
    """Raised when a required ``${...}`` variable is unset or a cast fails."""


class SecretError(ResolutionError):
    """Raised when a required secret/source cannot be resolved.

    An *anomalous* failure — an unreadable or root-escaping ``$file``, an
    unsupported source shape. The redactor fails closed on it, and a ``$from``
    fallback never swallows it.
    """


class SecretUnavailableError(SecretError):
    """A source that is simply *absent* — never resolvable this session.

    An unset ``$env``, an undeclared name, or a fully-exhausted ``$from`` chain.
    Distinguished from a plain :class:`SecretError` so the redactor can skip a
    benign gap while still failing closed on a source it cannot read, and a
    ``$from`` chain can try the next candidate. The value was never available
    this session, so it cannot have been echoed back into a response.
    """


# ── context ─────────────────────────────────────────────────────────────────


class Secrets(Protocol):
    """Resolves a declared secret name to its value; raises if it cannot."""

    def __getitem__(self, name: str, /) -> str:
        """Return the value of the secret named *name*."""
        ...


def _empty_secrets() -> Secrets:
    return {}


def _no_instance(_identifier: str) -> object:
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class Context:
    """The environment context resolution runs against.

    In the display sink ``mask_secrets`` is true and every declared secret
    renders as ``mask``; in the execute sink it is false and ``secret_values``
    resolves the real value lazily — so an unused, unavailable secret never
    fails a run. ``instances`` expands ``$val`` (id → the instance's value tree)
    and ``root`` confines inline ``$file``.
    """

    variables: dict[str, str]
    secret_names: frozenset[str]
    mask: str = "••••••"
    mask_secrets: bool = True
    secret_values: Secrets = dataclasses.field(default_factory=_empty_secrets)
    instances: Callable[[str], object] = _no_instance
    root: Path | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Interpolated:
    """The result of interpolating one string value."""

    value: object
    origin: Origin
    detail: str | None


# ── source backend: $env / $file / $literal / $from ─────────────────────────


def _env_source(variable: str) -> str:
    value = os.environ.get(variable)
    if value is None:
        message = f"environment variable '{variable}' is not set"
        raise SecretUnavailableError(message)
    return value


def _file_source(relative: object, root: Path | None) -> str:
    if root is None:
        message = f"$file '{relative}' has no project root to resolve against"
        raise SecretError(message)
    base = root.resolve()
    path = (base / str(relative)).resolve()
    if not path.is_relative_to(base):
        message = f"$file path escapes the project root: {relative}"
        raise SecretError(message)
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError, LookupError) as error:
        message = f"cannot read $file {path}"
        raise SecretError(message) from error


def resolve_source(source: object, root: Path | None) -> str:
    """Resolve a ``{$env|$file|$literal|$from: …}`` source dict to a real value.

    The one backend shared by the ``secrets:`` block (:class:`ExecuteSecrets`)
    and inline ``$env``/``$file``/``$from`` holes. A ``$from`` list tries each
    candidate in order, skipping only a benign :class:`SecretUnavailableError`
    and letting an anomalous :class:`SecretError` fail closed.

    Args:
        source: The source dict to resolve.
        root: The project root, for confining ``$file``.

    Returns:
        The resolved value as a string.

    Raises:
        SecretUnavailableError: If the source is absent (unset env, exhausted chain).
        SecretError: If the source is anomalous (unreadable/escaping file, bad shape).
    """
    if isinstance(source, dict):
        if "$env" in source:
            return _env_source(str(source["$env"]))
        if "$literal" in source:
            return str(source["$literal"])
        if "$file" in source:
            return _file_source(source["$file"], root)
        candidates = source.get("$from")
        if isinstance(candidates, list):
            for candidate in candidates:
                try:
                    return resolve_source(candidate, root)
                except SecretUnavailableError:
                    # A benign absence — try the next source. An anomalous
                    # SecretError (unreadable/escaping $file) propagates and fails
                    # closed: a fallback must never mask a real misconfiguration.
                    continue
            message = "no source in '$from' resolved"
            raise SecretUnavailableError(message)
    message = f"unsupported source: {source!r}"
    raise SecretError(message)


@dataclasses.dataclass(slots=True)
class ExecuteSecrets:
    """Resolves declared ``secrets:`` names to real values on demand, cached."""

    sources: dict[str, object]
    root: Path
    _cache: dict[str, str] = dataclasses.field(default_factory=dict)

    def __getitem__(self, name: str) -> str:
        """Resolve *name* to its secret value, caching the result.

        Raises:
            SecretUnavailableError: If *name* is undeclared or its source is absent.
            SecretError: If the source is anomalous (unreadable/escaping file).
        """
        if name in self._cache:
            return self._cache[name]
        if name not in self.sources:
            message = f"no secret named '{name}'"
            raise SecretUnavailableError(message)
        try:
            value = resolve_source(self.sources[name], self.root)
        except SecretError as error:
            # Re-stamp the anonymous backend error with the secret's name.
            cls = type(error)
            raise cls(f"secret '{name}': {error}") from error
        self._cache[name] = value
        return value


# ── ${...} string interpolation ─────────────────────────────────────────────

_INTERP = re.compile(r"\$\{([^}]*)\}")
_WHOLE = re.compile(r"^\$\{([^}]*)\}$")
_CASTS = ("int", "number", "bool")


def interpolate(text: str, context: Context) -> Interpolated:
    """Interpolate ``${...}`` references in *text* against *context*.

    A string that is exactly one ``${...}`` yields a typed value (honouring a
    ``:cast``); otherwise every ``${...}`` is substituted and the result is a
    string whose origin is the strongest of its parts (secret > variable).

    Raises:
        InterpolationError: If a required variable is unset or a cast fails.
    """
    whole = _WHOLE.match(text)
    if whole is not None:
        return _resolve_one(whole.group(1), context)

    origin = Origin.LITERAL
    details: list[str] = []

    def _substitute(match: re.Match[str]) -> str:
        nonlocal origin
        part = _resolve_one(match.group(1), context)
        if part.origin is Origin.SECRET:
            origin = Origin.SECRET
        elif part.origin is Origin.VARIABLE and origin is not Origin.SECRET:
            origin = Origin.VARIABLE
        if part.detail is not None:
            details.append(part.detail)
        return "" if part.value is None else str(part.value)

    result = _INTERP.sub(_substitute, text)
    return Interpolated(result, origin, ", ".join(details) if details else None)


def _resolve_name(name: str, context: Context, cast: str | None) -> Interpolated | None:
    """Resolve a bare variable/secret *name* — the shared core of ``${…}`` and ``$var``.

    Secret-first: a name declared in ``secret_names`` always resolves as a secret
    even if it is also a variable, so a secret can never be surfaced by writing it
    as a plain variable. Returns ``None`` when the name is neither.
    """
    if name in context.secret_names:
        value = context.mask if context.mask_secrets else context.secret_values[name]
        return Interpolated(value, Origin.SECRET, f"{name} → secret")
    if name in context.variables:
        return Interpolated(_cast(context.variables[name], cast), Origin.VARIABLE, name)
    return None


def _resolve_one(inner: str, context: Context) -> Interpolated:
    name, cast, optional, default = _parse(inner)
    resolved = _resolve_name(name, context, cast)
    if resolved is not None:
        # Preserve the ``${name}`` detail shape (callers/trail depend on it).
        if resolved.origin is Origin.SECRET:
            return dataclasses.replace(resolved, detail=f"${{{name}}} → secret")
        return dataclasses.replace(resolved, detail=f"${{{name}}}")
    if default is not None:
        return Interpolated(_cast(default, cast), Origin.LITERAL, None)
    if optional:
        return Interpolated(None, Origin.LITERAL, None)
    message = f"required variable '{name}' is not set"
    raise InterpolationError(message)


def _parse(inner: str) -> tuple[str, str | None, bool, str | None]:
    name_part, separator, default_part = inner.partition("|")
    default = default_part.strip() if separator else None
    name_part = name_part.strip()
    optional = name_part.endswith("?")
    if optional:
        name_part = name_part[:-1].strip()
    name, colon, cast = name_part.partition(":")
    return name.strip(), (cast.strip() if colon else None), optional, default


def _cast(text: str, cast: str | None) -> object:
    if cast is None:
        return text
    if cast not in _CASTS:
        message = f"unknown cast ':{cast}'"
        raise InterpolationError(message)
    try:
        if cast == "int":
            return int(text)
        if cast == "number":
            return float(text)
    except ValueError as error:
        message = f"cannot cast '{text}' to {cast}"
        raise InterpolationError(message) from error
    return text.strip().lower() in ("true", "1", "yes")


# ── the value-tree engine: strings interpolate, {$sigil} holes dispatch ─────


def hole(node: dict[object, object]) -> tuple[str, object] | None:
    """The one hole detector: a single-key dict whose key starts with ``$``."""
    if len(node) != 1:
        return None
    (key, value) = next(iter(node.items()))
    if isinstance(key, str) and key.startswith("$"):
        return key, value
    return None


def _join(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)


class Engine:
    """Resolves a value tree against a :class:`Context`, holes and all.

    Path- and provenance-aware: every non-literal value it fills appends a
    :class:`Trail` to :attr:`trail`, so the resolved-request UI can explain each
    value. One engine per resolve so the ``$val`` cycle guard is shared across the
    whole walk — including the header-block ``$val`` the request resolver expands.
    """

    def __init__(self, context: Context) -> None:
        """Bind the engine to *context* and start an empty trail + cycle guard."""
        self.context = context
        self._resolving: set[str] = set()
        self.trail: list[Trail] = []

    def value(self, node: object, path: str = "") -> object:
        """Resolve *node* (string, hole, dict, or list); record trail as a side effect."""
        if isinstance(node, str):
            part = interpolate(node, self.context)
            if part.origin is not Origin.LITERAL and part.detail is not None:
                self.trail.append(Trail(path, part.origin, part.detail))
            return part.value
        if isinstance(node, dict):
            spot = hole(node)
            if spot is not None:
                return self.reference(spot[0], spot[1], path)
            return {key: self.value(val, _join(path, key)) for key, val in node.items()}
        if isinstance(node, list):
            return [self.value(item, f"{path}[{index}]") for index, item in enumerate(node)]
        return node

    def reference(self, sigil: str, target: object, path: str) -> object:
        """Resolve one ``{$sigil: target}`` hole at *path*, recording its trail."""
        if sigil == "$literal":
            # Verbatim, no recursion and no trail — the interpolation escape hatch,
            # so a literal ``{"$ref": ...}`` body is sent as data, not resolved.
            return target
        if sigil == "$val" and isinstance(target, str):
            return self._instance(target, path)
        if sigil == "$var" and isinstance(target, str):
            return self._variable(target, path)
        if sigil == "$secret" and isinstance(target, str):
            self.trail.append(Trail(path, Origin.SECRET, f"$secret:{target}"))
            if self.context.mask_secrets:
                return self.context.mask
            return self.context.secret_values[target]
        if sigil in ("$env", "$file", "$from"):
            return self._source(sigil, target, path)
        # An unknown sigil is not a directive — pass the dict through untouched.
        return {sigil: target}

    def _instance(self, identifier: str, path: str) -> object:
        if identifier in self._resolving:
            chain = " → ".join([*self._resolving, identifier])
            raise InterpolationError(f"$val cycle: {chain}")
        self.trail.append(Trail(path, Origin.INSTANCE, identifier))
        self._resolving.add(identifier)
        try:
            return self.value(self.context.instances(identifier), path)
        finally:
            self._resolving.discard(identifier)

    def _variable(self, name: str, path: str) -> object:
        resolved = _resolve_name(name, self.context, None)
        if resolved is None:
            raise InterpolationError(f"required variable '{name}' is not set")
        detail = f"$var:{name} → secret" if resolved.origin is Origin.SECRET else f"$var:{name}"
        self.trail.append(Trail(path, resolved.origin, detail))
        return resolved.value

    def _source(self, sigil: str, target: object, path: str) -> object:
        """Resolve an inline ``$env``/``$file``/``$from`` to its real value.

        Masking is not the directive's job — the value is real, and the redactor's
        floor masks it iff it is a declared secret. A benign absence degrades to an
        empty string in the display sink (a preview must not crash on an unset
        var); in the execute sink it fails, because the request cannot be sent
        without the value. An anomalous source always fails closed.
        """
        origin = Origin.FILE if sigil == "$file" else Origin.ENV
        # A ``$env``/``$file`` target is a var name / path (safe to show); a ``$from``
        # target is a list that may carry a ``$literal`` secret fallback, so never
        # render it — the detail is the directive alone.
        detail = "$from" if sigil == "$from" else f"{sigil}:{target}"
        try:
            value: object = resolve_source({sigil: target}, self.context.root)
        except SecretUnavailableError:
            if not self.context.mask_secrets:  # execute sink: cannot send without it
                raise
            value = ""  # display sink: degrade, never crash a preview
        self.trail.append(Trail(path, origin, detail))
        return value


def resolve_value(node: object, context: Context) -> tuple[object, list[Trail]]:
    """Resolve a standalone value tree, returning it with its provenance trail."""
    engine = Engine(context)
    return engine.value(node), engine.trail
