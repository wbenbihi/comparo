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
import hashlib
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
    if not isinstance(relative, str):
        # A nested hole (e.g. ``{$file: {$literal: secret}}``) is malformed; never
        # repr it — the value could be a secret — and treat it as never-resolvable.
        raise SecretUnavailableError("$file target is not a path string")
    if root is None:
        raise SecretError(f"$file '{relative}' has no project root to resolve against")
    try:
        base = root.resolve()
        # ``.resolve()`` raises a raw ValueError on a NUL byte / OSError/RuntimeError
        # on a symlink loop, so the path build sits inside a try — those are anomalous.
        path = (base / relative).resolve()
    except (OSError, ValueError, LookupError, RuntimeError) as error:
        raise SecretError(f"cannot resolve $file '{relative}'") from error
    if not path.is_relative_to(base):
        raise SecretError(f"$file path escapes the project root: {relative}")
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError) as error:
        # A merely-absent file is BENIGN, like an unset $env: never available this
        # session, so a $from chain tries the next candidate and the redactor skips
        # it. An EXISTS-but-unreadable file (EACCES) below stays anomalous.
        raise SecretUnavailableError(f"$file not found: '{relative}'") from error
    except (OSError, ValueError, LookupError) as error:
        raise SecretError(f"cannot read $file '{relative}'") from error


#: The source directives a ``secrets:`` value (or an inline ``$env``/``$file``/
#: ``$from`` hole) may use — the allowlist for the shape diagnostic, so a malformed
#: source's error never echoes an unrecognised (possibly secret) key.
_DIRECTIVE_KEYS = frozenset({"$env", "$file", "$literal", "$from"})

#: Cap on nesting depth for both the value-tree walk (:meth:`Engine.value`) and a
#: ``$from`` chain (:func:`resolve_source`), so a runaway structure raises a caught
#: error rather than an uncaught ``RecursionError``. Real configs nest a handful deep.
_MAX_DEPTH = 200

#: A resolved string this many bytes or larger is replaced by a hash+size marker in
#: the DISPLAY sink, so a megabyte-scale value (a base64 blob, a generated body)
#: never reaches the terminal renderer and freezes it. The execute sink is untouched.
_DISPLAY_ELIDE_BYTES = 4096

_UNITS = ("B", "KiB", "MiB", "GiB")


def _human_bytes(size: int) -> str:
    """Render *size* bytes as a short human string, e.g. ``1.0 MiB``."""
    value = float(size)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


def resolve_source(source: object, root: Path | None, _depth: int = 0) -> tuple[str, Origin]:
    """Resolve a ``{$env|$file|$literal|$from: …}`` source dict to a real value.

    The one backend shared by the ``secrets:`` block (:class:`ExecuteSecrets`)
    and inline ``$env``/``$file``/``$from`` holes. A ``$from`` list tries each
    candidate in order, skipping only a benign :class:`SecretUnavailableError`
    and letting an anomalous :class:`SecretError` fail closed.

    Args:
        source: The source dict to resolve.
        root: The project root, for confining ``$file``.

    Returns:
        ``(value, origin)`` — the resolved value and which concrete source
        produced it (``ENV`` / ``FILE`` / ``LITERAL``; for a ``$from`` chain, the
        winning candidate's origin).

    Raises:
        SecretUnavailableError: If the source is never resolvable (unset env,
            exhausted/too-deep ``$from`` chain, malformed/unrecognised shape).
        SecretError: If the source is anomalous — a real file that cannot be read
            or a root-escaping ``$file`` (fails closed, never skipped by ``$from``).
    """
    if _depth > _MAX_DEPTH:
        # A runaway ``$from`` recurses through this function (not Engine.value), so
        # it needs its own cap. Structurally never-resolvable → benign, so the redactor
        # skips it and a caught error (not RecursionError) reaches every handler.
        raise SecretUnavailableError("$from nesting too deep")
    if isinstance(source, dict):
        if "$env" in source:
            return _env_source(str(source["$env"])), Origin.ENV
        if "$literal" in source:
            return str(source["$literal"]), Origin.LITERAL
        if "$file" in source:
            return _file_source(source["$file"], root), Origin.FILE
        candidates = source.get("$from")
        if isinstance(candidates, list):
            for candidate in candidates:
                try:
                    return resolve_source(candidate, root, _depth + 1)  # (value, origin)
                except SecretUnavailableError:
                    # A benign absence — try the next source. An anomalous
                    # SecretError (unreadable/escaping $file) propagates and fails
                    # closed: a fallback must never mask a real misconfiguration.
                    continue
            message = "no source in '$from' resolved"
            raise SecretUnavailableError(message)
    # A malformed/unrecognised source was never resolvable, so it is BENIGN
    # (SecretUnavailableError): the redactor skips it instead of crashing the whole
    # TUI/CLI build, and a $from chain tries the next candidate. NEVER repr the
    # source — show only its RECOGNISED directive keys, so neither a $literal secret
    # value nor a ``$``-leading secret in key position can leak into a displayed or
    # persisted error.
    if isinstance(source, dict):
        keys = sorted(key for key in map(str, source) if key in _DIRECTIVE_KEYS)
        shape = str(keys) if keys else "a dict with no recognised directive key"
    else:
        shape = type(source).__name__
    raise SecretUnavailableError(f"unsupported source shape: {shape}")


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
            value, _origin = resolve_source(self.sources[name], self.root)
        except SecretError as error:
            # Re-stamp the anonymous backend error with the secret's name. The
            # backend never reprs a value, so this carries no plaintext.
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
        self._depth = 0
        self.trail: list[Trail] = []

    def value(self, node: object, path: str = "") -> object:
        """Resolve *node* (string, hole, dict, or list); record trail as a side effect.

        The display sink degrades ANY resolution failure of a value — an unset
        ``${VAR}``, a bad cast, a ``$val`` cycle, an unreadable ``$file``, a runaway
        nesting — to an empty string, so a preview (Explorer, report tree, export)
        can never crash on one bad value. The execute sink re-raises the caught
        error, so only the offending request/check degrades and the send fails
        closed. Each recursive call catches its own subtree, so one bad leaf never
        takes down its siblings.
        """
        self._depth += 1
        try:
            if self._depth > _MAX_DEPTH:
                # A runaway nesting (a pathological value tree, or a $val chain the
                # static cycle check missed) would otherwise raise an uncaught
                # RecursionError; convert it to a caught ResolutionError.
                raise InterpolationError("value nesting too deep")
            if isinstance(node, str):
                part = interpolate(node, self.context)
                if part.origin is not Origin.LITERAL and part.detail is not None:
                    self.trail.append(Trail(path, part.origin, part.detail))
                return self._display_elide(part.value, path)
            if isinstance(node, dict):
                spot = hole(node)
                if spot is not None:
                    return self._display_elide(self.reference(spot[0], spot[1], path), path)
                return {key: self.value(val, _join(path, key)) for key, val in node.items()}
            if isinstance(node, list):
                return [self.value(item, f"{path}[{index}]") for index, item in enumerate(node)]
            return node
        except ResolutionError as error:
            if not self.context.mask_secrets:  # execute sink: fail closed, only this value
                raise
            # display sink: degrade so a preview never crashes, but record it so a
            # caller (comparo render) can report it rather than silently succeed.
            self.trail.append(Trail(path, Origin.UNRESOLVED, f"unresolved: {error}"))
            return ""
        finally:
            self._depth -= 1

    def _display_elide(self, value: object, path: str) -> object:
        """Replace a large string with a hash+size marker — display sink only.

        A megabyte value (a base64 blob, a generated body) would freeze the
        terminal renderer, so the display sink shows a compact, self-describing
        artifact instead: a size and a truncated sha256 that identifies the value
        (equal values render identically) without revealing it — sha256 is one-way,
        so this is safe even when the value is a declared secret. The execute sink
        (real send, curl copy) never elides — it keeps the value whole. A declared
        secret referenced by name is already the small mask here, so it never
        reaches this path; only a large inline value does.
        """
        if not self.context.mask_secrets or not isinstance(value, str):
            return value
        raw = value.encode("utf-8", "surrogatepass")
        if len(raw) < _DISPLAY_ELIDE_BYTES:
            return value
        size = _human_bytes(len(raw))
        digest = hashlib.sha256(raw).hexdigest()[:12]
        self.trail.append(Trail(path, Origin.ELIDED, f"elided {size} · sha256:{digest}"))
        return f"«elided · {size} · sha256:{digest} · display-only, sent whole»"

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
        floor masks it iff it is a declared secret. On any bad source the raised
        error propagates to :meth:`value`, which fails closed in the execute sink and
        degrades to "" in the display sink (a preview reads disk/env now, unlike the
        old mask-only path, so it must not crash on a missing file or malformed source).
        """
        # A ``$env``/``$file`` target is a var name / path (safe to show); a ``$from``
        # target is a list that may carry a ``$literal`` secret fallback, so never
        # render it — the detail is the directive alone.
        detail = "$from" if sigil == "$from" else f"{sigil}:{target}"
        value, origin = resolve_source({sigil: target}, self.context.root)
        self.trail.append(Trail(path, origin, detail))
        return value


def resolve_value(node: object, context: Context) -> tuple[object, list[Trail]]:
    """Resolve a standalone value tree, returning it with its provenance trail."""
    engine = Engine(context)
    return engine.value(node), engine.trail
