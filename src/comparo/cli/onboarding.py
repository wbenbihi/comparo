"""Small output and file-publication helpers for local onboarding commands."""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import CliCommandResult
from .contracts import CliExitCode
from .contracts import CliMessage
from .contracts import CliMessageLevel
from .contracts import CliPayloadKind


def installed_version() -> str:
    """Return the version of the installed distribution without a repository dependency."""
    try:
        return version("comparo")
    except PackageNotFoundError:
        return "unknown"


def document(text: str, kind: CliPayloadKind = CliPayloadKind.TEXT) -> CliCommandResult:
    """Return one complete UTF-8 document, including an empty completion response."""
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS, payload_kind=kind, payload=(text or "\n").encode()
    )


def outcome(
    command: str,
    status: str,
    message: str,
    *,
    machine: bool = False,
    success: bool = True,
    details: dict[str, object] | None = None,
) -> CliCommandResult:
    """Build a human or JSON onboarding outcome with an explicit success state."""
    exit_code = CliExitCode.SUCCESS if success else CliExitCode.INVALID_INVOCATION
    if machine:
        body = {
            "apiVersion": "comparo.cli/v1alpha1",
            "command": command,
            "version": installed_version(),
            "status": status,
            **(details or {}),
        }
        return CliCommandResult(
            exit_code=exit_code,
            payload_kind=CliPayloadKind.JSON,
            payload=(json.dumps(body, sort_keys=True, ensure_ascii=True) + "\n").encode(),
        )
    return CliCommandResult(
        exit_code=exit_code,
        payload_kind=CliPayloadKind.HUMAN,
        messages=(
            CliMessage(
                level=CliMessageLevel.SUCCESS if success else CliMessageLevel.ERROR,
                text=message,
            ),
        ),
    )


def publish_text(destination: Path, content: str, *, replace: bool = False) -> None:
    """Publish complete UTF-8 text, preserving existing permissions on managed updates."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".comparo-write-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "content"
        staged.write_text(content, encoding="utf-8")
        if replace:
            staged.chmod(destination.stat().st_mode & 0o777 if destination.exists() else 0o600)
            staged.replace(destination)
        else:
            os.link(staged, destination)
