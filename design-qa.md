# Current TUI design QA

Updated **2026-11-02**. Implementation: `src/comparo/tui`. Primary reference viewport: **160×52**;
responsive regression sizes: **120×40** and **80×30**. Approved mockups remain in
[`docs/design/tui-v2/references`](docs/design/tui-v2/references/).

## Current evidence

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
