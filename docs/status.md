# Current implementation status

Reconciled against the working tree on **2026-11-02**. Package metadata remains **0.2.0**, alpha,
Python **3.13+**. The rebuilt `comparo/v1alpha1` implementation and recent TUI work are unreleased;
the dated entries in [CHANGELOG](../CHANGELOG.md) describe earlier releases, not this working tree.

## Implemented product surfaces

| Surface | Current behavior |
| --- | --- |
| CLI | Schema, validation, planning, diagnostics, sanitized previews, Request assertions, directed diffs, execution profiles, artifacts and JSON/Markdown/JUnit/SARIF reports |
| Explore | Resource hierarchy, kind-aware overview, masked source/effective/resolved inspection, dependencies, Environment context and health checks; `c` hands a Request/profile to Launch |
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

The latest feature verification passed **176 TUI/architecture tests** and **49 artifact/body/cURL
tests**, plus Ruff, formatting, strict mypy and all **12 import contracts**. These are targeted
results, not a claim of full-suite coverage or universal mockup acceptance. Test discovery on
November 2 found **4,313 cases** after the Settings section tests; collection is not execution. The configured
coverage threshold is now 95% (maintainer-approved November 2), and a full release coverage/build audit remains a separate gate.

This documentation refresh additionally passed **296 CLI/architecture tests**, Ruff, formatting
and strict mypy. All three example projects validated, and all six showcase ExecutionProfiles
planned successfully without HTTP. Local Markdown file links and anchors were checked; external
links and a new complete visual review were not part of this refresh.

The November 2 commit checks passed **416 TUI/CLI/architecture tests** before adding General,
then **20 Settings/build/packaging tests** for that addition. The General layout was inspected at
80 columns. Built wheel/sdist members passed the distribution audit; a wheel rebuilt from the
sdist retained the same build SHA. This verifies provenance packaging, not full release qualification.

The subsequent sandboxed full-suite run had **4,289 passing / 24 failing tests** and measured
**97.33% coverage**. Of those failures, 23 were localhost binding restrictions and one was the
showcase inventory's stale 44-resource expectation (45 are present). These results do not pass
the release gate. The release PR delegates definitive validation to GitHub Actions; see the
[release guide](releasing.md) for checks, required follow-up and publication after merge.

Initial GitHub Actions validation confirmed 4,312 passing tests on Python 3.13 and 4,311 on
3.14; failures were the stale example count and a 3.14 catalog-shutdown race. Both have focused
fixes and targeted regression coverage. Both compatibility jobs passed in the second run; its
main job found a separate action-bar timing race and measured **98.00% coverage**. The shared
component now preserves buttons during availability updates, with regression coverage. The
maintainer approved a **95% overall coverage floor**, retaining branch measurement and existing
tests. Full CI, distribution validation and installed-wheel checks must still pass.

- [User TUI guide](tui.md), [Launch](codebase/tui-launch.md), [Reports](codebase/tui-reports.md),
  [Settings](codebase/tui-settings.md): actual controls and behavior.
- [Application APIs](codebase/application-api-reference.md): backend contracts and ownership.
- [Design QA](../design-qa.md): current evidence and outstanding visual acceptance work.
- [Code health snapshot](../CODE_HEALTH_REPORT.md): measured source inventory and verification limits.
- [ADRs](decisions/README.md): accepted product constraints; their original dates remain historical.
