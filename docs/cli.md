# comparo CLI & GitHub Action

Reference for the `comparo` command-line interface and the `comparo diff` GitHub
Action. **comparo** replays the same HTTP requests against two environments and
diffs the responses to catch regressions before they ship.

The CLI, the TUI, and the GitHub Action are all thin front-ends over one engine
(`comparo.core`), so their behavior — including exit codes — is identical.

## Table of contents

- [Installation](#installation)
- [Getting started](#getting-started)
- [Projects and `--config`](#projects-and---config)
- [Synopsis](#synopsis)
- [Global options](#global-options)
- [Exit codes](#exit-codes)
- [Commands](#commands)
  - [`comparo init`](#comparo-init)
  - [`comparo import openapi`](#comparo-import-openapi)
  - [`comparo validate`](#comparo-validate)
  - [`comparo doctor`](#comparo-doctor)
  - [`comparo schema`](#comparo-schema)
  - [`comparo render`](#comparo-render)
  - [`comparo run`](#comparo-run)
  - [`comparo exec`](#comparo-exec)
  - [`comparo diff`](#comparo-diff)
  - [`comparo tui`](#comparo-tui)
  - [`comparo help`](#comparo-help)
- [Selecting environments](#selecting-environments)
- [Report formats](#report-formats)
- [The gate](#the-gate)
- [GitHub Action](#github-action)

## Installation

Install with [pipx](https://pipx.pypa.io) so the tool lands in its own isolated
environment:

```console
pipx install comparo
```

This puts a single `comparo` executable on your `PATH` (the console entry point
defined by the package). Verify the install:

```console
$ comparo --version
comparo 0.0.0
```

## Getting started

Scaffold a new project with `comparo init`, then open it:

```console
$ comparo init
Project name: my-api
✓ created comparo.yaml
✓ created .comparo/ with a sample environment and request

Next:
  comparo validate    # check it loads
  comparo             # open the TUI
```

`init` writes a `comparo.yaml` manifest plus a `.comparo/` data directory holding a
runnable starter environment and request (pointed at
[postman-echo](https://postman-echo.com)), so the project validates and runs
immediately. It never overwrites an existing manifest or data directory.

From a directory that already has a `comparo.yaml`, every command works with **no
path argument** — the manifest is picked up automatically:

```console
comparo validate            # check it loads
comparo run --env prod      # run every request
comparo diff --pair …       # diff a pair
comparo                     # open the TUI (same as `comparo tui`)
```

To work on a project somewhere else, point [`--config`](#projects-and---config) at
its manifest.

## Projects and `--config`

A comparo project is a `comparo.yaml` **manifest** — the `Project` object — plus the
objects (environments, requests, matrices, schemas, …) it references. The manifest's
`spec.data` field says where those objects live, **relative to the manifest**:

- Projects created by `comparo init` use `data: .comparo`, a hidden directory that
  never collides with the rest of your repository.
- The bundled [examples](../examples) are **self-contained** — they set `data: .`, so
  their object files sit right beside the manifest.

Every command that loads a project accepts `--config` / `-C` to choose it:

| You pass | comparo loads |
| --- | --- |
| _nothing_ | `./comparo.yaml` in the current directory (the default) |
| `--config path/to/comparo.yaml` | that manifest, and the objects under its `spec.data` |
| `--config path/to/dir` | a directory — **every** `*.yaml` beneath it (back-compat) |

Pointing `--config` at a **manifest file** loads only that manifest's `spec.data`;
pointing it at a **directory** loads every YAML object under the directory, regardless
of any manifest. Both forms are validated identically.

If the config does not exist, the command exits `1` with a friendly message:

```
no project at 'comparo.yaml' — run `comparo init` to create one, or point --config at a manifest
```

## Synopsis

```
comparo [OPTIONS] COMMAND [ARGS]...
```

Running `comparo` with **no command** opens the terminal UI on `./comparo.yaml` — the
same as `comparo tui`. Shell-completion commands are not installed.

Every command that loads a project takes `--config` / `-C` to select the manifest (or a
project directory); it defaults to `comparo.yaml` in the current directory.

```
comparo init     [DIRECTORY] [--name NAME] [--data DIR] [--config FILE] [--description TEXT]
comparo import openapi SPEC [--output DIR] [--name NAME]
comparo validate [--config CONFIG]
comparo doctor
comparo schema   [--output FILE]
comparo render   REQUEST_ID [--config CONFIG] [--env NAME]
comparo run      [REQUEST_ID] [--config CONFIG] [--env NAME]
comparo exec     EXECUTION_ID [--config CONFIG]
comparo diff     [REQUEST_ID] [--config CONFIG] [--pair NAME | --baseline NAME --candidate NAME] [--report FMT]... [--output DIR]
comparo tui      [--config CONFIG]
comparo help
```

## Global options

| Option | Short | Description |
| --- | --- | --- |
| `--version` | `-V` | Print `comparo <version>` and exit. |
| `--help` | | Show help and exit. Available on the root and on every command. |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success — the command completed and (for `run`/`exec`/`diff`) the gate passed. |
| `1` | **Gate failure** — the command *ran*, but the gate failed: a `diff` drifted, a `run` / `exec` assertion failed, or a cell errored. This is the real-regression signal CI gates on. |
| `2` | **Usage / config error** — the command could not run: the config was missing or failed to load, an environment / profile / request was unknown, an argument could not be resolved, a report format was unknown, or the selected plan expanded to zero cells. |

So CI can tell a real regression (`1`) from a broken setup (`2`). Every command that
loads a project exits `2` when the config is missing (`no project at '…'`) or the
project cannot be loaded, printing each diagnostic to standard error followed by a
`✗ N problem(s)` summary. The command-specific failure conditions are listed under each
command below.

## Commands

### `comparo init`

Scaffold a new project — a `comparo.yaml` manifest plus a starter data directory that
validates and runs immediately.

```
comparo init [DIRECTORY] [--name NAME] [--data DIR] [--config FILE] [--description TEXT]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `DIRECTORY` | no | Where to create the project. Defaults to the current directory (`.`). |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--name` | `-n` | prompted | The project name. If omitted, `comparo` prompts for it. |
| `--data` | | `.comparo` | Directory the project's objects live in, relative to `DIRECTORY`. |
| `--config` | `-C` | `comparo.yaml` | Filename for the manifest. |
| `--description` | | — | A one-line project description. |

**Behavior & exit code**

Writes the manifest and a `<data>/` directory containing `environments/local.yaml` and
`requests/example.yaml` — a runnable starter pointed at `https://postman-echo.com`, so
the project validates and runs immediately. The starter files carry a
`# yaml-language-server: $schema=…` modeline so editors autocomplete and validate them
against the [JSON Schema](#comparo-schema), and an **`AGENTS.md`** is dropped alongside
them — a compact authoring guide (object model, `${…}` grammar, diff modes, the
`$secret` rule, the `validate` loop) so any coding agent working in the project is
instantly competent. It **refuses to overwrite** an existing manifest or data directory,
exiting `1` without touching your files. On success it prints what it created and the
next commands to run, then exits `0`.

**Example**

```console
$ comparo init
Project name: my-api
✓ created comparo.yaml
✓ created .comparo/ with a sample environment and request

Next:
  comparo validate    # check it loads
  comparo             # open the TUI
```

Scaffold into a new directory, naming the project non-interactively:

```console
$ comparo init services/checkout --name checkout
✓ created services/checkout/comparo.yaml
✓ created services/checkout/.comparo/ with a sample environment and request

Next:
  comparo validate --config services/checkout/comparo.yaml    # check it loads
  comparo --config services/checkout/comparo.yaml             # open the TUI
```

### `comparo import openapi`

Scaffold a project from an existing **OpenAPI 3.x** document (JSON or YAML). It turns
the mechanical parts of the spec into comparo objects so you don't hand-write them.

```
comparo import openapi SPEC [--output DIR] [--name NAME]
```

**Arguments & options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `SPEC` | | *(required)* | Path to the OpenAPI 3.0/3.1 document. |
| `--output` | `-o` | derived from the title | Directory to create the project in. |
| `--name` | `-n` | the spec's `info.title` | The project name. |

**What it maps**

| OpenAPI | → comparo |
| --- | --- |
| `servers` | an **Environment** each (two or more become a `diffPairs` entry) |
| `paths` + operations | a **Request** each (method, path, query, body stub, 2xx status + schema `$use`) |
| `components.schemas` | a **Schema** each |
| `securitySchemes` | an **auth** stub — bearer / basic / apiKey as a `$secret` reference |

**Scaffold, not a finished project.** It never writes a **DiffProfile** — which fields
are volatile is your judgment — and never writes a real credential: every secret is a
`$secret` reference sourced from an environment variable placeholder. Refuses to
overwrite an existing project. Swagger 2.0 is rejected. After import, add DiffProfiles
and real secret values, then `comparo validate`.

```console
$ comparo import openapi petstore.yaml --output petstore
✓ created petstore/ — 2 environments, 5 requests, 3 schemas

This is a scaffold: no diff profiles were generated — add DiffProfiles (and real
secret values), then validate.
```

### `comparo validate`

Validate a project's envelope, ids, and references without making any network
requests.

```
comparo validate [--config CONFIG]
```

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--config` | `-C` | `comparo.yaml` | The manifest (or project directory) to validate. |

**Behavior & exit code**

- On success, prints `✓ N object(s) valid` and exits `0`.
- On failure, prints **every** diagnostic to standard error, then
  `✗ N problem(s)`, and exits `1`.

**Example**

From a project directory, the `comparo.yaml` is picked up automatically:

```console
$ comparo validate
✓ 14 object(s) valid
```

Or point `--config` at a manifest anywhere:

```console
$ comparo validate --config examples/sample-project/comparo.yaml
✓ 14 object(s) valid
```

### `comparo doctor`

Run the **never-leak self-check**: a canary secret is pushed through every output
path and each is verified to have masked it. Takes no project — it builds its own
canary scenario. The TUI runs the same check in **Settings → Security & Redaction**
(press `t`).

```
comparo doctor
```

**Behavior & exit code**

- Prints one line per sink (`✓`/`✗`) — TUI display, saved runs, saved reports,
  the JUnit/SARIF/JSON/Markdown reporters, the copied curl, and the crash report —
  then `N/N sinks masked the canary`.
- Exits `0` if every sink masked the canary; `1` if any leaked (use it as a CI
  guard on the redaction invariant).

```console
$ comparo doctor
✓ TUI display        masked on render
✓ saved runs         .runs/*.json
…
9/9 sinks masked the canary
```

### `comparo schema`

Emit a **JSON Schema** — by default the `comparo/v1` config schema, or with
`--report` the saved-report record schema. Both are generated from the same object
models the code uses, so they can never drift from the real shapes.

```
comparo schema [--output FILE] [--report]
```

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--output` | `-o` | *(stdout)* | Write the schema to a file instead of printing it. |
| `--report` | | off | Emit the saved-report record schema (run/diff/execution) instead of the config schema. See [Report format](report-format.md). |

Point your editor's YAML language server at the config schema for autocomplete and
inline validation. A copy is shipped at [`schema/comparo-v1.schema.json`](../schema/comparo-v1.schema.json);
add a modeline to any project file:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/wbenbihi/comparo/main/schema/comparo-v1.schema.json
apiVersion: comparo/v1
kind: Request
# …
```

It's also the ground truth an LLM agent can author against, then check its work
with `comparo validate`.

### `comparo render`

Show a single request fully resolved for one environment — method, URL, headers,
query, and body — with a provenance trail. Secret values are **masked**; they are
never printed.

```
comparo render REQUEST_ID [--config CONFIG] [--env NAME]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `REQUEST_ID` | yes | The `metadata.id` of the request to render (e.g. `request.get-uuid`). |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--config` | `-C` | `comparo.yaml` | The manifest (or project directory) to load. |
| `--env` | `-e` | project default | Environment name or id to resolve against. See [Selecting environments](#selecting-environments). |

**Behavior & exit code**

Exits `1` (with a message on standard error) if the project fails to load, if no
`Request` has the given id, or if the environment cannot be selected. Otherwise
prints the resolved request and exits `0`. The `provenance` block records where
each filled value came from (literal, variable, instance, matrix, or `secret`).

**Example**

```console
$ comparo render --config examples/sample-project/comparo.yaml request.get-uuid --env prod
GET https://httpbin.org/uuid
  env: Production

headers:
  accept: application/json
  user-agent: comparo-showcase

provenance:
  headers                    instance  ← instance.default-headers
```

### `comparo run`

Execute requests against a single environment and report each cell's status code
and latency. Matrix requests expand to one cell per case.

```
comparo run [REQUEST_ID] [--config CONFIG] [--env NAME]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `REQUEST_ID` | no | A single request id to run. Omit to run every request. |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--config` | `-C` | `comparo.yaml` | The manifest (or project directory) to load. |
| `--env` | `-e` | project default | Environment name or id to run against. |

**Behavior & exit code**

- Prints `run · <environment>` followed by one line per cell: `✓` with status and
  latency on success, `✗` with the error (or the failing check) otherwise.
- A cell that returns a response is **not** an automatic pass: the request's
  `response.status` / `response.schema` sugar **and any `response.assert` profiles**
  are evaluated as assertions, so a `500` against a declared `200`, a schema mismatch,
  or a failed `assert` rule prints red and fails the gate, with the offending check
  named on the line.
- Exits `1` if the project fails to load, the environment cannot be selected, no
  requests match, an execution fails, **or any `error`-severity check fails**.
  Otherwise exits `0`.

**Example**

```console
$ comparo run --config examples/sample-project/comparo.yaml --env prod
run · Production
  ✗ request.echo-anything [currency=USD, locale=en-US] secret 'API_TOKEN': environment variable 'COMPARO_DEMO_TOKEN' is not set
  ✗ request.echo-anything [currency=EUR, locale=fr-FR] secret 'API_TOKEN': environment variable 'COMPARO_DEMO_TOKEN' is not set
  ✗ request.echo-anything [currency=JPY, locale=ja-JP] secret 'API_TOKEN': environment variable 'COMPARO_DEMO_TOKEN' is not set
  ✓ request.get-json                             200  450ms
  ✓ request.get-uuid                             200  444ms
  ✓ request.health-status                        200  445ms
```

That run exits `1` because three cells failed (the demo secret was not provided).
Run a single request that has everything it needs, and it passes:

```console
$ comparo run --config examples/sample-project/comparo.yaml request.get-json --env prod
run · Production
  ✓ request.get-json                             200  443ms
```

### `comparo exec`

Run an [`ExecutionProfile`](configuration.md#executionprofile) headless: replay its
selected requests against the profile's own `baseline` (and `candidate`, when set),
assert the request's checks on **both** environments, diff the pair, and exit on the
gate. This is the headless equivalent of the TUI's Execution screen, and its exit code
is the exact gate that screen shows.

```
comparo exec EXECUTION_ID [--config CONFIG] [--report FORMAT ...] [--output DIR]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `EXECUTION_ID` | yes | The `metadata.id` of the `ExecutionProfile` to run (e.g. `execution.release-gate`). |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--config` | `-C` | `comparo.yaml` | The manifest (or project directory) to load. |
| `--report` | | manifest's `report.formats` | Report format(s) to write: `junit`, `sarif`, `json`, `markdown` (repeatable). |
| `--output` | `-o` | manifest's `report.output`, else `reports/` | Directory to write report files into. |

The environments, request selection, matrix scoping, and which checks run (`assertions`
/ `diff`) all come from the profile itself — `exec` takes no `--env` / `--pair` flags.

Like [`comparo diff`](#comparo-diff), `--report` writes CI artifacts (and falls back to
the manifest's `report` block when omitted). The report's pass/fail is the **execution**
gate, so a cell that failed only its assertions — with no drift — is still a failure in
the artifact; each such failure appears as a row tagged `assert[<env>]`.

**Behavior & exit code**

- Prints `exec · <id>  <baseline> ⇄ <candidate>` (just `<baseline>` when the profile
  sets no candidate), one line per cell (`✓`, or `✗` with the error, the first failing
  assertion, or `drift`), and a final `✓ gate PASS` / `✗ gate FAIL` with the cell,
  drift, and error counts.
- The gate **passes** only when every cell passes — no execution error, every
  `error`-severity assertion holds on both environments, and nothing drifted — and it
  **fails closed** on an empty run (a selection or matrix scope that matched nothing).
- Exits `0` when the gate passes, `1` otherwise — including when the project fails to
  load, there is no `ExecutionProfile` with the given id, or an environment cannot be
  resolved.

**Example**

```console
$ comparo exec execution.methods --config examples/postman-echo-project/comparo.yaml
exec · execution.methods  Echo ⇄ Echo (mirror)
  ✓ request.delete
  ✓ request.get
  ✓ request.get-matrix [page=1, q=widgets]
  …
  ✓ request.put

✓ gate PASS  16 cells · 0 drift · 0 error
```

### `comparo diff`

Replay every request cell against two environments — a **baseline** and a
**candidate** — and diff the responses, applying each request's diff profile. This
is the command CI runs.

```
comparo diff [REQUEST_ID] [--config CONFIG] \
  [--pair NAME | --baseline NAME --candidate NAME] \
  [--report FMT]... [--output DIR]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `REQUEST_ID` | no | A single request id to diff. Omit to diff every request. |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--config` | `-C` | `comparo.yaml` | The manifest (or project directory) to load. |
| `--pair` | `-p` | first declared pair | A named diff pair from the project manifest. |
| `--baseline` | `-b` | — | Baseline environment name or id. |
| `--candidate` | `-c` | — | Candidate environment name or id. |
| `--report` | | none | Report format to write. Repeatable. One of `junit`, `sarif`, `json`, `markdown`. See [Report formats](#report-formats). |
| `--output` | `-o` | `reports` | Directory report files are written to (created if missing). |

**Choosing the two environments**

- If **both** `--baseline` and `--candidate` are given, they are used and any pair
  is ignored.
- Otherwise the named `--pair` is looked up in the project's
  `spec.environments.diffPairs`; if `--pair` is omitted, the first declared pair is
  used.
- If neither an explicit pair of environments nor a manifest pair applies, the
  command exits `1` with `specify --pair, or both --baseline and --candidate`.

**Behavior & exit code**

- Prints `diff · <baseline> ⇄ <candidate>`, one line per cell (`✓ same`,
  `✗ drift`, or `! <error>`), a `summary:` line, and a final `gate: PASS` / `gate: FAIL`.
- Reports (if any `--report` was passed) are written **after** the console output
  and regardless of gate outcome.
- Exits `1` if the project fails to load, the environments cannot be resolved, no
  requests match, or **the gate fails**. Otherwise exits `0`. See
  [The gate](#the-gate).

**Examples**

Diff the project's default pair and write CI reports:

```console
$ comparo diff --config examples/sample-project/comparo.yaml --pair local-vs-prod \
    --report junit --report markdown --output reports
diff · Local ⇄ Production
  ✓ request.get-json                             same
  ✓ request.get-uuid                             same
  ✓ request.health-status                        same

summary: 3 same · 0 drift · 0 error · 0 fields skipped
gate: PASS
  wrote reports/junit.xml
  wrote reports/summary.md
```

Diff an explicit environment pair, overriding any manifest pair:

```console
$ comparo diff --config examples/sample-project/comparo.yaml --baseline local --candidate prod
```

> The `local` environment in the sample project points at `http://localhost:8080`;
> start a local httpbin (`docker run -d -p 8080:80 kennethreitz/httpbin`) before
> diffing against it.

### `comparo tui`

Launch the interactive terminal UI to explore a project. Running `comparo` with **no
command** is equivalent to `comparo tui` on `./comparo.yaml`.

```
comparo tui [--config CONFIG]
```

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--config` | `-C` | `comparo.yaml` | The manifest (or project directory) to open. |

**Behavior & exit code**

If the project loads, the TUI opens. If the project **fails to load**, the TUI
still launches but shows a dedicated **error screen** listing the diagnostics, and
the process exits `1` when you leave it.

**Example**

Open the project in the current directory:

```console
$ comparo
```

Or open one elsewhere:

```console
$ comparo tui --config examples/sample-project/comparo.yaml
```

### `comparo help`

Print the full command reference — identical to `comparo --help`.

```
comparo help
```

Writes the root help (every command and global option) to standard output and exits
`0`.

## Selecting environments

`--env`, `--baseline`, and `--candidate` all accept the same forms:

- the full `metadata.id`, e.g. `environment.prod`; or
- the short segment after the prefix, e.g. `prod`.

When no environment is given, commands fall back to the project's declared default
(`spec.environments.default`). If none is given and the project declares no
default, the command exits `1` with
`no environment given and the project declares no default`.

## Report formats

`comparo diff --report <fmt>` renders the run to one or more formats. The flag is
repeatable; pass it once per format. Files are written into the `--output`
directory (default `reports/`), which is created if it does not exist.

| Format | Filename | Contents |
| --- | --- | --- |
| `junit` | `junit.xml` | JUnit `testsuites` document — drift is a `failure`, error is an `error`. |
| `sarif` | `comparo.sarif` | SARIF 2.1.0 log for GitHub code scanning — one result per drift or error. |
| `json` | `report.json` | Pretty JSON: baseline/candidate, a summary block, and every cell. |
| `markdown` | `summary.md` | A markdown table with a gate line, suitable for a PR/step summary. |

An unrecognized `--report` value is skipped with a warning on standard error
(`unknown report format '<name>' (known: json, junit, markdown, sarif)`); it does
not fail the command.

**GitHub step summary.** When the `markdown` reporter runs and the
`GITHUB_STEP_SUMMARY` environment variable is set (as it always is inside a GitHub
Actions step), the rendered markdown is **appended** to that file in addition to
being written to `summary.md`. This is what makes the diff table appear on the
workflow run's summary page.

## The gate

`comparo diff` (and the report's `passed` flag) apply one gate rule:

> The run **passes** if there is **no drift and no errors**.

Deliberately skipped fields do **not** fail the gate — they are counted and
reported (`… fields skipped`) for visibility only. A failing gate makes
`comparo diff` exit `1`, which is what fails a CI job.

## GitHub Action

The repository ships a composite action that runs `comparo diff` in CI. Because it
drives the same engine as the CLI, its process exit code — and therefore whether
the job passes or fails — matches `comparo diff` exactly (see [The gate](#the-gate)).

### Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `project` | yes | — | Path to the comparo project (its `comparo.yaml` manifest, or the project directory). |
| `pair` | no | `""` | A named diff pair from the project manifest. |
| `baseline` | no | `""` | Baseline environment (with `candidate`, overrides `pair`). |
| `candidate` | no | `""` | Candidate environment (with `baseline`, overrides `pair`). |
| `version` | no | `""` | comparo version specifier to install, appended to the package name — e.g. `==1.2.0`. Empty installs the latest release. |

### Outputs

The action declares **no** outputs. It communicates its result through the
**process exit code**: a failing gate fails the step (and the job). The diff table
is published to the run's step summary via the markdown reporter, and machine-
readable reports are left in `$RUNNER_TEMP/comparo`.

### What it does

The action:

1. installs [uv](https://github.com/astral-sh/setup-uv) with Python 3.13;
2. installs comparo with `uv tool install "comparo<version>"`; and
3. runs `comparo diff` on the given project, writing the `markdown`, `junit`, and
   `sarif` reports to `$RUNNER_TEMP/comparo`.

The `--pair`, `--baseline`, and `--candidate` flags are only passed when the
corresponding input is non-empty. The `markdown`, `junit`, and `sarif` reports are
always written; the markdown reporter additionally appends the diff table to
`$GITHUB_STEP_SUMMARY`.

### Sample workflow

```yaml
name: comparo

on:
  pull_request:

jobs:
  diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Diff staging against prod
        uses: wbenbihi/comparo@main   # pin to a released tag once one is published
        with:
          project: examples/sample-project/comparo.yaml
          pair: local-vs-prod
          # or, overriding the manifest pair:
          # baseline: local
          # candidate: prod
          # version: "==1.2.0"   # pin a release; omit for latest
```

To surface drift in GitHub's Security tab, upload the SARIF report in a follow-up
step:

```yaml
      - name: Upload SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: ${{ runner.temp }}/comparo/comparo.sarif
```
