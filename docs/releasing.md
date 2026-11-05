# Release through GitHub Actions

The current preparation target is **0.4.0**, based on the existing **v0.3.0** tag. See the
[0.4.0 notes](releases/0.4.0.md) for scope and migration. Package metadata and `uv.lock` remain at
0.3.0 while the branch is reviewed; the [Release workflow](../.github/workflows/release.yml)
creates the version/changelog commit and tag after merge. Do not create a local release tag or
manually bump the package version to simulate publication.

## Pull request validation

The [CI workflow](../.github/workflows/ci.yml) runs on pull requests and pushes to `main` on
GitHub-hosted Ubuntu 24.04 runners. It checks:

1. Locked dependencies, Ruff lint/format, strict mypy and all import contracts.
2. The full test suite with the configured **95% combined statement/branch coverage floor**.
3. Wheel/sdist builds, exact distribution membership and archive integrity.
4. Installed-wheel CLI/schema/doctor smoke checks and Python 3.13/3.14 compatibility.

Preserve existing coverage and meaningful regression tests; the floor is not a target to reduce
coverage to. Local tests requiring HTTP servers must be allowed to bind loopback sockets. A
sandbox-denied bind is not a passing test, and a local pass is not the final GitHub result.
Follow [contributor quality gates](../CONTRIBUTING.md#quality-gates). Keep a release PR in draft
until failures are resolved and the required checks pass.

Distribution audits compare archives with the reviewed Git index. Include every intended new
package module in a focused commit before building; untracked package files are rejected. Builds
embed the source SHA and modified-source flag. An editable workspace and a pipx release are
separate installations; inspect local changes with `.venv/bin/comparo` from the repository.

## Version selection

Conventional `feat` commits since v0.3.0 select the next minor version, **0.4.0**. `fix` commits
select patches when there are no feature changes. The pre-1.0 settings keep this release line at
0.x (`allow_zero_version = true`, `major_on_zero = false`). Keep implementation and documentation
commits focused; do not squash feature work into a `docs`-only message that loses its release signal.
The workflow logs its version/tag decision before doing any publication. A feature branch is not a
release channel; `main`, `alpha`, `beta` and `rc` are the configured channels.

## Current qualification findings

Branch-local evidence is recorded in [code health](../CODE_HEALTH_REPORT.md) and
[design QA](../design-qa.md). No existing 0.3.0 CI result qualifies the new 0.4.0 code. Review the
[current implementation status](status.md) and explicit preview/remote-schema limitations before
release. The repository's own CI is separate from a distributable Comparo GitHub Action, which
remains deferred.

## Release planning incident and repair

The first 0.3.0 release attempt calculated the right version but skipped publication because
semantic-release's print-only queries overwrote `GITHUB_OUTPUT` with `released=false`. The repaired
planner removes that variable from query subprocesses, validates the version/tag and writes its
own outputs once. Real-package regressions cover a new release, no release and tag recovery.
The repair is merged and `v0.3.0` now exists. Historical qualification is retained in the
[0.3.0 release notes](releases/0.3.0.md); the old instruction to wait for the repair is obsolete.

## Publishing credentials

Checkout uses the ordinary Actions token without persisting Git credentials. Only the final
atomic commit/tag push receives repository secret `RELEASE_TOKEN`; its owner must be authorized
to push generated version commits to the protected release branch. The workflow does not change
branch protection. GitHub assets use the ordinary Actions token. PyPI publishing is a separate
job using OIDC Trusted Publishing and the `pypi` environment; `RELEASE_TOKEN` is not a PyPI credential.

The generated version commit starts normal CI, while a release-job guard prevents recursive release.

## After CI and review

1. Review the concrete changes, resolve CI failures and obtain the required approvals.
2. Merge to `main`, preserving the feature release signal and focused history where repository
   rules allow. **Merging is the publication trigger**; creating a branch or PR is not.
3. Release revalidates source, derives the version, and creates the version/changelog commit and
   tag locally on the runner.
4. It rebuilds distributions after that commit and verifies version identity, archive membership,
   integrity and the installed wheel. Embedded build SHA must match the release commit and the
   modified flag must be false.
5. Only then does it atomically push the version commit/tag and create the GitHub release. The
   separate OIDC `publish` job uploads the verified distributions to PyPI.
6. Confirm Release and publish both succeed, inspect GitHub assets and PyPI version, then install
   the released wheel and check Settings → General. A green job that skipped publication is not
   evidence that a package was released.

## Recovery

If publication is interrupted after a tag exists, use **Actions → Release → Run workflow** on
the release branch with `release_tag` set to that existing tag, for example `v0.3.0` or, after it
has been created, `v0.4.0`. The workflow validates ancestry, checks out the exact commit, reruns
verification, rebuilds assets and resumes publication. Leave `release_tag` empty only when
intentionally evaluating a new release. Never overwrite or force-move a release tag.
