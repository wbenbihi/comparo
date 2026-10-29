"""Strips: stage breadcrumb, run strip, index strip — one-line components."""

from __future__ import annotations

from rich.text import Text

from comparo.tuibis.theme import style
from comparo.tuibis.viewmodels import IndexItemVM
from comparo.tuibis.viewmodels import RunStripVM
from comparo.tuibis.viewmodels import StageVM
from comparo.tuibis.widgets.sections import SplitRow
from comparo.tuibis.widgets.sections import TextRow


class StageBreadcrumb(TextRow):
    """`└ scope ▸ plan ▸ run ▸ analyse` — subordinate to the tab bar."""

    def __init__(self, stages: tuple[StageVM, ...]) -> None:
        """Render the breadcrumb from stage states."""
        text = Text(" " * 13)
        text.append("└ ", style=style("dim"))
        for index, stage in enumerate(stages):
            if index:
                text.append("  ▸  ", style=style("dim"))
            if stage.state == "done":
                text.append("+ ", style=style("ok"))
                text.append(stage.name, style=style("muted"))
            elif stage.state == "active":
                text.append("▌", style=style("baseline"))
                text.append(stage.name, style=style("bright"))
            elif stage.state == "run":
                text.append("> ", style=style("baseline"))
                text.append(stage.name, style=style("baseline"))
            elif stage.state == "fail":
                text.append("x ", style=style("err"))
                text.append(stage.name, style=style("err"))
            else:
                text.append(stage.name, style=style("dim"))
        super().__init__(text, classes="stage-breadcrumb")


class RunStrip(SplitRow):
    """The slim run header from the validated run-results lineage."""

    def __init__(self, vm: RunStripVM) -> None:
        """Render run identity, lanes, verdict tallies, and save state."""
        left = Text(" ")
        left.append("▤ ", style=style("glyph"))
        left.append("RUN ", style=style("label"))
        left.append(vm.run_id, style=style("bright"))
        left.append("  ·  ", style=style("dim"))
        left.append("▶ ", style=style("glyph"))
        left.append(vm.profile, style=style("fg"))
        left.append("  ·  ", style=style("dim"))
        left.append(vm.baseline, style=style("baseline"))
        left.append(" ⇄ ", style=style("muted"))
        left.append(vm.candidate, style=style("candidate"))
        left.append("  ·  ", style=style("dim"))
        verdict_style = "status.failed" if vm.verdict == "failed" else "status.passed"
        glyph_char = "x FAILED" if vm.verdict == "failed" else "+ PASSED"
        left.append(glyph_char, style=f"bold {style(verdict_style)}")
        left.append(f"  {vm.tallies}", style=style("muted"))
        right = Text()
        if vm.replay:
            right.append(" REPLAY ", style=style("chip.warn"))
            right.append("  read-only · ", style=style("muted"))
            right.append("esc", style=style("fg"))
            right.append(" back to reports ", style=style("muted"))
        else:
            right.append(vm.right, style=style("muted"))
            right.append(" ")
        super().__init__(left, right, classes="run-strip")


class IndexStrip(SplitRow):
    """The analyse index chips with counts; `i` cycles."""

    def __init__(self, items: tuple[IndexItemVM, ...], active: str) -> None:
        """Render index chips, highlighting the active one."""
        self._items = items
        left = self._line(items, active)
        right = Text()
        right.append("i", style=style("fg"))
        right.append(" next index · ", style=style("muted"))
        right.append("/", style=style("fg"))
        right.append(" filter ", style=style("muted"))
        super().__init__(left, right, classes="index-strip")

    @staticmethod
    def _line(items: tuple[IndexItemVM, ...], active: str) -> Text:
        """Build the chip row."""
        left = Text(" ")
        for item in items:
            if item.key == active:
                left.append(f" {item.label} ", style=style("chip.active"))
            else:
                left.append(item.label, style=style("muted"))
            left.append("    ")
        return left

    def set_active(self, active: str) -> None:
        """Re-highlight the active index chip."""
        self.set_left(self._line(self._items, active))


class FacetChips(TextRow):
    """`ALL REQUEST RESPONSE HEADERS EVENTS RAW` facet row for INSPECT."""

    def __init__(self, facets: tuple[str, ...], active: str, note: str = "") -> None:
        """Render facet chips with the active one highlighted."""
        text = Text(" ")
        for facet in facets:
            if facet == active:
                text.append(f" {facet} ", style=style("chip.active"))
            else:
                text.append(facet, style=style("muted"))
            text.append("   ")
        if note:
            text.append(note, style=style("dim"))
        super().__init__(text)
