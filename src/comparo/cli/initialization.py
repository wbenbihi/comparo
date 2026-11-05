"""Offline starter-project creation with exclusive publication of the manifest."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import CliCommandResult
from .contracts import CliExitCode
from .contracts import CliMessage
from .contracts import CliMessageLevel
from .contracts import CliOutputMode
from .contracts import CliPayloadKind
from .contracts import InitRequest

_STARTER = """# Edit the two baseUrl values and the /health endpoint to match your APIs.
# Validate without HTTP: comparo validate
# Preview the comparison: comparo plan
# Run the comparison: comparo diff
apiVersion: comparo/v1alpha1
kind: Project
metadata:
  id: my-project
  name: My API comparison
spec:
  # Additional YAML resources can later be placed in this directory.
  data: .comparo
  defaults:
    subjects:
      - $use: Request/health
    baseline:
      $use: baseline
    candidates:
      - $use: candidate
    assertions:
      - $use: http-success
    diffs:
      - $use: stable-status
    gate:
      $use: strict
---
apiVersion: comparo/v1alpha1
kind: Environment
metadata:
  id: baseline
  name: Baseline
spec:
  baseUrl: http://localhost:8000
---
apiVersion: comparo/v1alpha1
kind: Environment
metadata:
  id: candidate
  name: Candidate
spec:
  baseUrl: http://localhost:8001
---
apiVersion: comparo/v1alpha1
kind: Request
metadata:
  id: health
  name: Health check
spec:
  request:
    method: GET
    endpoint: /health
    headers:
      Accept: application/json
---
apiVersion: comparo/v1alpha1
kind: AssertionProfile
metadata:
  id: http-success
  name: HTTP success
spec:
  rules:
    - id: status-is-200
      selector: response.status
      operator:
        name: equals
        expected: 200
      severity: blocking
      quantifier: one
      completeness: complete
---
apiVersion: comparo/v1alpha1
kind: DiffProfile
metadata:
  id: stable-status
  name: Stable status
spec:
  rules:
    - id: status-is-stable
      selector: response.status
      comparator:
        name: exact
      severity: blocking
      completeness: complete
---
apiVersion: comparo/v1alpha1
kind: GateProfile
metadata:
  id: strict
  name: Strict gate
spec: {}
---
apiVersion: comparo/v1alpha1
kind: ExecutionProfile
metadata:
  id: compare-health
  name: Compare health
spec:
  subjects:
    - $use: Request/health
  baseline:
    $use: baseline
  candidates:
    - $use: candidate
"""


def _publish_split(directory: Path, staging: Path) -> None:
    """Publish split resources before the manifest, rolling back only our new paths."""
    manifest = directory / "comparo.yaml"
    if manifest.exists() or manifest.is_symlink():
        raise FileExistsError
    data = directory / ".comparo"
    data.mkdir()  # Existing directories and symlinks belong to the user.
    created_files: list[Path] = []
    created_directories = [data]
    try:
        documents = _STARTER.split("\n---\n")
        contents = {
            "environments.yaml": "\n---\n".join(documents[1:3]),
            "requests/health.yaml": documents[3],
            "profiles/checks.yaml": "\n---\n".join(documents[4:]),
        }
        for name, content in contents.items():
            destination = data / name
            if destination.parent != data and destination.parent not in created_directories:
                destination.parent.mkdir()
                created_directories.append(destination.parent)
            source = staging / destination.name
            source.write_text(content.rstrip() + "\n", encoding="utf-8")
            os.link(source, destination)
            created_files.append(destination)
        source = staging / "comparo.yaml"
        source.write_text(documents[0].rstrip() + "\n", encoding="utf-8")
        os.link(source, manifest)
    except OSError:
        for created in reversed(created_files):
            with suppress(OSError):
                created.unlink()
        for created in reversed(created_directories):
            with suppress(OSError):
                created.rmdir()
        raise


def initialize_project(request: InitRequest) -> CliCommandResult:
    """Publish a complete starter manifest, refusing files, directories, and symlinks."""
    try:
        request.directory.mkdir(parents=True, exist_ok=True)
        # A hard link publishes the complete file atomically and fails if the destination exists.
        with TemporaryDirectory(prefix=".comparo-init-", dir=request.directory) as staging:
            if request.layout == "split":
                _publish_split(request.directory, Path(staging))
            else:
                manifest = Path(staging) / "comparo.yaml"
                manifest.write_text(_STARTER, encoding="utf-8")
                os.link(manifest, request.directory / "comparo.yaml")
    except FileExistsError:
        return _result(
            request,
            "already-exists",
            "Initialization refused: the destination already exists. "
            "Choose a directory without comparo.yaml (or .comparo for a split project).",
        )
    except OSError:
        return _result(
            request,
            "write-error",
            "Could not create the starter project. Check the destination and write permissions.",
        )
    created = (
        "comparo.yaml and .comparo/ resources" if request.layout == "split" else "comparo.yaml"
    )
    return _result(
        request,
        "created",
        f"Created {created} in the project directory.\n"
        "In that directory, edit the baseline and candidate baseUrl values and the /health "
        "endpoint in the generated YAML files.\n"
        "Then run: comparo validate\n"
        "Preview: comparo plan\n"
        "Compare: comparo diff\n"
        "Open the terminal UI: comparo",
    )


def _result(request: InitRequest, status: str, message: str) -> CliCommandResult:
    """Return a safe human message or a deterministic machine result."""
    success = status == "created"
    exit_code = CliExitCode.SUCCESS if success else CliExitCode.INVALID_INVOCATION
    if request.output is CliOutputMode.JSON:
        payload = {
            "apiVersion": "comparo.cli/v1alpha1",
            "command": "init",
            "status": status,
        }
        return CliCommandResult(
            exit_code=exit_code,
            payload_kind=CliPayloadKind.JSON,
            payload=(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
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
