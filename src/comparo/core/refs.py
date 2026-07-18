"""Resolve an attachment slot — a ``$ref``, an inline object, or a list of either.

Every place that references an object (a request's ``diff`` / ``assert``, a
project or execution default) accepts one of three shapes: a ``{$ref: id}``
pointer, an inline spec written in place, or a list mixing both. This is the one
resolver they share, so an inline object works everywhere a ``$ref`` does — the
"keep it all in one file" shape — and a list composes.
"""

import msgspec

from comparo.core.loader import LoadedProject


class SpecResolutionError(Exception):
    """Raised when a ``$ref`` / inline attachment slot cannot be resolved.

    Failing loud here is a trust requirement: a swallowed profile resolves to
    *no rules*, and an empty rule set passes every gate — a silent false green.
    """


def ref_id(reference: object) -> str | None:
    """Return the id of a ``{$ref: id}`` mapping, or ``None`` if it is not one.

    Args:
        reference: A value that may be a ``{"$ref": "<id>"}`` mapping.

    Returns:
        The referenced id string, or ``None``.
    """
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None


def resolve_specs[Spec](project: LoadedProject, value: object, spec_type: type[Spec]) -> list[Spec]:
    """Resolve *value* into a list of ``spec_type`` specs, in order.

    A ``$ref`` yields the referenced object's ``.spec`` (which must be a
    ``spec_type``); an inline mapping is converted with the strict envelope; a
    list resolves each element and concatenates. Anything that does not fit is a
    hard error — never silently dropped — so a typo cannot quietly disable a
    profile and turn a real check into a green gate.

    Args:
        project: The loaded project, used to resolve ``$ref`` ids.
        value: A ``$ref`` mapping, an inline mapping, or a list of either.
        spec_type: The spec struct the slot expects (e.g. ``DiffProfileSpec``).

    Returns:
        The resolved specs, in the order they appear.

    Raises:
        SpecResolutionError: If any element is not a valid ``$ref`` or inline spec.
    """
    kind = spec_type.__name__
    specs: list[Spec] = []
    for item in _items(value):
        if not isinstance(item, dict):
            message = f"expected a {{$ref: id}} pointer or an inline {kind}, got {item!r}"
            raise SpecResolutionError(message)
        reference = item.get("$ref")
        if isinstance(reference, str):
            obj = project.objects.get(reference)
            if obj is None:
                raise SpecResolutionError(f"$ref '{reference}' resolves to no object")
            spec = getattr(obj, "spec", None)
            if not isinstance(spec, spec_type):
                found = type(obj).__name__
                raise SpecResolutionError(f"$ref '{reference}' is a {found}, not a {kind}")
            specs.append(spec)
        else:
            try:
                specs.append(msgspec.convert(item, type=spec_type, strict=True))
            except msgspec.ValidationError as error:
                raise SpecResolutionError(f"inline {kind} is invalid: {error}") from error
    return specs


def _items(value: object) -> list[object]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]
