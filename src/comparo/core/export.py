"""Serialize a run's results to JSON with secrets masked.

Request values render through the display sink, so declared secrets arrive
already masked. Response bodies are redacted by string-match against the real
secret values, so a secret echoed back by the server is masked too.
"""

import dataclasses
import json

from comparo.core.checks import Check
from comparo.core.execute import Execution
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.secrets import ExecuteSecrets
from comparo.core.secrets import SecretError

_MASK = "••••••"


@dataclasses.dataclass(frozen=True, slots=True)
class RunEntry:
    """One executed cell paired with its checks, ready to serialize."""

    request: Request
    cell: MatrixCell
    execution: Execution
    checks: list[Check]


def export_run(project: LoadedProject, environment: Environment, entries: list[RunEntry]) -> str:
    """Serialize *entries* to indented JSON with every secret masked.

    Args:
        project: The loaded project.
        environment: The environment the run executed against.
        entries: The executed cells and their checks.

    Returns:
        A JSON document safe to write to disk — no real secret value survives.
    """
    secrets = _secret_values(project, environment)
    payload = {
        "environment": environment.metadata.name,
        "baseUrl": _redact(environment.spec.base_url, secrets),
        "results": [_entry(project, environment, entry, secrets) for entry in entries],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _entry(
    project: LoadedProject, environment: Environment, entry: RunEntry, secrets: set[str]
) -> dict[str, object]:
    resolver = Resolver(project, environment, Sink.DISPLAY)
    resolved = resolver.resolve_request(entry.request, entry.cell)
    response = entry.execution.response
    return {
        "request": entry.request.metadata.id or entry.request.metadata.name,
        "case": entry.cell.key or None,
        "method": resolved.method,
        "url": _redact(resolved.url, secrets),
        "requestHeaders": {key: _redact(str(value), secrets) for key, value in resolved.headers},
        "requestBody": _redact_value(resolved.body, secrets),
        "status": response.status if response else None,
        "durationMs": round(response.elapsed_ms, 1) if response else None,
        "error": entry.execution.error,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": _redact(check.detail, secrets)}
            for check in entry.checks
        ],
        "responseHeaders": (
            {key: _redact(value, secrets) for key, value in response.headers} if response else None
        ),
        "responseBody": _redact_body(response.body, secrets) if response else None,
    }


def _secret_values(project: LoadedProject, environment: Environment) -> set[str]:
    sources = environment.spec.secrets or {}
    execute = ExecuteSecrets(dict(sources), project.root)
    values: set[str] = set()
    for name in sources:
        try:
            value = execute[name]
        except SecretError:
            continue
        if value:
            values.add(value)
    return values


def _redact(text: str, secrets: set[str]) -> str:
    for value in secrets:
        text = text.replace(value, _MASK)
    return text


def _redact_body(body: bytes, secrets: set[str]) -> object:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return _redact(body.decode("utf-8", "replace"), secrets)
    return _redact_value(payload, secrets)


def _redact_value(value: object, secrets: set[str]) -> object:
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, dict):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    return value
