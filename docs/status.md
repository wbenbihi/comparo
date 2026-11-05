# Current implementation status

Reconciled on **2026-11-05** for **0.4.0 preparation**, based on tagged `v0.3.0`.
Package metadata remains **0.3.0** until GitHub Actions creates the next version commit. Python
**3.13+** is required; the project remains alpha. The [0.4.0 notes](releases/0.4.0.md) describe
branch changes, while [CHANGELOG](../CHANGELOG.md) separates them from tagged releases.

## Implemented product surfaces

| Surface | Current behavior |
| --- | --- |
| CLI | Single-file/split starter projects, managed project-aware shell completion, bundled Rich/Markdown/JSON docs and an AI authoring skill; schema, validation, planning, diagnostics, sanitized previews, Request assertions, directed diffs, execution profiles, artifacts and JSON/Markdown/JUnit/SARIF reports |
| Explore | Kind-aware inspectors; standalone Value resolution and Matrix cases; numbered JSON Body/path inspection; nested/local-reference Schema browsing; reversible relationships; masked source, Environment context and health checks; `c` hands a Request/profile to Launch |
| Launch | Requests, saved/manual profiles or assertions; Target → Plan → Run → Analyze; Matrix case filters, trials, retries, fail-fast, assertion/diff/gate overrides, mutation approval and cancellation |
| Profile saving | Review ID/name, exact YAML and destination; validate and exclusively save a new ExecutionProfile; refresh Explore and Launch |
| Analyze | Shared Issues, Requests, Rules, Fields and Timeline lenses, occurrence identity, HTTP metadata, rule evidence and body comparison |
| Capture inspector | Paged raw captures, JSON subtrees, SSE events/nested JSON, and review/copy of sanitized request cURL; shared by Launch and Reports |
| Reports | Authenticated bounded archive, metadata search/filtering, outcome preview, shared Analyze, and explicit export without overwriting existing files |
| Settings | General package/build information first; user-owned dark/light theme, spacing, startup workspace, mouse reporting and main workspace/Launch/Analyze/quit shortcut remapping |
| Debug | Existing reusable component gallery |

The canonical package is `src/comparo/tui`, developed as TUI4. Retired `tui3`, `tuibis` and the
versioned `tui4` entry point are removed. Retained design references are under `docs/design/`;
reference filenames containing `compare` and `report` map to today's Launch and Reports workspaces.

## Limits and application timing

- Value inspection needs no Request, Scenario or Environment. Project defaults and the active
  Environment supply declared inputs; absent runtime-only inputs stay unavailable. Public siblings
  remain readable beside masked secrets. Samples are regenerated when inspection is refreshed.
- Matrix inspection compiles up to 10,000 data cases, pages 50 cases at a time and previews up to
  64 value nodes. There are no consumer, binding, trial or match selectors in Explorer.
- Request Body is a safe JSON preview, not a transmitted or validated request. Its document is
  capped at 512 lines; the resolved overlay covers at most 16 field targets with up to 64 nodes
  per target. Uninspected leaves stay marked as not resolved. Copy uses the masked preview.
- Schema browsing expands project-local references with explicit cycle stops and bounds of
  512 nodes/depth 32. Remote schemas are not fetched by Explorer and sample validation is not
  implemented there. This is separate from runtime response-schema validation.

- Reports loads at most 500 physical archive entries by default (`--maximum-report-entries`),
  then filters and displays 50 reports per page. This is not cursor-based large-archive browsing.
- Raw capture windows are 64 KiB. JSON/SSE pages contain at most 50 items; structured parsing is
  bounded to 4 MiB, 50,000 JSON nodes, depth 64, 10,000 SSE events and 64 KiB per SSE line.
  Node/data previews are capped at 1,024 characters. Larger captures remain accessible through
  the raw pager within the selected artifact read limits.
- cURL uses the exact stored request attempt/exchange, embeds at most 16 KiB of body and produces
  at most 64 Ki characters. It declares replay limitations, never injects live credentials and
  never sends HTTP. Preview text expires after 60 seconds; the system clipboard is user-owned.
- Profile YAML cannot represent invocation-only case filters or clearing an inherited gate.
  Such choices are rejected rather than dropped. Invocation value, Request, Matrix replacement
  and budget overrides are also outside this writer. Mutation approval is never persisted.
- Density and supported shortcut remaps apply when saved. Theme, mouse and startup-workspace
  choices apply next launch. Preferences default to `~/.config/comparo/tui.json` and respect
  `XDG_CONFIG_HOME`; they do not edit project configuration or execution policy.
- Non-persisted runs retain result metadata but do not retain temporary detached body bytes for
  later inspection. Persisted capture availability still depends on capture policy and success.

## Backend capabilities without current TUI controls

The application APIs additionally support cursor-based archive queries, annotations, reviewed
retention/trash/restore, paged result projections and broader invocation overlays. Their availability
must not be read as a claim that the current Reports or Launch form exposes every operation.
Large-archive UX remains follow-up work. See the [backend capability audit](codebase/frontend-capability-audit.md).

Generic resource editing, glyph preferences and reduced-motion settings are not implemented.
Scenario declarations can be inspected, but stateful Scenario/step execution is rejected before
effects. WebSocket/gRPC, community plugins, external judges and dedicated restricted CI integration
remain deferred. Browser automation is outside the product scope. The repository's own CI workflow
is separate from a distributable Comparo GitHub Action. Release qualification remains pending.

## Verification and sources of truth

Local verification for this branch is recorded in the [code health snapshot](../CODE_HEALTH_REPORT.md)
and [design QA](../design-qa.md). A local pass is not a GitHub Actions release qualification or a
claim of complete mockup acceptance. The repository requires 95% combined statement/branch
coverage, strict static checks, Python 3.13/3.14 compatibility, distribution membership/integrity
checks and an installed-wheel smoke test before publication. See the [release guide](releasing.md).

Historical 0.3.0 CI results belong to the [0.3.0 release record](releases/0.3.0.md); they do not
certify new 0.4.0 code.

- [TUI guide](tui.md), [Launch](codebase/tui-launch.md), [Reports](codebase/tui-reports.md),
  [Settings](codebase/tui-settings.md): actual controls and behavior.
- [Explorer contract](explorer-0.4.0-plan.md): standalone inspection, navigation and bounds.
- [Application APIs](codebase/application-api-reference.md): backend contracts and ownership.
- [ADRs](decisions/README.md): accepted constraints; original decision dates remain historical.
