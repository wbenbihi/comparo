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
| `4` | Settings | Reserved workspace; settings controls are not implemented yet. |
| `5` | Debug | Inspect the reusable component gallery. |

The header supports pointer selection. `q` quits when a text input is not consuming the key.
Workspace switches preserve the active run. The frontend uses shared components, glyphs,
status colors, and theme tokens; backend services own planning, execution, evaluation, and artifacts.

- [Resource component guide](codebase/tui-resource-components.md): Explorer views and extension rules.
- [Launch guide](codebase/tui-launch.md): Target → Plan → Run → Analyze, controls, and keyboard navigation.
- [Reports guide](codebase/tui-reports.md): archive filters, preview, exports, and shared analysis.
- [Backend integration contract](codebase/tui-backend-integration.md): application DTOs and service lifetimes.

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
