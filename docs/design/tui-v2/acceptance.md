# TUI v2 acceptance matrix

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

- Native Textual widgets own standard mechanics.
- Compare and Report mount the same analysis component.
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
   environment/matrix context, follow a relationship, and transfer a Request into Compare.
2. Select an Execution Profile, edit Target sections, prepare an immutable plan, return to edit,
   re-prepare, run, inspect progress, cancel, and complete without losing state when switching
   workspace.
3. Analyze one failure from Outcome to Issue to Occurrence to Evidence, then pivot through Request,
   Rule, Field, and Timeline without losing the selected stable reference.
4. Inspect normalized SSE evidence with raw events available on demand.
5. Filter Report, isolate a corrupt entry, open a valid report, and use the same analysis journey
   read-only.
6. Change theme and density, exercise no-color semantics, and verify that no execution setting or
   project file changes.

## Build gate

Ruff, formatting, strict mypy, import-linter, the full test suite, 100 percent statement and branch
coverage, screenshot acceptance, and distribution verification must pass before handoff.
