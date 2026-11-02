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
