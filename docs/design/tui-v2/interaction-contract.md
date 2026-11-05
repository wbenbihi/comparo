# Interaction and feature contract

Implementation mapping updated **2026-11-05**. This remains a design contract; the
[current status](../../status.md) and [workspace guides](../../tui.md) distinguish implemented
controls from remaining acceptance targets. In older screenshots, Compare means Launch and Report
means Reports. Use the existing component library around Textual navigation primitives.

## Commands and key maps

A semantic command definition contains identity, label, description, scope, availability, and
dispatch intent. The default key map and user key maps
associate one or more Textual key strings with command identities.

The resolved key map is the only physical-key source for Textual bindings, buttons, native Footer,
the command palette, and contextual help. A required visible action must retain at least one
resolved binding. Conflicts fail closed within overlapping contexts and are allowed between
mutually exclusive contexts.

Defaults include:

| Command | Default |
| --- | --- |
| focus next major region | `tab` |
| focus previous major region | `shift+tab` |
| navigate active collection | arrow keys |
| activate selected item | `enter` |
| go back / close contextual inspector | `escape` |
| open command palette | `ctrl+p` |
| reload Explore catalog | `ctrl+r` |
| reload Reports archive | `r` |
| use selected Request/profile in Launch | `c` |
| prepare Launch plan | `ctrl+r` |
| execute reviewed plan | `ctrl+x` |
| cancel running execution | `ctrl+k` |
| Analyze Issues/Requests/Rules/Fields/Timeline | `alt+1` through `alt+5` |
| cycle Analyze views | `[` / `]` |
| inspect selected capture | `i` |

The implemented main-command remapping lives in [Settings](../../codebase/tui-settings.md), not
project YAML or execution policy. Supported keys are the command default, F1–F24 or Ctrl+Alt+letter;
conflicting assignments are rejected across nested contexts. Remapping every local component action
and a fully unified palette/help catalog remain broader design targets.

## Major-region focus

Tab does not walk every row or every control. Each surface registers a small ordered set of major
regions. Moving to a region restores its last focused native child. Within that region, a native
Tree, OptionList, SelectionList, Select, DataTable, Tabs, or scroll view owns navigation.

Mouse click focuses the clicked region and item. Mouse wheel scrolls the native scroll owner under
the pointer. A background update never changes workspace, selection, scroll position, or focus.

The native Footer displays active bindings merged from the application, workspace, and focused
widget. Footer bindings are clickable through Textual.

## Explorer

The left region is a filterable Tree grouped by kind with visible counts and concise resource
labels. Node highlight updates the right preview immediately. Node activation performs the
resource's primary action or opens the selected relation.

The inspector exposes applicable facets instead of empty universal tabs:

- Request: Overview, Resolved, Authored, Relationships; resolved sub-facets include URL/query,
  headers, body, and response contract.
- Environment: Overview, Available values, Sources, Relationships.
- Schema: Overview, Document, References, Provenance.
- Matrix: Overview, Axes and cases, Consumers, Authored.
- Profiles, policies, Value, and Project: kind-aware overview, effective composition,
  relationships, source, and provenance as permitted by their sanitized projection.

Environment and matrix-case selectors are local Explorer context. `Use in Launch` transfers only
safe resource identity and explicit local context. Sensitive values never reach a widget.

## Launch

Launch starts with Run requests, Run an execution profile, or Run assertions. Scenario execution
is unsupported and must not appear as a working mode. The lifecycle is:

1. Target starts with intent, then uses the component library for Request selection, saved/manual
   profile configuration, topology, variants, validation and safety. Profile selection belongs
   inside the profile section. The scope summary identifies session overrides. Save as profile
   reviews exact YAML before creating a new resource; unsupported invocation-only fields fail explicitly.
2. Plan is a dedicated immutable checkpoint. It shows destinations, methods, expanded scope,
   validation formula, limits, safety, sensitive source names, permitted destinations, writes,
   skips, diagnostics, and override provenance. Editing invalidates admission.
3. Run shows grouped cells and executions, aggregate progress, selected execution detail,
   baseline/candidate lanes, counters, elapsed time, and cancellation. It remains active while the
   user visits another workspace.
4. Analyze opens with Outcome and Issues, then drills through Occurrence to sanitized Evidence.
   Requests, Rules, Fields and Timeline are alternate lenses over stable references; Matrix cases
   stay in the Request/occurrence hierarchy rather than a separate Cells lens.

LLM and SSE analysis presents normalized semantic, structural, and timing evidence. Chunk count or
ordering is not itself a failure unless configured comparison rules make it one. Raw chunks remain
an evidence view.

## Reports

Reports browses a bounded authenticated archive. The selected report shows outcome, important
causes, profile, environments, time, commit, and integrity. Opening analysis mounts the same
components as Launch with read-only access. Reports never sends an HTTP request.

## Settings

Settings currently contains Appearance & layout, Interaction and Keyboard shortcuts. It owns
theme, density, startup workspace, mouse and main command remapping. Glyph preference, reduced
motion and broader accessibility controls remain design targets. Settings never edits project
configuration or execution policy. Theme/mouse changes apply next launch; spacing/keys apply on Save.

## Safety

Presentation receives sanitized application projections only. Detached bodies and event streams
are fetched lazily through authenticated, bounded leases. Data-bearing Rich markup is disabled;
terminal controls and bidi controls are contained. Exceptions collapse to fixed safe messages.

The shared capture inspector now displays/copies application-generated cURL from an authenticated
stored sanitized request. It declares replay limitations and clipboard disclosure, requires a separate
Copy action, expires its preview after 60 seconds and never injects live credentials or executes HTTP.

## Explorer 0.4.0 resource inspection

`r` resolves a Value or browses Matrix case data without a consumer picker. The active Environment
supplies available inputs but a standalone Value needs no Environment. Matrix page controls are
only collection paging; execution planning and trial selection belong to Launch. Body selection
updates the focused-path inspector; Schema selection updates the exact source/definition pane.
Enter follows typed resource links, and Escape returns through Explorer history with the prior
filter, view, cursor, focus and scroll. Shared ActionBar controls keep the established compact style.

Missing inputs, partial previews and locally unavailable schema targets remain explicit. A
masked value signals successful protection, not failure: violet masks and blue JSON booleans are
distinct, with textual markers retained without color. See [the implementation contract](../../explorer-0.4.0-plan.md).
