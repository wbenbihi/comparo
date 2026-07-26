"""Shared responsive and semantic design-system contracts."""

from __future__ import annotations

import msgspec
import pytest

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
