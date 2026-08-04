# Terminal UI

Comparo's terminal interface is a first-class frontend over the same application services used by
the headless CLI. It explores a compiled project, prepares and executes HTTP assertions or
cross-environment comparisons, follows live progress, and opens authenticated reports. It does
not parse YAML, resolve values, send HTTP requests, evaluate rules, compute gates, or persist
artifacts itself.

The reusable result surface and its safety boundary are specified in the
[TUI results implementation contract](tui-results-implementation-contract.md).
The backend calls, DTO fields, lifetimes, paging rules, and screen-level information map are
specified in the [TUI/backend integration contract](codebase/tui-backend-integration.md). A
replacement interface should use that document rather than the rejected current TUI as its source
of truth.

## Launching

From an interactive terminal, either command opens `./comparo.yaml`:

```console
comparo
comparo tui
```

Use the explicit command to select another project or authority:

```console
comparo tui \
  --config examples/project/comparo.yaml \
  --source team-overrides.yaml \
  --policy ci-policy.yaml \
  --artifact-root .comparo/artifacts
```

`--source` is repeatable. Bare `comparo` opens the TUI only when stdin, stdout, and stderr are
terminals; in a redirected or non-interactive process it prints CLI help. Every other CLI
subcommand remains headless.

For a deterministic local walkthrough, start the repository's loopback demo service and open one
of the [runnable example projects](../examples/README.md). The `tui-showcase` project is designed to
exercise all four workspaces, multiple execution profiles, two Matrices on one Request, and both
clean and drifted persisted results.

### Launch limits and artifact root

One immutable launch contract owns every operational ceiling for the process. Defaults are named,
versioned starting points rather than application-level maxima; a caller can replace them all:

| Boundary | `comparo tui` options |
| --- | --- |
| Project and policy loading | `--maximum-config-sources`, every `--maximum-config-yaml-*`, and every `--maximum-policy-yaml-*` option |
| HTTP capture and decoding | `--body-memory-threshold-bytes`, `--maximum-transient-body-bytes`, `--maximum-authorization-occurrences`, and every `--maximum-encoded-*`, `--maximum-entity-*`, `--maximum-expansion-*`, `--maximum-stream-*`, `--maximum-decoded-*`, `--maximum-text-*`, and `--maximum-json-*` option |
| Artifact persistence and reads | `--maximum-blobs`, `--maximum-blob-bytes`, `--maximum-total-blob-bytes`, `--maximum-manifest-bytes`, `--maximum-report-bytes`, and every `--maximum-redaction-*` option |
| Interactive process | `--maximum-report-entries`, `--maximum-outstanding-preparations`, `--maximum-input-characters`, `--maximum-input-items`, and `--maximum-navigation-frames-per-workspace` |

Run `comparo tui --help` for exact names, descriptions, and current version-owned defaults. Invalid
values fail as an invalid invocation before the terminal app starts.

TUI executions persist sanitized completed artifacts so Report can open them. The default
`.comparo/artifacts` is resolved relative to the successfully loaded Project root and created as a
private directory when absent. Every existing ancestor must be owned by the current user and must
not be group- or world-writable; symlink traversal fails closed. An absolute `--artifact-root`
selects an operator-managed archive and therefore must already exist. This differs deliberately
from headless execution, where persistence is opt-in and every selected root must already exist.

## Shell

The shell has exactly four persistent workspaces:

| Workspace | Responsibility |
| --- | --- |
| **Explorer** | Inspect the compiled project catalog, resource metadata, tags, and source coordinates. |
| **Compare** | Prepare and run assertions, environment comparisons, and `ExecutionProfile` executions; follow progress and inspect the result. |
| **Report** | Browse completed authenticated artifact bundles and open their results without executing HTTP again. |
| **Settings** | Change terminal-only appearance and interaction preferences. It never edits project YAML or execution policy. |

Each workspace retains an independent, caller-bounded navigation history. Re-selecting the current
entity is coalesced; Back restores the prior resource or result detail instead of only changing an
internal route. Opening a resource or result never redirects to another workspace. A run continues
when the user visits Explorer, Report, or Settings, and completion does not steal focus.

The shared command catalog is the single declaration site for product commands and keys:

| Key | Command |
| --- | --- |
| `1` / `&` | Explorer |
| `2` / `é` | Compare |
| `3` / `"` | Report |
| `4` / `'` | Settings |
| `esc` / `backspace` | Back within the active workspace |
| `?` | Active-command help |
| `ctrl+k` | Command palette |
| `q` | Quit; it is never Back |

The footer is generated from that catalog for the active context. Screens do not declare private
copies of navigation or command semantics.

## Explorer

Explorer loads the project through a bounded application inspection service. The tree/list shows
the Project and every compiled resource, including future `Scenario` resources when the compiler
learns them. The detail panel shows only safe identity and metadata:

- kind, id, namespace, and compiled fingerprint;
- name, description, and tags;
- source id, document, line, and column.

The default catalog contains only value-safe structural outlines. Separate application services
provide revision-pinned masked or explicitly exact authored source, bounded effective structure,
dependency edges, and plan-scoped masked resolved-field inspection. Environment-file contents and
live secret containers never cross the frontend boundary. A failed compilation produces
structured, value-free diagnostics instead of an exception or raw source dump.

Explorer's contextual commands are:

| Key | Command |
| --- | --- |
| `o` | Show the selected resource's kind-specific Overview |
| `s` | Load its revision-pinned masked source |
| `g` | Show its dependency graph |
| `r` | Toggle raw/resolved values inside Overview |
| `Space` | Make the selected Environment the shared execution context |
| `h` | Run a selected Environment's declared health requests, when available |
| `l` | Toggle the resource-kind legend |
| `Ctrl+R` | Reload the project catalog |

Overview/source/graph and raw/resolved mode persist when selection moves in the tree. Explorer
loads the corresponding view for the newly selected resource instead of resetting to Overview.
Changing execution context while resolved mode is active refreshes context-bound values without
changing the selected view. Environment resolution is bound to the Environment being inspected;
the shared execution context remains the default for other resource kinds. Project reload also
preserves the active view while rebinding it to the latest catalog and source revision.

Request Overview is a native tabbed HTTP inspector rather than a generic field list. Stable tabs
cover URL and parameters, query, headers, cookies, authentication, body, runtime behavior, and
contracts/policies; absent capabilities show an explicit empty state. Tabs support mouse and
left/right navigation, and the selected tab persists across raw/resolved toggling and Request
selection. Its leading method/target pill shows the application-sanitized authored
endpoint in raw mode. In resolved mode it shows the canonical sanitized prepared URL, including
the selected Environment's `baseUrl` and effective endpoint/query composition; the TUI does not
join URL strings itself. `r` prepares only that Request in the selected execution Environment,
validates the returned exact revisions, and overlays independently masked field projections onto
the same layout. Authentication and cookies can therefore remain masked while unrelated public
Request fields stay inspectable. The inspection surface is aggregate-bounded and reports a partial
view instead of failing when more field roots exist than the display budget permits. Request
selection and composition remain inside Compare rather than appearing as Explorer actions.

Environment `healthChecks` are ordered, unique references to Request resources. `h` prepares and runs
those requests against the selected Environment through the normal assertion, safety, artifact,
and reporting pipeline. The latest safe health summary remains inline and completion is announced
as a contextual toast; neither surface contains response bodies, exception text, or secrets. A
probe remains owned by its exact Environment revision when tree selection changes, so returning to
that row restores its loading/result state and cannot start a duplicate while it is active.

## Compare

Compare contains every kind of current execution in one lifecycle. The mode selector changes the
inputs, not the underlying planner or runtime:

| Mode | Inputs | Effective topology |
| --- | --- | --- |
| Assertions | subjects, environments | Execute and evaluate assertions across every selected Environment. |
| Differences | subjects, baseline, candidates | Execute each directed baseline/candidate pair and evaluate its diff profile. |
| Execution profile | `ExecutionProfile` id plus optional topology overrides | Use the reusable profile and deliberately replace only the topology fields supplied for this run. |

Safe project resource identities provide defaults and hints; users may type another valid id. The
plural selectors use bounded comma-separated ids. Advanced run options expose Matrix bindings,
trials, maximum attempts, tri-state fail-fast, planning budgets, and parameter/variable/secret
overlays sourced from compiled `Value` resources. Their adapter syntax is deliberately small:

```text
subjects:             ping, health
matrices:             region=regions, tenant=tenants
planning budgets:     concurrency=4, attempts=20
parameter Values:     Value/params, staging=Value/staging-params
secret Values:        Value/secrets, prod=Value/prod-secrets
```

An omitted advanced field inherits project/profile configuration. Fail-fast explicitly selects
inherit, enabled, or disabled. Invalid adapter text becomes a closed `tui.compare.input.*`
diagnostic; authored values and parser exceptions never enter diagnostics or backend errors.

The form is a frontend draft only. `p` translates it to the application's typed
`PlanningOptions`, `MatrixOverride`, `BudgetOverride`, and `ValueResourceOverride` contracts. The
shared planner—not the TUI—applies normal precedence and returns an opaque one-shot handle plus a
secret-free admitted-plan preview. The preview reports run identity, cells, trials, environment
executions, comparisons, retry ceiling, slices, and effective budgets.

`x` consumes that exact handle. The workspace cannot silently rebuild a different plan between
preview and execution. Per-run mutation approval is part of the draft: its Boolean value is
snapshotted with the admitted handle and applies only when that exact handle starts. Editing it
invalidates the preview; it never changes application-wide defaults. While running Compare
displays a bounded, coalesced snapshot—queued, active,
finished, and comparison counts—rather than retaining an unbounded runtime event history. `a`
requests first-wins cancellation through the run-owned cancellation port.

Terminal results are projected from the independently sanitized `RunArtifact`; widgets do not
re-evaluate assertions, diffs, or the gate. TUI executions persist sanitized completed artifacts
by default under the selected artifact root so the same run becomes available in Report.

## Shared results

Compare and Report mount the exact same result components. The only capability difference is that
Compare is a live journey and Report is read-only. Both surfaces show:

- the artifact-owned completion and gate verdict;
- execution, assertion, comparison, and diff indexes;
- stable cross-references between those entities;
- outcomes, rule identities, bounded evidence summaries, attempts, exchanges, and decomposed
  timing facts already present in the artifact.

The shared result projection is deliberately manifest-only. Full request/response bodies are
captured and sanitized through the bounded spool/storage abstraction according to execution
policy. The backend now exposes authenticated byte windows bound to exact result coordinates; the
interface should load those windows lazily without allowing terminal memory to scale with all
responses.

## Report

Report enumerates a caller-bounded artifact directory and authenticates each candidate's completion
marker, manifest, result document, and declared blob metadata before listing it. Corrupt entries
are isolated as typed failures; they neither hide valid siblings nor expose parser, path, or
exception text. Selecting a valid entry verifies it again and opens the shared read-only result
surface. It never resends HTTP requests. `r` refreshes the archive.

## Settings

Settings contains only TUI concerns:

- system, dark, or light theme;
- standard or dense layout;
- mouse support (applied on the next launch when changed at runtime);
- the preferred default workspace.

These preferences never change environment selection, execution policy, classification, limits,
assertions, diff rules, or project files. Persistence of terminal preferences is not part of the
initial migrated slice; settings remain active for the current process.

## Responsive and accessible semantics

All components use the same terminal-cell breakpoints:

| Width | Layout |
| ---: | --- |
| `< 88` | compact, with index and detail stacked |
| `88–139` | standard, with a 36-column index rail |
| `>= 140` | wide, with a 44-column index rail |

One semantic catalog owns every status glyph, label, sort rank, and CSS class. Color is always
paired with a glyph and label. All untrusted presentation text has terminal control and bidi
controls contained before it reaches a widget; Rich markup is disabled for data-bearing text.

## Security and failure behavior

- Widgets consume only safe application projections, never live value or secret objects.
- The TUI package has no backend capability or business rule; deleting it leaves execution,
  evaluation, sanitization, persistence, and reporting intact.
- Blocking local compilation and artifact reads run off the event loop behind bounded services.
- Repeated reload, preparation, report, and execution requests are coalesced into finite semantic
  operation slots; the shell cannot accumulate duplicate background tasks.
- Backend and unexpected UI failures collapse to closed codes or fixed messages. Textual's default
  value-bearing traceback renderer is replaced at the application boundary.
- Shutdown cancels unfinished work, waits for cleanup, discards prepared handles, and closes the
  backend idempotently.

## Deferred scope

Stateful Scenario execution remains Step 15. Its future placement is already stable: definitions
appear in Explorer, active execution appears in Compare, and persisted outcomes appear in Report.
The backend can export safe cURL from an authenticated sanitized post-run request capture. The TUI
integration remains presentation work: the displayed and copied command must be identical, expose
its redaction/executability warnings, and never place a live credential in the clipboard.
Browser automation is outside Comparo's scope; WebSocket and gRPC transports and a public plugin
system remain deferred.
