"""Shared responsive and semantic design-system contracts."""

from __future__ import annotations

import asyncio
from typing import cast

import msgspec
import pytest
from textual.app import App
from textual.widgets import ContentSwitcher

from comparo.tui.design.breakpoints import BreakpointPolicy
from comparo.tui.design.breakpoints import LayoutClass
from comparo.tui.design.breakpoints import layout_class
from comparo.tui.design.semantics import SemanticStatus
from comparo.tui.design.semantics import SemanticStyle
from comparo.tui.design.semantics import semantic_style
from comparo.tui.design.themes import BUILTIN_THEMES
from comparo.tui.design.themes import COMPARO_DARK_THEME
from comparo.tui.design.themes import ThemeDefinition
from comparo.tui.design.themes import ThemeId
from comparo.tui.design.themes import theme_definition
from comparo.tui.layout_adapter import apply_responsive_class
from comparo.tui.layout_adapter import focus_workspace
from comparo.tui.state import GlyphPreference
from comparo.tui.state import TuiPreferences
from comparo.tui.theme_adapter import apply_tui_preferences
from comparo.tui.theme_adapter import register_builtin_themes
from comparo.tui.theme_adapter import textual_theme


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (0, LayoutClass.COMPACT),
        (87, LayoutClass.COMPACT),
        (88, LayoutClass.STANDARD),
        (139, LayoutClass.STANDARD),
        (140, LayoutClass.WIDE),
        (10_000, LayoutClass.WIDE),
    ],
)
def test_default_breakpoints_are_shared_exact_boundaries(
    width: int,
    expected: LayoutClass,
) -> None:
    assert layout_class(width) is expected


def test_breakpoint_policy_rejects_invalid_shapes() -> None:
    with pytest.raises(TypeError, match="exact integers"):
        BreakpointPolicy(standard_at=True)
    with pytest.raises(ValueError, match="strictly ordered"):
        BreakpointPolicy(standard_at=88, wide_at=88)
    policy = BreakpointPolicy()
    with pytest.raises(ValueError, match="non-negative"):
        policy.classify(-1)
    with pytest.raises(ValueError, match="non-negative"):
        policy.classify(True)


def test_every_semantic_status_has_accessible_non_color_grammar() -> None:
    styles = tuple(semantic_style(status) for status in SemanticStatus)
    assert len(styles) == len(SemanticStatus)
    assert len({style.glyph for style in styles}) == len(styles)
    assert len({style.label for style in styles}) == len(styles)
    assert len({style.css_class for style in styles}) == len(styles)
    assert len({style.rank for style in styles}) == len(styles)
    assert all(style.glyph and style.label for style in styles)


def test_semantic_style_contract_rejects_untyped_or_invalid_values() -> None:
    with pytest.raises(TypeError, match="SemanticStatus"):
        semantic_style("success")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded"):
        SemanticStyle(glyph="", label="bad", css_class="bad", rank=0)
    with pytest.raises(ValueError, match="non-negative"):
        msgspec.structs.replace(semantic_style(SemanticStatus.SUCCESS), rank=-1)


def test_builtin_theme_definitions_are_extensible_semantic_palettes() -> None:
    assert theme_definition(COMPARO_DARK_THEME.theme_id) is COMPARO_DARK_THEME
    assert theme_definition(ThemeId(value="future-community-theme")) is None
    assert len({item.theme_id for item in BUILTIN_THEMES}) == len(BUILTIN_THEMES)
    with pytest.raises(TypeError, match="ThemeId"):
        theme_definition("comparo-dark")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="six-digit"):
        msgspec.structs.replace(COMPARO_DARK_THEME, border="blue")
    with pytest.raises(TypeError, match="darkness"):
        ThemeDefinition(
            **{
                **msgspec.to_builtins(COMPARO_DARK_THEME),
                "theme_id": COMPARO_DARK_THEME.theme_id,
                "dark": 1,
            }
        )
    with pytest.raises(TypeError, match="ID"):
        msgspec.structs.replace(COMPARO_DARK_THEME, theme_id="comparo-dark")
    for label in ("", "x" * 65, 1):
        with pytest.raises(ValueError, match="label"):
            msgspec.structs.replace(COMPARO_DARK_THEME, label=label)
    for color in (1, "#123", "1234567", "#zzzzzz"):
        with pytest.raises(ValueError, match="six-digit"):
            msgspec.structs.replace(COMPARO_DARK_THEME, background=color)
    with pytest.raises(TypeError, match="ThemeDefinition"):
        textual_theme(cast("ThemeDefinition", object()))


def test_textual_theme_preferences_and_shared_layout_adapter_edges() -> None:
    async def scenario() -> None:
        app = App[None]()
        register_builtin_themes(app)
        assert textual_theme(COMPARO_DARK_THEME).name == "comparo-dark"
        async with app.run_test(size=(100, 30)):
            apply_tui_preferences(
                app,
                TuiPreferences(
                    theme=ThemeId(value="system"),
                    dense=True,
                    mouse=False,
                    glyphs=GlyphPreference.ASCII,
                    reduced_motion=True,
                ),
                "comparo-dark",
            )
            assert app.theme == "comparo-dark"
            assert app.screen.has_class("glyphs-ascii")
            apply_tui_preferences(
                app,
                TuiPreferences(
                    theme=ThemeId(value="missing-theme"),
                    glyphs=GlyphPreference.UNICODE,
                ),
                "comparo-light",
            )
            assert app.theme == "comparo-light"
            assert app.screen.has_class("glyphs-unicode")

        with pytest.raises(TypeError, match="App"):
            register_builtin_themes(cast("App[None]", object()))
        with pytest.raises(TypeError, match="App"):
            apply_tui_preferences(cast("App[None]", object()), TuiPreferences(), "comparo-dark")
        with pytest.raises(TypeError, match="TuiPreferences"):
            apply_tui_preferences(app, cast("TuiPreferences", object()), "comparo-dark")
        with pytest.raises(TypeError, match="ContentSwitcher"):
            apply_responsive_class(cast("ContentSwitcher", object()), 100)
        with pytest.raises(TypeError, match="Widget"):
            focus_workspace(cast("ContentSwitcher", object()), None)

    asyncio.run(scenario())
