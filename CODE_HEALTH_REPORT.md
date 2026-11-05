# Code health snapshot

Measured **2026-11-05** for Explorer commit `7aa94db` and CLI-test correction `2878a07`
on `feat/0.4.0-explorer`, preparing 0.4.0 from tagged v0.3.0. Uncommitted work outside
those changes is not included in this verification record.
The [July prototype audit](docs/history/code-health-2026-07-18.md) and
[0.3.0 qualification](docs/releases/0.3.0.md) remain historical evidence, not current measurements.

## Source inventory

Counts include Python files, comments and blank lines under the named directory. They exclude
dependencies, caches, Git objects, documentation, images and quarantined prototype sources.

| Scope | Python files | Lines |
| --- | ---: | ---: |
| Production package (`src/comparo`) | 469 | 117,817 |
| Canonical TUI (subset of production) | 112 | 19,648 |
| Tests | 363 | 121,048 |

Production plus tests total **238,865 Python lines**; TUI is already included in
production. These counts do not measure quality. Package metadata is 0.3.0 pending automated
0.4.0 versioning; Python 3.13+ and nine runtime dependencies remain unchanged: Cyclopts, httpcore,
jsonschema, google-re2, msgspec, PyYAML, referencing, Rich and Textual.

## Architecture and maintenance

The CLI/TUI consume safe application projections. Standalone Value/Matrix inspection now uses
`ResolutionInputs` without a synthetic Request or execution plan; normal execution retains the
`PreparedRun` adapter. Resource components share navigation, paired panes and compact actions.
Direct native Button imports in resource inspectors are prohibited by an architecture regression.
Theme tokens distinguish violet masked values from blue booleans, amber warnings and red failures.

All 12 import contracts pass. Existing large modules remain maintenance work:
`application/evaluation_binding.py` (2,083 lines), `application/resolved_inspection.py` (1,791),
`application/results.py` (1,695), and `cli/app.py` (1,661). A passing check does not make them small.

## Verification evidence

- Full local Python 3.13 run: **4,377 passed, 2 failed** in 567 seconds. Both failures were CLI
  tests hard-coding 0.2.0 instead of checking installed package metadata; no production exception
  caused them. After correction, the complete CLI app/initialization subset passed **91 tests**.
  The full suite was not repeated after that assertion-only fix.
- Combined coverage after the CLI rerun: **97.94%**, with branch measurement retained and the
  configured **95% floor** satisfied. Preserve existing coverage; this is not a reduction target.
- The final Body/standalone-Value/Matrix subset passed **13 tests** after the color changes.
  Earlier focused Explorer and resolution suites are recorded in [design QA](design-qa.md).
- Ruff lint/format, strict mypy (**830 files**), all **12 import contracts**, lock consistency and
  diff whitespace checks passed. Git hooks also checked the implementation and CLI-test commits.
- All four example projects validate without HTTP. Explorer fixtures include nested public data,
  masked secrets, unused Matrices, cross-schema references and recursive schemas.
- Wheel/sdist builds, exact membership audits and gzip/ZIP integrity checks passed. An isolated
  offline wheel installation loaded the new Body and standalone inspection modules and passed
  CLI help, configuration-schema and doctor smoke checks. The local build was made from a modified
  checkout, correctly stamped as such; release automation must rebuild from its clean version commit.
- A no-operation semantic-release preview in an isolated local clone selects **0.4.0** without
  changing this branch, tags or remotes.

These local results do not replace GitHub Actions on the final reviewed commits, Python 3.14
compatibility or release-commit packaging. No new production-readiness or blanket visual-acceptance
claim is made. See [status](docs/status.md), [contributor gates](CONTRIBUTING.md#quality-gates),
[release process](docs/releasing.md) and [visual QA](design-qa.md).

## Documentation review

The refresh covers root guides/changelog/status, CLI initialization and example inventory, Explorer
user journeys, application APIs, resolution boundaries, module/pipeline catalogs, design/color and
quality-policy rules, release notes and automation guidance. Local Markdown targets and heading
anchors are checked across the current documentation inventory. Historical decisions/reports keep
their dates; unchanged subsystem contracts are not rewritten merely to change version numbers.
The README AI Disclaimer is preserved exactly. External websites were not revalidated.
