# comparo

> HTTP regression & diff testing across environments — TUI, headless CLI, and CI.

**comparo** replays the same HTTP requests against two environments (say `staging` and
`prod`), then diffs the responses to catch regressions before they ship. It understands that
responses are not always byte-identical — so alongside a git-style **content diff** it does a
**structural diff** that you configure: ignore volatile fields, tolerate array-length
differences, validate against a schema, or require exact equality, per JSON path.

It is **not tied to any particular API** — the bundled example targets an LLM gateway, but the
engine knows nothing about LLMs. Anything domain-specific lives behind a plugin.

## Status

Pre-1.0, under active development. A runnable, self-contained example lives in
[`examples/sample-project`](examples/sample-project); the full configuration specification is
being written.

## Install

```console
pipx install comparo
```

## Concepts

Projects are described by version-controlled YAML objects, each with a Kubernetes-style
envelope (`apiVersion` / `kind` / `metadata` / `spec`):

| `kind` | Purpose |
| --- | --- |
| `Environment` | a target: base URL, timeout, credentials, variables, health checks |
| `Request` | an HTTP request, optionally matrix-expanded, with a response schema and diff profile |
| `Schema` | a JSON Schema used for structural validation |
| `Instance` | a reusable value injected by reference to avoid duplication |
| `Matrix` | a set of parameter cases a request is run against |
| `DiffProfile` | how two responses are compared, per JSON path |
| `Project` | run-wide defaults: environments, concurrency, reporting, plugins |

Secrets are resolved lazily and never leak: values are masked in the TUI, scrubbed from
reports, and kept out of version control.

## Architecture

A single engine (`comparo.core`) powers three front-ends — the TUI, the CLI, and the GitHub
Action — none of which the core depends on. The boundary is enforced in CI.

## License

[MIT](LICENSE) © Walid Benbihi
