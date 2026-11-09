# TUI results implementation contract

Reconciled **2026-10-31**. This contract defines the reusable, sanitized result surface shared by
Launch executions and persisted Reports. Older mockups call those workspaces Compare and Report.

## Boundary

The only result input is `application.results.ResultProjection`, derived from a sanitized
`RunArtifact`. Presentation code must not:

- accept live HTTP observations, run contexts, or sensitive values;
- open artifact blobs or storage paths directly;
- recompute assertion, diff, completion, or gate outcomes;
- infer missing facts;
- render exception text or enable Rich markup for data-bearing content.

Detached request and response bodies are never loaded by the manifest result projection. Each
exchange instead has closed request and response slots. A slot is either retained sanitized
metadata, an explicit typed omission reason, or—for a response only—not produced because the
exchange failed. Retained request metadata contains method, sanitized URL, ordered sanitized
headers and masked cookies, plus exact retained URL/header/cookie redaction-and-omission counts.
Those counts are typed producer provenance; a frontend must not infer redaction from marker text.
Retained response
metadata contains status, protocol, ordered sanitized headers and trailers. Both sides expose only
whether capture metadata exists; neither
side contains body bytes, blob descriptors, or storage locators.

Each retained exchange message also projects an immutable `ArtifactBodyReference` coordinate
bound to the owning artifact ID, artifact API version, and SHA-256 of the exact canonical
`RunArtifact` results document. A stale coordinate therefore cannot select a replacement report
that reuses the same bundle ID and nested execution coordinates. A
frontend must pass that coordinate to the separate, authenticated, bounded
`read_artifact_body_window` service to obtain content; request coordinates may also be passed to
`export_artifact_curl`.

The coordinate types live in the pure `application.artifact_references` contract rather than the
effectful artifact service. Each execution also projects every attempt and exchange with canonical
zero-based ordinals and explicit terminal markers. Message rows repeat their owning ordinals and
markers and are validated against the retained metadata in canonical wire order. Attempt and
exchange rows retain their exact artifact timing sequences, including explicit not-measured
reasons; frontends must not derive those values from the execution aggregate. A final pre-send
attempt remains visible with its closed failure, explicit retained-or-omitted request slot, and
`pre_send_terminal` marker even when it produced no exchange body coordinate; a frontend must not
infer terminal state from the last visible message. Terminal pre-send bodies are metadata-only in
this contract: `has_body_capture` may report artifact capture metadata, but the body-window service
accepts only exchange coordinates and must not be bypassed.

## Component ownership

| Component | Single responsibility |
| --- | --- |
| `ResultsView` | Shared composition, artifact-owned summary, five lenses and local evidence navigation. |
| `GroupedTable` / `Table` | Typed index, occurrence and message rows using the existing component library. |
| `ResultDetail` | Evidence-light detail for one stable result reference. |
| `components.status` | Shared outcome glyph, label and semantic color mapping. |
| `DiffView` / `resultbody` / `resultlayout` | Body comparison, loading state and responsive evidence layout. |
| `CaptureInspector` | Bounded raw/JSON/SSE browsing and reviewed sanitized cURL. |

Launch and Reports mount the same `ResultsView` class and feed it sanitized result projections.
The owning workspace routes body requests through the backend. Neither view gains HTTP execution
authority from an artifact; opening a stored report does not rerun it. There is no separate archived
Analyze implementation or `ResultAccess` UI enum.

## Responsive contract

Terminal widths use the shared breakpoint policy:

| Width | Result composition |
| ---: | --- |
| `< 88` | index stacked over detail |
| `88–139` | 36-column index beside detail |
| `>= 140` | 44-column index beside detail |

No result widget may declare a private breakpoint.

## Navigation contract

- Selecting a row passes a typed `ResultReference` to the shared detail component.
- `ResultsView` records selected references in its local history; `esc` backs out of detail first.
- Issues, Requests, Rules, Fields and Timeline use `Alt+1`–`Alt+5` or `[` / `]` by default.
  Settings can remap these commands. Fields shows retained rule/selector evidence, not every JSON key.
- `i` opens the shared capture inspector; Escape returns to Analyze.
- Opening a report never enters Launch; completing a live run never enters Reports.
- Background completion updates Launch without changing active workspace or focus; Run remains
  visible until the user explicitly opens Analyze.

## Safety and resource contract

- Runtime progress is a latest-value mailbox, not an event log.
- Report listing bounds every physical directory entry, including rejected names.
- Stored reports are authenticated before listing and verified before opening.
- Result metadata mirrors the already bounded and sanitized artifact manifest; it does not load
  detached captures or retain live runtime objects.
- Text is bounded and terminal-control-safe.
- A transport error remains distinct from a failed assertion or diff.
- A missing comparison side remains unavailable; it is not synthesized as drift.
- Color never carries meaning without a visible glyph and label.

## Acceptance gate

These are acceptance requirements, not a statement that a fresh full-suite, coverage or visual
audit has passed. See [current QA evidence](../design-qa.md) and [status](status.md).

- the real shared result view mounts at 80, 120, and 160 columns;
- Launch and Reports instantiate the same result component;
- behavioral regressions cover changed components; the repository-wide combined statement/branch
  coverage floor is 95%, without a separate blanket 100% module requirement;
- live progress, cancellation, terminal results, report opening, local back navigation, and
  background no-focus-theft work through an injected fake backend;
- a canary supplied through safe text and failure paths never appears as control sequences,
  traceback text, or markup;
- Ruff, formatting, strict mypy, import-linter, the full test suite, and distribution verification
  pass.
