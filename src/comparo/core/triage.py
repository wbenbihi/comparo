"""Silence a drift by writing an ignore rule into a DiffProfile's YAML file.

Triage is a reviewable act: silencing a diff appends a rule to a committed
config file rather than hiding it in memory. The write is round-tripped through
ruamel so comments and formatting survive.
"""

from pathlib import Path

from ruamel.yaml import YAML

from comparo.core.loader import LoadedProject
from comparo.core.models import DiffProfile


class TriageError(Exception):
    """Raised when a drift cannot be silenced (profile file not found)."""


def silence(project: LoadedProject, profile_id: str, path: str, mode: str = "ignore") -> Path:
    """Append a ``{path, mode}`` rule to the diff profile identified by *profile_id*.

    Args:
        project: The loaded project (its root is searched for the profile file).
        profile_id: The ``metadata.id`` of the DiffProfile to edit.
        path: The JSON path to silence, e.g. ``$.headers``.
        mode: The diff mode to write (``ignore`` by default).

    Returns:
        The file that was written.

    Raises:
        TriageError: If no file declares the given profile.
    """
    if not isinstance(project.objects.get(profile_id), DiffProfile):
        message = f"'{profile_id}' is not a diff profile"
        raise TriageError(message)
    yaml = YAML(typ="rt")
    for file in sorted(project.root.rglob("*.yaml")):
        with file.open() as handle:
            data = yaml.load(handle)
        if not isinstance(data, dict) or data.get("kind") != "DiffProfile":
            continue
        metadata = data.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("id") != profile_id:
            continue
        spec = data.setdefault("spec", {})
        rules = spec.setdefault("rules", [])
        if not any(isinstance(rule, dict) and rule.get("path") == path for rule in rules):
            rules.append({"path": path, "mode": mode})
        with file.open("w") as handle:
            yaml.dump(data, handle)
        return file
    message = f"no file declares diff profile '{profile_id}'"
    raise TriageError(message)
