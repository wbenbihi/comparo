"""Bounded offline resource-name discovery for shell completion."""

from __future__ import annotations

from pathlib import Path

from comparo.configuration.loader import ProjectLoadLimits
from comparo.configuration.loader import compile_project_path
from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.core.domain.identity import ResourceKind

from .documentation import TOPICS

_OPTIONS = {
    "--environment": ResourceKind.ENVIRONMENT,
    "--env": ResourceKind.ENVIRONMENT,
    "--baseline": ResourceKind.ENVIRONMENT,
    "--candidate": ResourceKind.ENVIRONMENT,
    "--subject": ResourceKind.REQUEST,
    "--profile": ResourceKind.EXECUTION_PROFILE,
}
_LIMITS = ProjectLoadLimits(
    maximum_sources=128,
    maximum_discovery_entries=2048,
    maximum_total_bytes=2 * 1024 * 1024,
    maximum_total_documents=512,
    maximum_total_nodes=100_000,
    maximum_resources=512,
    yaml=YamlParseLimits(
        maximum_bytes=1024 * 1024, maximum_documents=128, maximum_depth=64, maximum_nodes=50_000
    ),
)


def _joined_words(words: tuple[str, ...]) -> tuple[str, ...]:
    """Rejoin Bash's equals-sign word breaks without interpreting shell syntax."""
    joined: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word == "=" and joined and joined[-1].startswith("--"):
            joined[-1] += "="
            if index + 1 < len(words):
                joined[-1] += words[index + 1]
                index += 1
        else:
            joined.append(word)
        index += 1
    return tuple(joined)


def _project_inputs(words: tuple[str, ...]) -> tuple[Path, tuple[Path, ...]]:
    """Read preceding config/source options, supporting equals and short attached forms."""
    config = Path("comparo.yaml")
    sources: list[Path] = []
    index = 2
    while index < len(words):
        word = words[index]
        option, equals, value = word.partition("=")
        if word.startswith(("-C", "-S")) and len(word) > 2:
            option, value, equals = word[:2], word[2:], "="
        if option in {"--config", "-C", "--source", "-S"}:
            if not equals and index + 1 < len(words):
                index += 1
                value = words[index]
            if value:
                if option in {"--config", "-C"}:
                    config = Path(value)
                else:
                    sources.append(Path(value))
        index += 1
    return config, tuple(sources)


def _exec_needs_profile(words: tuple[str, ...]) -> bool:
    """Recognize the exec operand even when valued options precede it."""
    if words[1] != "exec":
        return False
    flags = {"--fail-fast", "--no-fail-fast", "--approve-mutations", "--help", "-h"}
    index = 2
    while index < len(words) - 1:
        word = words[index]
        if not word.startswith("-"):
            return False
        if (
            "=" not in word and word not in flags and not word.startswith(("-C", "-S"))
        ) or word in {"-C", "-S"}:
            index += 1
        index += 1
    return True


def candidates(raw_words: tuple[str, ...]) -> tuple[str, ...]:
    """Suggest static topics or compiled identities without resolving any values."""
    if len(raw_words) < 3 or len(raw_words) > 256 or sum(map(len, raw_words)) > 32768:
        return ()
    words = _joined_words(raw_words)
    current = words[-1]
    option = words[-2]
    prefix = current
    if current.startswith("--") and "=" in current:
        option, _, prefix = current.partition("=")
    elif current.startswith("-"):
        return ()
    if words[1] == "docs" and len(words) == 3:
        return tuple(slug for slug, _title, _summary in TOPICS if slug.startswith(prefix))
    kind = _OPTIONS.get(option)
    if kind is None and _exec_needs_profile(words):
        kind = ResourceKind.EXECUTION_PROFILE
    if kind is None or words[1] not in {"run", "diff", "exec", "plan", "render"}:
        return ()
    config, sources = _project_inputs(words[:-1])
    try:
        project = compile_project_path(
            config, additional_sources=sources, limits=_LIMITS
        ).compilation.project
    except (OSError, ValueError, RuntimeError):
        return ()
    if project is None:
        return ()
    names = {
        resource.definition.identity.resource_id.value
        for resource in project.resources
        if resource.definition.identity.kind is kind
    }
    return tuple(sorted(name for name in names if name.startswith(prefix)))[:256]
