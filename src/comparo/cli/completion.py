"""Install and inspect shell completion using narrowly owned startup-file blocks."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from .completion_scripts import MARKER
from .contracts import CliCommandResult
from .onboarding import outcome
from .onboarding import publish_text

_START = "# >>> comparo completion >>>"
_END = "# <<< comparo completion <<<"


@dataclass(frozen=True)
class CompletionPaths:
    """User-owned script and startup-file paths for one selected shell."""

    shell: str
    script: Path
    startup: Path | None


def completion_paths(shell: str | None) -> CompletionPaths:
    """Resolve explicit or login-shell paths, honoring absolute XDG and ZDOTDIR values."""
    selected = shell or Path(os.environ.get("SHELL", "")).name
    home = Path.home()
    data = _absolute_environment("XDG_DATA_HOME", home / ".local" / "share")
    config = _absolute_environment("XDG_CONFIG_HOME", home / ".config")
    if selected == "zsh":
        startup = _absolute_environment("ZDOTDIR", home) / ".zshrc"
        return CompletionPaths(selected, data / "comparo" / "completions" / "comparo.zsh", startup)
    if selected == "bash":
        return CompletionPaths(
            selected, data / "bash-completion" / "completions" / "comparo", home / ".bashrc"
        )
    if selected == "fish":
        return CompletionPaths(selected, config / "fish" / "completions" / "comparo.fish", None)
    raise ValueError("unsupported shell")


def _absolute_environment(name: str, fallback: Path) -> Path:
    """Ignore unset or relative XDG-style directories."""
    value = os.environ.get(name)
    return Path(value) if value and Path(value).is_absolute() else fallback


def _startup_block(paths: CompletionPaths) -> str:
    """Build quoted shell code that initializes zsh only when needed."""
    source = f"[ -r {shlex.quote(str(paths.script))} ] && . {shlex.quote(str(paths.script))}"
    lines = [_START]
    if paths.shell == "zsh":
        lines.extend(
            ["if ! (( $+functions[compdef] )); then", "  autoload -Uz compinit && compinit", "fi"]
        )
    lines.extend([source, _END])
    return "\n".join(lines) + "\n"


def _read(path: Path | None) -> str:
    """Read existing UTF-8 configuration; absence is an empty configuration."""
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("dangling configuration symlink") from None
        return ""


def _block_span(content: str) -> tuple[int, int] | None:
    """Locate exactly one complete marked block; reject ambiguous ownership."""
    lines = content.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == _START]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == _END]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValueError("incomplete or duplicate managed blocks")
    return sum(map(len, lines[: starts[0]])), sum(map(len, lines[: ends[0] + 1]))


def _updated_startup(content: str, block: str) -> str:
    """Replace or remove only the owned block; otherwise append one complete block."""
    span = _block_span(content)
    if span is not None:
        return content[: span[0]] + block + content[span[1] :]
    if not block:
        return content
    return content + ("\n" if content and not content.endswith("\n") else "") + block


def manage_completion(
    action: str,
    shell: str | None,
    *,
    script: str | None = None,
    machine: bool = False,
) -> CliCommandResult:
    """Install, diagnose, or uninstall completion without replacing unowned scripts."""
    try:
        paths = completion_paths(shell)
        existing = _read(paths.script)
        startup = _read(paths.startup)
        block = _startup_block(paths)
        span = _block_span(startup)
        owned = existing.startswith(MARKER + "\n")
        configured = paths.startup is None or (
            span is not None and startup[span[0] : span[1]] == block
        )
        if action == "status":
            return _status(paths, owned=owned, configured=configured, machine=machine)
        if (paths.script.exists() or paths.script.is_symlink()) and (
            not owned or paths.script.is_symlink()
        ):
            raise ValueError("unowned completion script")
        if action == "install":
            if script is None or not script.startswith(MARKER + "\n"):
                raise ValueError("missing generated script")
            _install(paths, script, existing, startup, block)
            return outcome(
                "completion install",
                "installed",
                f"Installed {paths.shell} completion at {str(paths.script)!r}.\n"
                f"Restart {paths.shell} to load it. "
                f"Run comparo completion status {paths.shell} to check setup.",
                machine=machine,
                details=_details(paths),
            )
        if action != "uninstall":
            raise ValueError("unknown completion action")
        updated = _updated_startup(startup, "")
        if paths.startup is not None and updated != startup:
            publish_text(paths.startup.resolve(), updated, replace=True)
        if owned:
            paths.script.unlink()
        return outcome(
            "completion uninstall",
            "removed",
            "Removed Comparo completion files and its startup block. "
            "Restart your shell to unload active functions.",
            machine=machine,
            details=_details(paths),
        )
    except ValueError:
        return outcome(
            f"completion {action}",
            "configuration-conflict",
            "Cannot manage completion: select bash, zsh, or fish and check for an existing "
            "unowned script or incomplete Comparo startup block.",
            machine=machine,
            success=False,
        )
    except (OSError, UnicodeError):
        return outcome(
            f"completion {action}",
            "io-error",
            "Could not read or update shell completion. "
            "Check file permissions and configuration paths.",
            machine=machine,
            success=False,
        )


def _install(paths: CompletionPaths, script: str, existing: str, startup: str, block: str) -> None:
    """Install the script and roll it back if startup-file publication fails."""
    updated = _updated_startup(startup, block)
    existed = paths.script.exists()
    publish_text(paths.script, script, replace=existed)
    try:
        if paths.startup is not None and updated != startup:
            publish_text(paths.startup.resolve(), updated, replace=True)
    except OSError:
        if existed:
            publish_text(paths.script, existing, replace=True)
        else:
            paths.script.unlink()
        raise


def _details(paths: CompletionPaths) -> dict[str, object]:
    """Describe installed paths for automation without serializing shell contents."""
    return {
        "shell": paths.shell,
        "script": str(paths.script),
        "startup": str(paths.startup) if paths.startup is not None else None,
    }


def _status(
    paths: CompletionPaths, *, owned: bool, configured: bool, machine: bool
) -> CliCommandResult:
    """Report on-disk readiness without claiming another shell has loaded the script."""
    ready = owned and configured
    status = "ready" if ready else "incomplete"
    message = (
        f"{paths.shell} completion is configured on disk. Restart your shell if Tab is not working."
        if ready
        else f"{paths.shell} completion needs setup. Run: comparo completion install {paths.shell}"
    )
    return outcome(
        "completion status",
        status,
        message,
        machine=machine,
        success=ready,
        details={**_details(paths), "scriptInstalled": owned, "startupConfigured": configured},
    )
