"""Version-matched documentation and an installable Comparo authoring skill."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from .contracts import CliCommandResult
from .contracts import CliPayloadKind
from .onboarding import document
from .onboarding import installed_version
from .onboarding import outcome
from .onboarding import publish_text

TOPICS: tuple[tuple[str, str, str], ...] = (
    ("quickstart", "Quickstart", "Initialize, configure, validate, and compare two APIs."),
    (
        "configuration",
        "Project layout",
        "Single-file and split layouts, discovery, and references.",
    ),
    ("requests", "Requests", "Define HTTP methods, endpoints, inputs, and bodies."),
    ("authentication", "Authentication", "Use environment-backed bearer credentials."),
    ("assertions", "Assertions", "Check each response against explicit expectations."),
    ("comparisons", "Comparisons", "Compare environments and interpret gate outcomes."),
    ("completion", "Shell completion", "Install, diagnose, refresh, and remove completions."),
    ("agents", "AI authoring", "Install and use the Comparo skill in your API repository."),
    (
        "limitations",
        "Executable limitations",
        "Distinguish accepted syntax from runnable features.",
    ),
)


def topic_markdown(topic: str) -> str:
    """Read an allowlisted topic from the installed package."""
    if topic not in {item[0] for item in TOPICS}:
        raise ValueError("unknown documentation topic")
    return files("comparo.cli").joinpath("content", "docs", f"{topic}.md").read_text("utf-8")


def read_documentation(topic: str | None, output_format: str) -> CliCommandResult:
    """Return a topic or index in raw Markdown, terminal Markdown, or JSON."""
    entries = [
        {"topic": slug, "title": title, "summary": summary} for slug, title, summary in TOPICS
    ]
    if topic is not None and topic not in {item[0] for item in TOPICS}:
        return outcome(
            "docs",
            "unknown-topic",
            "Unknown documentation topic. Run comparo docs for the index.",
            machine=output_format == "json",
            success=False,
        )
    body = (
        topic_markdown(topic)
        if topic is not None
        else "# Comparo documentation\n\n"
        f"Installed version: **{installed_version()}**\n\n"
        "Read a topic with `comparo docs TOPIC`. These docs are bundled with this version.\n\n"
        + "\n".join(f"- **{slug}** — {summary}" for slug, _title, summary in TOPICS)
        + "\n\nUse `--format markdown` for source Markdown "
        "or `--format json` for structured output.\n"
    )
    if output_format == "json":
        data: dict[str, object] = {
            "apiVersion": "comparo.cli/v1alpha1",
            "command": "docs",
            "version": installed_version(),
            "topic": topic,
        }
        if topic is None:
            data["topics"] = entries
        else:
            data["title"] = next(title for slug, title, _summary in TOPICS if slug == topic)
            data["markdown"] = body
        return document(
            json.dumps(data, ensure_ascii=True, sort_keys=True) + "\n", CliPayloadKind.JSON
        )
    return document(
        body,
        CliPayloadKind.MARKDOWN if output_format == "markdown" else CliPayloadKind.RICH_MARKDOWN,
    )


def skill_markdown() -> str:
    """Read the distributable authoring skill without requiring an installed agent."""
    return (
        files("comparo.cli").joinpath("content", "skills", "comparo", "SKILL.md").read_text("utf-8")
    )


def install_skill(directory: Path, *, user: bool, machine: bool) -> CliCommandResult:
    """Install a repo or personal skill without overwriting an existing different skill."""
    base = Path.home() if user else directory
    destination = base / ".agents" / "skills" / "comparo" / "SKILL.md"
    content = skill_markdown()
    try:
        if destination.is_symlink():
            raise FileExistsError
        if destination.is_file() and destination.read_text("utf-8") == content:
            status = "unchanged"
        else:
            publish_text(destination, content)
            status = "installed"
    except FileExistsError:
        return outcome(
            "skill install",
            "already-exists",
            "A different Comparo skill already exists; review it before replacing it.",
            machine=machine,
            success=False,
        )
    except (OSError, UnicodeError):
        return outcome(
            "skill install",
            "write-error",
            "Could not install the skill. Check the destination and permissions.",
            machine=machine,
            success=False,
        )
    return outcome(
        "skill install",
        status,
        f"Comparo skill {status} at {str(destination)!r}.\n"
        "In Codex, ask: Use $comparo to configure comparisons for this repository.\n"
        "If it is not discovered, restart your agent session.",
        machine=machine,
        details={"path": str(destination)},
    )
