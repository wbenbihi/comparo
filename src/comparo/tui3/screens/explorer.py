"""Explorer — foldable kind tree beside one shared, faceted detail shell."""

from __future__ import annotations

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widget import Widget

from comparo.tui3 import fixtures as fx
from comparo.tui3.screens.base import WorkspaceScreen
from comparo.tui3.theme import style
from comparo.tui3.widgets import CursorList
from comparo.tui3.widgets import FilterInput
from comparo.tui3.widgets import Panel
from comparo.tui3.widgets import PanelHeader
from comparo.tui3.widgets import Row
from comparo.tui3.widgets import Seg
from comparo.tui3.widgets import TextRow
from comparo.tui3.widgets import fragments as fr

REQ_FACETS = ("overview", "resolved", "headers", "body", "checks", "raw")

GROUPS: tuple[tuple[str, tuple[tuple[str, str, str, Text], ...]], ...] = ()


def _tree_groups() -> tuple[tuple[str, tuple[tuple[str, Text, str], ...]], ...]:
    """(group, ((key, rendered row, search), …)) for every kind."""

    def req_row(r: fx.RequestFx) -> Text:
        parts = [fr.entity("req", r.name), fr.t("  "), fr.method(r.method)]
        if r.matrix:
            parts += [fr.t("  "), fr.t("x plans", "axis")]
        if r.write:
            parts += [fr.t("  "), fr.t("~w", "badge.w")]
        if r.stream:
            parts += [fr.t("  "), fr.t("sse", "badge.s")]
        return fr.join(*parts)

    return (
        (
            "ENVIRONMENTS",
            (
                (
                    "env-stable",
                    fr.join(
                        fr.entity("env", "stable"), fr.t("  "), fr.pill("default", "pill.default")
                    ),
                    "stable",
                ),
                (
                    "env-canary",
                    fr.join(fr.entity("env", "canary"), fr.t("  "), fr.pill("live", "pill.live")),
                    "canary",
                ),
            ),
        ),
        ("REQUESTS", tuple((f"req-{r.id}", req_row(r), r.name) for r in fx.REQS)),
        (
            "MATRICES",
            (("mat-plans", fr.join(fr.entity("mat", "Plans"), fr.sep(" · 3 values")), "plans"),),
        ),
        (
            "SCHEMAS",
            (
                ("sch-contract", fr.entity("sch", "Contract (strict)"), "contract"),
                ("sch-health", fr.entity("sch", "Health"), "health schema"),
            ),
        ),
        (
            "VALUES",
            (
                ("val-auth", fr.entity("val", "auth-headers"), "auth-headers"),
                ("val-params", fr.entity("val", "staging-params"), "staging-params"),
            ),
        ),
        (
            "PROFILES",
            (
                ("prof-diff", fr.entity("diff", "diff.showcase"), "diff.showcase"),
                ("prof-assert", fr.entity("assert", "assert.order"), "assert.order"),
                ("prof-exec", fr.entity("exec", "Release gate"), "release gate"),
            ),
        ),
    )


class GraphModal(ModalScreen[None]):
    """The dependency-graph overlay (g) — esc closes."""

    BINDINGS: ClassVar = [("escape", "dismiss", "close"), ("g", "dismiss", "close")]

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the modal panel."""
        graph = Text()
        graph.append("                  ", style=style("dim"))
        graph.append("● stable", style=style("k.env"))
        graph.append(" ──$env TAX_RATE──┐\n", style=style("dim"))
        graph.append("▦ Plans", style=style("k.mat"))
        graph.append(" ──x plan────────► ", style=style("dim"))
        graph.append("◆ Price quote", style=style("k+req"))
        graph.append(" ◄──$secret── ", style=style("dim"))
        graph.append("● canary\n", style=style("k.env"))
        graph.append("◈ auth-headers", style=style("k.val"))
        graph.append(" ──$val─────────────┤\n", style=style("dim"))
        graph.append("                                    ├──validated──► ", style=style("dim"))
        graph.append("⬡ Contract\n", style=style("k.sch"))
        graph.append("    ⊨ assert.order", style=style("k.assert"))
        graph.append(" ◄──asserts─────┤\n", style=style("dim"))
        graph.append("    ± diff.showcase", style=style("k.diff"))
        graph.append(" ◄──diffs──────┘──runs in──► ", style=style("dim"))
        graph.append("▶ Release gate", style=style("k.exec"))
        yield Panel(
            PanelHeader("◆ dependency graph · focus Price quote", "ego · columns · matrix"),
            TextRow(graph),
            TextRow(
                fr.join(
                    fr.t(
                        "every $ref / $val / $env is one edge, colored by the kind it points at · ",
                        "dim",
                    ),
                    fr.t("esc", "acc"),
                    fr.t(" close", "dim"),
                )
            ),
            hi=True,
            id="graph-modal",
            classes="-auto",
        )


class ExplorerScreen(WorkspaceScreen):
    """The Explorer workspace."""

    def __init__(self) -> None:
        """Start on Price quote, overview facet, nothing folded."""
        super().__init__(id="ws-explorer")
        self._sel = "req-quote"
        self._facet = "overview"
        self._fold: set[str] = set()
        self._query = ""
        self._tree = self._make_tree()
        self._detail = Vertical(id="explorer-detail")
        self._filter = FilterInput("filter… name · kind · tag", id="explorer-filter")

    # ----------------------------------------------------------- composition

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Left tree panel + right detail panel."""
        yield Horizontal(
            Panel(
                PanelHeader("Project — showcase", "18 objects"),
                self._filter,
                self._tree,
                id="explorer-tree-panel",
                classes="col-left",
            ),
            Panel(self._detail, hi=True, id="explorer-detail-panel", classes="col-right"),
            classes="cols",
        )

    def on_mount(self) -> None:
        """Render the initial detail."""
        self._render_detail()

    def _make_tree(self) -> CursorList:
        rows: list[Row] = []
        for group, leaves in _tree_groups():
            folded = group in self._fold
            visible = [
                leaf for leaf in leaves if not self._query or self._query.lower() in leaf[2].lower()
            ]
            head = fr.join(
                fr.t("▸ " if folded else "▾ ", "dim"), fr.t(f"{group} · {len(visible)}", "dim")
            )
            rows.append(Row(f"grp:{group}", head, header=True))
            if not folded:
                rows.extend(
                    Row(key, text, search=search, indent=2) for key, text, search in visible
                )
        return CursorList(tuple(rows), id="explorer-tree")

    def _rebuild_tree(self) -> None:
        old = self._tree
        self._tree = self._make_tree()
        container = old.parent
        old.remove()
        if isinstance(container, Widget):
            container.mount(self._tree, after=self._filter)
        self._tree.move_cursor_to(self._sel)
        self._tree.focus()

    # -------------------------------------------------------------- detail

    def _render_detail(self) -> None:
        self._detail.remove_children()
        self._detail.mount_all(self._detail_widgets())

    def _shell(
        self,
        kd: str,
        kindname: str,
        name: str,
        badges: Text,
        meta: str,
        desc: str,
        body: list[Widget],
        chips: Text | None,
        facets: Widget | None = None,
    ) -> list[Widget]:
        header_right: Widget | None = facets
        head_left = fr.join(
            fr.kind(kd, bold=True),
            fr.t(f" {kindname} — ", "dim"),
            fr.t(name, "b"),
            fr.t("  "),
            badges,
        )
        out: list[Widget] = [TextRow(head_left)]
        if header_right is not None:
            out.append(header_right)
        out.append(TextRow(fr.t(meta, "dim")))
        if desc:
            out.append(TextRow(fr.t(desc, "dim"), classes="mb1"))
        out.extend(body)
        if chips is not None:
            out.append(TextRow(Text(" ")))
            out.append(TextRow(chips))
        return out

    def _kv(self, label: str, *value: Text) -> TextRow:
        return TextRow(fr.join(fr.t(f"  {label:<11}", "dim"), *value))

    def _detail_widgets(self) -> list[Widget]:
        key = self._sel
        if key.startswith("req-"):
            return self._req_detail(key.removeprefix("req-"))
        if key.startswith("env-"):
            return self._env_detail(key == "env-stable")
        if key == "mat-plans":
            return self._mat_detail()
        if key.startswith("sch-"):
            return self._sch_detail(key == "sch-contract")
        if key.startswith("val-"):
            return self._val_detail(key == "val-auth")
        if key.startswith("prof-"):
            return self._prof_detail(key.removeprefix("prof-"))
        return [TextRow(fr.t("select a resource on the left", "dim"))]

    # ------------------------------------------------------------- per kind

    def _req_detail(self, rid: str) -> list[Widget]:
        r = fx.REQ_BY_ID[rid]
        badges = fr.join(fr.method(r.method))
        if r.matrix:
            badges.append_text(fr.t("  x plans", "axis"))
        if r.write:
            badges.append_text(fr.t("  ~ write", "badge.w"))
        if r.stream:
            badges.append_text(fr.t("  sse", "badge.s"))
        seg = Seg(REQ_FACETS, self._facet)
        chips = fr.join(
            fr.chip("asserted by", "⊨ assert.order", "k+assert"),
            fr.t("  "),
            fr.chip("diffed by", "± diff.showcase", "k+diff"),
            fr.t("  "),
            fr.chip("runs in", "▶ Release gate", "k+exec"),
            fr.t("  "),
            fr.chip("last", "✗ 8c3e11", "drift.b"),
        )
        return self._shell(
            "req",
            "Request",
            r.name,
            badges,
            f"{r.file} · tags {' '.join(r.tags)}",
            r.desc,
            self._req_facet_body(r),
            chips,
            facets=seg,
        )

    def _req_facet_body(self, r: fx.RequestFx) -> list[Widget]:
        f = self._facet
        if f == "overview":
            rows: list[Widget] = [
                self._kv(
                    "method",
                    fr.method(r.method),
                    fr.t("  ~ write — needs approval", "badge.w") if r.write else fr.t(""),
                ),
                self._kv(
                    "resolves",
                    fr.t("against ", "dim"),
                    fr.t("stable", "k.env"),
                    fr.t(" ● ", "same"),
                    fr.pill("default", "pill.default"),
                ),
            ]
            if r.matrix:
                rows.append(
                    self._kv(
                        "matrix",
                        fr.t("▦ Plans", "k.mat"),
                        fr.t(" → 3 cells: basic · pro · scale", "dim"),
                    )
                )
            if r.schema:
                rows.append(
                    self._kv(
                        "contract",
                        fr.t(f"⬡ {r.schema}", "k.sch"),
                        fr.t(" · validated on every run", "dim"),
                    )
                )
            rows.append(
                self._kv(
                    "checks",
                    fr.t("⊨ assert.order · 3", "k.assert"),
                    fr.sep(),
                    fr.t("± diff.showcase · 4", "k.diff"),
                )
            )
            return rows
        if f == "resolved":
            lines = Text()
            lines.append(
                " GET " if r.method == "GET" else " POST ", style=f"bold #0d1017 on {style('same')}"
            )
            lines.append(f" http://127.0.0.1:8091/{r.id}", style=style("hi"))
            lines.append("\n▾ query\n", style=style("dim"))
            if r.matrix:
                lines.append("    plan: ", style=style("axis"))
                lines.append("basic", style=style("k+mat"))
                lines.append("  ← ▦ matrix Plans · first case\n", style=style("dim"))
                lines.append("    taxRate: 0.20  ← $env TAX_RATE · stable\n", style=style("dim"))
            else:
                lines.append("    — none\n", style=style("dim"))
            lines.append("▾ headers · 3\n", style=style("dim"))
            lines.append("    accept: application/json  ← $val auth-headers\n", style=style("dim"))
            lines.append("    authorization: ", style=style("dim"))
            lines.append("Bearer ••••", style=style("drift"))
            lines.append("  ← $secret SHOWCASE_TOKEN · masked\n", style=style("dim"))
            lines.append(
                f"· body — {'json · 842 B' if r.write else 'none (GET)'}", style=style("dim")
            )
            return [
                TextRow(lines),
                TextRow(
                    fr.t(
                        "the SAME request facet results mount · every value carries ← provenance",
                        "dim",
                    )
                ),
            ]
        if f == "headers":
            return [
                self._kv(
                    "accept", fr.t("application/json", "hi"), fr.t("  ← ◈ $val auth-headers", "dim")
                ),
                self._kv(
                    "authorization",
                    fr.t("Bearer ••••", "drift"),
                    fr.t("  ← $secret SHOWCASE_TOKEN · masked", "dim"),
                ),
                self._kv("x-client", fr.t("comparo/1.0", "hi"), fr.t("  ← default", "dim")),
            ]
        if f == "body":
            if not r.write:
                return [TextRow(fr.t(f"no body — {r.method} request", "dim"))]
            body = Text()
            body.append('{\n  "quote_id": ', style=style("dim"))
            body.append('"qt_01J4Z8"', style=style("hi"))
            body.append(",      ← $use quote_id · once per cell\n", style=style("dim"))
            body.append('  "plan": ', style=style("dim"))
            body.append('"basic"', style=style("k+mat"))
            body.append(",           ← ▦ matrix Plans\n", style=style("dim"))
            body.append('  "payment": { "token": ', style=style("dim"))
            body.append('"••••"', style=style("drift"))
            body.append(" }  ← $secret CHECKOUT_TOKEN\n}", style=style("dim"))
            return [TextRow(body)]
        if f == "checks":
            return [
                TextRow(fr.t("⊨ ASSERT.ORDER — 3 RULES", "ph")),
                self._kv("$status", fr.t("in 200..299", "acc"), fr.t("  error", "drift")),
                self._kv("$.price", fr.t("> 0", "acc"), fr.t("  error", "drift")),
                self._kv("latency", fr.t("≤ 150ms", "acc"), fr.t("  warn", "warn")),
                TextRow(fr.t("± DIFF.SHOWCASE — DRIFT RULES", "ph")),
                self._kv("$.headers.*", fr.t("ignore", "skip"), fr.t("  volatile", "dim")),
                self._kv("$.total", fr.t("tolerance ±0.5", "axis"), fr.t("  rounding", "dim")),
                self._kv("$.*", fr.t("exact", "acc"), fr.t("  default", "dim")),
            ]
        raw = Text()
        raw.append("kind: ", style=style("dim"))
        raw.append("Request\n", style=style("hi"))
        raw.append(f"name: {r.name}\nrequest:\n  method: ", style=style("dim"))
        raw.append(f"{r.method}\n", style=style("acc"))
        raw.append(f"  path: /{r.id}", style=style("dim"))
        return [TextRow(raw)]

    def _env_detail(self, stable: bool) -> list[Widget]:
        name = "stable" if stable else "canary"
        badge = fr.pill("default", "pill.default") if stable else fr.pill("live", "pill.live")
        body: list[Widget] = [
            self._kv(
                "baseUrl",
                fr.t("http://127.0.0.1:8091" if stable else "https://canary.internal", "hi"),
                fr.t("" if stable else "  · live host", "danger"),
            ),
            self._kv(
                "vars",
                fr.t("TAX_RATE", "hi"),
                fr.t("=0.20", "hi")
                if stable
                else fr.join(
                    fr.t("=", "hi"),
                    fr.t("0.25", "drift.b"),
                    fr.t(" · differs from stable — source of the taxRate drift", "dim"),
                ),
            ),
            self._kv(
                "secrets", fr.t("SHOWCASE_TOKEN", "drift"), fr.t(" · resolved, never shown", "dim")
            ),
            self._kv("timeouts", fr.t("connect 2s · read 5s", "hi")),
            TextRow(fr.t("HEALTH — MANUAL, POINT-IN-TIME (H RE-CHECKS)", "ph")),
            self._kv("GET /health", fr.t("✓ up", "same"), fr.t("  200 · 2ms", "dim")),
        ]
        chips = fr.join(
            fr.chip("used by", "8 requests"),
            fr.t("  "),
            fr.chip("in pairs", "1"),
            fr.t("  "),
            fr.chip("health", "✓ 1/1", "same.b")
            if stable
            else fr.chip("Δ vs stable", "1 var", "warn.b"),
        )
        return self._shell(
            "env",
            "Environment",
            name,
            badge,
            f"environments/{name}.yaml · "
            f"{'loopback' if stable else 'non-loopback — comparo warns before writes'}",
            "The stable release — the diff baseline."
            if stable
            else "The canary — judged against stable on every diff.",
            body,
            chips,
        )

    def _mat_detail(self) -> list[Widget]:
        body: list[Widget] = [
            TextRow(fr.t("VALUE     INJECTS       CONSUMERS", "ph")),
            *(
                TextRow(
                    fr.join(
                        fr.t(f"  {value:<9}", "k+mat"),
                        fr.t("query.plan    ", "dim"),
                        fr.t("Price quote", "hi"),
                    )
                )
                for value in fx.PLAN_CELLS
            ),
        ]
        return self._shell(
            "mat",
            "Matrix",
            "Plans",
            fr.t("1 axis · 3 values", "dim"),
            "matrices/plans.yaml",
            "Expands consumers across plans. Cells are data expansion only — "
            "env topology is chosen at launch.",
            body,
            fr.chip("capacity", "3 cells x 2 envs = 6 calls"),
        )

    def _sch_detail(self, strict: bool) -> list[Widget]:
        if strict:
            doc = Text()
            doc.append("▾ object (root) · required 4\n", style=style("dim"))
            doc.append("    price     ", style=style("hi"))
            doc.append("number", style=style("acc"))
            doc.append("   · min 0 · multipleOf 0.01\n", style=style("dim"))
            doc.append("    taxRate   ", style=style("hi"))
            doc.append("string\n", style=style("acc"))
            doc.append("    total     ", style=style("hi"))
            doc.append("number\n", style=style("acc"))
            doc.append("    plan      ", style=style("hi"))
            doc.append("string", style=style("acc"))
            doc.append("   · enum basic pro scale", style=style("dim"))
        else:
            doc = Text("▾ object · status ", style=style("dim"))
            doc.append("string", style=style("acc"))
            doc.append(' · const "ok"', style=style("dim"))
        return self._shell(
            "sch",
            "Schema",
            "Contract (strict)" if strict else "Health",
            fr.t("draft 2020-12", "dim"),
            "schemas/contract.json · additionalProperties: false"
            if strict
            else "schemas/health.json",
            "The strict response contract for pricing responses."
            if strict
            else 'Tiny health contract: {"status":"ok"}.',
            [TextRow(doc)],
            fr.join(
                fr.chip("validated by", "2 requests" if strict else "1 request"),
                fr.t("  "),
                fr.chip("document", "✓ valid", "same.b"),
            ),
        )

    def _val_detail(self, auth: bool) -> list[Widget]:
        body = Text()
        if auth:
            body.append("accept: application/json\nauthorization: ", style=style("fg"))
            body.append("$secret SHOWCASE_TOKEN", style=style("drift"))
        else:
            body.append("region: eu-west\ntenant: showcase", style=style("fg"))
        return self._shell(
            "val",
            "Value",
            "auth-headers" if auth else "staging-params",
            fr.t("merged with $val" if auth else "launch overlay", "dim"),
            "values/auth.yaml" if auth else "values/params.yaml",
            "Shared header block referenced by 8 requests."
            if auth
            else "Per-env parameter overlay, selected at launch.",
            [TextRow(body)],
            fr.chip("used by", "8 requests" if auth else "launch overrides"),
        )

    def _prof_detail(self, which: str) -> list[Widget]:
        if which == "diff":
            body: list[Widget] = [
                TextRow(fr.t("PATH          MODE             WHY", "ph")),
                self._kv(
                    "$.headers.*",
                    fr.t("ignore          ", "skip"),
                    fr.t("volatile — trace ids, hosts", "dim"),
                ),
                self._kv(
                    "$.total",
                    fr.t("tolerance ±0.5  ", "axis"),
                    fr.t("rounding drift expected", "dim"),
                ),
                self._kv(
                    "$.price", fr.t("exact           ", "acc"), fr.t("pricing must not move", "dim")
                ),
                self._kv(
                    "$.*", fr.t("exact           ", "acc"), fr.t("default — everything else", "dim")
                ),
            ]
            return self._shell(
                "diff",
                "Diff profile",
                "diff.showcase",
                fr.t("4 rules", "dim"),
                "profiles/diff.yaml",
                "What counts as drift — reviewable line by line.",
                body,
                fr.join(
                    fr.chip("used by", "all diffs"),
                    fr.t("  "),
                    fr.chip("silenced this run", "5 fields", "skip"),
                ),
            )
        if which == "assert":
            body = [
                TextRow(fr.t("TARGET     OP            SEVERITY", "ph")),
                self._kv("$status", fr.t("in 200..299  ", "acc"), fr.t("error", "drift")),
                self._kv("$.price", fr.t("> 0          ", "acc"), fr.t("error", "drift")),
                self._kv("latency", fr.t("≤ 150ms      ", "acc"), fr.t("warn", "warn")),
            ]
            return self._shell(
                "assert",
                "Assertion profile",
                "assert.order",
                fr.t("3 rules", "dim"),
                "profiles/assert.yaml",
                "Per-response checks, judged on every side independently.",
                body,
                fr.join(
                    fr.chip("held last run", "33/36", "same.b"),
                    fr.t("  "),
                    fr.chip("warned", "3", "warn.b"),
                ),
            )
        body = [
            self._kv("scope", fr.t("tags pricing checkout · 5 requests · Plans x3", "hi")),
            self._kv("envs", fr.t("stable", "k.env"), fr.t(" ⇄ ", "axis"), fr.t("canary", "k.env")),
            self._kv(
                "checks",
                fr.t("⊨ assert.order", "k.assert"),
                fr.sep(),
                fr.t("± diff.showcase", "k.diff"),
            ),
            self._kv("limits", fr.t("rate 60/min · timeout 5s · retries 2", "hi")),
        ]
        return self._shell(
            "exec",
            "Execution profile",
            "Release gate",
            fr.t("the full pre-release run", "dim"),
            "profiles/release.yaml",
            "A saved launch: scope + envs + checks + limits. Selecting it at "
            "launch prefills everything (still editable).",
            body,
            fr.join(
                fr.chip("last", "✗ 8c3e11 · 3 drift", "drift.b"), fr.t("  "), fr.chip("runs", "14")
            ),
        )

    # --------------------------------------------------------------- events

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Highlight follows the tree instantly, for every leaf."""
        if event.source is not self._tree or event.key.startswith("grp:"):
            return
        self._sel = event.key
        self._facet = "overview"
        self._render_detail()

    def on_cursor_list_activated(self, event: CursorList.Activated) -> None:
        """Enter or click on a group header folds/unfolds it."""
        if not event.key.startswith("grp:"):
            return
        group = event.key.removeprefix("grp:")
        if group in self._fold:
            self._fold.discard(group)
        else:
            self._fold.add(group)
        self._rebuild_tree()

    def on_seg_changed(self, event: Seg.Changed) -> None:
        """Facet pills switch the request body."""
        self._facet = event.value
        self._render_detail()

    def on_filter_input_filter_changed(self, event: FilterInput.FilterChanged) -> None:
        """Filter the tree live."""
        self._query = event.query
        self._rebuild_tree()

    # ----------------------------------------------------------- app wiring

    @override
    def crumb(self) -> str:
        """Navrow crumb."""
        return f"explorer · {self._sel.split('-', 1)[-1]}"

    @override
    def tier(self) -> str:
        """Navrow tier badge."""
        return "EXPLORER"

    @override
    def hints(self) -> list[tuple[str, str]]:
        """Footer keys."""
        return [
            ("↑↓", "move"),
            ("enter", "fold group"),
            ("[ ]", "facet"),
            ("g", "graph"),
            ("h", "health"),
            ("/", "filter"),
            ("q", "quit"),
        ]

    @override
    def focus_default(self) -> None:
        """Focus the tree."""
        self._tree.focus()

    @override
    def on_ws_key(self, key: str) -> bool:
        """Workspace keys: facets, graph, health, filter."""
        if key == "g":
            self.app.push_screen(GraphModal())
            return True
        if key == "h" and self._sel.startswith("env-"):
            self.notify("health re-checked · ✓ up · 200 · 2ms")
            return True
        if key == "slash":
            self._filter.focus()
            return True
        if key in ("left_square_bracket", "right_square_bracket") and self._sel.startswith("req-"):
            step = 1 if key == "right_square_bracket" else -1
            index = REQ_FACETS.index(self._facet)
            self._facet = REQ_FACETS[(index + step) % len(REQ_FACETS)]
            self._render_detail()
            return True
        return False
