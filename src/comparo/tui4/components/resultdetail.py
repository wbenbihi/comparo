"""Structured occurrence and evidence detail using the TUI4 display library."""

from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.content import Content
from textual.reactive import reactive

from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultFact
from comparo.application.results import result_value_preview
from comparo.tui4.components.disclosure import Disclosure
from comparo.tui4.components.launchsummary import SummaryContents
from comparo.tui4.components.launchsummary import SummarySection
from comparo.tui4.components.status import status_text
from comparo.tui4.components.typography import Caption
from comparo.tui4.models.results import ResultItem
from comparo.tui4.models.results import item_label
from comparo.tui4.models.results import timings_text
from comparo.tui4.theme import style


class ResultDetail(Vertical):
    """Recompose evidence inside the analysis surface's stable scrolling owner."""

    DEFAULT_CSS = "ResultDetail { height: auto; } ResultDetail Caption { height: auto; }"
    item: reactive[ResultItem | None] = reactive(None, recompose=True)
    context: reactive[str] = reactive("", recompose=True)

    @override
    def compose(self) -> ComposeResult:
        """Make outcome and observed evidence prominent, with provenance below."""
        item = self.item
        if self.context:
            yield Caption(self.context, markup=False)
        if item is None:
            return
        heading = status_text(item.outcome)
        heading.append(f"  {item_label(item)}")
        yield Caption(
            "Environment comparison" if isinstance(item, ComparisonResultRow) else heading,
            markup=False,
            classes="result-heading",
        )
        sections = detail_sections(item)
        if isinstance(item, ResultFact):
            with Horizontal(classes="fact-columns"):
                with Vertical():
                    yield Caption("Why Comparo judged it", classes="result-section-title")
                    if item.evidence:
                        for evidence in item.evidence[:3]:
                            for value in evidence.values[:4]:
                                label = {"actual": "Observed", "expected": "Expected"}.get(
                                    value.name, value.name
                                )
                                text = Text(f"{label}: ", style("label"))
                                text.append(
                                    result_value_preview(value.value, maximum_characters=180),
                                    style("fg"),
                                )
                                yield Caption(text, markup=False)
                    else:
                        yield Caption(
                            "The recorded rule outcome has no inline explanation. "
                            "Compare the captured responses below.",
                            markup=False,
                        )
                with Vertical():
                    yield Caption("Configuration provenance", classes="result-section-title")
                    yield Caption(
                        f"Severity: {item.severity}\nSource: {item.rule.resource_id}", markup=False
                    )
            yield Disclosure(
                "Full rule evidence & evaluation counts",
                SummaryContents(sections),
                Caption("\n".join(evidence.location for evidence in item.evidence), markup=False),
            )
        elif isinstance(item, ExecutionResultRow):
            yield Caption(
                f"{item.timing.attempts} attempts · {item.timing.exchanges} HTTP exchanges",
                markup=False,
            )
            yield Disclosure("Attempts, exchanges & headers", SummaryContents(sections[1:-1]))
            yield Disclosure("Matrix coordinates & identities", SummaryContents(sections[-1:]))
        else:
            yield Caption(
                f"{len(item.diffs)} diff evaluations · {len(item.unavailable)} unavailable sides",
                markup=False,
            )
            yield Caption(
                "Choose Baseline or Candidate above for HTTP details; "
                "Compare bodies for response differences.",
                markup=False,
            )
            yield Disclosure("Recorded identities", SummaryContents(sections[:1]))


def detail_sections(item: ResultItem) -> tuple[SummarySection, ...]:
    """Keep transport, rule evidence, timing and provenance in distinct sections."""
    if isinstance(item, ResultFact):
        evidence = tuple(
            SummarySection(
                f"Evidence {index + 1} · {evidence.outcome}",
                tuple(
                    (value.name, result_value_preview(value.value, maximum_characters=1500))
                    for value in evidence.values
                ),
                "",
            )
            for index, evidence in enumerate(item.evidence)
        )
        return (
            *evidence,
            SummarySection(
                "Rule",
                (
                    ("Rule", item.rule_id),
                    ("Severity", item.severity),
                    ("Source", item.rule.resource_id),
                ),
            ),
            SummarySection(
                "Evaluation",
                tuple(
                    (metric.name, str(metric.value)) for metric in (*item.counts, *item.coverage)
                ),
                f"{item.evidence_omitted} evidence records omitted by producer.",
            ),
            *(
                SummarySection("Evaluation issue", ((issue.stage, issue.code),))
                for issue in item.issues
            ),
        )
    if isinstance(item, ComparisonResultRow):
        return (
            SummarySection(
                "Comparison",
                (
                    ("Cell", item.cell_id),
                    ("Trial", item.trial_id),
                    ("Diff facts", str(len(item.diffs))),
                    ("Unavailable sides", str(len(item.unavailable))),
                ),
            ),
            note_for_comparison(item),
        )
    return execution_sections(item)


def note_for_comparison(item: ComparisonResultRow) -> SummarySection:
    """Explain explicit comparison links without manufacturing missing evidence."""
    return SummarySection(
        "Evidence",
        note=(
            "Open the baseline, candidate or a diff rule below. "
            "Compare response bodies to inspect captured content."
        )
        if not item.unavailable
        else (
            "A comparison side is unavailable. Open its execution to inspect the recorded failure."
        ),
    )


def execution_sections(item: ExecutionResultRow) -> tuple[SummarySection, ...]:
    """Preserve every canonical attempt, exchange and explicit metadata omission."""
    sections = [
        SummarySection(
            "Execution",
            (
                ("Request", item.subject.resource_id),
                ("Environment", item.environment.resource_id),
                ("Attempts / exchanges", f"{item.timing.attempts} / {item.timing.exchanges}"),
                ("Timing", timings_text(item.timing.measurements)),
            ),
        )
    ]
    if item.failure:
        sections.append(
            SummarySection("Transport error", ((item.failure.stage, item.failure.code),))
        )
    for attempt in item.attempts:
        sections.append(
            SummarySection(
                f"Attempt {attempt.ordinal + 1}" + (" · terminal" if attempt.terminal else ""),
                note=timings_text(attempt.timings),
            )
        )
        if attempt.terminal_pre_send is not None:
            sections.append(
                SummarySection(
                    "Pre-send",
                    (("Request", attempt.terminal_pre_send.state.value),),
                    f"{attempt.failure.stage}: {attempt.failure.code}" if attempt.failure else "",
                )
            )
        for exchange in attempt.exchanges:
            sections.append(
                SummarySection(
                    f"Exchange {exchange.ordinal + 1}"
                    + (" · terminal" if exchange.terminal else ""),
                    note=timings_text(exchange.timings),
                )
            )
            request, response = exchange.request, exchange.response
            if request.metadata is not None:
                metadata = request.metadata
                sections.append(
                    SummarySection(
                        "Request",
                        (
                            ("Method", metadata.method),
                            ("URL", metadata.url),
                            *((field.name, field.value) for field in metadata.headers),
                            *(
                                (f"Cookie {cookie.name}", cookie.value)
                                for cookie in metadata.cookies
                            ),
                        ),
                        f"Redactions: URL {metadata.url_redaction_count}"
                        " · headers "
                        f"{metadata.header_redaction_count} · cookies "
                        f"{metadata.cookie_redaction_count}",
                    )
                )
            else:
                sections.append(
                    SummarySection(
                        "Request", note=f"{request.state.value}: {request.omission_reason}"
                    )
                )
            if response.metadata is not None:
                sections.append(
                    SummarySection(
                        "Response",
                        (
                            ("HTTP status", str(response.metadata.status)),
                            ("Protocol", response.metadata.protocol),
                            *((field.name, field.value) for field in response.metadata.headers),
                            *(
                                (f"Trailer {field.name}", field.value)
                                for field in response.metadata.trailers
                            ),
                        ),
                    )
                )
            else:
                sections.append(
                    SummarySection(
                        "Response",
                        note=f"{response.state.value}: "
                        f"{response.omission_reason or 'no response produced'}",
                    )
                )
            if exchange.failure:
                sections.append(
                    SummarySection(
                        "Transport error", ((exchange.failure.stage, exchange.failure.code),)
                    )
                )
    sections.append(
        SummarySection(
            "Coordinates",
            (
                ("Cell", item.cell_id),
                ("Trial", item.trial_id),
                *(
                    (
                        coordinate.axis,
                        Content(result_value_preview(coordinate.value, maximum_characters=200)),
                    )
                    for coordinate in item.coordinates
                ),
            ),
        )
    )
    return tuple(sections)
