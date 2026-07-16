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

## Install

```console
pipx install comparo
```

## Quickstart

```console
# validate a project's envelope, ids, and references
comparo validate examples/sample-project

# explore it interactively
comparo tui examples/sample-project

# run every request against an environment
comparo run examples/sample-project --env prod

# diff two environments and write CI reports
comparo diff examples/sample-project --report junit --report markdown
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
