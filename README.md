# comparo

> Validate HTTP behavior and selected semantic differences across environments.

Comparo executes the same logical HTTP input against environments such as development, staging,
and production. It evaluates per-environment assertions, compares selected environment pairs under
explicit diff profiles, and can reduce those facts into a CI gate verdict.

## Status

Comparo is alpha software. The production core, local CLI, and terminal UI have been rebuilt
around the `comparo/v1alpha1` configuration lineage; the former prototype is quarantined and is
not a compatibility target. Dedicated CI integration has not yet been migrated.

The executable subset is deliberately fail-closed: configuration can describe more of the future
model than the first production runtime implements, and an execution command rejects unsupported
capabilities before resolving values, performing DNS, or opening a network connection. Review the
[current CLI limitations](https://github.com/wbenbihi/comparo/blob/main/docs/cli.md#current-executable-subset) before using Comparo for a real
service. This remains a pre-production release.

## CLI

The command graph includes offline schema, validation, planning, diagnostics, and sanitized render
journeys; shared production `run`, `diff`, and `exec` journeys; and authenticated artifact/report
readers:

```console
uv run --frozen comparo --help
uv run --frozen comparo validate --config comparo.yaml
uv run --frozen comparo plan --config comparo.yaml --format json
uv run --frozen comparo run --config comparo.yaml --env staging
uv run --frozen comparo
uv run --frozen comparo tui --config comparo.yaml
```

Local execution defaults to the explicitly permissive trusted-local authority. A project used in
CI or obtained from an untrusted source must be paired with an operator-owned policy outside that
project:

```console
uv run --frozen comparo diff \
  --config comparo.yaml \
  --policy /operator/policies/comparo-ci.yaml \
  --baseline production \
  --candidate staging
```

See the [CLI reference](https://github.com/wbenbihi/comparo/blob/main/docs/cli.md) for headless commands, trust semantics, exit codes,
persistence, and runtime limits, and the [TUI guide](https://github.com/wbenbihi/comparo/blob/main/docs/tui.md) for the four-workspace terminal
journey.

## Examples

The repository includes three runnable local projects backed by one deterministic loopback demo
service. They cover a passing quickstart, deliberate cross-Environment drift, Environment-local
values, execution profiles, and a larger two-Matrix TUI showcase:

```console
uv run --frozen python examples/support/demo_server.py
uv run --frozen comparo tui --config examples/tui-showcase/comparo.yaml
```

See the [example project guide](https://github.com/wbenbihi/comparo/blob/main/examples/README.md) for the expected outcomes and suggested
Explorer, Compare, and Report journeys.

## Architecture

The refactor is built from an immutable, framework-independent domain center. Configuration,
networking, persistence, CLI, and TUI concerns are adapters around that core.

- [Core refactor roadmap](https://github.com/wbenbihi/comparo/blob/main/docs/core-refactor-roadmap.md)
- [Immutable core domain foundation](https://github.com/wbenbihi/comparo/blob/main/docs/core-domain-foundation.md)
- [Shared selector language](https://github.com/wbenbihi/comparo/blob/main/docs/selector-language.md)
- [Versioned configuration compiler](https://github.com/wbenbihi/comparo/blob/main/docs/configuration-compiler.md)
- [Deterministic run planner](https://github.com/wbenbihi/comparo/blob/main/docs/run-planner.md)
- [Run-scoped values and secrets](https://github.com/wbenbihi/comparo/blob/main/docs/run-scoped-values.md)
- [Production HTTP adapter](https://github.com/wbenbihi/comparo/blob/main/docs/production-http-adapter.md)
- [Sanitized artifacts, persistence, and reporters](https://github.com/wbenbihi/comparo/blob/main/docs/artifacts-and-reporters.md)
- [CLI reference](https://github.com/wbenbihi/comparo/blob/main/docs/cli.md)
- [Terminal UI](https://github.com/wbenbihi/comparo/blob/main/docs/tui.md)
- [Accepted architecture decisions](https://github.com/wbenbihi/comparo/blob/main/docs/decisions/README.md)

The canonical product promise, security boundary, configuration model, HTTP semantics, assertion
and diff pipelines, artifact rules, and compatibility policy are recorded in those documents.

## Development

Comparo requires Python 3.13 or newer and uses `uv` for reproducible environments:

```console
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
uv run --frozen python -m pytest --cov --cov-report=term-missing
```

See [CONTRIBUTING.md](https://github.com/wbenbihi/comparo/blob/main/CONTRIBUTING.md) for repository conventions.

## License

[MIT](LICENSE) © Walid Benbihi
