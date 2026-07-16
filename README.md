# comparo

> HTTP regression & diff testing across environments — TUI, headless CLI, and CI.

**comparo** replays the same HTTP requests against two environments (say `staging` and `prod`),
then diffs the responses to catch regressions before they ship. It understands that responses
are not always byte-identical — so alongside a git-style **content diff** it does a **structural
diff** you configure: ignore volatile fields, tolerate array-length differences, validate against
a schema, or require exact equality, per JSON path.

It is **not tied to any particular API** — the bundled example targets httpbin, but the engine
knows nothing about your domain. Anything domain-specific lives behind a plugin.

One engine (`comparo.core`) powers three front-ends that never leak back into it:

- a **TUI** to explore a project, run requests with live results, diff environments, and triage;
- a headless **CLI** for the same, scriptable;
- a **GitHub Action** that fails the build on untriaged drift.

## Status

Pre-1.0, under active development — a first beta. A runnable, self-contained example lives in
[`examples/sample-project`](examples/sample-project).

> [!WARNING] Warning: AI Disclaimer
>
> `comparo` was created as a personal intiative to learn and play with terminal user interface frameworks
> and coding agents for real life use cases. This tools has been really handy for both my personal and corporate projects.
> Due to its **AI co-Authoring** and despite the fact I tried to require good development practice from an LLM agent,
> I cannot advise you to rely on this tool for production purpose in its current state. This warning will stay here until I have fully
> audited the code and can confidently release a prod-ready version.
>
> To be clear:
>
> **AI CODED THIS PROJECT BECAUSE I DIDN'T HAD TIME TO SPEND ON THIS. DO NOT TRUST BLINDLY THIS PROJECT NOR ANY VIBE CODED PROJECT YOU**
> **HAVE NOT FULLY UNDERSTOOD OR AUDITED.**

## Install

```console
pipx install comparo
```

## Quickstart

Scaffold a project (a `comparo.yaml` manifest plus a `.comparo/` starter), then explore it —
from a directory with a `comparo.yaml`, commands need no path and a bare `comparo` opens the TUI:

```console
# create a new project and open the terminal UI
comparo init
comparo

# validate, run, and diff — the ./comparo.yaml is picked up automatically
comparo validate
comparo run --env prod
comparo diff --pair local-vs-prod --report junit --report markdown
```

Or work on a project elsewhere by pointing `--config` at its manifest — for example the
runnable, self-contained [`examples/sample-project`](examples/sample-project):

```console
comparo validate --config examples/sample-project/comparo.yaml
comparo tui      --config examples/sample-project/comparo.yaml
comparo diff     --config examples/sample-project/comparo.yaml --pair local-vs-prod
```

The [Terminal UI guide](docs/tui.md) walks through each screen; the [CLI reference](docs/cli.md)
documents every command and the GitHub Action.

## Highlights

- **Structural diff, not just bytes.** A `DiffProfile` decides, per JSON path, whether a field
  must be exact, match a shape, keep its type, stay within a tolerance, or be ignored.
- **Grouped drift.** A field that drifts across three matrix cells reads as one bug, not three.
- **Reviewable triage.** Silencing a drift writes an ignore rule into a committed profile — it
  shows up in `git diff`, not just in memory.
- **Secrets never leak.** Values are masked in the TUI, redacted from saved runs and reports
  (even when a server echoes them back), and kept out of version control.
- **The gate is the gate.** The TUI's Report screen, the CLI, and the Action share one reporter
  engine, so exit codes and verdicts match everywhere.

## Concepts

Projects are described by version-controlled YAML objects, each with a Kubernetes-style envelope
(`apiVersion` / `kind` / `metadata` / `spec`):

| `kind`        | Purpose |
| ------------- | ------- |
| `Environment` | a target: base URL, timeout, credentials, variables, health checks |
| `Request`     | an HTTP request, optionally matrix-expanded, with a response schema and diff profile |
| `Schema`      | a JSON Schema used for structural validation |
| `Instance`    | a reusable value injected by reference to avoid duplication |
| `Matrix`      | a set of parameter cases a request is run against |
| `DiffProfile` | how two responses are compared, per JSON path |
| `Project`     | run-wide defaults: environments, concurrency, reporting, plugins |

The full format — every field, the `${...}` interpolation grammar, the `$ref`/`$val`/`$secret`
sigils, matrices, and diff modes — is in the [configuration reference](docs/configuration.md).

## Documentation

- [Configuration reference](docs/configuration.md) — the `comparo/v1` object model
- [Terminal UI guide](docs/tui.md) — every screen and keybinding
- [CLI & GitHub Action](docs/cli.md) — commands, flags, exit codes, CI
- [Architecture](docs/architecture.md) — the engine, the ports, the boundary

## Architecture

A single engine (`comparo.core`) powers the three front-ends — none of which the core depends
on. The core imports no HTTP library; that lives behind an adapter. Both boundaries are enforced
in CI by import-linter. See [Architecture](docs/architecture.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The gates — `ruff`, `mypy --strict`, `import-linter`,
`pytest` — run on every commit via pre-commit.

## License

[MIT](LICENSE) © Walid Benbihi
