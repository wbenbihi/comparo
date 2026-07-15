"""The ``${...}`` interpolation grammar.

Grammar (in one ``${...}``):

- ``${NAME}``          required — abort if unset
- ``${NAME?}``         optional — resolves to ``None`` if unset
- ``${NAME | default}``  default value, used only when ``NAME`` is unset
- ``${NAME:int}``      typed whole-value cast (``int`` | ``number`` | ``bool``)

Resolution is **secret-first**: a name that is a secret in the environment
resolves to a masked value even when reached via ``${...}`` — so a secret can
never be surfaced by writing it as a plain variable.
"""

import dataclasses
import re
from typing import Protocol

from comparo.core.provenance import Origin

_INTERP = re.compile(r"\$\{([^}]*)\}")
_WHOLE = re.compile(r"^\$\{([^}]*)\}$")
_CASTS = ("int", "number", "bool")


class InterpolationError(Exception):
    """Raised when a required variable is unset or a value cannot be cast."""


class Secrets(Protocol):
    """Resolves a secret name to its value; raises if it cannot."""

    def __getitem__(self, name: str, /) -> str:
        """Return the value of the secret named *name*."""
        ...


def _empty_secrets() -> Secrets:
    return {}


@dataclasses.dataclass(frozen=True, slots=True)
class Context:
    """The environment context interpolation resolves against.

    In the display sink ``mask_secrets`` is true and every secret renders as
    ``mask``; in the execute sink it is false and ``secret_values`` resolves the
    real value lazily — so an unused, unavailable secret never fails a run.
    """

    variables: dict[str, str]
    secret_names: frozenset[str]
    mask: str = "••••••"
    mask_secrets: bool = True
    secret_values: Secrets = dataclasses.field(default_factory=_empty_secrets)


@dataclasses.dataclass(frozen=True, slots=True)
class Interpolated:
    """The result of interpolating one string value."""

    value: object
    origin: Origin
    detail: str | None


def interpolate(text: str, context: Context) -> Interpolated:
    """Interpolate ``${...}`` references in *text* against *context*.

    A string that is exactly one ``${...}`` yields a typed value (honouring a
    ``:cast``); otherwise every ``${...}`` is substituted and the result is a
    string whose origin is the strongest of its parts (secret > variable).

    Args:
        text: The raw string to interpolate.
        context: The variables and secret names to resolve against.

    Returns:
        The interpolated value with its origin and a provenance detail.

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


def _resolve_one(inner: str, context: Context) -> Interpolated:
    name, cast, optional, default = _parse(inner)
    if name in context.secret_names:
        value = context.mask if context.mask_secrets else context.secret_values[name]
        return Interpolated(value, Origin.SECRET, f"${{{name}}} → secret")
    if name in context.variables:
        return Interpolated(_cast(context.variables[name], cast), Origin.VARIABLE, f"${{{name}}}")
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
