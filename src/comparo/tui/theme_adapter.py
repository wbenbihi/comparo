"""Register framework-neutral Comparo theme definitions with Textual."""

from __future__ import annotations

from textual.app import App
from textual.theme import Theme

from comparo.tui.commands import textual_keymap
from comparo.tui.design.themes import BUILTIN_THEMES
from comparo.tui.design.themes import ThemeDefinition
from comparo.tui.state import GlyphPreference
from comparo.tui.state import TuiPreferences


def textual_theme(definition: ThemeDefinition) -> Theme:
    """Translate one semantic definition into Textual's native theme model."""
    if type(definition) is not ThemeDefinition:
        raise TypeError("Textual theme conversion requires ThemeDefinition")
    return Theme(
        name=definition.theme_id.value,
        primary=definition.primary,
        secondary=definition.muted,
        warning=definition.advisory,
        error=definition.failure,
        success=definition.success,
        accent=definition.primary,
        foreground=definition.foreground,
        background=definition.background,
        surface=definition.surface,
        panel=definition.panel,
        dark=definition.dark,
        variables={
            "comparo-border": definition.border,
            "comparo-muted": definition.muted,
        },
    )


def register_builtin_themes[ReturnType](app: App[ReturnType]) -> None:
    """Register every built-in theme through Textual's supported API."""
    if not isinstance(app, App):
        raise TypeError("theme registration requires a Textual App")
    for definition in BUILTIN_THEMES:
        app.register_theme(textual_theme(definition))


def apply_tui_preferences[ReturnType](
    app: App[ReturnType],
    preferences: TuiPreferences,
    system_theme: str,
) -> None:
    """Apply native key-map, registered theme, density, and mouse presentation state."""
    if not isinstance(app, App):
        raise TypeError("preference application requires a Textual App")
    if type(preferences) is not TuiPreferences:
        raise TypeError("preference application requires TuiPreferences")
    app.set_keymap(textual_keymap(preferences.keymap))
    requested = system_theme if preferences.theme.value == "system" else preferences.theme.value
    if requested in app.available_themes:
        app.theme = requested
    elif system_theme in app.available_themes:
        app.theme = system_theme
    app.screen.set_class(preferences.dense, "density-dense")
    app.screen.set_class(preferences.mouse, "mouse-next-launch-enabled")
    app.screen.set_class(not preferences.mouse, "mouse-next-launch-disabled")
    app.screen.set_class(preferences.reduced_motion, "reduced-motion")
    app.screen.set_class(preferences.glyphs is GlyphPreference.ASCII, "glyphs-ascii")
    app.screen.set_class(preferences.glyphs is GlyphPreference.UNICODE, "glyphs-unicode")


__all__ = ("apply_tui_preferences", "register_builtin_themes", "textual_theme")
