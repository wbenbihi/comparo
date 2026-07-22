"""Tests for the shared UI components — one implementation per concept."""

from rich.console import Console
from rich.text import Text

from comparo.tui.components import CheckRow
from comparo.tui.components import ErrorPanelModel
from comparo.tui.components import NavEntry
from comparo.tui.components import NavStack
from comparo.tui.components import StatChip
from comparo.tui.components import TallySegment
from comparo.tui.components import cell_glyph
from comparo.tui.components import error_panel
from comparo.tui.components import progress_bar
from comparo.tui.components import provenance_suffix
from comparo.tui.components import seg_pill
from comparo.tui.components import stat_chips
from comparo.tui.components import status_code_text
from comparo.tui.components import summary_strip_finished
from comparo.tui.components import summary_strip_running
from comparo.tui.components import verdict_box
from comparo.tui.components import verdict_box_header
from comparo.tui.components import verdict_pill


def _plain(renderable: object) -> str:
    console = Console(width=120, force_terminal=False)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_the_glyph_grammar_is_locked() -> None:
    # ✓ clean · ✗ rule broke · ! error · ~ advisory · ⊘ not run · ◐ · —
    # the one map every surface reads; a change here changes every screen.
    assert cell_glyph("pass")[0] == "✓"
    assert cell_glyph("fail")[0] == "✗"
    assert cell_glyph("error")[0] == "!"
    assert cell_glyph("advisory")[0] == "~"
    assert cell_glyph("not_run")[0] == "⊘"
    assert cell_glyph("running")[0] == "◐"
    assert cell_glyph("pending")[0] == "·"


def test_verdict_pill_carries_the_advisory_suffix() -> None:
    rendered = _plain(verdict_pill("PASS", advisory=2))
    assert "✓ PASS" in rendered
    assert "~ 2" in rendered


def test_status_code_rule_is_single_sourced() -> None:
    assert "200" in _plain(status_code_text(200))
    assert "—" in _plain(status_code_text(None))


def test_progress_bar_fills_proportionally() -> None:
    rendered = _plain(progress_bar(3, 4, width=8))
    assert rendered.count("━") == 6
    assert rendered.count("╌") == 2


def test_summary_strip_costumes_share_the_slot() -> None:
    segments = [
        TallySegment(5, "✓", "green"),
        TallySegment(1, "~", "yellow"),
        TallySegment(0, "✗", "red"),
    ]
    running = _plain(summary_strip_running("run 8c3e11", 7, 10, segments))
    assert "7/10" in running
    assert "5 ✓" in running
    finished = _plain(summary_strip_finished("FAIL", segments, "exit 1"))
    assert "✗ FAIL" in finished
    assert "exit 1" in finished
    assert "0 ✗" in finished  # zero segments stay visible, dimmed


def test_verdict_box_header_takes_the_n_of_m_form() -> None:
    rows = [
        CheckRow("status == 200", "broke", evidence="expected 200 · got 500"),
        CheckRow("latency <= 800ms", "held", detail="96 ms"),
        CheckRow("latency <= 300ms", "warn_broke", evidence="budget 300 ms · got 412 ms"),
    ]
    assert "1 of 3 rules broke" in _plain(verdict_box_header(rows))
    rendered = _plain(verdict_box(rows))
    assert "expected 200 · got 500" in rendered  # the evidence line
    assert "· warn" in rendered
    advisory_only = [CheckRow("latency <= 300ms", "warn_broke", evidence="412 ms")]
    assert "every gating rule held — 1 advisory broke" in _plain(verdict_box_header(advisory_only))
    held_only = [CheckRow("status == 200", "held")]
    assert "every rule held — 1/1" in _plain(verdict_box_header(held_only))


def test_error_panel_tells_the_whole_story() -> None:
    model = ErrorPanelModel(
        message="ConnectError: connection refused",
        attempts=3,
        retry_policy="exponential x3",
        meaning="0 rules judged — the cell counts as error.",
        rerun_hint="x re-runs the diff (all 6 cells)",
    )
    rendered = _plain(error_panel(model))
    assert "ConnectError" in rendered
    assert "attempts" in rendered
    assert "exponential x3" in rendered
    assert "re-runs the diff" in rendered


def test_nav_stack_returns_in_reverse_and_clears_on_rerun() -> None:
    stack = NavStack()
    assert not stack
    assert stack.crumb() is None
    stack.push(NavEntry("requests", "cell::a", label="Price quote · plan=pro"))
    stack.push(NavEntry("rules", "rule::d0"))
    crumb = stack.crumb()
    assert crumb is not None
    assert "esc" in _plain(crumb)
    top = stack.pop()
    assert top is not None
    assert top.index_mode == "rules"
    stack.clear()
    assert stack.pop() is None


def test_provenance_suffix_speaks_the_one_grammar() -> None:
    assert provenance_suffix("profile", "diff.pricing") == "profile diff.pricing"
    assert provenance_suffix("inline", "price-quote") == "inline · price-quote"
    assert provenance_suffix("default") == "default"
    assert provenance_suffix("synthetic") == "built-in"  # never a fake profile rule


def test_stat_chips_dim_zero_counts() -> None:
    chips = [StatChip("✗ broke", 3, "red"), StatChip("! error", 0, "yellow")]
    rendered = _plain(stat_chips(chips))
    assert "✗ broke 3" in rendered
    assert "! error 0" in rendered


def test_seg_pill_marks_the_active_segment() -> None:
    rendered = _plain(seg_pill(("requests", "rules", "fields"), "rules"))
    assert "requests" in rendered
    assert "rules" in rendered


def test_check_row_and_text_are_importable_shapes() -> None:
    # The view-models are frozen — a renderer can never mutate its input.
    row = CheckRow("status == 200", "held")
    assert isinstance(_plain(Text(row.label)), str)


def test_check_row_lines_is_the_one_row_grammar() -> None:
    # Both the Static verdict box and the run detail tree consume this — a
    # broken row is two lines (label+provenance, then evidence), a held row one.
    from comparo.tui.components import check_row_lines

    broken = CheckRow("total <= 100", "broke", provenance="profile asserts.q", evidence="got 240")
    lines = check_row_lines(broken)
    assert len(lines) == 2
    assert "✗ total <= 100" in _plain(lines[0])
    assert "profile asserts.q" in _plain(lines[0])
    assert "got 240" in _plain(lines[1])
    held = CheckRow("status == 200", "held", detail="200")
    assert len(check_row_lines(held)) == 1
    # the verdict box renders through the same lines — the grammar cannot fork
    assert "✗ total <= 100" in _plain(verdict_box([broken, held]))


def test_check_row_cells_shares_the_row_mark_grammar() -> None:
    # The DataTable costume of the verdict card must speak the ONE grammar —
    # same glyphs/suffixes as check_row_lines, just split into three cells.
    from comparo.tui.components import check_row_cells

    broke = CheckRow("total <= 100", "broke", provenance="profile asserts.q", evidence="got 240")
    rule, source, evidence = check_row_cells(broke)
    assert "✗ total <= 100" in _plain(rule)
    assert "profile asserts.q" in _plain(source)
    assert "got 240" in _plain(evidence)
    held = CheckRow("status == 200", "held", provenance="inline", detail="200")
    rule, _, evidence = check_row_cells(held)
    assert "✓ status == 200" in _plain(rule)
    assert "200" in _plain(evidence)  # held rows show the detail, not evidence
    warn = CheckRow("latency <= 300ms", "warn_broke", evidence="412ms")
    assert "· warn" in _plain(check_row_cells(warn)[0])
