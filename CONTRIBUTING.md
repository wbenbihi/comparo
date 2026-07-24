# Contributing to comparo

Thanks for your interest in improving comparo.

## Development setup

comparo uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```console
uv sync --frozen                          # create the venv from the committed lock
uv run --frozen pre-commit install --hook-type pre-commit --hook-type commit-msg
```

## Quality gates

Every change must pass the same checks CI runs:

```console
uv run --frozen ruff check .          # lint (and import sorting)
uv run --frozen ruff format --check . # formatting
uv run --frozen mypy                  # strict type checking
uv run --frozen lint-imports          # architecture contracts
uv run --frozen python -m pytest --cov --cov-report=term-missing
uv lock --check
uv build
uv run --frozen python -m tests.architecture.distribution_members \
  dist/comparo-*.tar.gz dist/comparo-*.whl
gzip --test dist/comparo-*.tar.gz
unzip -t dist/comparo-*.whl
```

The archive checks require a clean reviewed Git index: untracked package files are deliberately
rejected rather than treated as release inputs.

## Architecture rules

The domain in `comparo.core.domain` is an immutable, I/O-free semantic kernel. It does not import
HTTP clients, parsers, schema engines, front ends, or other adapter frameworks. External document
DTOs and sanitized artifact DTOs are separate from live domain objects. Architecture tests and
import-linter enforce these boundaries.

Every concrete domain `msgspec.Struct` declares `frozen=True` and `kw_only=True`; its nested state
must also be immutable. Type annotations do not validate direct `msgspec` constructors, so domain
invariants belong in `__post_init__` and focused factories. Do not use `Any`, mutable containers,
generic recursive dictionaries, `msgspec.structs.force_setattr`, or broad serialization helpers in
the domain.

Prefer small, final value types and composition. Inheritance is appropriate only for a genuine
substitutability relationship. Ports are narrow standard-library protocols, and effectful work
stays outside the domain.

## Commits

Commit messages follow the [Angular Commit Convention](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit)
(`type(scope): subject`), which drives automated semantic releases. Keep subjects concise and
imperative; use a body only for breaking changes or large features. Common types: `feat`,
`fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`.

## Pull requests

Keep PRs focused. Ensure the quality gates are green and add tests for behavioural changes.
