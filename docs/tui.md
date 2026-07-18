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
- [Execution](#execution)
- [Report](#report)
- [Settings](#settings)
- [When a project won't load](#when-a-project-wont-load)
- [Conventions that hold everywhere](#conventions-that-hold-everywhere)

## The shell

A top nav bar carries the six screen tabs; the active tab is highlighted, and the right side
shows a per-screen status. A bottom status bar always lists the keys that are usable *right now*
(keys coloured, actions dim) plus a context on the right.

Switch screens with the number row — and, because a laptop without a numpad may need Shift for
digits, each tab is **also** bound to the un-shifted character on the same physical key (handy on
an AZERTY layout):

| Screen    | Key            |
| --------- | -------------- |
| Explorer  | `1` or `&`     |
| Run       | `2` or `é`     |
| Diff      | `3` or `"`     |
| Execution | `4` or `'`     |
| Report    | `5` or `(`     |
| Settings  | `6` or `-`     |

`?` opens a help overlay listing every key for the current screen. **`q` always quits the app —
it is never "back."** Close a sub-screen or step back a level with `esc` (or `⌫`); every footer
says so. The **accent border always marks the active panel** — whichever panel the keyboard is
driving — and `tab` moves focus between panels.

Tabs are **self-contained**: a screen never redirects you to a *different* tab to show a result.
When you drill in — a diff, a report, a cell — it pushes a sub-view **within the same tab**, so
you never lose your place.

## Explorer

The Explorer is dedicated to understanding *how the project is configured*. A foldable tree on
the left lists every object — the `◆` project manifest as a root node, then Environments,
Requests, Matrices, Schemas, Instances, Diff Profiles, **Assertion Profiles**, and **Execution
Profiles** (empty kinds are hidden). The detail panel shows the selected object; for a request it
renders the **resolved outbound request** (method chip, URL, headers with secrets masked in red, a
syntax-highlighted JSON body) with a **provenance** panel below showing where each value came from.

Pressing `enter` on an **ExecutionProfile** opens it in the [Execution](#execution) tab, ready to
launch — the same profile picker the Execution tab opens with.

| Key     | Action |
| ------- | ------ |
| `↑ ↓`   | move through the tree |
| `space` | fold / unfold a section |
| `tab`   | switch the active panel (tree ⇄ detail ⇄ provenance) |
| `enter` | on an **environment**: make it the default everything resolves against |
| `h`     | on an **environment**: run its health checks live (dot turns green / orange / red) — a manual, point-in-time probe |
| `r`     | on a **request** or **instance**: toggle raw source ⇄ resolved values |
| `p`     | on a **request**: show its `curl` (masked); inside, `c` copies the real one |
| `/`     | filter the tree by name, kind, or tag |
| `g`     | open the reference graph — what links to what |

The coloured dot next to an environment reflects its last health check; the red **`live`** badge
marks an environment whose `baseUrl` is not a loopback host — a reminder that requests hit a real
server. Health is deliberately **manual** — comparo never auto-probes on focus (that would hammer a
live env on every cursor move), so the detail shows how fresh the last probe is (`checked 2m ago ·
press h to re-check`, or `not checked yet · press h`) and you re-run it on demand.

## Run

Run executes selected request cells against the **current environment** (it does not compare
environments — that is Diff's job). It has two visually distinct states so you always know where
you are.

### Prepare

A calm checklist (not a table). Fold a matrix request to see its cases; the icon shows whether a
request will run in **full** (`◉`), **partial** (`◐`), or **none** (`○`) of its cases, with a
`will run` count. A footer CTA spells out the workload — `N requests · M cases × 1 env = C calls ·
up to 4 in parallel` — and, below it, the **equivalent CLI command** (`$ comparo run --env <env>`):
every screen is a command, and the TUI writes the flags.

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
  response. JSON, HTML, and SSE bodies are collapsible sub-trees you can walk into. `t` cycles the
  facet it shows — **all · request · response · headers · raw** (the pill strip is the panel
  subtitle); **raw** dumps the unparsed request line and response body verbatim.

| Key       | Action |
| --------- | ------ |
| `↑ ↓`     | move / navigate the detail tree |
| `enter`   | drill into the next split |
| `t`       | cycle the detail facet — all · request · response · headers · raw |
| `bksp`    | collapse a split (or return to Prepare) |
| `z`       | maximize the detail panel |
| `f`       | filter the tables to failures only |
| `/`       | filter by request or case name (shown on the panel) |
| `a`       | abort the run and return to Prepare |
| `s`       | save the finished run to masked JSON **and archive it as an assertions report** (visible in the Report tab; secrets redacted, even when echoed back) |

## Diff

The signature screen — interactive, real-time, and, like Run, split into two states.

### Prepare

Choose **what** to diff before running. A checklist of every request (matrix requests fold to
their cases, with a `will diff` count) and, at the top, the **baseline ⇄ candidate** pair. The
Diff never silently replays the whole project — it runs exactly what you select. The CTA totals the
work — `N requests · M cells × 2 envs = C calls` (a diff hits both sides) — and previews the
equivalent `$ comparo diff --baseline <b> --candidate <c>`.

| Key     | Action |
| ------- | ------ |
| `space` / `enter` | toggle a request or matrix case in / out of the diff |
| `b` / `c` | pick the **baseline** / **candidate** environment in place |
| `m`     | choose matrix **values** globally (deselecting a value excludes it everywhere) |
| `x`     | diff the selected requests against the pair → **Running** |

### Running

While the pair is being fetched, a **running** panel (mirroring the Execution tab's) shows a
progress bar over the plan and the cells in flight, so it's clear results aren't ready yet —
rather than a blank or stale panel. It gives way to **Results** as soon as the diff completes.

### Results

A bordered **summary bar** — tri-state counts (`22 same · 2 drift · 0 error`), the **gate
verdict** (`gate FAIL · 2 untriaged drifts`), and an inline `baseline Stable ● ⇄ candidate
Canary ●` selector — over two panels:

- **Drift index** — drifts collapse to **one row per field** (a field that drifts across three
  cells is one bug, not three), each naming its request(s); the **skipped `◐` fields** are listed
  too, because skip stays visible. A pill toggle (`r`) flips between grouped-by-field and the
  broken-rules view.
- **Compare** — a **git-style unified diff**: `diff a/<baseline>/<req>.json b/<candidate>/<req>.json`,
  an `@@ request · $.path @@` hunk header, `−`/`+` lines (baseline red / candidate green), and
  `⋯` skipped lines annotated with the rule that skipped them. A pill toggle (`v`) flips
  unified ⇄ side-by-side. `o` swaps in the **outbound-request diff** — the *request sent* to each
  side (method, URL, headers, body), resolved per environment. Since comparo replays the same
  request, it only differs where env config does (a different base URL, a per-env token), so this
  answers the first triage question: is the drift the service's, or did you send two different
  requests? An errored cell shows the request, environment, and the real error.

| Key   | Action |
| ----- | ------ |
| `↑ ↓` | move through the drift index (fields, cells, skips, errors) |
| `r`   | toggle grouped ⇄ broken-rules index |
| `v`   | toggle unified ⇄ side-by-side |
| `o`   | toggle the **outbound-request diff** (what was sent to each side) |
| `i`   | **silence** the selected field — writes an ignore rule into its committed DiffProfile |
| `s`   | **save** the diff to the archive as a report (redacted; re-openable in the Report tab) |
| `esc` | return to **Prepare** to change the selection |

Silencing is a reviewable act: `i` opens a confirmation naming the exact file, then appends a
`{path, mode: ignore}` rule to the profile's YAML (comments preserved), so quieting a diff shows
up in `git diff`. Re-run to confirm it's gone. Each finished diff is archived to the
[Report](#report).

## Execution

The Execution tab runs an **ExecutionProfile** — one declarative run that asserts **both**
environments *and* diffs the pair — as a **self-contained flow of five in-tab views**. It is
**Run + Diff + Report consulted together**; nothing ever redirects you to another tab.

### Launch

The tab opens on a profile picker. The left **PROFILES** panel lists every ExecutionProfile; the
right **SETUP** panel previews the highlighted one before anything is sent: the `baseline ⇄
candidate` pair, a segmented `mode` toggle (`assert` / `diff` / `both`), the `select` clause
(tags with `✓` / `☐`), and a **plan preview** that counts the exact cells that will run
(`will run 3 cells`).

| Key      | Action |
| -------- | ------ |
| `enter`  | launch the highlighted profile |
| `space`  | toggle the highlighted profile / selection |
| `t`      | tags · `m` mode |
| `esc` / `⌫` | close · `q` quit |

### Running

A first-class in-tab transition: a progress bar over the whole plan, the cell **in flight on both
sides** (`stable ◐  candidate ◐`), and the finished cells with early verdicts. `esc` cancels —
**nothing is written until it finishes**.

### Results

A stacked read-out from global to granular:

- an **execution header** (the profile, `baseline ● / candidate ●`, `mode both`, the `select`
  clause, the counted plan);
- two **assertion roll-ups** side by side — assertions are evaluated **independently on both
  environments**, each headed with a `N ✓ · N ✗ · N !` count;
- a **diff panel** — full tri-state counts and a **drift index naming which request/cell** to
  investigate (skipped `◐` fields stay visible);
- a **gate** (`assertions ∧ diff`) that separates the two: `assertions pass on both sides … but
  N untriaged drifts block the run`, with `exit code N — matches headless comparo exec <id>`.

| Key      | Action |
| -------- | ------ |
| `↑ ↓`    | move through the drift sections |
| `enter`  | drill into a drifted **cell** |
| `d`      | open the run's scoped **diff** (in-tab) |
| `e`      | open the run's **report** (in-tab) · `s` save · `r` re-run |
| `esc` / `⌫` | close · `q` quit |

### Cell detail

`enter` on a drifted cell drills in (breadcrumb `profile › request › cell`), **within the tab**:
the cell's baseline and candidate assertions, a plain-language verdict, and the scoped git-style
**body diff** — reusing the Diff component verbatim. `v` flips unified ⇄ side-by-side, `i`
silences the field, `esc` steps back.

### In-flow diff

`d` opens the run's **own scoped diff in place** — every drifted cell's body diff, stacked, with
the matrix grouping made explicit (one field, two cells, one bug) — never redirecting to the
shared Diff tab (which would lose the execution context). `v` toggles unified ⇄ side-by-side;
`esc` returns.

The run is auto-archived on launch, so it is immediately re-openable from the [Report](#report)
tab.

## Report

The Report tab is a **browser over saved reports** archived under `<data>/.reports/` (configurable
via `spec.report.dir`, default `.reports`), fed by executions and by saved diffs and runs (the
`s` key). Its key idea: a saved report doesn't just restate numbers — it **replays through the
same live Diff and Run panels**, read-only, so re-reading a past run feels exactly like the live
screens.

### Browse

- **Left** — a list of **every** saved report, each row carrying its **kind** (`◆` execution,
  `◇` diff/run), `when`, `envs`, `gate`, and drift/error counts; gate-coloured, newest first, with
  `/` to filter by id, envs, kind, or gate.
- **Right** — a reading pane for the highlighted report: a gate banner, stat pills (`calls · same
  · drift · error · skipped` — skip stays visible so green never means "full coverage"), the
  baseline and candidate **assertion roll-ups**, and a per-request **DIFF BREAKDOWN** whose legend
  **names the drifted field(s)**.

### Analyze (in-place replay)

`enter` opens the report's full analysis **inside the Report tab** — never jumping to another tab:

- a saved **diff** reopens in the **Diff screen's layout** — the drift index plus the git-style
  **body-diff well**, replaying the real before/after values persisted with the report (no
  requests are re-sent);
- a saved **run / execution** reopens in the **Run screen's layout** — the request rows plus the
  detail tree (metrics, request, response, checks), rebuilt from disk.

A purple **"analyzing a saved …"** banner and a `read-only` marker make clear it is a replay.
`esc` returns to the list.

| Key       | Action |
| --------- | ------ |
| `↑ ↓`     | move through saved reports |
| `enter`   | analyze the report in place (Diff/Run panels, read-only) |
| `o`       | export a Markdown summary |
| `d`       | delete the saved report (after confirmation — it removes a file) |
| `r`       | reload the archive directory from disk |
| `/`       | filter by id, envs, kind, or gate |
| `esc` / `⌫` | back to the list · `q` quit |

The gate shown here is the same one the CLI and the GitHub Action enforce, so what you read
matches CI exactly.

## Settings

App-level settings (installed globally, so they're about comparo, not the current project). A left
list of sections, a detail panel on the right; move with `↑ ↓`. Most sections are read-only — a few
carry one interactive control, toggled with `enter`, and Security runs a live check on `t`.
Preferences persist to `~/.config/comparo/config.toml` (XDG-respecting).

| Section | What it shows |
| ------- | ------------- |
| **About** | version, author, license, links |
| **Project** | a read-only summary of the loaded project (counts, manifest, default env, concurrency) |
| **Security & Redaction** | the never-leak guarantee, plus a live **self-check** — `t` runs a canary secret through **every sink** (TUI, saved runs/reports, JUnit/SARIF/JSON/Markdown, curl copy, crash report) and shows a ✓ per sink. Same check as `comparo doctor` |
| **Appearance** | theme, and the default body-diff layout (`enter` flips unified ⇄ side-by-side) |
| **Keybindings** | a read-only cheat sheet of the global keys |
| **Updates & Privacy** | an **opt-in** check for updates — `enter` toggles it. When on, comparo fetches PyPI's public version JSON once at launch (a version string, **no telemetry**) and toasts if a newer release is out |
| **Plugins** | a placeholder — plugins are a post-alpha extension point |
| **Engine** | `core` is the whole engine; the two import-linter contracts, `comparo/v1`, docs |
| **Behavior** | startup prefs — confirm-on-quit (`enter`), default tab, default diff layout |

The version check is **off by default** — it's the one outbound call comparo makes for itself, so
you enable it consciously. It sends nothing about you or your projects.

## When a project won't load

If the loader finds problems, the TUI (like `comparo validate`) shows every diagnostic grouped
by file, each loader hint rendered as **the fix to apply** (`did you mean 'schema.checkout'?`).
Press `r` to re-check after editing — fix a file, press `r`, watch the list shrink to zero.

## Conventions that hold everywhere

- **`q` always quits; `esc`/`⌫` is back.** `q` is never a "back" or "close" key on any screen or
  modal — it always exits the app. Stepping back a level is `esc` (or `⌫`), and every footer
  reflects it.
- **Tabs are self-contained.** A screen never redirects you to a different tab to show a result;
  it pushes a sub-view in place.
- **Secrets are never shown.** Any declared secret value is masked in every display and redacted
  from every saved artifact — saved runs, `.reports/*.json`, exports, and CI reports — even when a
  server echoes it back, and even when it appears as a JSON key or field path.
- **The accent border marks the active panel;** `tab` cycles panels; `?` lists every key.
- **Nothing is hidden silently.** An active filter is shown on the panel; skipped diff fields are
  counted; a matrix value turned off still appears (as `✕ matrix off`) rather than vanishing.
