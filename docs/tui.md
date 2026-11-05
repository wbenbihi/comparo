# Terminal UI

Comparo's main terminal interface is the component-based implementation developed as TUI4,
now installed as `comparo.tui`. The older `tui`, `tuibis`, and `tui3` implementations have been
removed. Design mockups and references remain under `docs/design/`.

## Launching

From an interactive terminal, either command opens `./comparo.yaml`:

```console
comparo
comparo tui
```

Select a project explicitly:

```console
comparo tui --config examples/tui-showcase/comparo.yaml
```

The module entry point accepts the same options:

```console
python -m comparo.tui --config examples/tui-showcase/comparo.yaml
```

When reviewing local changes, launch `.venv/bin/comparo tui --config
examples/explorer-inspection/comparo.yaml` from the repository root. This selects the workspace
environment even when a pipx installation of `comparo` appears first on `PATH`. Quit and relaunch
the TUI after code changes; reloading the project does not reload Python modules.

`--source PATH` adds a local YAML source and may be repeated. `--policy PATH` selects an external
ExecutionPolicy; omission uses trusted-local authority. Bare `comparo` opens the interface only
when stdin, stdout, and stderr are terminals. Redirected invocation prints CLI help, and the
other CLI subcommands remain headless.

For actual HTTP execution in the example project, start the loopback demo in another terminal:

```console
uv run --frozen python examples/support/demo_server.py
```

Browsing saved reports does not need the demo server.

## Workspaces

| Key | Workspace | Responsibility |
| --- | --- | --- |
| `1` | Explore | Browse resources, inspect authored/effective/resolved views, dependencies, and Environment health. |
| `2` | Launch | Configure a target, review its plan, follow execution, and analyze results. |
| `3` | Reports | Search saved runs, preview outcomes, reopen the shared Analyze view, and export reports. |
| `4` | Settings | Persist theme, density, startup workspace, mouse reporting and main command shortcuts. |
| `5` | Debug | Inspect the reusable component gallery. |

These are default keys and can be changed in Settings. The header shows the selected execution
Environment and installed package version; compact widths hide the helper text to preserve navigation.
The header supports pointer selection. `q` quits when a text input is not consuming the key.
Workspace switches preserve the active run. The frontend uses shared components, glyphs,
status colors, and theme tokens; backend services own planning, execution, evaluation, and artifacts.

- [Resource component guide](codebase/tui-resource-components.md): Explorer views and extension rules.
- [Launch guide](codebase/tui-launch.md): Target → Plan → Run → Analyze, controls, and keyboard navigation.
- [Reports guide](codebase/tui-reports.md): archive filters, preview, exports, and shared analysis.
- [Settings guide](codebase/tui-settings.md): preferences, save/apply behavior, and shortcut remapping.
- [Backend integration contract](codebase/tui-backend-integration.md): application DTOs and service lifetimes.

## Following resource relationships

In Explore, press **g** to see the selected resource's **Uses** and **Used by** relationships.
Focus the graph and press **Enter** on an edge to open its target. Consumer tables in Matrix,
Schema, Value and profile overviews support Enter and mouse activation too.

**Escape** returns through explicit relationship visits, restoring the previous filter, resource,
view, tab, collection selection, scroll and focus. The filter retains its own Escape behavior:
clear its text, then return focus to the resource tree. Following a link reveals the target by
clearing the filter; Back restores the original filter. Tree highlighting does not create history.
Navigation leaves the execution Environment unchanged and never runs a Request. After a catalog
reload, Back uses the current resource revision and skips removed targets with a notice.

### Resource-specific inspectors

- **Request:** Summary links to the HTTP tabs. Body displays a numbered JSON document; arrows
  select a path and update its value, directive and declaration origins. Copy uses only the masked
  preview. Schema validation is explicitly marked as not checked.
- **Matrix:** select an axis to inspect its options. Press `r` to browse generated data cases and
  their values, including for an unused Matrix. Case pages contain at most 50 rows. No consumer,
  execution plan or companion/trial selection is required. Selection survives Source/Graph visits.
- **Schema:** the tree preserves objects, array item schemas, definitions and combinators.
  Local `$ref` targets expand in place, including other project schemas identified by `$id`.
  Recursive branches stop explicitly. The reference list opens the corresponding tree location.
- **Environment:** connection and policy settings, runtime inputs and value sources, defaults,
  relationships and health information are grouped separately.
- **Value:** press `r` to resolve the Value independently of its consumers, even without an
  Environment. Public fields remain readable and sensitive fields are masked individually.
  A missing input affects that field; it never asks you to select a Request or Scenario.
- **Profiles and Scenario:** rule/step collections drive selected-item details; execution, gate,
  safety and artifact settings use semantic groups. Unsupported and reserved capabilities retain
  their existing labels. Scenario inspection does not imply executable Scenario support.

Paired panes sit side by side on wide terminals and stack below 140 columns. Arrow keys select
rows; Tab moves between controls. A Matrix preview is bounded to 64 nodes and labels partial
results. Case expansion failures and missing value inputs remain explicit. Request body declaration origins come from the application's compiled
provenance. Other inspection views do not invent per-value source provenance that the application
cannot provide.

Masked/resolved secrets are violet in both themes, while JSON booleans are blue; amber and red
remain warning/failure colors. The literal `<masked>` marker also works without color. The preview
limits are 10,000 Matrix cases, 50 cases/page and 64 value nodes; Body documents stop at 512 lines
and resolved batches cover at most 16 field targets; Schema expansion stops at 512 nodes/depth 32.
Copying a Body copies its masked preview, not an unlimited payload. Remote Schema fetching and
sample validation are not Explorer controls.

Use the [inspection example](../examples/explorer-inspection/README.md) to exercise these paths
without starting an HTTP server.

## Working through a run

Explore's **c** action sends a selected Request or ExecutionProfile to Launch without executing it.
A Request uses the selected Environment for assertion checks; a profile keeps its own topology.
A busy Launch or stale resource revision rejects the handoff.

Target starts with **Run requests**, **Run an execution profile**, or **Run assertions**. Profile
mode offers saved profiles and manual configuration. Ctrl+R prepares a plan; Ctrl+X executes the
reviewed plan; Ctrl+E returns to editing; Ctrl+K cancels a running execution. Run stays visible when
execution finishes, so even fast runs can be inspected before choosing **Analyze results**.

**Save as profile** previews a new ExecutionProfile's YAML and destination before enabling Save.
Existing files are never overwritten. Invocation-only Matrix case filters and clearing an inherited
gate cannot be represented in the profile schema and are rejected explicitly. Mutation approval
remains session-only.

Analyze has Issues, Requests, Rules, Fields and Timeline lenses. Fields shows retained rule evidence,
not every JSON body property. Press **i** on an occurrence for the shared capture inspector:
raw bytes page in 64 KiB windows with `n` / `p`; JSON and SSE use 50-item pages; Enter drills down,
`b` returns to the parent, and Escape restores Analyze. Structured inspection accepts up to 4 MiB,
50,000 JSON nodes and 10,000 SSE events. **Review request cURL** uses the selected stored request,
shows replay limitations, and requires a separate Copy action. It never sends the command.

Reports opens persisted results without running HTTP. It filters a bounded listing (500 physical
archive entries by default) and displays 50 reports per page. Large-archive cursor queries,
retention/trash/restore and annotations exist in backend APIs but have no TUI controls yet.

## Preferences

General is the first Settings section, showing version, author, repository, source, license and
full build SHA. Packaged builds retain their source revision; development installs identify the
Comparo checkout. Modified source and missing revision information are explicit.

Settings edits a draft until **Save & apply**. Preferences are stored in
`$XDG_CONFIG_HOME/comparo/tui.json`, defaulting to `~/.config/comparo/tui.json`. Density and command
shortcuts apply immediately; theme, mouse and startup-workspace changes apply next launch. Invalid
saved preferences fall back to defaults with a notice and are not overwritten at startup.

## Analyze navigation on macOS

`Alt+1–5` selects Issues, Requests, Rules, Fields, and Timeline. On macOS, Alt is **Option (⌥)**.
In Apple Terminal, enable **Use Option as Meta key** in Settings → Profiles → your profile → Keyboard.
In iTerm2, set **Left Option Key** to **Esc+** in Settings → Profiles → Keys → General.

`[` / `]` cycles views from the index or evidence inspector. These remain ordinary characters
inside a filter. In saved-report analysis, Alt+Left returns to the archive with its selection intact.

## Launch limits and artifact root

The launch contract carries project/policy load limits, HTTP capture/decode limits, artifact
read/write limits, `--maximum-report-entries` (default 500), and
`--maximum-outstanding-preparations` (default 2). Run `comparo tui --help` for the complete set.
Invalid options fail before the terminal application starts.

The retired frontend's `--maximum-input-characters`, `--maximum-input-items`,
`--maximum-output-characters`, `--maximum-output-items`, and
`--maximum-navigation-frames-per-workspace` options are removed; they are not silently ignored.

Sanitized run reports are persisted by default. `--no-persist` disables run persistence and does
not create an archive for planning or execution. Existing reports remain available for reading.

`--artifact-root` defaults to `.comparo/artifacts`, relative to the loaded Project root. A relative
archive is created privately when preparing a persisted run. An absolute path selects an existing,
operator-managed archive; the interface does not create it. Browsing Reports never creates storage.
Application services validate archive ownership and filesystem safety before granting access.

## Development

The production implementation lives in `src/comparo/tui`; its tests live in `tests/tui`.
The versioned `comparo.tui4` module has been replaced by `comparo.tui`; use the commands above.

```console
uv run --frozen pytest tests/tui tests/cli tests/architecture
```
