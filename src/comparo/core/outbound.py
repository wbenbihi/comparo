"""Diff the resolved outbound request across an environment pair.

comparo replays the *same* declared request against both sides, so any outbound
difference can only come from environment config — the base URL, an env-specific
header or query var, auth, or a value injected into the body. This module names
each differing field and the config surface it came from, answering the first
triage question: is the response drift the service's, or did we send two
different requests?

It is typed against a structural protocol so the same logic serves the live TUI
(two ``ResolvedRequest``s) and report replay (two serialized outbound requests)
— one implementation, per the components rule.
"""

import dataclasses
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol

from comparo.core.diff import State
from comparo.core.diff import diff
from comparo.core.redaction import redact_tree


class Outbound(Protocol):
    """The shape both a live ``ResolvedRequest`` and a replayed record satisfy."""

    # Attribute protocol — bare property declarations, no behavior to document.
    @property
    def method(self) -> str: ...  # noqa: D102
    @property
    def url(self) -> str: ...  # noqa: D102
    @property
    def headers(self) -> Sequence[tuple[str, object]]: ...  # noqa: D102
    @property
    def query(self) -> Mapping[str, object]: ...  # noqa: D102
    @property
    def body(self) -> object: ...  # noqa: D102


@dataclasses.dataclass(frozen=True, slots=True)
class OutboundFieldDiff:
    """One differing outbound field: redacted before → after, and its source."""

    label: str
    baseline: str
    candidate: str
    source: str


def outbound_source(label: str) -> str:
    """Attribute an outbound difference to the config surface that produced it.

    Names the surface (not a fabricated var name — that provenance is not
    tracked on the pair diff yet), so a reviewer knows where to look.
    """
    if label == "url":
        return "env · base url"
    if label == "method":
        return "request method"
    if label.startswith("body"):
        return "env · injected body value"
    if label.startswith(("header authorization", "header proxy-authorization")):
        return "env · auth"
    if label.startswith("header"):
        return "env · header"
    if label.startswith("query"):
        return "env · query var"
    return "env config"


def outbound_diffs(
    baseline: Outbound,
    candidate: Outbound,
    *,
    redact: Callable[[str], str],
) -> list[OutboundFieldDiff]:
    """The redacted field-level differences between two outbound requests.

    Every value is redacted first, so masked secrets compare equal and a hidden
    token can never surface as a false drift. Bodies diff structurally (leaf by
    leaf, through the same tree walker as response bodies) rather than as one
    opaque "bodies differ" row.
    """
    diffs: list[OutboundFieldDiff] = []

    def scalar(label: str, a: object, b: object) -> None:
        sa, sb = redact(str(a)), redact(str(b))
        if sa != sb:
            diffs.append(OutboundFieldDiff(label, sa, sb, outbound_source(label)))

    def mapping(
        prefix: str,
        a: Sequence[tuple[str, object]] | Mapping[str, object],
        b: Sequence[tuple[str, object]] | Mapping[str, object],
    ) -> None:
        am = dict(a) if not isinstance(a, Mapping) else a
        bm = dict(b) if not isinstance(b, Mapping) else b
        ad = {redact(str(k)): redact(str(v)) for k, v in am.items()}
        bd = {redact(str(k)): redact(str(v)) for k, v in bm.items()}
        for key in sorted(set(ad) | set(bd)):
            av, bv = ad.get(key, "—"), bd.get(key, "—")
            if av != bv:
                label = f"{prefix} {key}"
                diffs.append(OutboundFieldDiff(label, av, bv, outbound_source(label)))

    scalar("method", baseline.method, candidate.method)
    scalar("url", baseline.url, candidate.url)
    mapping("header", baseline.headers, candidate.headers)
    mapping("query", baseline.query, candidate.query)
    diffs.extend(_body_diffs(baseline.body, candidate.body, redact))
    return diffs


def _body_diffs(
    baseline: object, candidate: object, redact: Callable[[str], str]
) -> list[OutboundFieldDiff]:
    """Structural leaf-level body differences, redact-first.

    Redaction happens before comparison so two different secrets injected into
    the same field mask to the same glyph and never read as drift.
    """
    if baseline == candidate:
        return []
    masked_a = redact_tree(baseline, redact)
    masked_b = redact_tree(candidate, redact)
    fields = diff(masked_a, masked_b, "exact", [])
    out: list[OutboundFieldDiff] = []
    for field in fields:
        if field.state is not State.DRIFT:
            continue
        label = "body" if field.path == "$" else f"body {field.path.removeprefix('$.')}"
        out.append(
            OutboundFieldDiff(
                label,
                _shown(field.baseline),
                _shown(field.candidate),
                outbound_source("body"),
            )
        )
    if not out:
        # The raw values differed but every masked leaf agrees — an injected
        # secret differs; say so without exposing either side.
        out.append(
            OutboundFieldDiff("body", "an env value is injected", "—", outbound_source("body"))
        )
    return out


def _shown(value: object) -> str:
    return "—" if value is None else str(value)
