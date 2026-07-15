# Contributing to comparo

Thanks for your interest in improving comparo.

## Development setup

comparo uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```console
uv sync                                   # create the venv and install everything
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

## Quality gates

Every change must pass the same checks CI runs:

```console
uv run ruff check .          # lint (and import sorting)
uv run ruff format .         # format
uv run mypy                  # strict type checking
uv run lint-imports          # architecture contract (core must not import interfaces)
uv run pytest                # tests
```

## Architecture rule

The engine in `comparo.core` must not import from `comparo.cli`, `comparo.tui`, or
`comparo.adapters`. Front-ends depend on the core, never the reverse. This is enforced by
import-linter and will fail CI if violated.

## Commits

Commit messages follow the [Angular Commit Convention](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit)
(`type(scope): subject`), which drives automated semantic releases. Keep subjects concise and
imperative; use a body only for breaking changes or large features. Common types: `feat`,
`fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`.

## Pull requests

Keep PRs focused. Ensure the quality gates are green and add tests for behavioural changes.
