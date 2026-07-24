# comparo

> Validate HTTP behavior and selected semantic differences across environments.

Comparo executes the same logical HTTP input against environments such as development, staging,
and production. It evaluates per-environment assertions, compares selected environment pairs under
explicit diff profiles, and can reduce those facts into a CI gate verdict.

## Status

Comparo is undergoing a complete pre-production core refactor. The former prototype implementation,
tests, and examples have been quarantined and are not compatibility requirements. There is no
usable CLI or TUI on this branch until the new core is built and integrated.

The new configuration lineage will begin at `comparo/v1alpha1`. Do not use the current package for
production workloads.

## Architecture

The refactor is built from an immutable, framework-independent domain center. Configuration,
networking, persistence, CLI, and TUI concerns are adapters around that core.

- [Core refactor roadmap](https://github.com/wbenbihi/comparo/blob/main/docs/core-refactor-roadmap.md)
- [Immutable core domain foundation](https://github.com/wbenbihi/comparo/blob/main/docs/core-domain-foundation.md)
- [Shared selector language](https://github.com/wbenbihi/comparo/blob/main/docs/selector-language.md)
- [Versioned configuration compiler](https://github.com/wbenbihi/comparo/blob/main/docs/configuration-compiler.md)
- [Deterministic run planner](https://github.com/wbenbihi/comparo/blob/main/docs/run-planner.md)
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for repository conventions.

## License

[MIT](LICENSE) © Walid Benbihi
