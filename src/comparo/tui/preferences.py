"""Validated, user-owned terminal preferences with explicit atomic persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

# Command groups organize the editor; keys remain unique across nested workspaces.
SHORTCUTS: dict[str, tuple[str, str, str]] = {
    **{
        f"workspace.{name.lower()}": (name.title(), str(index), "global")
        for index, name in enumerate(("Explore", "Launch", "Reports", "Settings", "Debug"), 1)
    },
    "app.quit": ("Quit", "q", "global"),
    "launch.prepare": ("Prepare plan", "ctrl+r", "launch"),
    "launch.run": ("Execute reviewed plan", "ctrl+x", "launch"),
    "launch.edit": ("Edit target", "ctrl+e", "launch"),
    "launch.cancel": ("Cancel run", "ctrl+k", "launch"),
    "analyze.previous": ("Previous Analyze view", "left_square_bracket", "analyze"),
    "analyze.next": ("Next Analyze view", "right_square_bracket", "analyze"),
    **{
        f"analyze.{name}": (f"Analyze {name.title()}", f"alt+{index}", "analyze")
        for index, name in enumerate(("issues", "requests", "rules", "fields", "timeline"), 1)
    },
}


@dataclass(frozen=True)
class Preferences:
    """Presentation choices only; no project, execution, or policy authority."""

    theme: str = "comparo-dark"
    density: str = "comfortable"
    workspace: str = "EXPLORE"
    mouse: bool = True
    keymap: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject unsupported values and overlapping command bindings."""
        if self.theme not in {"comparo-dark", "comparo-light"}:
            raise ValueError("Choose a registered Comparo theme.")
        if self.density not in {"comfortable", "compact"}:
            raise ValueError("Choose comfortable or compact density.")
        if self.workspace not in {"EXPLORE", "LAUNCH", "REPORTS", "SETTINGS", "DEBUG"}:
            raise ValueError("Choose an available startup workspace.")
        if type(self.mouse) is not bool or type(self.keymap) is not dict:
            raise ValueError("Invalid interaction preferences.")
        for command, key in self.keymap.items():
            if command not in SHORTCUTS or type(key) is not str:
                raise ValueError("Unknown shortcut command.")
            if key != SHORTCUTS[command][1] and not re.fullmatch(
                r"f(?:[1-9]|1[0-9]|2[0-4])|ctrl\+alt\+[a-z]", key
            ):
                raise ValueError("Use the default key, F1-F24, or Ctrl+Alt+letter.")
        resolved = [self.keymap.get(name, default) for name, (_, default, _) in SHORTCUTS.items()]
        if len(set(resolved)) != len(resolved):
            raise ValueError("Shortcut conflicts with another command in this context.")


def preferences_path() -> Path:
    """Select the user's config directory without creating it at startup."""
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "comparo" / "tui.json"


def load_preferences(path: Path) -> tuple[Preferences, str]:
    """Read bounded settings; invalid files fall back visibly without being overwritten."""
    try:
        with path.open("rb") as source:
            content = source.read(65537)
        if len(content) > 65536:
            raise ValueError("preferences too large")
        value = json.loads(content)
        if type(value) is not dict or set(value) != {
            "theme",
            "density",
            "workspace",
            "mouse",
            "keymap",
        }:
            raise ValueError("invalid preferences")
        return Preferences(**value), ""
    except FileNotFoundError:
        return Preferences(), ""
    except (OSError, ValueError, TypeError):
        return Preferences(), "Saved preferences could not be loaded. Defaults are active."


def save_preferences(path: Path, preferences: Preferences) -> None:
    """Replace only the explicit preference file, keeping a failed save atomic."""
    content = json.dumps(asdict(preferences), indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
