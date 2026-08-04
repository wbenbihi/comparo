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

The overview header is intentionally compact. It renders the plain description followed by one
muted provenance line: abbreviated resource revision, logical source and start line, then authored
tags. Stable identity remains in `ResourceDetail` and the panel title instead of consuming a
separate key/value block. The active representation is the panel's secondary border hint, keeping
`overview`, `authored`, `effective`, or `resolved` out of the identity title.

### Common inspection contract

Project, Environment, and Request inspection deliberately have no numbered representation bar.
Their views are workspace bindings, enabled and rendered in the footer only while a resource is
selected. Request keeps only its workflow actions (`u` Use in Compare and the currently disabled
composition action) as Textual buttons, so mouse users retain the same capabilities:

| Key | View | Contract |
| --- | --- | --- |
| `o` | Overview | Return to the kind-specific overview without changing its raw/resolved mode |
| `s` | Source | Load the revision-pinned, masked authored document through the existing worker |
| `g` | Dependency graph | Show every safe one-hop `uses` and `used-by` relationship in the resource outline |
| `r` | Raw/resolved | Toggle the value layer inside Overview and return to Overview |

The selected representation and raw/resolved Overview mode belong to the Explorer workspace, not
to an individual tree row. Selecting another resource retains that view. Source and resolved
views load the newly selected resource's exact revision/context, Graph projects the new
resource's relationships, and stale worker output is rejected by the selection generation.
Changing the execution Environment while resolved mode is active likewise reloads context-bound
data without returning the user to raw Overview. A successful catalog reload follows the same
rule: the selected logical resource is rebound to its new exact revision, the active view is
rebuilt, and Source is re-read against the new whole-file digest even when only a sibling YAML
document changed.

`r` does not resolve values in the Explorer process. Raw mode uses the safe resource outline;
entering resolved mode asks `TuiBackend` for an ephemeral single-Environment assertion
preparation and calls the application-owned masked batch inspection. For a Request, that
preparation explicitly selects only the highlighted Request ID, and the backend verifies both the
exact Request revision and Environment revision on the returned occurrence before inspecting any
field. Project and Environment inspection use the Project's default subject only as the execution
context required by the resolver. The handle is always discarded after success, failure, or
cancellation. The returned view includes the Environment revision, execution ordinal, cell/trial
identities, exact field paths, safe node previews, and an explicit incomplete marker when the
frontend-owned inspection budget is reached. Explorer inspects at most 16 field roots per request,
64 projected nodes per root, 4 KiB per scalar preview, and an aggregate 4 MiB traversal budget.
Additional roots produce a safe partial view rather than failing the entire resolved screen. These
are display/inspection budgets only; they do not constrain execution or artifact capture. Kind
widgets overlay those exact paths onto the same
cards, sections, ordering, and setting rows used by raw mode; resolved mode is not a separate
diagnostic layout.
Public runtime expressions and inert exact compiled values are substituted in place, while
sensitive values remain `<masked>`. Runtime expressions come from the application-owned masked
resolver. Inert literals come from `ProjectOutlineField.exact_value`; linked identities additionally
use the field's typed `reference`. The application contract excludes value namespaces, bounds and
escapes strings, and reduces Project data/source paths to basenames. The TUI does not parse
reference display text, read authored YAML, expose filesystem coordinates, or invent an exact
preview. Other non-value structure retains its compiled structural presentation.

Exact and runtime values use shared semantic theme tokens for null, boolean, integer/number,
text, reference, and masked values. A reference also carries its target kind into the TUI model;
`ResourceSettingRow` renders the shared colored kind glyph before the target identity only when the
resolved overlay is active. Raw mode remains a faithful structural `$use` presentation. Widgets
must not parse `Kind/id` strings to rediscover that metadata.

The execution context is shared application presentation state, not a property of one widget. On
project load it rebinds an existing Environment identity to the new revision, otherwise defaults
to the Project baseline (or the first Environment when no baseline exists). The active Environment
has an `EXEC` badge in `ExplorerResourceTree`. Highlighting an Environment and pressing `Space`
changes the context and invalidates any stale resolved inspection. Enter and mouse selection only
select/open a resource; they never mutate the execution context. The contextual footer binding is
shown only while an Environment is selected.
The `EXEC` badge uses the shared semantic `active` badge tone, keeping its blue treatment distinct
from baseline, candidate, locality, and live-danger badges and replaceable by future themes.

The dependency graph is a presentation of application-owned relationship DTOs, not a second
dependency resolver. `ResourceDependencyTree` inherits the generic `TreeMenu` mechanics and owns
only the directional branches and row grammar.

Explorer reload is `Ctrl+R`, leaving the unmodified `r` key to switch resource value layers.

Resource provenance renders `ProjectSourceSummary.filename`, a safe basename projected by the
application boundary. Logical IDs such as `entry-config` remain internal coordinates used for
revision-pinned reads and must not be presented as filenames.

## Kind widgets

`BaseKindResourceDetails` supplies immutable `resource` and `entry` accessors plus the shared
field renderer. Its `entries` accessor is retained as a compatibility alias; new code uses
`entry`. Each subclass also owns a `ResourcePresentation` declaration for host chrome: whether
common representation hints apply and which semantic actions are visible. The generic host asks
the registry for that declaration; it contains no Project, Environment, or Request kind switch.

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

The Project widget is intentionally more explicit than the generic field-based widgets. Its two
comparison cards obtain baseline/candidate names and `local`/`live` locality from the related
Environment catalog entries. Dedicated source, variable, secret, and semantic default sections
then enumerate every field available in the Project's safe structural outline, including nested
definitions. Literal secrets remain redacted and other literals retain the outline contract's
safe previews; the widget never reparses `comparo.yaml` or resolves a value itself.

The Environment widget uses one stable raw/resolved layout: execution-context/role/locality,
connection, runtime inputs, request defaults, policies, health checks, and additional
configuration. Resolution is always bound to the Environment being inspected, irrespective of
the shared execution context badge. In resolved mode, typed public values and masked secrets use
semantic theme tokens; reference kind glyphs appear only when the overlay proves the resolved
target metadata.

The Request widget likewise retains one layout across raw and resolved modes: method/routing,
parameters, query/headers/cookies/authentication, payload/body type, stream/timeouts/retry/redirect
behavior, Matrix expansion, Schema/assertion/diff evaluation, and safety/redaction/artifact
attachments. Empty optional sections are omitted so a minimal Request remains compact; every
authored section appears as soon as the corresponding capability exists. The compact top strip
consumes only the immutable entry and pure Request projection and communicates method,
relative-versus-absolute routing,
streaming, and Matrix count. Resolved inspection selects visible Request fields independently;
this is security-significant because a sensitive cookie or authentication object collapses to
`<masked>` without unnecessarily collapsing unrelated public method, endpoint, query, or header
values. Exact linked Matrix, Schema, and profile identities gain their kind glyph only in resolved
mode. `u` forwards the exact Request coordinate to Compare; the disabled composition action does
not imply a locally synthesized effective request.

An Environment may declare ordered `healthChecks` references to `Request` resources. The
contextual `h` binding is available only when at least one probe is declared and no probe run is
already active. The backend selects those requests and that Environment in a normal assertion
plan, keeps safety policy authoritative, persists the sanitized run for Report, and derives health
from terminal completion, gate verdict, and adverse assertion/diff facts. Operational health state
belongs to the Explorer workspace and exact Environment revision, not the selected detail row.
Tree selection and execution-context changes therefore neither cancel nor detach a probe; returning
to the Environment shows the same loading/result state and cannot launch a duplicate. Completion
additionally uses `App.notify` rather than constructing Textual Toast widgets directly; toast text
is bounded, value-free, and rendered with Rich markup disabled.

Threaded Compare preparation is serialized by the backend. Cancellation keeps that ownership until
the worker thread has actually returned, then discards any late preparation handle before
propagating cancellation. Shutdown therefore cannot close `CompareService` underneath an orphaned
preparation. Health state is retained only while both the Environment coordinate and its ordered
Request coordinates still match the latest catalog; a reload that changes a probe revision drops
the old status.

Health execution identities come from the ordered, revision-bearing
`ProjectOutlineField.reference` projection, not the one-hop dependency graph. The configuration
compiler rejects repeated target identities (including qualified/unqualified spellings), while the
backend repeats that uniqueness check at its trust boundary instead of silently changing a run.

Scenario execution remains deferred. The current planner override surface lowers runtime subject
choices to resource IDs; if a project deliberately defines both `Request/foo` and `Scenario/foo`,
the health preparation fails closed with `planning.subject.not-found-or-ambiguous` before a handle,
run, or network effect exists. Scenario implementation must carry kind-qualified subject identity
through planning rather than adding a Request-first inference rule.

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
