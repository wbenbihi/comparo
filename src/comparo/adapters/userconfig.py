"""App-level user preferences, persisted to an XDG config file.

These are *app* settings — theme, the opt-in version check, small UI defaults —
not project settings (a project is described by its version-controlled YAML). The
file is shared by the TUI and the CLI, so it lives in an adapter below both. It is
read with the stdlib ``tomllib`` and written with a tiny emitter for our own known,
flat schema, so comparo carries no TOML-writer dependency.

Location, first match wins:

* ``$COMPARO_CONFIG_HOME`` — explicit override (used by tests)
* ``$XDG_CONFIG_HOME/comparo``
* ``~/.config/comparo``
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path

_FILENAME = "config.toml"


@dataclasses.dataclass(slots=True)
class UserConfig:
    """The persisted app preferences, with safe defaults for a fresh install."""

    #: Opt-in: check PyPI for a newer release on startup (an outside network call).
    update_check: bool = False
    #: ISO date (``YYYY-MM-DD``) of the last version check — throttles it to once a day.
    update_last_checked: str = ""
    #: The latest version PyPI last reported, so a known update survives a restart.
    update_latest_seen: str = ""
    #: The active Textual theme slug.
    theme: str = "comparo-ink"
    #: Default body-diff layout: ``unified`` or ``side-by-side``.
    diff_view: str = "unified"
    #: Ask for confirmation before ``q`` quits the app.
    confirm_quit: bool = False
    #: The tab the app opens on: explorer / run / diff / execution / report / settings.
    default_tab: str = "explorer"

    def with_(self, **changes: object) -> UserConfig:
        """Return a copy with *changes* applied (config is treated as immutable)."""
        return dataclasses.replace(self, **changes)  # type: ignore[arg-type]


def config_home() -> Path:
    """The directory comparo stores its user config in (not guaranteed to exist)."""
    override = os.environ.get("COMPARO_CONFIG_HOME")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "comparo"


def config_path() -> Path:
    """The full path to ``config.toml``."""
    return config_home() / _FILENAME


def load() -> UserConfig:
    """Read the user config, tolerating a missing or malformed file (→ defaults)."""
    path = config_path()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return UserConfig()

    def section(name: str) -> dict[str, object]:
        value = raw.get(name)
        return value if isinstance(value, dict) else {}

    updates, appearance, behavior = section("updates"), section("appearance"), section("behavior")
    defaults = UserConfig()
    return UserConfig(
        update_check=bool(updates.get("check", defaults.update_check)),
        update_last_checked=str(updates.get("last_checked", defaults.update_last_checked)),
        update_latest_seen=str(updates.get("latest_seen", defaults.update_latest_seen)),
        theme=str(appearance.get("theme", defaults.theme)),
        diff_view=str(appearance.get("diff_view", defaults.diff_view)),
        confirm_quit=bool(behavior.get("confirm_quit", defaults.confirm_quit)),
        default_tab=str(behavior.get("default_tab", defaults.default_tab)),
    )


def save(config: UserConfig) -> Path:
    """Persist *config* to ``config.toml`` (creating the directory), return its path."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    sections: dict[str, dict[str, object]] = {
        "updates": {
            "check": config.update_check,
            "last_checked": config.update_last_checked,
            "latest_seen": config.update_latest_seen,
        },
        "appearance": {
            "theme": config.theme,
            "diff_view": config.diff_view,
        },
        "behavior": {
            "confirm_quit": config.confirm_quit,
            "default_tab": config.default_tab,
        },
    }
    # Write to a sibling temp file then atomically rename over the target, so a
    # crash mid-write can never leave a truncated / corrupt config on disk.
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(_dump_toml(sections), encoding="utf-8")
    tmp.replace(path)
    return path


def _dump_toml(sections: dict[str, dict[str, object]]) -> str:
    """Emit a flat ``[section] key = value`` TOML document for our known schema."""
    blocks: list[str] = []
    for section, values in sections.items():
        lines = [f"[{section}]"]
        lines += [f"{key} = {_toml_scalar(value)}" for key, value in values.items()]
        blocks.append("\n".join(lines))
    header = "# comparo user preferences — see `comparo` Settings tab\n\n"
    return header + "\n\n".join(blocks) + "\n"


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
