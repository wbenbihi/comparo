# TUI4 resource component contract

This document is the extension guide for Explorer resource presentation in `src/comparo/tui4`.
The implementation deliberately separates project inspection, navigation, presentation state,
kind-specific projections, widgets, and effects.

## Data flow

```text
application inspection DTOs
        │
        ▼
ExplorerCatalog / ResourceEntry       models/explorer.py
        │
        ├──► ExplorerResourceTree      components/explorernavigator.py
        │       └── generic TreeMenu
        │
        ▼
ResourceDetail lifecycle state        models/resource.py
        │
        ├──► pure kind projections     models/resource_presenters.py
        │       └── secret-safe fields / Matrix structure
        │
        ▼
explicit kind widget registry         components/resources/registry.py
        │
        ▼
ResourceDetailOverview host           components/resourcedetail.py
        │
        └── semantic ActionRequested
                │
                ▼
ExplorerWorkspace workers             workspaces/explorer.py
                └── TuiBackend → application services
```

Widgets do not compile configuration, resolve values, read files, or call application services.
The workspace is the effect coordinator and applies worker results only when the resource key and
selection generation still match.

## Navigation boundary

`TreeMenu[T]` is the generic collapsible-menu primitive. It knows immutable `MenuNode` values,
cursor preservation, expansion, and generic highlighted/selected messages. It knows nothing about
Comparo resources.

`ExplorerResourceTree(TreeMenu[str])` owns the conversion from an `ExplorerCatalog` plus filter
text to menu nodes and the Explorer-specific row grammar. `TreeMenu.format_node(node)` is the
single formatting hook called for every branch and leaf during a rebuild; subclasses customize
rows there without duplicating tree construction, expansion, cursor, or mouse mechanics.
Explorer rows deliberately display the resource name but not its internal ID; the ID remains in
the immutable node key for stable selection and in the resource detail data for inspection.
`ExplorerNavigator` owns the filter, resource tree, optional legend, focus transitions, and
conversion from a generic menu message to `ResourceHighlighted(entry)`. The Explorer workspace's
default `l` binding delegates to the navigator, which hides the legend completely so the tree
reclaims the available rows. The key declaration remains centralized in `bindings.py` for future
remapping.

`ExplorerWorkspace` must not:

- construct `MenuNode` values;
- assign `TreeMenu.nodes`;
- handle `TreeMenu` messages;
- query the filter or legend directly; or
- duplicate resource matching logic.

It loads the catalog, assigns it to `ExplorerNavigator`, and consumes the navigator's semantic
resource message.

## Resource representations

`ResourceDetail` keeps each representation's state independently through
`ResourceInspection[T]`:

| Representation | Source | Disclosure rule |
| --- | --- | --- |
| Overview | `ProjectResourceSummary` and its outline | Value-safe application projection |
| Authored | `inspect_authored_resource` | Exact resource document, masked by default, revision pinned |
| Effective | `inspect_effective_resource` | Compiled structure and relationships; values remain conservative |
| Resolved | Compare preparation inspection | Requires an Environment/occurrence/Matrix context and remains unavailable without it |

Authored, effective, and resolved are separate products. A widget must never synthesize one from
another. In particular, Explorer must not resolve a Request without an execution context.

## Kind widgets

`BaseKindResourceDetails` supplies immutable `resource` and `entry` accessors plus the shared
field renderer. Its `entries` accessor is retained as a compatibility alias; new code uses
`entry`.

The explicit registry maps stable application kind strings to widget classes. It contains current
resource kinds and uses `GenericResourceDetails` for future or plugin-defined kinds. Registration
is explicit rather than an import-time subclass side effect, which makes missing and duplicate
registrations testable.

Current widget families are:

| Widget | Kinds |
| --- | --- |
| `ProjectResourceDetails` | `Project` |
| `EnvironmentResourceDetails` | `Environment` |
| `RequestResourceDetails` | `Request` |
| `MatrixResourceDetails` | `Matrix` |
| `SchemaResourceDetails` | `Schema` |
| `ValueResourceDetails` | `Value` |
| `ProfileResourceDetails` | Assertion/Diff/Gate/Safety/Redaction/Artifact/Execution profiles and `ExecutionPolicy` |
| `ScenarioResourceDetails` | `Scenario` |
| `GenericResourceDetails` | Unknown/future kinds |

Kind widgets consume pure projections from `models/resource_presenters.py`; they do not traverse
compiler objects. Matrix presentation lists declared axes and case collections from the safe
outline. Expanded execution cells are deliberately a Compare-plan action because multiple Matrix
attachments, filters, includes/excludes, and invocation overrides affect the real cell set.

## Adding a kind

1. Confirm that `inspect_project_workspace` exposes enough secret-safe structure. Add an
   application inspection DTO if the required information is unavailable; do not import core
   compiler objects into TUI code.
2. Add a pure presenter in `models/resource_presenters.py`. It accepts `ResourceEntry` and returns
   immutable fields or a focused immutable projection.
3. Add a widget under `components/resources/`. Keep shared mechanics in the base or reusable
   components and kind semantics in the subclass.
4. Add the stable kind string to the widget's `KINDS` and the widget class to
   `_REGISTERED_WIDGETS` in `registry.py`.
5. Add semantic actions to `models/resource.py` only when their capability is real. A selectable
   unavailable representation may explain its missing context; disabled effect actions require a
   trusted explanation.
6. Add the default key in `bindings.py`; every rendered action button must show its key. Future
   remapping can replace this binding source without changing widgets.
7. Test the pure projection, registry selection, capability state, and mounted interaction.

## Styling and interaction rules

- Colors, glyphs, and semantic tones come from `theme.py` and TCSS; kind widgets do not embed
  palette values.
- `BadgeSpec` is the shared badge contract. `badge_text()` embeds it in Rich tree/table text and
  the native `Badge` widget uses the same renderer in layouts. Its `solid`, `quiet`, and `status`
  emphasis variants express role, metadata, and live state without faux rounded Unicode caps.
- Buttons are native Textual `Button` components and expose the same semantic action as their key.
- Widgets emit Textual messages rather than callback closures or workspace imports.
- Authored content is masked by default. Failures cross the boundary only as closed, value-free
  codes; unexpected exceptions collapse to `internal`.
- Resource coordinates include the fingerprint and source reads pin the expected SHA-256. Worker
  output for an old selection is discarded.
