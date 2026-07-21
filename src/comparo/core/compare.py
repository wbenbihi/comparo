"""Run a diff pair: execute every request-cell against both environments and diff.

The baseline and candidate runs happen concurrently; results are paired by
(request id, matrix cell) and diffed under each request's profile. Every cell
compares the whole exchange: the synthetic ``$status`` field, the response
headers under the ``$headers`` namespace, and the body (or the ordered event
sequence for streams) — and records, per effective rule, how the rule fared.
"""

import asyncio
import dataclasses
import json

from comparo.core.diff import FieldDiff
from comparo.core.diff import RuleRef
from comparo.core.diff import SourcedRule
from comparo.core.diff import State
from comparo.core.diff import default_ref
from comparo.core.diff import diff
from comparo.core.diff import source_rules
from comparo.core.execute import Execution
from comparo.core.execute import execute_all
from comparo.core.http import HttpClient
from comparo.core.loader import LoadedProject
from comparo.core.models import DiffProfile
from comparo.core.models import DiffProfileSpec
from comparo.core.models import DiffRule
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.outcomes import CheckOutcome
from comparo.core.outcomes import Provenance
from comparo.core.redaction import mask_credential_header
from comparo.core.refs import ref_id as _ref_id
from comparo.core.refs import resolve_sources


@dataclasses.dataclass(frozen=True, slots=True)
class RuleOutcome:
    """How one effective rule fared against one cell.

    ``outcome`` is the rolled-up verdict (``broke`` > ``silenced`` > ``held`` >
    ``absent``; ``error`` when the cell produced nothing comparable, so the rule
    was never judged). The counts are the fields this rule governed on this cell.
    """

    ref: RuleRef
    outcome: CheckOutcome
    broke: int = 0
    held: int = 0
    silenced: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class CellDiff:
    """The diff outcome for one request cell across the environment pair.

    ``baseline_body`` and ``candidate_body`` carry the parsed response trees so a
    front-end can render a git-style body diff; they are ``None`` for error or
    non-JSON cells. They are not part of the serialized report.
    ``rule_outcomes`` records how every effective rule fared on this cell —
    including rules that held or matched nothing — so a rules index never has to
    reconstruct traceability from the fields.
    """

    request: Request
    cell_key: str
    fields: list[FieldDiff]
    error: str | None = None
    baseline_body: object = None
    candidate_body: object = None
    #: The two executions this cell paired — the exact request sent and the full
    #: response received, per side — so the v1 report builder can serialize both
    #: sides' request+response. In-memory only (they hold live secrets); redacted
    #: at build time, never part of an already-serialized report.
    baseline: Execution | None = None
    candidate: Execution | None = None
    rule_outcomes: list[RuleOutcome] = dataclasses.field(default_factory=list)

    @property
    def drifted(self) -> bool:
        """Whether any compared field differs."""
        return any(field.state is State.DRIFT for field in self.fields)

    @property
    def skipped(self) -> int:
        """How many fields the profile deliberately did not compare."""
        return sum(1 for field in self.fields if field.state is State.SKIP)

    @property
    def drifts(self) -> list[FieldDiff]:
        """The fields that drifted."""
        return [field for field in self.fields if field.state is State.DRIFT]


async def diff_run(
    project: LoadedProject,
    baseline: Environment,
    candidate: Environment,
    requests: list[Request],
    client: HttpClient,
    candidate_client: HttpClient | None = None,
) -> list[CellDiff]:
    """Execute *requests* against both environments and diff the paired results.

    Args:
        project: The loaded project.
        baseline: The baseline environment.
        candidate: The candidate environment.
        requests: The requests to run and diff.
        client: The transport for the baseline run.
        candidate_client: A separate transport for the candidate run, so the two
            do not share a cookie jar; defaults to *client* when omitted.

    Returns:
        One :class:`CellDiff` per baseline request cell.
    """
    baseline_runs, candidate_runs = await asyncio.gather(
        execute_all(project, baseline, requests, client),
        execute_all(project, candidate, requests, candidate_client or client),
    )
    index = {(run.request.metadata.id, run.cell_key): run for run in candidate_runs}
    return [
        _compare(project, run, index.get((run.request.metadata.id, run.cell_key)))
        for run in baseline_runs
    ]


def compare_cell(
    project: LoadedProject,
    baseline: Execution,
    candidate: Execution | None,
    diff_override: object = None,
) -> CellDiff:
    """Diff one already-executed cell (baseline vs candidate) under its profile.

    Args:
        project: The loaded project (for the request's diff profile).
        baseline: The baseline execution.
        candidate: The candidate execution, or ``None`` if it is missing.
        diff_override: An execution-level diff profile ($ref/inline/list) that
            composes on top of the request/project profile, or ``None``.

    Returns:
        The cell's diff outcome.
    """
    return _compare(project, baseline, candidate, diff_override=diff_override)


#: Headers that differ call-to-call by their nature, never by API behavior: the
#: clock family, per-call counters, connection management, body framing (a real
#: payload change is the body diff's and the size ledger's job), and per-request
#: correlation ids — a fresh trace id on every response is transport, not drift.
#: Built-in ignores, overridable — a user rule for the same path loads later and
#: wins the precedence tie, so ``{path: $headers.date, mode: exact}`` re-checks.
_VOLATILE_HEADERS = (
    # clock
    "date",
    "age",
    "expires",
    "retry-after",
    # per-call counters
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    # connection / framing
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-length",
    # per-request correlation ids
    "x-request-id",
    "x-correlation-id",
    "traceparent",
    "tracestate",
    "x-amzn-requestid",
    "x-amzn-trace-id",
    "cf-ray",
    "x-served-by",
)

_HEADERS_ROOT = "$headers"

#: The synthetic built-in ignore paths, for consumers that must classify a stored
#: rule path (e.g. replay reconstruction) without the live RuleRef origin.
VOLATILE_HEADER_PATHS = frozenset(f"{_HEADERS_ROOT}.{name}" for name in _VOLATILE_HEADERS)


def _volatile_rules() -> list[SourcedRule]:
    return [
        SourcedRule(
            DiffRule(path=name, mode="ignore"),
            RuleRef(f"{_HEADERS_ROOT}.{name}", "ignore", "synthetic", None, index),
        )
        for index, name in enumerate(_VOLATILE_HEADERS)
    ]


def headers_tree(headers: list[tuple[str, str]]) -> dict[str, object]:
    """Fold wire headers into a comparable tree — the ``$headers`` namespace.

    Names lowercase (header names are case-insensitive and stacks differ);
    duplicates comma-join per RFC 9110 except ``set-cookie``, which stays a list
    (joining it is lossy). Credential-bearing values are masked *before* the diff
    ever sees them, so two secrets compare equal as ``••••••`` and a session
    cookie can never land in a ``FieldDiff`` value.
    """
    tree: dict[str, object] = {}
    for name, value in headers:
        key = name.lower()
        masked = mask_credential_header(key, value)
        if key == "set-cookie":
            existing = tree.get(key)
            if isinstance(existing, list):
                existing.append(masked)
            else:
                tree[key] = [masked]
        elif key in tree:
            tree[key] = f"{tree[key]}, {masked}"
        else:
            tree[key] = masked
    return tree


def _compare(
    project: LoadedProject,
    baseline: Execution,
    candidate: Execution | None,
    diff_override: object = None,
) -> CellDiff:
    request, key = baseline.request, baseline.cell_key
    default_mode, sourced = _compose_diff(project, request, diff_override)
    status_sourced, header_sourced, body_sourced = _partition(sourced)
    effective = _effective_refs(default_mode, status_sourced, header_sourced, body_sourced)

    def errored(message: str, candidate_exec: Execution | None) -> CellDiff:
        outcomes = [RuleOutcome(ref, "error") for ref in effective]
        return CellDiff(
            request,
            key,
            [],
            message,
            baseline=baseline,
            candidate=candidate_exec,
            rule_outcomes=outcomes,
        )

    if candidate is None:
        return errored("no candidate result", None)
    if baseline.error is not None:
        return errored(f"baseline: {baseline.error}", candidate)
    if candidate.error is not None:
        return errored(f"candidate: {candidate.error}", candidate)
    baseline_response, candidate_response = baseline.response, candidate.response
    if baseline_response is None or candidate_response is None:
        return errored("missing response", candidate)

    # ``$status`` and ``$headers.*`` rules are partitioned by their literal path
    # prefix, never through the JSON-path compiler — a body field literally named
    # ``status`` or ``headers`` can never collide with the synthetic namespaces.
    status_field = _status_field(
        baseline_response.status, candidate_response.status, status_sourced
    )
    header_fields = diff(
        headers_tree(baseline_response.headers),
        headers_tree(candidate_response.headers),
        "exact",
        [*_volatile_rules(), *_strip_headers_prefix(header_sourced)],
        root=_HEADERS_ROOT,
    )
    prefix = [status_field, *header_fields]

    if baseline_response.events is not None and candidate_response.events is not None:
        # Streamed responses diff as their ordered event sequence, not raw bytes.
        events_a, events_b = baseline_response.events, candidate_response.events
        fields = [*prefix, *diff(events_a, events_b, default_mode, body_sourced)]
        return CellDiff(
            request,
            key,
            fields,
            baseline_body=events_a,
            candidate_body=events_b,
            baseline=baseline,
            candidate=candidate,
            rule_outcomes=_rule_outcomes(effective, fields),
        )
    try:
        baseline_body = json.loads(baseline_response.body)
        candidate_body = json.loads(candidate_response.body)
    except (ValueError, RecursionError):
        # Empty or non-JSON responses (e.g. a status-only check) diff as raw bytes;
        # a pathologically deep JSON body (RecursionError) also falls back to raw.
        if baseline_response.body == candidate_response.body:
            body_field = FieldDiff("$", State.SAME, "exact", rule=default_ref(default_mode))
        else:
            body_field = FieldDiff(
                "$",
                State.DRIFT,
                "exact",
                "response bodies differ",
                rule=default_ref(default_mode),
            )
        fields = [*prefix, body_field]
        return CellDiff(
            request,
            key,
            fields,
            baseline=baseline,
            candidate=candidate,
            rule_outcomes=_rule_outcomes(effective, fields),
        )
    fields = [*prefix, *diff(baseline_body, candidate_body, default_mode, body_sourced)]
    return CellDiff(
        request,
        key,
        fields,
        baseline_body=baseline_body,
        candidate_body=candidate_body,
        baseline=baseline,
        candidate=candidate,
        rule_outcomes=_rule_outcomes(effective, fields),
    )


_STATUS_REF = RuleRef("$status", "exact", "synthetic")
_HEADERS_DEFAULT_REF = default_ref("exact", _HEADERS_ROOT)


def _partition(
    sourced: list[SourcedRule],
) -> tuple[list[SourcedRule], list[SourcedRule], list[SourcedRule]]:
    status: list[SourcedRule] = []
    headers: list[SourcedRule] = []
    body: list[SourcedRule] = []
    for rule in sourced:
        if rule.ref.path == "$status":
            status.append(rule)
        elif rule.ref.path == _HEADERS_ROOT or rule.ref.path.startswith(f"{_HEADERS_ROOT}."):
            headers.append(rule)
        else:
            body.append(rule)
    return status, headers, body


def _strip_headers_prefix(header_sourced: list[SourcedRule]) -> list[SourcedRule]:
    """Rewrite ``$headers.*`` rule paths relative to the headers tree root.

    The name portion is case-folded to match the folded tree — a rule written
    with canonical casing (``$headers.Date``) must govern the ``date`` field.
    The ref keeps the original declared path (identity/display); only the
    compiled matcher sees the stripped, folded path.
    """
    stripped: list[SourcedRule] = []
    for sourced in header_sourced:
        rule = sourced.rule
        relative = rule.path.removeprefix(_HEADERS_ROOT).lower()
        stripped.append(
            SourcedRule(
                DiffRule(
                    path=relative or "$",
                    mode=rule.mode,
                    array_length=rule.array_length,
                    tolerance=rule.tolerance,
                ),
                sourced.ref,
            )
        )
    return stripped


def _effective_refs(
    default_mode: str,
    status_sourced: list[SourcedRule],
    header_sourced: list[SourcedRule],
    body_sourced: list[SourcedRule],
) -> list[RuleRef]:
    """Every rule this cell is judged under, in display order.

    The synthetic ``$status`` check, the built-in volatile-header ignores, the
    profile's own rules, and the two catch-alls — the full set a rules index
    must account for, whether or not a rule ends up matching anything.
    """
    # Every $status ref is listed — a shadowed override grades "absent" instead of
    # silently vanishing from the ledger (the last-loaded one actually governs).
    status_refs = [s.ref for s in status_sourced] if status_sourced else [_STATUS_REF]
    return [
        *status_refs,
        *(s.ref for s in _volatile_rules()),
        *(s.ref for s in header_sourced),
        *(s.ref for s in body_sourced),
        _HEADERS_DEFAULT_REF,
        default_ref(default_mode),
    ]


def _rule_outcomes(effective: list[RuleRef], fields: list[FieldDiff]) -> list[RuleOutcome]:
    by_ref: dict[RuleRef, list[FieldDiff]] = {}
    for field in fields:
        if field.rule is not None:
            by_ref.setdefault(field.rule, []).append(field)
    outcomes: list[RuleOutcome] = []
    for ref in effective:
        matched = by_ref.get(ref, [])
        broke = sum(1 for field in matched if field.state is State.DRIFT)
        # "Silenced" means deliberately ignored; a max-depth cut is a SKIP with a
        # non-ignore mode and must not read as a choice the rule made.
        silenced = sum(
            1 for field in matched if field.state is State.SKIP and field.mode == "ignore"
        )
        held = sum(1 for field in matched if field.state is State.SAME)
        if broke:
            outcome: CheckOutcome = "broke"
        elif silenced:
            outcome = "silenced"
        elif held:
            outcome = "held"
        else:
            outcome = "absent"
        outcomes.append(RuleOutcome(ref, outcome, broke=broke, held=held, silenced=silenced))
    return outcomes


#: A rule's written identity — origin, owner, path, mode, and parameters.
WrittenRule = tuple[str, str | None, str, str, float | None, str | None]


def written_identity(ref: RuleRef) -> WrittenRule:
    """The identity of a rule *as written* — stable across compositions.

    ``RuleRef.index`` is composition-relative (the same profile rule lands at
    different offsets on requests that compose it after different siblings), so
    cross-cell folds must key on what the user wrote, not where it landed. The
    parameters belong to the identity: two same-path tolerance rules with
    different bands are two different rules.
    """
    return (ref.origin, ref.profile, ref.path, ref.mode, ref.tolerance, ref.array_length)


def unused_rules(cells: list[CellDiff]) -> list[RuleRef]:
    """Rules that matched nothing anywhere — a typo'd path silently checks nothing.

    A rule is unused when every cell that could judge it graded it ``absent`` —
    error cells are inconclusive and count as neither use nor disuse. Synthetic
    and catch-all refs are excluded: only rules someone wrote can be typos.
    """
    seen: dict[WrittenRule, tuple[RuleRef, bool]] = {}
    for cell in cells:
        for outcome in cell.rule_outcomes:
            if outcome.ref.origin not in ("profile", "inline"):
                continue
            if outcome.outcome == "error":
                continue
            key = written_identity(outcome.ref)
            ref, used = seen.get(key, (outcome.ref, False))
            seen[key] = (ref, used or outcome.outcome != "absent")
    return [ref for ref, used in seen.values() if not used]


def _status_field(baseline: int, candidate: int, rules: list[SourcedRule]) -> FieldDiff:
    """Compare HTTP status as a synthetic ``$status`` field, honouring an override.

    A 200→500 with identical bodies is a real regression the body diff can't see,
    so status is always compared; a ``{path: $status, mode: ignore}`` rule (e.g.
    for an endpoint whose status legitimately varies) skips it. When several
    ``$status`` rules compose, the last-loaded one wins — the same tie-break as
    everywhere else, so an execution override can re-check an ignored status.
    """
    override = rules[-1] if rules else None
    ref = override.ref if override is not None else _STATUS_REF
    if override is not None and override.rule.mode == "ignore":
        return FieldDiff("$status", State.SKIP, "ignore", rule=ref)
    if baseline == candidate:
        return FieldDiff(
            "$status", State.SAME, "exact", baseline=baseline, candidate=candidate, rule=ref
        )
    return FieldDiff(
        "$status",
        State.DRIFT,
        "exact",
        f"{baseline} → {candidate}",
        baseline=baseline,
        candidate=candidate,
        rule=ref,
    )


def _compose_diff(
    project: LoadedProject, request: Request, override: object
) -> tuple[str, list[SourcedRule]]:
    sources: list[tuple[str | None, DiffProfileSpec]] = []
    response = request.spec.response
    if response is not None and response.diff is not None:
        sources.extend(resolve_sources(project, response.diff, DiffProfileSpec))
    else:
        sources.extend(_project_default_diff(project))
    if override is not None:
        sources.extend(resolve_sources(project, override, DiffProfileSpec))
    if not sources:
        return "exact", []
    sourced: list[SourcedRule] = []
    for profile_id, spec in sources:
        origin: Provenance = "profile" if profile_id is not None else "inline"
        sourced.extend(source_rules(spec.rules or [], origin, profile_id, start=len(sourced)))
    return sources[-1][1].default, sourced


def _project_default_diff(project: LoadedProject) -> list[tuple[str | None, DiffProfileSpec]]:
    if project.project is None:
        return []
    config = project.project.spec.diff
    if isinstance(config, dict):
        return resolve_sources(project, config.get("default"), DiffProfileSpec)
    return []


def profile_for(project: LoadedProject, request: Request) -> DiffProfile | None:
    """Return the diff profile that applies to *request*, if any.

    Args:
        project: The loaded project.
        request: The request whose effective profile is resolved.

    Returns:
        The request's own profile, else the project default, else ``None``.
    """
    return _profile_for(project, request)


def _profile_for(project: LoadedProject, request: Request) -> DiffProfile | None:
    response = request.spec.response
    if response is not None:
        identifier = _ref_id(response.diff)
        profile = project.objects.get(identifier) if identifier is not None else None
        if isinstance(profile, DiffProfile):
            return profile
    if project.project is not None:
        config = project.project.spec.diff
        if isinstance(config, dict):
            identifier = _ref_id(config.get("default"))
            profile = project.objects.get(identifier) if identifier is not None else None
            if isinstance(profile, DiffProfile):
                return profile
    return None
