# TUI results implementation contract

This contract defines the reusable, sanitized result surface shared by live Compare executions and
persisted Report entries.

## Boundary

The only result input is `application.results.ResultProjection`, derived from a sanitized
`RunArtifact`. Presentation code must not:

- accept live HTTP observations, run contexts, or sensitive values;
- open artifact blobs or storage paths directly;
- recompute assertion, diff, completion, or gate outcomes;
- infer missing facts;
- render exception text or enable Rich markup for data-bearing content.

Detached response bodies require a future explicit, bounded content-lease application service.
They are not loaded by the manifest result projection.

## Component ownership

| Component | Single responsibility |
| --- | --- |
| `ResultsWorkspaceView` | Shared responsive composition for live and stored results. |
| `ResultSummary` | Artifact-owned completion, gate, and roll-up metrics. |
| `ResultIndex` | Typed execution, assertion, comparison, or diff rows. |
| `ResultDetail` | Evidence-light detail for one stable result reference. |
| `StatusBadge` | Sole glyph, label, and semantic CSS mapping. |
| `StatePanel` | Shared loading, empty, error, and content lifecycle. |

Compare mounts the surface with `ResultAccess.LIVE`; Report mounts the same class with
`ResultAccess.READ_ONLY`. Access is a UI capability label, not an alternate interpretation of the
artifact.

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
- The owning workspace records the reference in its own navigation stack.
- `esc` pops that local stack before changing the workspace surface.
- Opening a report never enters Compare; completing a live run never enters Report.
- Background completion updates Compare without changing active workspace or focus.

## Safety and resource contract

- Runtime progress is a latest-value mailbox, not an event log.
- Report listing bounds every physical directory entry, including rejected names.
- Stored reports are authenticated before listing and verified before opening.
- Text is bounded and terminal-control-safe.
- A transport error remains distinct from a failed assertion or diff.
- A missing comparison side remains unavailable; it is not synthesized as drift.
- Color never carries meaning without a visible glyph and label.

## Acceptance gate

The implementation is complete only when tests prove:

- the real shared result view mounts at 80, 120, and 160 columns;
- Compare and Report instantiate the same result component;
- every component/workspace module reaches 100% statement and branch coverage;
- live progress, cancellation, terminal results, report opening, local back navigation, and
  background no-focus-theft work through an injected fake backend;
- a canary supplied through safe text and failure paths never appears as control sequences,
  traceback text, or markup;
- Ruff, formatting, strict mypy, import-linter, the full test suite, and distribution verification
  pass.
