"""Serialize a run's results to JSON with secrets masked.

Request values render through the display sink, so declared secrets arrive
already masked. Response bodies are redacted through the single project-wide
:class:`~comparo.core.redaction.Redactor` (longest-first, encoding-robust), so a
secret echoed back by the server is masked too — and server-issued credential
headers are masked by name even when they were never declared.
"""

import dataclasses
import json
from collections.abc import Callable

from comparo.core.checks import Check
from comparo.core.execute import Execution
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.redaction import Redactor
from comparo.core.redaction import environment_secret_values
from comparo.core.redaction import mask_credential_header
from comparo.core.redaction import secret_values
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink


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
    # Mask secrets declared anywhere in the project AND in the environment the run
    # used (which a caller may pass without indexing it into the project).
    values = secret_values(project) | environment_secret_values(environment, project.root)
    redact = Redactor.from_values(values).text
    payload = {
        "environment": environment.metadata.name,
        "baseUrl": redact(environment.spec.base_url),
        "results": [_entry(project, environment, entry, redact) for entry in entries],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _entry(
    project: LoadedProject,
    environment: Environment,
    entry: RunEntry,
    redact: Callable[[str], str],
) -> dict[str, object]:
    resolver = Resolver(project, environment, Sink.DISPLAY)
    resolved = resolver.resolve_request(entry.request, entry.cell)
    response = entry.execution.response
    return {
        # A matrix case value can equal a declared secret, so the case key
        # (``token=<value>``) — and, defensively, the request id — are masked too.
        "request": redact(entry.request.metadata.id or entry.request.metadata.name),
        "case": redact(entry.cell.key) if entry.cell.key else None,
        "method": resolved.method,
        "url": redact(resolved.url),
        "requestHeaders": {redact(key): redact(str(value)) for key, value in resolved.headers},
        "requestBody": _redact_value(resolved.body, redact),
        "status": response.status if response else None,
        "durationMs": round(response.elapsed_ms, 1) if response else None,
        "error": redact(entry.execution.error) if entry.execution.error else None,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": redact(check.detail)}
            for check in entry.checks
        ],
        "responseHeaders": (
            {
                redact(key): redact(mask_credential_header(key, value))
                for key, value in response.headers
            }
            if response
            else None
        ),
        "responseBody": _redact_body(response.body, redact) if response else None,
    }


def _redact_body(body: bytes, redact: Callable[[str], str]) -> object:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return redact(body.decode("utf-8", "replace"))
    return _redact_value(payload, redact)


def _redact_value(value: object, redact: Callable[[str], str]) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        # Redact the KEY too: a server can echo a secret as an object key, so
        # masking only the value would still write the secret to disk.
        return {redact(str(key)): _redact_value(item, redact) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, redact) for item in value]
    return value
