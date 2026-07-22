"""Load a dotenv-style env file that backs the ``$env`` directive.

An ``Environment`` may declare ``envFile: <path>`` (and the CLI may pass
``--env-file``); the merged ``KEY=VALUE`` mapping is consulted by ``$env`` — and
only by ``$env`` — ahead of ``os.environ``. Nothing is auto-injected: a value
reaches a request only through a ``$env`` reference (in ``secrets:``, an env
value, or inline anywhere in a request tree). Every value the file supplies is
masked, so its contents never reach the TUI, a report, or an export.

The parser is deliberately minimal — ``KEY=VALUE``, ``#`` full-line comments,
blank lines, an optional ``export`` prefix, and surrounding quotes on the value —
and does NO variable expansion, so it can never collide with comparo's own
``${...}`` grammar nor pull a value out of the ambient environment.
"""

from collections.abc import Mapping
from pathlib import Path

from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.resolution import SecretError
from comparo.core.resolution import SecretUnavailableError
from comparo.core.resolution import _file_source


def parse_env_file(text: str) -> dict[str, str]:
    """Parse dotenv-style *text* into a ``{KEY: VALUE}`` mapping.

    Supports ``KEY=VALUE`` lines, ``#`` full-line comments, blank lines, an
    optional leading ``export``, and one layer of surrounding single/double
    quotes on the value. Does NO variable expansion and strips no inline ``#`` (a
    secret may legitimately contain one). A line with no ``=`` or an empty key is
    skipped; on a duplicate key the last line wins.

    Args:
        text: The env file contents.

    Returns:
        The parsed key/value pairs.
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export") and line[6:7].isspace():
            line = line[6:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        result[key] = _unquote(value.strip())
    return result


def _unquote(value: str) -> str:
    """Strip one layer of matching surrounding quotes, without unescaping."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_overlay(
    environment: Environment,
    root: Path,
    *,
    cli_env: Mapping[str, str] | None = None,
    best_effort: bool = False,
) -> dict[str, str]:
    """The effective ``$env`` overlay for *environment* — its file merged under *cli_env*.

    The profile's ``envFile`` is resolved relative to (and confined to) *root* and
    parsed; *cli_env* (the ``--env-file`` override) is layered on top, winning per
    key. A merely-absent profile file is benign — the overlay is just *cli_env*, so
    ``$env`` still falls back to ``os.environ``. An *unreadable* or root-escaping
    file is anomalous: it raises for a persisted/execute sink and, with
    ``best_effort`` (an ephemeral display), degrades to *cli_env* rather than crash.

    Args:
        environment: The environment whose ``envFile`` (if any) is loaded.
        root: The project root the file path is confined to.
        cli_env: The CLI ``--env-file`` override, merged over the file per key.
        best_effort: Degrade to *cli_env* rather than raise on an unreadable file.

    Returns:
        The merged ``{KEY: VALUE}`` overlay.

    Raises:
        SecretError: If a declared file exists but cannot be read (and not best_effort).
    """
    parsed: dict[str, str] = {}
    declared = environment.spec.env_file
    if declared:
        try:
            parsed = parse_env_file(_file_source(declared, root))
        except SecretUnavailableError:
            # A merely-absent file contributes nothing; $env falls back to os.environ.
            parsed = {}
        except SecretError as error:
            # An exists-but-unreadable / root-escaping file. Persisted/execute sinks
            # fail closed; an ephemeral display degrades. Re-stamp so the message
            # names envFile, not the $file directive (the path is safe to show).
            if not best_effort:
                raise SecretError(f"envFile '{declared}': {error}") from error
            parsed = {}
    if cli_env:
        return {**parsed, **cli_env}
    return parsed


def env_file_values(
    project: LoadedProject,
    *,
    cli_env: Mapping[str, str] | None = None,
    best_effort: bool = False,
) -> set[str]:
    """Every non-empty value any environment's overlay supplies — the redactor floor.

    The whole env file is secret material, so every value it (or *cli_env*) provides
    is masked wherever it appears, exactly as a declared secret's value is. Empty
    values are skipped — masking ``""`` would replace the gaps between all text.

    Args:
        project: The loaded project.
        cli_env: The CLI ``--env-file`` override, included in the floor.
        best_effort: Degrade rather than raise on an unreadable file.

    Returns:
        The set of secret values to mask.
    """
    values: set[str] = set()
    for obj in project.objects.values():
        if isinstance(obj, Environment):
            overlay = load_env_overlay(obj, project.root, cli_env=cli_env, best_effort=best_effort)
            values |= {value for value in overlay.values() if value}
    return values
