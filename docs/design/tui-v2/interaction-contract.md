# Interaction and feature contract

## Commands and key maps

A semantic command definition contains identity, label, description, scope, availability, and
dispatch intent. It does not contain a physical key. The default key map and later user key maps
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
| reload current catalog | `r` |
| use selected resource in Compare | `c` |

The exact defaults for contextual Compare and analysis actions are declared with the implementing
surface. User remapping is a future TUI preference, so neither project YAML nor execution policy
owns it.

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

Environment and matrix-case selectors are local Explorer context. `Use in Compare` transfers only
safe resource identity and explicit local context. Sensitive values never reach a widget.

## Compare

Compare owns Assertions, Differences, Execution Profile, and the future Scenario mode in one
lifecycle:

1. Target uses project-aware native selectors for the starting profile, requests/scenarios,
   environment topology, matrix variants, validation, and safety. The scope summary identifies
   session overrides.
2. Plan is a dedicated immutable checkpoint. It shows destinations, methods, expanded scope,
   validation formula, limits, safety, sensitive source names, permitted destinations, writes,
   skips, diagnostics, and override provenance. Editing invalidates admission.
3. Run shows grouped cells and executions, aggregate progress, selected execution detail,
   baseline/candidate lanes, counters, elapsed time, and cancellation. It remains active while the
   user visits another workspace.
4. Analyze opens with Outcome and Issues, then drills through Occurrence to sanitized Evidence.
   Requests, Rules, Fields, Cells, and Timeline are alternate lenses over stable references.

LLM and SSE analysis presents normalized semantic, structural, and timing evidence. Chunk count or
ordering is not itself a failure unless configured comparison rules make it one. Raw chunks remain
an evidence view.

## Report

Report browses a bounded authenticated archive. The selected report shows outcome, important
causes, profile, environments, time, commit, and integrity. Opening analysis mounts the same
components as Compare with read-only access. Report never sends an HTTP request.

## Settings

Settings contains Appearance, Layout, Interaction, and Accessibility. It owns registered-theme
selection, density, default workspace, mouse behavior, glyph preference, reduced motion, and later
key-map editing. It never edits project configuration or execution policy.

## Safety

Presentation receives sanitized application projections only. Detached bodies and event streams
are fetched lazily through authenticated, bounded leases. Data-bearing Rich markup is disabled;
terminal controls and bidi controls are contained. Exceptions collapse to fixed safe messages.

Displayed and copied cURL is deferred until a sanitized resolved-request projection, explicit
secret placeholders, clipboard disclosure, and expiry behavior are accepted and tested.
