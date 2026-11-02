# Release through GitHub Actions

The rebuilt implementation is proposed through a pull request from `refactor` to `main`.
Versioning, tags, GitHub releases and PyPI publication belong to the repository's
[Release workflow](../.github/workflows/release.yml), not a local publishing command.
The target is the **0.3.0 minor release**, confirmed by a semantic-release no-operation preview.
The existing pre-1.0 configuration keeps this history in the 0.x line; no major release is requested.
See the [complete changes since v0.2.0](releases/0.3.0.md). Package metadata remains 0.2.0 until that workflow
creates the version commit.

## Pull request validation

Opening the PR starts the [CI workflow](../.github/workflows/ci.yml) on GitHub-hosted Ubuntu 24.04
runners. CI installs the locked dependencies and runs:

1. Lock consistency, Ruff lint/format, strict mypy and import contracts.
2. The full test suite with the configured **95% combined statement/branch coverage gate**.
3. Wheel/sdist builds, exact distribution membership and archive-integrity checks.
4. An installed-wheel smoke test and Python 3.13/3.14 compatibility jobs.

These isolated CI runs provide the release evidence. Local tests that cannot bind a loopback
server in the development sandbox are not a substitute for the GitHub result. Keep the PR in
draft until failures are resolved and the required checks pass. The maintainer approved a 95%
overall coverage floor on November 2, retaining branch measurement and all existing tests. This
replaces the previous blanket 100% requirement; it does not certify the uncovered paths. See the
[quality policy](../CONTRIBUTING.md#quality-gates). Do not bypass this gate or treat it as a target
to reduce existing coverage to.

The repository's CI runners are distinct from Comparo's operator-owned ExecutionPolicy for
running untrusted API projects. This release does not introduce a distributable Comparo CI action.

## Current qualification findings

The November 2 local sandbox run completed **4,289 passing / 24 failing tests**:

- 23 failures were localhost binding restrictions (`PermissionError`), requiring CI execution.
- The showcase inventory test in `tests/examples/test_projects.py` expects 44 resources; the
  current project contains 45. The PR follow-up corrects this assertion.
- That restricted run measured **97.33%** combined coverage. The definitive CI measurement is
  recorded below; the then-configured 100% release gate was not met by this local run.

Lint, formatting, strict typing, import contracts and lock verification passed locally. The latest
Settings section-navigation change passed 17 targeted Settings/architecture tests. These results
do not constitute release approval. See [current implementation status](status.md) for scope and gaps.

## First GitHub Actions results and fixes

[The initial PR run](https://github.com/wbenbihi/comparo/actions/runs/41530231464) completed:

- Python 3.13: **4,312 passed / 1 failed**, solely the stale showcase count.
- Python 3.14: **4,311 passed / 2 failed**, the count and a Launch catalog reload completing during
  widget teardown after saving a profile.
- Main check: **98.01% coverage**, below the then-required 100%, plus the stale count failure.

The count is corrected in `eff61cf`. The teardown race is fixed in `fb0ae71`, with deterministic
regressions for both successful and failed catalog reads arriving after shutdown. Targeted checks
passed (3 example-plan cases and 15 profile-authoring/architecture cases).

[The second PR run](https://github.com/wbenbihi/comparo/actions/runs/41533468340) passed both Python
compatibility jobs. The main check found a separate action-bar race in the profile-save journey
(4,314 passed / 1 failed) and measured 98.00% coverage. Availability changes now update existing
buttons instead of asynchronously removing and rebuilding them, preserving controls and focus.
Regression tests exercise repeated availability changes, disabled/stale actions, and profile-save
failures. Additional tests cover profile publication, build metadata, and platform/contract edges.

The approved coverage policy is now 95% overall, with branch measurement retained. The next full
GitHub run must pass tests, compatibility, packaging and wheel checks before qualification.

## After CI and review

1. Resolve the CI failures, review migration behavior and mark the PR ready for review.
2. Obtain the required approvals and merge to `main`, preserving the focused commits with a merge
   commit where repository rules allow. Do not manually edit the version or create a release tag.
3. The main-branch push starts Release. It revalidates the source, derives the version, and creates
   the version/changelog commit and tag locally on the runner.
4. Release rebuilds distributions **after** the version commit. It verifies the version, archive
   members and installed wheel, including that the embedded build SHA equals the release commit
   and the build is unmodified.
5. Only after verification does the workflow atomically push the commit/tag and create the GitHub
   release. The separate `publish` job uses the `pypi` environment and OIDC Trusted Publishing.
   Complete an environment approval if the repository requires one.
6. Confirm the Release and publish jobs succeed, inspect the GitHub assets and PyPI version, then
   install the released wheel and verify Settings → General reports the release version and SHA.

Merging is the publishing trigger; creating or updating the PR does not publish a release. The
`alpha`, `beta` and `rc` branches are also configured release channels, but this PR targets `main`.

## Recovery

If publication is interrupted after a tag exists, use **Actions → Release → Run workflow** on
the corresponding release branch with `release_tag` set to that existing tag (for example,
`v0.3.0`). The workflow validates ancestry, checks out that exact commit, reruns verification,
rebuilds assets and resumes publication. Leave `release_tag` empty only when intentionally asking
the workflow to evaluate a new release. Never overwrite or force-move an existing release tag.
