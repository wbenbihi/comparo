# Comparo documentation

Documentation reconciled for the **0.4.0 preparation branch on 2026-11-05**, based on tagged
v0.3.0. The current implementation includes the immutable domain, versioned compiler, planner,
run-scoped values, HTTP runtime, sanitized artifacts/reporters, local CLI and `comparo.tui`.
The branch adds standalone Value/Matrix inspection, navigable Body/Schema views and starter-project
creation. Start with [current status and remaining gaps](status.md). The authoritative documents are:

- [Core/backend codebase golden reference](codebase/README.md) — current implementation truth,
  end-to-end pipelines, module catalog, contributor workflow, and backend and TUI capability audit
- [TUI/backend integration contract](codebase/tui-backend-integration.md) — callable application
  APIs, safe display fields, lifecycles, paging, cancellation, and screen-level data mapping
- [Core domain foundation](core-domain-foundation.md)
- [Selector language](selector-language.md)
- [Pure evaluation kernel](core-evaluation-kernel.md)
- [Configuration authoring guide](configuration.md) and [versioned compiler](configuration-compiler.md)
- [Deterministic run planner](run-planner.md)
- [Run-scoped values and secrets](run-scoped-values.md)
- [Offline runtime](offline-runtime.md)
- [Production HTTP adapter](production-http-adapter.md)
- [Report/artifact format guide](report-format.md) and [sanitization/persistence contract](artifacts-and-reporters.md)
- [CLI reference](cli.md)
- [0.4.0 release notes](releases/0.4.0.md) — branch changes, migration and qualification
- [0.3.0 release notes](releases/0.3.0.md) — the tagged baseline and changes since v0.2.0
- [Release through GitHub Actions](releasing.md) — PR checks, publishing sequence and recovery
- [Terminal UI](tui.md) — launch commands, default keys and limits
- [Explorer 0.4.0 scope](explorer-0.4.0-plan.md) — implemented resource views, resolution boundaries and remaining limits
- [Launch](codebase/tui-launch.md), [Reports](codebase/tui-reports.md), and
  [Settings](codebase/tui-settings.md) — implemented workspace behavior
- [TUI results implementation contract](tui-results-implementation-contract.md)
- [Historical TUI rework audit](tui-rework-plan.md) and [current design QA](../design-qa.md)
- [Visual and interaction references](design/tui-v2/README.md) — retained mockups and design targets
- [Runnable example projects](../examples/README.md)
- [Architecture](architecture.md)
- [Core refactor roadmap](core-refactor-roadmap.md)
- [Accepted product decisions](decisions/README.md)

Documents explicitly labelled as historical or exploratory describe the quarantined prototype or
future product surfaces. They are retained as design input, not as runnable commands or
compatibility promises. The codebase golden reference distinguishes capabilities that are merely
modeled or compiled from capabilities wired through the current production composition and safe
application APIs. The current tree has a `comparo/v1alpha1` compiler, injected run-resolution
ports, fake and production HTTP execution paths, sanitized artifacts, atomic POSIX local
persistence, pure JSON/JUnit/SARIF/Markdown reporters, and a shared production execution
composition used by `run`, `diff`, `exec`, and the TUI Launch workspace. The public plugin host,
scenarios, and dedicated GitHub Action remain later roadmap work.
