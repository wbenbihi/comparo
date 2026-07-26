"""Framework-neutral definitions for registered Comparo terminal themes."""

from __future__ import annotations

import re
from typing import Final
from typing import final

import msgspec

_THEME_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SYSTEM_THEME_ID: Final = "system"
COMPARO_DARK_THEME_ID: Final = "comparo-dark"
COMPARO_LIGHT_THEME_ID: Final = "comparo-light"


@final
class ThemeId(msgspec.Struct, frozen=True, kw_only=True):
    """An extensible registered theme identifier or the system selection."""

    value: str

    def __post_init__(self) -> None:
        """Require a bounded portable identifier suitable for persistence."""
        if (
            type(self.value) is not str
            or len(self.value) > 64
            or _THEME_ID_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("theme ID must be a bounded lowercase hyphenated identifier")


@final
class ThemeDefinition(msgspec.Struct, frozen=True, kw_only=True):
    """Semantic palette registered with Textual by the renderer adapter."""

    theme_id: ThemeId
    label: str
    dark: bool
    background: str
    surface: str
    panel: str
    foreground: str
    muted: str
    border: str
    primary: str
    success: str
    advisory: str
    failure: str

    def __post_init__(self) -> None:
        """Reject unbounded labels, non-hex colors, and untyped flags."""
        if type(self.theme_id) is not ThemeId:
            raise TypeError("theme definition ID must be typed")
        if type(self.label) is not str or not self.label or len(self.label) > 64:
            raise ValueError("theme label must be bounded and non-empty")
        if type(self.dark) is not bool:
            raise TypeError("theme darkness flag must be bool")
        colors = (
            self.background,
            self.surface,
            self.panel,
            self.foreground,
            self.muted,
            self.border,
            self.primary,
            self.success,
            self.advisory,
            self.failure,
        )
        if any(
            type(color) is not str
            or len(color) != 7
            or not color.startswith("#")
            or any(character not in "0123456789abcdefABCDEF" for character in color[1:])
            for color in colors
        ):
            raise ValueError("theme colors must be six-digit hexadecimal strings")


SYSTEM_THEME = ThemeId(value=SYSTEM_THEME_ID)
COMPARO_DARK_THEME = ThemeDefinition(
    theme_id=ThemeId(value=COMPARO_DARK_THEME_ID),
    label="Comparo Dark",
    dark=True,
    background="#0D1117",
    surface="#161B22",
    panel="#21262D",
    foreground="#E6EDF3",
    muted="#8B949E",
    border="#30363D",
    primary="#79A7FF",
    success="#56D364",
    advisory="#E3B341",
    failure="#FF7B72",
)
COMPARO_LIGHT_THEME = ThemeDefinition(
    theme_id=ThemeId(value=COMPARO_LIGHT_THEME_ID),
    label="Comparo Light",
    dark=False,
    background="#FFFFFF",
    surface="#F6F8FA",
    panel="#EAEEF2",
    foreground="#1F2328",
    muted="#656D76",
    border="#D0D7DE",
    primary="#0969DA",
    success="#1A7F37",
    advisory="#9A6700",
    failure="#CF222E",
)
BUILTIN_THEMES: Final = (COMPARO_DARK_THEME, COMPARO_LIGHT_THEME)


def theme_definition(theme_id: ThemeId) -> ThemeDefinition | None:
    """Return a built-in definition without closing future registered IDs."""
    if type(theme_id) is not ThemeId:
        raise TypeError("theme lookup requires ThemeId")
    return next((item for item in BUILTIN_THEMES if item.theme_id == theme_id), None)


__all__ = (
    "BUILTIN_THEMES",
    "COMPARO_DARK_THEME",
    "COMPARO_DARK_THEME_ID",
    "COMPARO_LIGHT_THEME",
    "COMPARO_LIGHT_THEME_ID",
    "SYSTEM_THEME",
    "SYSTEM_THEME_ID",
    "ThemeDefinition",
    "ThemeId",
    "theme_definition",
)
