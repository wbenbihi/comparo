"""Analyze — the results surface: cells/fields/rules indexes beside a faceted detail."""

from __future__ import annotations

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widget import Widget

from comparo.tui3 import fixtures as fx
from comparo.tui3.theme import DRIFT
from comparo.tui3.theme import style
from comparo.tui3.widgets import CursorList
from comparo.tui3.widgets import Panel
from comparo.tui3.widgets import PanelHeader
from comparo.tui3.widgets import Row
from comparo.tui3.widgets import Seg
from comparo.tui3.widgets import SplitRow
from comparo.tui3.widgets import TextRow
from comparo.tui3.widgets import fragments as fr

AN_FACETS = ("overview", "request", "response", "headers", "asserts", "events", "raw")
INDEXES = ("cells", "fields", "rules")
_LEGENDS = {
    "cells": "every call of the run — enter or click drills",
    "fields": "one row per field across cells",
    "rules": "every silencing rule, auditable",
}


def _tcols(*cols: tuple[Text, int]) -> TextRow:
    """One fixed-width table line; width 0 means no padding (last column)."""
    out = Text("  ")
    for txt, width in cols:
        cell = txt.copy()
        pad = width - cell.cell_len
        if pad > 0:
            cell.append(" " * pad)
        out.append_text(cell)
    return TextRow(out)


def _kv(label: str, *value: Text) -> TextRow:
    """Dim label + value fragments, the two-column detail row."""
    return TextRow(fr.join(fr.t(f"  {label:<10}", "dim"), *value))


def _field_diff_lines(f: fx.FieldFx, *, braces: bool = False) -> list[Widget]:
    """The git-diff hunk for one drifted field."""
    name = f.path.removeprefix("$.")
    out: list[Widget] = [TextRow(fr.dline("hunk", fr.t(f"{f.path} @@", "diff.hunk")))]
    if braces:
        out.append(TextRow(fr.dline("ctx", fr.t("  {", "diff.ctx"))))
    out.append(
        TextRow(
            fr.dline(
                "del",
                fr.t(f'  "{name}": ', "diff.del"),
                fr.t(f.b, "diff.em"),
                fr.t(",", "diff.del"),
            )
        )
    )
    out.append(
        TextRow(
            fr.dline(
                "add",
                fr.t(f'  "{name}": ', "diff.add"),
                fr.t(f.c, "diff.ema"),
                fr.t(",", "diff.add"),
            )
        )
    )
    if braces:
        out.append(TextRow(fr.dline("ctx", fr.t("  }", "diff.ctx"))))
    return out


def _side_by_side(base: Text, cand: Text) -> Widget:
    """Two small baseline/candidate panels."""
    return Horizontal(
        Panel(PanelHeader("baseline"), TextRow(base), classes="-auto"),
        Panel(PanelHeader("candidate"), TextRow(cand), classes="-auto"),
        classes="stack-auto",
    )


class _OutboundCallout(Vertical):
    """Drift-bordered callout shown on outbound-caused field drift."""

    DEFAULT_CSS: ClassVar[str] = f"""
    _OutboundCallout {{
        height: auto;
        border: round {DRIFT};
        padding: 0 1;
        margin-bottom: 1;
    }}
    """


class AnalyzeView(Vertical):
    """The mockup's renderResults view, mountable by launch (live) and reports (replay)."""

    def __init__(self, replay: bool = False) -> None:
        """Build summary bar, three indexes, and the detail shell."""
        super().__init__()
        self._replay = replay
        self._index = "cells"
        self._facet = "overview"
        self._view = "unified"
        self._outbound = True
        self._lists: dict[str, CursorList] = {
            "cells": CursorList(self._cell_rows()),
            "fields": CursorList(self._field_rows()),
            "rules": CursorList(self._rule_rows()),
        }
        self._lists["fields"].display = False
        self._lists["rules"].display = False
        self._sel = {name: lst.cursor_key for name, lst in self._lists.items()}
        self._index_seg = Seg(INDEXES, "cells")
        self._legend = TextRow(fr.t(_LEGENDS["cells"], "dim"))
        self._detail = Vertical()
        self._summary = self._summary_row()
        self._facet_seg: Seg | None = None
        self._view_seg: Seg | None = None

    # ----------------------------------------------------------- composition

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Summary bar on top, index panel beside the highlighted detail."""
        yield Panel(self._summary, classes="-auto mb1")
        yield Horizontal(
            Panel(
                Horizontal(TextRow(fr.t("INDEX", "ph")), self._index_seg, classes="shell-head"),
                self._lists["cells"],
                self._lists["fields"],
                self._lists["rules"],
                self._legend,
                classes="col-left",
            ),
            Panel(self._detail, hi=True, classes="col-right"),
            classes="cols",
        )

    def on_mount(self) -> None:
        """Render the initial detail."""
        self._render_detail()

    def _summary_row(self) -> SplitRow:
        left = fr.join(fr.t("✗ FAIL", "drift.b"), fr.t("  "))
        if self._replay:
            left.append_text(fr.pill("replay 8c3e11", "pill.replay"))
            left.append_text(fr.t("  "))
        left.append_text(
            fr.join(
                fr.t("14 same", "same"),
                fr.sep(),
                fr.t("3 drift", "drift.b"),
                fr.sep(),
                fr.t("1 warn", "warn"),
                fr.sep(),
                fr.t("8 skipped", "skip"),
                fr.t("  · 12 calls · 4s · exit 1", "dim"),
            )
        )
        right = fr.t(
            "read-only · esc back to reports"
            if self._replay
            else "saved · .runs/8c3e11 · s export",
            "dim",
        )
        return SplitRow(left, right)

    # ---------------------------------------------------------- index rows

    def _cell_rows(self) -> tuple[Row, ...]:
        rows: list[Row] = []
        for c in fx.CELLS:
            drift = c.verdict == "drift"
            parts = [
                fr.t("✗ " if drift else "✓ ", "drift" if drift else "same"),
                fr.t(c.req, "hi"),
                fr.t("  "),
                fr.t(c.cell, "axis"),
            ]
            if c.slow:
                parts.append(fr.t("  ▲ slow", "warn"))
            if c.write:
                parts.append(fr.t("  ~w", "badge.w"))
            rows.append(Row(c.key, fr.join(*parts), search=f"{c.req} {c.cell}"))
        return tuple(rows)

    def _field_rows(self) -> tuple[Row, ...]:
        return tuple(
            Row(
                f.key,
                fr.join(
                    fr.t(f"✗ {f.path}", "drift.b"),
                    fr.t(f"  {f.cells}", "dim"),
                    fr.t("  ◆ outbound", "warn") if f.outbound else fr.t(""),
                ),
                search=f.path,
            )
            for f in fx.FIELDS
        )

    def _rule_rows(self) -> tuple[Row, ...]:
        return tuple(
            Row(
                r.key,
                fr.join(fr.t(f"◌ {r.path}", "skip"), fr.t(f"  {r.mode}", "axis")),
                search=r.path,
            )
            for r in fx.RULES
        )

    # -------------------------------------------------------------- detail

    def _render_detail(self) -> None:
        self._facet_seg = None
        self._view_seg = None
        self._detail.remove_children()
        self._detail.mount_all(self._detail_widgets())

    def _detail_widgets(self) -> list[Widget]:
        if self._index == "cells":
            return self._cell_detail()
        if self._index == "fields":
            return self._field_detail()
        return self._rule_detail()

    # -------------------------------------------------------- cells detail

    def _cell_detail(self) -> list[Widget]:
        cell = fx.CELL_BY_KEY[self._sel["cells"]]
        drift = cell.verdict == "drift"
        self._facet_seg = Seg(AN_FACETS, self._facet)
        head = Horizontal(
            TextRow(
                fr.join(
                    fr.t("✗ " if drift else "✓ ", "drift.b" if drift else "same.b"),
                    fr.t(cell.req, "b"),
                    fr.t(" · ", "dim"),
                    fr.t(cell.cell, "axis"),
                )
            ),
            self._facet_seg,
            classes="shell-head",
        )
        return [head, *self._cell_facet_body(cell, self._facet)]

    def _cell_facet_body(self, cell: fx.CellFx, facet: str) -> list[Widget]:
        if facet == "overview":
            return self._facet_overview(cell)
        if facet == "request":
            return self._facet_request(cell)
        if facet == "response":
            return self._facet_response(cell)
        if facet == "headers":
            return self._facet_headers(cell)
        if facet == "asserts":
            return self._facet_asserts(cell)
        if facet == "events":
            if cell.stream:
                return self._facet_response(cell)
            return [TextRow(fr.t("not a streaming cell — events apply to Price feed only.", "dim"))]
        return self._facet_raw(cell)

    def _facet_overview(self, cell: fx.CellFx) -> list[Widget]:
        is_quote = cell.key.startswith("quote")
        if cell.slow:
            lat_delta = fr.t("▲ +383%", "warn")
        elif cell.stream:
            lat_delta = fr.t("=", "same")
        else:
            lat_delta = fr.t("▲ +124%", "warn")
        widgets: list[Widget] = [
            _tcols(
                (fr.t(""), 10),
                (fr.t("BASELINE", "ph"), 12),
                (fr.t("CANDIDATE", "ph"), 12),
                (fr.t("Δ", "ph"), 0),
            ),
            _tcols(
                (fr.t("status", "dim"), 10),
                (fr.t(cell.b[0], "same"), 12),
                (fr.t(cell.c[0], "same"), 12),
                (fr.t("=", "same"), 0),
            ),
            _tcols(
                (fr.t("latency", "dim"), 10),
                (fr.t(cell.b[1]), 12),
                (fr.t(cell.c[1]), 12),
                (lat_delta, 0),
            ),
            _tcols(
                (fr.t("size", "dim"), 10),
                (fr.t(cell.b[2]), 12),
                (fr.t(cell.c[2]), 12),
                (fr.t("+6 B" if is_quote else "=", "dim"), 0),
            ),
            TextRow(fr.t("VERDICTS", "ph")),
        ]
        if cell.verdict == "drift":
            verdict = fr.join(
                fr.t("✗ 3 fields drift", "drift.b"),
                fr.t(" — open the response facet, or jump to the fields index", "dim"),
            )
        else:
            verdict = fr.t("✓ no drift", "same.b")
            if cell.slow:
                verdict = fr.join(verdict, fr.t(" · latency warn (≤150ms broke)", "warn"))
        widgets.append(TextRow(verdict))
        widgets.append(
            TextRow(
                fr.join(
                    fr.t("asserts ", "dim"),
                    fr.t("✓ $status", "same"),
                    fr.sep(),
                    fr.t("✓ $.price>0", "same"),
                    fr.sep(),
                    fr.t("△ latency≤150ms broke on candidate", "warn")
                    if cell.slow
                    else fr.t("✓ latency", "same"),
                )
            )
        )
        if cell.write:
            widgets.append(TextRow(fr.t("~ write executed — approved this session", "warn")))
        return widgets

    def _facet_request(self, cell: fx.CellFx) -> list[Widget]:
        is_quote = cell.key.startswith("quote")
        path = cell.key.split("-")[0]
        widgets: list[Widget] = [
            TextRow(
                fr.t("✗ outbound DIFFERS on 1 field — is the drift ours?", "drift.b")
                if is_quote
                else fr.t("✓ outbound identical on both sides", "same.b")
            ),
            _tcols(
                (fr.t("PART", "ph"), 16),
                (fr.t("BASELINE", "ph"), 18),
                (fr.t(""), 3),
                (fr.t("CANDIDATE", "ph"), 18),
                (fr.t("SOURCE", "ph"), 0),
            ),
            _tcols(
                (fr.t("method · path", "dim"), 16),
                (fr.t(f"GET /{path}", "hi"), 18),
                (fr.t("=", "same"), 3),
                (fr.t(f"GET /{path}", "hi"), 18),
                (fr.t("authored", "dim"), 0),
            ),
            _tcols(
                (fr.t("host", "dim"), 16),
                (fr.t("127.0.0.1:8091", "hi"), 18),
                (fr.t("→", "axis"), 3),
                (fr.t("canary.internal", "hi"), 18),
                (fr.t("env · expected", "dim"), 0),
            ),
        ]
        if is_quote:
            widgets.append(
                _tcols(
                    (fr.t("query.taxRate", "drift"), 16),
                    (fr.t("0.20", "hi"), 18),
                    (fr.t("→", "axis"), 3),
                    (fr.t("0.25", "drift.b"), 18),
                    (fr.t("← $env TAX_RATE", "dim"), 0),
                )
            )
        widgets.append(
            _tcols(
                (fr.t("authorization", "dim"), 16),
                (fr.t("Bearer ••••", "skip"), 18),
                (fr.t("=", "same"), 3),
                (fr.t("Bearer ••••", "skip"), 18),
                (fr.t("masked · identical", "dim"), 0),
            )
        )
        if cell.write:
            widgets.append(
                _tcols(
                    (fr.t("body", "dim"), 16),
                    (fr.t("json · 842 B", "hi"), 18),
                    (fr.t("=", "same"), 3),
                    (fr.t("json · 842 B", "hi"), 18),
                    (fr.t("sha256 equal", "dim"), 0),
                )
            )
        if is_quote:
            widgets.append(
                TextRow(
                    fr.join(
                        fr.t("→ this drift is ", "warn"),
                        fr.t("ours", "warn.b"),
                        fr.t(
                            ": candidate env sends a different TAX_RATE. "
                            "Fix is config, not the service.",
                            "warn",
                        ),
                    )
                )
            )
        return widgets

    def _facet_response(self, cell: fx.CellFx) -> list[Widget]:
        if cell.key.startswith("quote"):
            self._view_seg = Seg(
                ("unified", "side-by-side"),
                "unified" if self._view == "unified" else "side-by-side",
            )
            widgets: list[Widget] = [
                Horizontal(TextRow(fr.t("BODY DIFF", "ph")), self._view_seg, classes="shell-head")
            ]
            if self._view == "unified":
                for f in fx.FIELDS:
                    widgets.extend(_field_diff_lines(f))
                widgets.append(
                    TextRow(
                        fr.dline(
                            "ctx",
                            fr.t('"plan": "pro", ', "diff.ctx"),
                            fr.t("⋯ headers skipped by rule $.headers.*", "skip"),
                        )
                    )
                )
            else:
                base = fr.join(
                    fr.t('{ "price": '),
                    fr.t("94.0", "drift.b"),
                    fr.t(', "taxRate": '),
                    fr.t('"0.20"', "drift.b"),
                    fr.t(', "total": '),
                    fr.t("112.80", "drift.b"),
                    fr.t(" }"),
                )
                cand = fr.join(
                    fr.t('{ "price": '),
                    fr.t("100.0", "same.b"),
                    fr.t(', "taxRate": '),
                    fr.t('"0.25"', "same.b"),
                    fr.t(', "total": '),
                    fr.t("125.00", "same.b"),
                    fr.t(" }"),
                )
                widgets.append(_side_by_side(base, cand))
            return widgets
        if cell.stream:
            return [
                TextRow(fr.t("EVENTS — 5 ⇄ 5, PAIRED IN ORDER", "ph")),
                _tcols(
                    (fr.t("#", "ph"), 5),
                    (fr.t("EVENT", "ph"), 10),
                    (fr.t("BASELINE", "ph"), 20),
                    (fr.t("CANDIDATE", "ph"), 20),
                    (fr.t(""), 0),
                ),
                _tcols(
                    (fr.t("1", "dim"), 5),
                    (fr.t("tick"), 10),
                    (fr.t("price 94.0"), 20),
                    (fr.t("price 94.0"), 20),
                    (fr.t("=", "same"), 0),
                ),
                _tcols(
                    (fr.t("2", "dim"), 5),
                    (fr.t("tick"), 10),
                    (fr.t("price 94.1"), 20),
                    (fr.t("price 94.1"), 20),
                    (fr.t("=", "same"), 0),
                ),
                _tcols(
                    (fr.t("3-5", "dim"), 5),
                    (fr.t("tick x3"), 10),
                    (fr.t("identical · chunk boundaries normalized", "dim"), 40),
                    (fr.t("=", "same"), 0),
                ),
            ]
        return [
            TextRow(fr.t('{ "status": "confirmed", "order_id": "ord_7f3a" }')),
            TextRow(fr.t("✓ bodies identical on both sides", "same")),
        ]

    def _facet_headers(self, cell: fx.CellFx) -> list[Widget]:
        return [
            _tcols(
                (fr.t("HEADER", "ph"), 16),
                (fr.t("BASELINE", "ph"), 18),
                (fr.t("CANDIDATE", "ph"), 18),
                (fr.t("RULE", "ph"), 0),
            ),
            _tcols(
                (fr.t("content-type", "hi"), 16),
                (fr.t("application/json"), 18),
                (fr.t("application/json"), 18),
                (fr.t("=", "same"), 0),
            ),
            _tcols(
                (fr.t("x-trace-id", "skip"), 16),
                (fr.t("a18f…", "skip"), 18),
                (fr.t("77b2…", "skip"), 18),
                (fr.t("◌ $.headers.* ignore", "skip"), 0),
            ),
            _tcols(
                (fr.t("host", "skip"), 16),
                (fr.t("127.0.0.1", "skip"), 18),
                (fr.t("canary.internal", "skip"), 18),
                (fr.t("◌ ignored", "skip"), 0),
            ),
            _tcols(
                (fr.t("content-length", "hi"), 16),
                (fr.t(cell.b[2]), 18),
                (fr.t(cell.c[2]), 18),
                (fr.t("follows body", "dim"), 0),
            ),
            TextRow(
                fr.t(
                    "silenced headers say which rule silenced them — "
                    "a skip is auditable, never a silent pass",
                    "dim",
                )
            ),
        ]

    def _facet_asserts(self, cell: fx.CellFx) -> list[Widget]:
        if cell.slow:
            lat_cand = fr.t("△ broke · 280ms", "warn")
        elif cell.stream:
            lat_cand = fr.t("✓", "same")
        else:
            lat_cand = fr.t("✓ 96ms", "same")
        return [
            _tcols(
                (fr.t("RULE", "ph"), 22),
                (fr.t("BASELINE", "ph"), 10),
                (fr.t("CANDIDATE", "ph"), 18),
                (fr.t("SEVERITY", "ph"), 0),
            ),
            _tcols(
                (fr.t("$status in 200..299", "hi"), 22),
                (fr.t("✓ held", "same"), 10),
                (fr.t("✓ held", "same"), 18),
                (fr.t("error", "dim"), 0),
            ),
            _tcols(
                (fr.t("$.price > 0", "hi"), 22),
                (fr.t("✓ held", "same"), 10),
                (fr.t("✓ held", "same"), 18),
                (fr.t("error", "dim"), 0),
            ),
            _tcols(
                (fr.t("latency ≤ 150ms", "hi"), 22),
                (fr.t("✓ 41ms", "same"), 10),
                (lat_cand, 18),
                (fr.t("warn", "dim"), 0),
            ),
        ]

    def _facet_raw(self, cell: fx.CellFx) -> list[Widget]:
        if cell.key.startswith("quote"):
            body = '{"price":94.0,"taxRate":"0.20","total":112.80,"plan":"pro"}'
        elif cell.stream:
            body = 'event: tick\ndata: {"price":94.0}\n⋯ 4 more events'
        else:
            body = '{"status":"confirmed","order_id":"ord_7f3a"}'
        raw = Text()
        raw.append("# exactly what landed, sanitized — baseline side\n", style=style("dim"))
        raw.append("HTTP/1.1 200 OK\ncontent-type: application/json\n", style=style("fg"))
        raw.append(body, style=style("fg"))
        return [TextRow(raw)]

    # ------------------------------------------------------- fields detail

    def _field_detail(self) -> list[Widget]:
        f = fx.FIELD_BY_KEY[self._sel["fields"]]
        name = f.path.removeprefix("$.")
        self._view_seg = Seg(
            ("unified", "side-by-side"), "unified" if self._view == "unified" else "side-by-side"
        )
        head = Horizontal(
            TextRow(
                fr.join(
                    fr.t("FIELD — ", "ph"),
                    fr.t(f.path, "b"),
                    fr.t(f"  {f.cells} · {f.mode}", "dim"),
                )
            ),
            self._view_seg,
            classes="shell-head",
        )
        widgets: list[Widget] = [head]
        if f.outbound and self._outbound:
            widgets.append(
                _OutboundCallout(
                    TextRow(
                        fr.join(
                            fr.t("OUTBOUND DIFFERS", "drift.b"),
                            fr.t(" — candidate env sends TAX_RATE=0.25 · o folds", "dim"),
                        )
                    )
                )
            )
        if self._view == "unified":
            widgets.extend(_field_diff_lines(f, braces=True))
        else:
            widgets.append(
                _side_by_side(
                    fr.join(fr.t(f'"{name}": '), fr.t(f.b, "drift.b")),
                    fr.join(fr.t(f'"{name}": '), fr.t(f.c, "same.b")),
                )
            )
        widgets.append(
            TextRow(
                fr.join(
                    fr.t("why: ", "dim"),
                    fr.t(f.why, "hi"),
                    fr.t(" · appears in ", "dim"),
                    fr.t("quote · pro", "acc"),
                    fr.t(" and 2 more cells", "dim"),
                )
            )
        )
        return widgets

    # -------------------------------------------------------- rules detail

    def _rule_detail(self) -> list[Widget]:
        r = fx.RULE_BY_KEY[self._sel["rules"]]
        return [
            PanelHeader(
                Text(f"RULE — {r.path}", style=style("ph")),
                Text(f"{r.profile} · {r.mode}", style=style("dim")),
            ),
            _kv("profile", fr.t(r.profile, "axis")),
            _kv("mode", fr.t(r.mode, "skip")),
            _kv("absorbed", fr.t(r.absorbed, "hi")),
            _kv("why", fr.t(r.why)),
            TextRow(
                fr.t(
                    "✓ green never means full coverage — the tool says what it chose not to check.",
                    "same",
                )
            ),
        ]

    # --------------------------------------------------------------- state

    def _set_index(self, index: str) -> None:
        self._index = index
        self._index_seg.set_active(index)
        for name, lst in self._lists.items():
            lst.display = name == index
        self._legend.update(fr.t(_LEGENDS[index], "dim"))
        self._sel[index] = self._lists[index].cursor_key
        self._lists[index].focus()
        self._render_detail()

    # --------------------------------------------------------------- events

    def on_seg_changed(self, event: Seg.Changed) -> None:
        """Seg strips: index switch, cell facet switch, diff view switch."""
        event.stop()
        if event.source is self._index_seg:
            self._set_index(event.value)
        elif event.source is self._facet_seg:
            self._facet = event.value
            self._render_detail()
        elif event.source is self._view_seg:
            self._view = "unified" if event.value == "unified" else "side"
            self._render_detail()

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """The detail follows the active index cursor."""
        for name, lst in self._lists.items():
            if event.source is lst:
                event.stop()
                self._sel[name] = event.key
                if name == self._index:
                    self._render_detail()
                return

    # -------------------------------------------------------- host contract

    def route_key(self, key: str) -> bool:
        """Handle a results key from the hosting workspace; True when consumed."""
        if key == "t":
            order = {"cells": "fields", "fields": "rules", "rules": "cells"}
            self._set_index(order[self._index])
            return True
        if key in ("left_square_bracket", "right_square_bracket"):
            if self._index != "cells":
                return False
            step = 1 if key == "right_square_bracket" else -1
            position = AN_FACETS.index(self._facet)
            self._facet = AN_FACETS[(position + step) % len(AN_FACETS)]
            self._render_detail()
            return True
        if key == "v":
            self._view = "side" if self._view == "unified" else "unified"
            self._render_detail()
            return True
        if key == "o":
            self._outbound = not self._outbound
            self._render_detail()
            return True
        return False

    def hints(self) -> list[tuple[str, str]]:
        """Footer keys for the results stage."""
        return [
            ("↑↓", "row"),
            ("t", "index"),
            ("[ ]", "facet"),
            ("v", "diff view"),
            ("o", "outbound"),
            ("esc", "back"),
            ("q", "quit"),
        ]

    def focus_default(self) -> None:
        """Focus the active index list."""
        self._lists[self._index].focus()

    def crumb_suffix(self) -> str:
        """Crumb tail contributed to the hosting workspace."""
        return "replay 8c3e11" if self._replay else "results · 8c3e11"
