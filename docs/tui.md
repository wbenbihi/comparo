# Terminal UI guide

> `comparo` (or `comparo tui --config <manifest>`) — explore, run, diff, report, and configure, without leaving the terminal.

The TUI is one of three front-ends over the shared engine (alongside the [CLI](cli.md) and the
GitHub Action). It never contains diff or execution logic of its own — it drives
`comparo.core`, so anything you do here behaves exactly like the headless commands.

Run `comparo` with no command to open the TUI on `./comparo.yaml` (the manifest in the current
directory); point `--config` at a manifest to open one elsewhere:

```console
comparo                                                    # opens ./comparo.yaml
comparo tui --config examples/sample-project/comparo.yaml
```

If the project does not compile, the TUI opens on a full-screen **error report** instead of the
shell (see [When a project won't load](#when-a-project-wont-load)).

## Table of contents

- [The shell](#the-shell)
- [Explorer](#explorer)
- [Run](#run)
- [Diff](#diff)
- [Report](#report)
- [Settings](#settings)
- [When a project won't load](#when-a-project-wont-load)
- [Conventions that hold everywhere](#conventions-that-hold-everywhere)

## The shell

A top nav bar carries the five screen tabs; the active tab is highlighted, and the right side
shows the project and the active environment. A bottom status bar always lists the keys that
are usable *right now* (keys coloured, actions dim) plus a context on the right.

Switch screens with the number row — and, because a laptop without a numpad may need Shift for
digits, each tab is **also** bound to the un-shifted character on the same physical key (handy on
an AZERTY layout):

| Screen   | Key            |
| -------- | -------------- |
| Explorer | `1` or `&`     |
| Run      | `2` or `é`     |
| Diff     | `3` or `"`     |
| Report   | `4` or `'`     |
| Settings | `5` or `(`     |

`?` opens a help overlay listing every key for the current screen; `q` quits. The **accent
border always marks the active panel** — whichever panel the keyboard is driving — and `tab`
moves focus between panels.

## Explorer

The Explorer is dedicated to understanding *how the project is configured*. A foldable tree on
the left lists every object — the `◆` project manifest as a root node, then Environments,
Requests, Matrices, Schemas, Instances, and Diff Profiles. The detail panel shows the selected
object; for a request it renders the **resolved outbound request** (method chip, URL, headers
with secrets masked in red, a syntax-highlighted JSON body) with a **provenance** panel below
showing where each value came from.

| Key     | Action |
| ------- | ------ |
| `↑ ↓`   | move through the tree |
| `space` | fold / unfold a section |
| `tab`   | switch the active panel (tree ⇄ detail ⇄ provenance) |
| `enter` | on an **environment**: make it the default everything resolves against |
| `h`     | on an **environment**: run its health checks live (dot turns green / orange / red) |
| `r`     | on a **request** or **instance**: toggle raw source ⇄ resolved values |
| `p`     | on a **request**: show its `curl` (masked); inside, `c` copies the real one |
| `/`     | filter the tree by name, kind, or tag |
| `g`     | open the reference graph — what links to what |

The coloured dot next to an environment reflects its last health check; the red **`live`** badge
marks an environment whose `baseUrl` is not a loopback host — a reminder that requests hit a real
server.

## Run

Run executes selected request cells against the **current environment** (it does not compare
environments — that is Diff's job). It has two visually distinct states so you always know where
you are.

### Prepare

A calm checklist (not a table). Fold a matrix request to see its cases; the icon shows whether a
request will run in **full** (`◉`), **partial** (`◐`), or **none** (`○`) of its cases, with a
`will run` count. A footer CTA totals the cases that will run.

| Key     | Action |
| ------- | ------ |
| `↑ ↓`   | move |
| `space` | fold a request to reveal its cases |
| `enter` | toggle a request or cell in / out of the run |
| `m`     | choose matrix **values** globally — deselecting a value excludes it from *every* request that shares that matrix |
| `/`     | filter by request name |
| `x`     | run the selected cells |

### Running

`x` switches to a compact progress line (a **run id**, the environment, a bar, and `done · ✓ · ✗`
counts) over dynamic Miller columns that grow with how deep you drill:

- **Requests** table — status, a variant strip, p50 latency.
- **Variants** table (appears when you open a matrix request) — case, HTTP code, time, and a
  clear **result** (`✓ 3 passed` or `✗ schema`, naming the failed check). A single-case request
  skips this and goes straight to the report.
- **Detail** — a navigable tree of the whole exchange: checks, metrics, the request, and the
  response. JSON, HTML, and SSE bodies are collapsible sub-trees you can walk into.

| Key       | Action |
| --------- | ------ |
| `↑ ↓`     | move / navigate the detail tree |
| `enter`   | drill into the next split |
| `bksp`    | collapse a split (or return to Prepare) |
| `z`       | maximize the detail panel |
| `f`       | filter the tables to failures only |
| `/`       | filter by request or case name (shown on the panel) |
| `a`       | abort the run and return to Prepare |
| `s`       | save the finished run's results to masked JSON (secrets redacted, even when echoed back) |

## Diff

The signature screen. `x` replays every request against the manifest's **baseline ⇄ candidate**
pair and diffs the paired responses under each request's diff profile. Drifts collapse to **one
row per field** — a field that drifts across three cells is one bug, not three. Selecting a field
shows the tri-state comparison, baseline → candidate, with the one-cell gutter (`▏` identical,
`▌` drift, `╎` not compared).

| Key   | Action |
| ----- | ------ |
| `x`   | run the diff across the pair |
| `↑ ↓` | move through the drifted fields |
| `i`   | **silence** the selected field — writes an ignore rule into its committed DiffProfile |

Silencing is a reviewable act: `i` appends a `{path, mode: ignore}` rule to the profile's YAML
(comments preserved), so quieting a diff shows up in `git diff`. Re-run to confirm it's gone.

## Report

The pillar for CI. It reads the most recent diff run and leads with the **gate verdict** (pass
or fail, with the reason), a row of stat pills (`calls · same · drift · error · skipped` — skip
stays visible so green never means "full coverage"), a per-request breakdown, and exporters.

| Key       | Action |
| --------- | ------ |
| `j`       | export JUnit XML |
| `s`       | export SARIF |
| `m`       | export Markdown (GitHub step summary) |
| `o`       | export JSON |
| `enter`   | write every format |

These are the exact same reporters `comparo diff --report` uses headless, so the gate here
matches CI.

## Settings

A navigable, read-only overview of the effective configuration. Move through the categories on
the left — Project, Environments, Run defaults, Diff, Report, Redaction, Appearance, Plugins,
Engine — and the detail panel renders each. Switch the default environment from the Explorer;
Settings shows you what's in force.

## When a project won't load

If the loader finds problems, the TUI (like `comparo validate`) shows every diagnostic grouped
by file, each loader hint rendered as **the fix to apply** (`did you mean 'schema.checkout'?`).
Press `r` to re-check after editing — fix a file, press `r`, watch the list shrink to zero.

## Conventions that hold everywhere

- **Secrets are never shown.** Values are masked in every display and redacted from saved runs.
- **The accent border marks the active panel;** `tab` cycles panels; `?` lists every key.
- **Nothing is hidden silently.** An active filter is shown on the panel; skipped diff fields are
  counted; a matrix value turned off still appears (as `✕ matrix off`) rather than vanishing.
