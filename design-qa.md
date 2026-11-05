# Current TUI design QA

Updated **2026-11-05**. Implementation: `src/comparo/tui`. Primary reference viewport: **160×52**;
responsive regression sizes: **120×40** and **80×30**. Approved mockups remain in
[`docs/design/tui-v2/references`](docs/design/tui-v2/references/).

## November 5 release preparation

The final shared palette gives `value.masked` / `status.redacted` a dedicated violet token in dark
and light themes, while `value.boolean` is blue. Matrix fields use the same semantic spans rather
than mapping masks to warning tones. Body captures with `true` and `<masked>` visible together
were reviewed in both themes. The [current Body capture](docs/assets/explorer-body-0.4.0.png) is
reproducible with the [inspection fixture](examples/explorer-inspection/README.md); its 0.3.0 header
reflects pre-release package metadata, not the absence of branch changes. Textual markers remain
present independently of color.

The final document/Matrix subset passed 13 tests. Full-suite, CLI rerun, coverage, static and
packaging evidence is recorded in [code health](CODE_HEALTH_REPORT.md), including the two stale
version assertions corrected after the full run. This does not claim every state/theme/viewport
has received a new paired mockup review.

## Current evidence

The November 5 Explorer correction replaces the previous execution-oriented Value and Matrix
navigation. Values resolve directly from Project and active Environment inputs, including declared
parameter defaults, without a consumer selector. Public siblings remain readable beside masked
secrets. Matrix shows generated cases and selected values; consumer, binding, trial and match
controls have been removed from Explorer. Pagination appears only when a case list needs it.

Request Body now shows numbered JSON with a synchronized path/value/directive/source inspector
and a shared compact masked-copy action. Schema navigation preserves array item levels, nested
required flags, definitions and combinators; project-local references expand and cycles stop
explicitly. Resource inspectors use the shared action component, enforced by an architecture test.

The permanent [inspection fixture](examples/explorer-inspection/README.md) includes nested public
data, secret-derived values, an unused Matrix, a nested Request body and cross-schema/recursive
references. Pilot captures were generated at 160×52, 120×40 and 80×30. The reviewed wide captures
show Value masking, Matrix cases, the selected Body token and a required array-item schema field;
the reviewed narrow Body capture shows the selected path inspector below the document.

Focused regressions cover standalone resolution without any Environment or consumer, masked JSON
with public siblings at all three sizes, schema references/cycles, case pagination, Source return
and late-result rejection. The final focused Explorer suite passes 63 tests; the overlapping
resolution/document suite passes 80, and the 28 local-server regressions for Environment health,
Launch and Run analysis pass. Ruff, formatting, strict mypy and all 12 import
contracts pass. This is not whole-product visual acceptance. Remote schema fetching and sample
validation remain explicitly unperformed in the UI; no validation success is implied.

The resource journeys capture twelve resource kinds at 160×52 dark, 120×40 light and 80×30
monochrome, and assert collection/detail selection and responsive geometry. Captures were reviewed
for Matrix, Schema, Request, Environment, Value and rule profiles. Review found and fixed a shared
collection container retaining fractional height, an embedded tree inheriting the navigator's
fractional height, and invalid preview tone classes. Earlier Schema captures include the composition
and usage sections below the bounded tree. This is not a full cross-product visual sign-off for
every resource, interaction, theme and viewport.

Reproduce the focused evidence with:

```console
COMPARO_SCREENSHOTS=/tmp/comparo-040-resource-review uv run --frozen python -m pytest \
  tests/tui/test_resource_inspectors.py
uv run --frozen python -m pytest \
  tests/tui/test_matrix_browser.py tests/tui/test_resource_navigation.py \
  tests/tui/test_explorer_documents.py tests/application/test_value_inspection.py \
  tests/core/resolution tests/architecture/test_tui_architecture.py
```

Resource captures use the optional directory above; Matrix/navigation captures use pytest temporary
directories. Generated screenshots are reproducible evidence, not durable links to this session's
`/tmp` files. The [Explorer scope](docs/explorer-0.4.0-plan.md) records current limits and remaining
release-acceptance journeys.

The latest feature validation passed **176 TUI/architecture tests**, plus **49 artifact/body/cURL
tests**. Pilot journeys cover Target/Plan/Run/Analyze, Requests and Matrix cases, retained results,
Reports archive/filter/export, keyboard lens switching, persistent Settings, manual profile saving,
Explore-to-Launch handoff and shared capture inspection. Both Analyze hosts open the same inspector
and restore selection; live shortcut remapping is checked in both hosts.

The latest visual inspection included Settings at 120 columns in dark/light themes and the capture
inspector at 80 columns. Compact capture actions were shortened so Back remains visible; the active
raw/JSON/SSE/cURL mode is emphasized. These inspections do not establish whole-product acceptance
against every mockup or at every theme/viewport combination.

The November 2 General addition was inspected at 80 columns: full SHA and source link wrap inside
the existing key/value component, the read-only section remains first, and selecting it keeps
keyboard focus in the section menu. Settings/build/packaging regressions passed 20 tests.

The existing shared component library, semantic method colors, entity glyphs and status vocabulary
remain required. Launch and Reports share `ResultsView` and the capture inspector. The Run screen
remains visible after completion until Analyze is explicitly opened. Settings applies spacing and
main key changes on Save; theme and mouse changes take effect next launch.

## Reproduce and review

From the repository root, start the demo server in one terminal, then launch the showcase in another:

```console
uv run --frozen python examples/support/demo_server.py
uv run --frozen comparo tui --config examples/tui-showcase/comparo.yaml
```

Use **Launch → Run an execution profile → Launch review** for a mixed 22-call run. Compare Target,
Plan, Run, Analyze and Reports with their reference images. Test keyboard navigation and resizing,
including returning from saved Analyze to the same archive row.

```console
COMPARO_SCREENSHOTS=/tmp/comparo-tui-review uv run --frozen python -m pytest \
  tests/tui/test_launch_workspace.py tests/tui/test_run_analysis.py tests/tui/test_reports.py
uv run --frozen python -m pytest \
  tests/tui/test_settings.py tests/tui/test_profile_authoring.py tests/tui/test_capture_inspector.py
```

Settings/capture test snapshots use pytest temporary directories. Screenshots are generated evidence;
links to a previous session's `/tmp` files are not durable acceptance records.

## Still requiring separate acceptance

- A fresh paired review of every reference-backed state across dark/light/no-color and all three sizes.
- Complete palette/help/keycap parity for local component actions; current user remapping covers the
  main workspace, Launch, Analyze and quit commands.
- Design targets without controls yet: glyph preference, reduced motion and broader accessibility.
- Large-archive cursor UX, annotations and reviewed retention/restore controls; backend APIs alone
  do not satisfy these product flows.

The [acceptance matrix](docs/design/tui-v2/acceptance.md) remains the review checklist. Earlier
blanket pass/fail claims are historical, including the
[2026-07-26 report](docs/history/tui-design-qa-2026-07-26.md). No new full visual sign-off or coverage
percentage is claimed by this documentation refresh.
