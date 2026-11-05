# Contributing to comparo

Thanks for your interest in improving comparo.

## Development setup

comparo uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```console
uv sync --frozen                          # create the venv from the committed lock
uv run --frozen pre-commit install --hook-type pre-commit --hook-type commit-msg
```

To inspect local UI changes, run the workspace executable rather than an unrelated installed
release, then restart it after Python edits:

```console
.venv/bin/comparo tui --config examples/explorer-inspection/comparo.yaml
```

The inspection fixture needs no HTTP server. The three execution demos use the service documented
in [examples](examples/README.md).

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

CI requires at least **95% overall statement-and-branch coverage**. Branch measurement stays enabled;
this floor is not a target to reduce coverage to. Preserve existing tests and add behavioral regression
tests for bug fixes. Prioritize execution correctness, assertions, policy enforcement, secret handling,
and artifact integrity; UI tests should verify observable behavior, keyboard focus, and asynchronous
state transitions rather than merely execute lines. Review coverage changes alongside the code.

A release is qualified only when the full GitHub Actions run passes, including Python 3.13/3.14,
distribution validation, and the installed-wheel smoke test. Targeted local tests are supporting
evidence, not a substitute for those checks.

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

## Terminal UI changes

Use `src/comparo/tui` and `tests/tui`; retired versioned packages are not extension points.
Read the [workspace guides](docs/tui.md) and [visual references](docs/design/tui-v2/README.md).
Compose the existing component library and semantic glyph/color tokens. Standard Textual mechanics
belong inside those components; workspaces must not introduce differently styled parallel widgets.
Launch and Reports share `ResultsView` and the capture inspector. Resource inspectors use shared
`ActionBar` actions; importing native Textual `Button` there is forbidden. `value.masked` is a
dedicated violet role, distinct from blue booleans, amber warnings and red failures. Keep textual
mask markers in no-color mode.

Run focused Pilot journeys at 80×30, 120×40 and 160×52, then relevant regressions. Capture and inspect
actual screenshots when layout changes. A passing interaction test alone does not establish mockup
fidelity. Document implemented controls separately from backend-only capabilities and design goals.
User preference tests must use a temporary preference path rather than writing the developer's config.

## Commits

Commit messages follow the [Angular Commit Convention](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit)
(`type(scope): subject`), which drives automated semantic releases. Keep subjects concise and
imperative; use a body when it helps explain behavior or validation. Split unrelated changes into
focused, reviewable commits; do not put an entire mixed working tree into one commit. Common types: `feat`,
`fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`.

## Pull requests

Keep PRs focused. Ensure the quality gates are green and add tests for behavioural changes.

## Documentation ownership

Preserve the author's AI Disclaimer in the README, including its wording and visibility.
Documentation refreshes and release preparation must not remove or weaken it without an explicit
request from the author.

## Releases

Prepare the release as a PR to the intended release branch. GitHub Actions performs the full CI
validation and, after merge, versioning, packaging and publication. Preserve focused commits,
leave version/tag creation to semantic-release, and resolve failing checks before merging.
See the [release guide](docs/releasing.md) for the current findings, workflow and recovery steps.
