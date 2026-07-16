"""Resolve an attachment slot — a ``$ref``, an inline object, or a list of either.

Every place that references an object (a request's ``diff`` / ``assert``, a
project or execution default) accepts one of three shapes: a ``{$ref: id}``
pointer, an inline spec written in place, or a list mixing both. This is the one
resolver they share, so an inline object works everywhere a ``$ref`` does — the
"keep it all in one file" shape — and a list composes.
"""

import msgspec

from comparo.core.loader import LoadedProject


def resolve_specs[Spec](project: LoadedProject, value: object, spec_type: type[Spec]) -> list[Spec]:
    """Resolve *value* into a list of ``spec_type`` specs, in order.

    A ``$ref`` yields the referenced object's ``.spec`` (when it is a
    ``spec_type``); an inline mapping is converted directly; a list resolves each
    element and concatenates. Anything that does not fit is skipped.

    Args:
        project: The loaded project, used to resolve ``$ref`` ids.
        value: A ``$ref`` mapping, an inline mapping, or a list of either.
        spec_type: The spec struct the slot expects (e.g. ``DiffProfileSpec``).

    Returns:
        The resolved specs, in the order they appear.
    """
    specs: list[Spec] = []
    for item in _items(value):
        if not isinstance(item, dict):
            continue
        reference = item.get("$ref")
        if isinstance(reference, str):
            spec = getattr(project.objects.get(reference), "spec", None)
            if isinstance(spec, spec_type):
                specs.append(spec)
        else:
            try:
                specs.append(msgspec.convert(item, type=spec_type, strict=True))
            except msgspec.ValidationError:
                continue
    return specs


def _items(value: object) -> list[object]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]
