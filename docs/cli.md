# comparo CLI & GitHub Action

Reference for the `comparo` command-line interface and the `comparo diff` GitHub
Action. **comparo** replays the same HTTP requests against two environments and
diffs the responses to catch regressions before they ship.

The CLI, the TUI, and the GitHub Action are all thin front-ends over one engine
(`comparo.core`), so their behavior — including exit codes — is identical.

## Table of contents

- [Installation](#installation)
- [Synopsis](#synopsis)
- [Global options](#global-options)
- [Exit codes](#exit-codes)
- [Commands](#commands)
  - [`comparo validate`](#comparo-validate)
  - [`comparo render`](#comparo-render)
  - [`comparo run`](#comparo-run)
  - [`comparo diff`](#comparo-diff)
  - [`comparo tui`](#comparo-tui)
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

## Synopsis

```
comparo [OPTIONS] COMMAND [ARGS]...
```

Running `comparo` with no command prints help. Shell-completion commands are not
installed.

Every command takes a `PROJECT` **positional argument** — the path to a project
directory (it must exist and be a directory). It is not a `--project` option.

```
comparo validate PROJECT
comparo render   PROJECT REQUEST_ID [--env NAME]
comparo run      PROJECT [REQUEST_ID] [--env NAME]
comparo diff     PROJECT [REQUEST_ID] [--pair NAME | --baseline NAME --candidate NAME] [--report FMT]... [--output DIR]
comparo tui      PROJECT
```

## Global options

| Option | Short | Description |
| --- | --- | --- |
| `--version` | `-V` | Print `comparo <version>` and exit. |
| `--help` | | Show help and exit. Available on the root and on every command. |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success — the command completed and (for `run`/`diff`) the gate passed. |
| `1` | Failure — the project failed to load, an argument could not be resolved, an execution failed, or the diff gate failed. |

Every command exits `1` when the project cannot be loaded, printing each
diagnostic to standard error followed by a `✗ N problem(s)` summary. The
command-specific failure conditions are listed under each command below.

## Commands

### `comparo validate`

Validate a project's envelope, ids, and references without making any network
requests.

```
comparo validate PROJECT
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `PROJECT` | yes | Path to the project directory to validate. |

**Behavior & exit code**

- On success, prints `✓ N object(s) valid` and exits `0`.
- On failure, prints **every** diagnostic to standard error, then
  `✗ N problem(s)`, and exits `1`.

**Example**

```console
$ comparo validate examples/sample-project
✓ 13 object(s) valid
```

### `comparo render`

Show a single request fully resolved for one environment — method, URL, headers,
query, and body — with a provenance trail. Secret values are **masked**; they are
never printed.

```
comparo render PROJECT REQUEST_ID [--env NAME]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `PROJECT` | yes | Path to the project directory. |
| `REQUEST_ID` | yes | The `metadata.id` of the request to render (e.g. `request.get-uuid`). |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--env` | `-e` | project default | Environment name or id to resolve against. See [Selecting environments](#selecting-environments). |

**Behavior & exit code**

Exits `1` (with a message on standard error) if the project fails to load, if no
`Request` has the given id, or if the environment cannot be selected. Otherwise
prints the resolved request and exits `0`. The `provenance` block records where
each filled value came from (literal, variable, instance, matrix, or `secret`).

**Example**

```console
$ comparo render examples/sample-project request.get-uuid --env prod
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
comparo run PROJECT [REQUEST_ID] [--env NAME]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `PROJECT` | yes | Path to the project directory. |
| `REQUEST_ID` | no | A single request id to run. Omit to run every request. |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
| `--env` | `-e` | project default | Environment name or id to run against. |

**Behavior & exit code**

- Prints `run · <environment>` followed by one line per cell: `✓` with status and
  latency on success, `✗` with the error on failure.
- Exits `1` if the project fails to load, the environment cannot be selected, no
  requests match, **or any execution fails**. Otherwise exits `0`.

**Example**

```console
$ comparo run examples/sample-project --env prod
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
$ comparo run examples/sample-project request.get-json --env prod
run · Production
  ✓ request.get-json                             200  443ms
```

### `comparo diff`

Replay every request cell against two environments — a **baseline** and a
**candidate** — and diff the responses, applying each request's diff profile. This
is the command CI runs.

```
comparo diff PROJECT [REQUEST_ID] \
  [--pair NAME | --baseline NAME --candidate NAME] \
  [--report FMT]... [--output DIR]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `PROJECT` | yes | Path to the project directory. |
| `REQUEST_ID` | no | A single request id to diff. Omit to diff every request. |

**Options**

| Option | Short | Default | Description |
| --- | --- | --- | --- |
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
$ comparo diff examples/sample-project --pair local-vs-prod \
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
$ comparo diff examples/sample-project --baseline local --candidate prod
```

> The `local` environment in the sample project points at `http://localhost:8080`;
> start a local httpbin (`docker run -d -p 8080:80 kennethreitz/httpbin`) before
> diffing against it.

### `comparo tui`

Launch the interactive terminal UI to explore a project.

```
comparo tui PROJECT
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `PROJECT` | yes | Path to the project directory to open. |

**Behavior & exit code**

If the project loads, the TUI opens. If the project **fails to load**, the TUI
still launches but shows a dedicated **error screen** listing the diagnostics, and
the process exits `1` when you leave it.

**Example**

```console
$ comparo tui examples/sample-project
```

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
| `project` | yes | — | Path to the comparo project directory. |
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
3. runs the equivalent of:

   ```bash
   comparo diff "<project>" [--pair …] [--baseline …] [--candidate …] \
     --report markdown --report junit --report sarif \
     --output "$RUNNER_TEMP/comparo"
   ```

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
        uses: wbenbihi/comparo@v1
        with:
          project: examples/sample-project
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
