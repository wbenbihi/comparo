# TUI v2 acceptance matrix

Updated implementation mapping: **2026-11-05**. This is an acceptance checklist, not a pass
certificate. See [current QA](../../../design-qa.md) for evidence. Compare/Report reference captions
map to Launch/Reports; use the canonical component library, including its disclosure and selectors.

## Visual gate

Each reference-backed state is captured at 160 by 52 terminal cells with deterministic fixtures.
The real capture is reviewed beside the corresponding reference image. The screenshot is the
authority for hierarchy, density, placement, borders, information content, and visual emphasis.

The same state is exercised at 120 by 40 and 80 by 30 to prove deliberate degradation. A smaller
layout need not resemble the wide screenshot, but it must preserve the current decision, primary
action, active status, and access to every fact.

Theme captures cover the default dark theme, light theme, and no-color mode. Status must remain
unambiguous in every capture.

## Interaction gate

Headless Textual Pilot journeys prove:

- Tab and Shift+Tab traverse major regions, not collection rows.
- Arrow keys and mouse highlight update previews without Enter.
- Enter activates the highlighted entity.
- Lists and inspectors scroll with keyboard and mouse.
- Every visible action's resolved key calls the same command as pointer activation.
- The Footer changes when workspace, lifecycle, lens, selection, or focus context changes.
- Palette and help use the same command and resolved-key data.
- Remapping one key changes bindings, Footer, button keycap, help, and palette together.
- Conflicting key maps are rejected without a partially applied configuration.
- Background runs do not steal focus or navigation.
- Escape closes the contextual inspector before popping workspace history.

## Component and architecture gate

- Existing reusable components wrap native Textual mechanics; workspaces preserve their styling.
- Launch and Reports reuse `ResultsView` and the shared capture inspector.
- Theme definitions are the only raw-color source.
- Default and user key maps are the only physical-key sources.
- One breakpoint policy is shared by every component.
- Widgets and workspaces import safe application projections, never configuration, core, policy,
  adapters, live secrets, tracebacks, storage paths, or HTTP clients.
- Workspace modules compose components and remain free of business interpretation.
- Component modules remain independently testable and bounded in size.

## Safety gate

Canaries supplied through names, diagnostics, failures, headers, body evidence, artifact metadata,
and provenance never appear as terminal control sequences, Rich markup, traceback text, or live
secret material. Body/event access is authenticated, lazy, bounded, cancellable, and redacted
before presentation.

## Required journeys

1. Filter Explorer, highlight each supported resource kind, switch applicable facets, change
   environment/matrix context, follow a relationship, and transfer a Request into Launch.
2. Select an Execution Profile, edit Target sections, prepare an immutable plan, return to edit,
   re-prepare, run, inspect progress, cancel, and complete without losing state when switching
   workspace.
3. Analyze one failure from Outcome to Issue to Occurrence to Evidence, then pivot through Request,
   Rule, Field, and Timeline without losing the selected stable reference.
4. Inspect normalized SSE evidence with raw events available on demand.
5. Filter Reports, isolate a corrupt entry, open a valid report, and use the same analysis journey
   read-only.
6. Change theme and density, exercise no-color semantics, and verify that no execution setting or
   project file changes.

## Explorer 0.4.0 regression journeys

- Resolve a standalone Value with no consumers and no Environment; preserve public nested values
  and mask secrets/derived secrets. Missing inputs must not conceal unrelated fields.
- Browse unused and multiply-used Matrices without choosing a consumer; page data cases, return
  from Source/Graph and reject stale worker output.
- Select a nested Body leaf in JSON, verify its path/directive/declaration, and copy only masked
  data. Inspect boolean and masked fields together in dark/light/no-color modes.
- Expand nested Schema objects/items, required fields, local cross-schema references and cycles;
  update the source pointer inspector and label remote/sample-validation capabilities honestly.
- Follow resource links and return with filter, cursor, focus and scroll restored. Native Textual
  Buttons must not be imported directly by resource inspectors.

## Build gate

Ruff, formatting, strict mypy, import-linter, the full test suite, the repository-wide 95 percent
combined statement/branch coverage floor, screenshot acceptance, and distribution verification
must pass before handoff. Preserve existing coverage; the floor is not a reduction target.
