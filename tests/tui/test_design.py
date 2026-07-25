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
