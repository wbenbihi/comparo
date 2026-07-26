# Comparo TUI v2 design contract

Date: 2026-07-26
Status: approved for implementation
Primary acceptance viewport: 160 columns by 52 rows

This directory is the product and visual contract for the production Comparo terminal UI.
It defines the interface that replaces the rejected alpha TUI.

## Authority

When sources disagree, use this order:

1. The generated screenshots in [`references/`](references/) define visual hierarchy,
   information density, screen composition, typography, borders, colors, and the information
   presented in each state.
2. The written decisions in this directory define interaction, safety, accessibility,
   responsiveness, theming, key mapping, and behavior that a static image cannot show.
3. The clickable browser mockup is an incomplete interaction aid only. It is not a visual or
   information-design reference.
4. Historical prototypes demonstrate useful capabilities only. They are not designs to copy.

The current TUI has no product, interaction, or visual authority. It may be inspected only to
identify safe application boundaries that remain useful and implementation that must be removed.

## Canonical product journey

Comparo helps a user answer five questions in order:

1. What am I validating?
2. Is the effective execution plan correct and safe?
3. What failed, changed, or could not be evaluated?
4. Where and when did it happen?
5. What sanitized evidence proves it?

The four persistent workspaces are Explorer, Compare, Report, and Settings. Compare owns every
live execution mode and the Target -> Plan -> Run -> Analyze lifecycle. Report is read-only and
mounts the same analysis components as Compare.

## Reference states

| Surface | Primary reference |
| --- | --- |
| Explorer request, resolved | [request resolved](references/explorer-request-resolved.webp) |
| Explorer request, overview | [request overview](references/explorer-request-overview.webp) |
| Explorer request, resolved body | [request body](references/explorer-request-body.webp) |
| Explorer environment | [environment](references/explorer-environment.webp) |
| Explorer schema | [schema](references/explorer-schema.webp) |
| Explorer matrix | [matrix](references/explorer-matrix.webp) |
| Compare target | [target](references/compare-target.webp) |
| Compare admitted plan | [plan](references/compare-plan.webp) |
| Compare running | [running](references/compare-running.webp) |
| Analyze issues | [issues](references/analyze-issues.webp) |
| Analyze requests | [requests](references/analyze-requests.webp) |
| Analyze rules | [rules](references/analyze-rules.webp) |
| Analyze fields | [fields](references/analyze-fields.webp) |
| Analyze timeline | [timeline](references/analyze-timeline.webp) |
| Analyze SSE evidence | [SSE evidence](references/analyze-sse-evidence.webp) |
| Report archive and summary | [reports](references/reports.webp) |

## Non-negotiable implementation rules

- Native Textual widgets own cursor movement, selection, scrolling, focusable controls, mouse
  behavior, tabs, input validation, footer dispatch, command palette, and worker lifecycle.
- Comparo widgets compose native widgets and add domain meaning. They do not recreate framework
  mechanics.
- Tab and Shift+Tab move between major regions. Arrow keys navigate within the active region.
- Highlighting an Explorer or analysis row updates its preview immediately. Enter activates or
  opens it.
- Every visible action is backed by one semantic command. Its button, active binding, footer
  entry, help entry, and palette entry use the same resolved command definition.
- Color, glyphs, physical keys, density, and breakpoints are never hard-coded in feature widgets.
- Widgets consume sanitized application projections. They never parse YAML, resolve secrets,
  execute HTTP, evaluate rules, read artifact paths, or reconstruct backend facts.
- No screen follows a permanent two- or three-pane template. The current decision determines the
  composition.
- The implementation is not accepted until the corresponding screenshot, keyboard, mouse,
  no-color, safety, and responsive tests pass.

See [visual-system.md](visual-system.md), [interaction-contract.md](interaction-contract.md), and
[acceptance.md](acceptance.md) for executable details.
