# Code health snapshot

Measured **2026-11-02**, working tree on `refactor`. This replaces the root-level prototype audit;
the original [2026-07-18 report](docs/history/code-health-2026-07-18.md) is retained as historical
evidence. Its grades, findings, dependencies and test totals are not assessments of today's code.

## Source inventory

Counts include every Python file under the named directory.
Lines include comments and blank lines. Dependency environments, caches, Git objects, documentation,
images, generated JSON schemas and quarantined prototype files are excluded.

| Scope | Python files | Lines |
| --- | ---: | ---: |
| Production package (`src/comparo`) | 449 | 114,886 |
| Canonical TUI (subset of production) | 98 | 17,794 |
| Tests | 354 | 119,068 |

Production plus tests total **233,954 Python lines**; the TUI is already included in production.
These are source inventory figures, not a measure of feature quality or executable statements.
The package metadata is 0.2.0, requires Python 3.13+, and declares nine runtime dependencies:
Cyclopts, httpcore, jsonschema, google-re2, msgspec, PyYAML, referencing, Rich and Textual.

## Architecture and remaining maintenance

The CLI and canonical TUI use frontend-neutral application services. The domain and pure engines
remain separate from filesystem, network and presentation adapters. Launch and Reports reuse
Analyze and capture components; preferences stay in the terminal adapter. The former `tui3`,
`tuibis` and versioned `tui4` implementations are absent from the production package.

TUI architecture tests prohibit frontend-to-core widget imports and raw colors outside the theme,
and enforce module-size ceilings with explicit inherited baselines. Some older modules still need
incremental decomposition: `application/evaluation_binding.py` (2,083 lines),
`application/resolved_inspection.py` (1,710), `application/results.py` (1,695), and
`cli/app.py` (1,637). A passing architecture check does not mean every module is small.

## Verification evidence and limits

Prior feature validation passed 176 TUI/architecture tests and 49 artifact/body/cURL tests.
November 2 checks passed 416 TUI/CLI/architecture tests before General, then 20 Settings/build/
packaging tests for that addition.
Ruff, formatting, strict mypy (803 Python files) and 12 import contracts passed. Current test
collection finds 4,310 cases; this count is not a full-suite pass claim. The repository configures
a maintainer-approved 95% combined statement/branch coverage threshold (updated November 2). No new full coverage percentage, security
grade or production-readiness certification is asserted by this documentation audit.

Subsequent release preparation ran 4,313 tests in the local sandbox: 4,289 passed and 24 failed
(23 blocked localhost binds and one stale showcase resource count). Coverage was 97.33%, below
the then-required 100%. This restricted result is not release qualification; GitHub Actions must
validate the PR. The first GitHub run measured **98.01% coverage** and found the stale count
plus a catalog-shutdown race on Python 3.14. Both compatibility jobs passed in the second run,
which measured 98.00% coverage and exposed a separate action-bar race. That component now
preserves buttons during availability changes. The maintainer approved a 95% overall coverage
floor, retaining branch measurement and existing tests. Full CI and packaging verification remain
required; the policy change is not new evidence of coverage. The final PR #6 run subsequently
passed 4,335 tests on each supported Python version, measured 98.09% coverage and passed packaging
and installed-wheel verification. After merge, the Release job passed verification but skipped
publication due to an output collision in release planning. The workflow repair and recovery path
are documented in the [release guide](docs/releasing.md).

The documentation refresh also passed 296 CLI/architecture tests, rechecked Ruff/formatting/mypy,
validated all three example projects and planned all six showcase profiles without HTTP.

Distribution membership is checked against the reviewed Git index. The November 2 wheel/sdist
member audit passed against that index, and an sdist-to-wheel rebuild preserved the build SHA.
Final release verification still runs against the release commit. CI runs lint, formatting, typing,
import contracts, tests/coverage and packaging checks,
with compatibility jobs for Python 3.13 and 3.14.

See [current status and gaps](docs/status.md), [contributor checks](CONTRIBUTING.md), and
[current design QA](design-qa.md). Dedicated CI integration and release qualification remain pending.
