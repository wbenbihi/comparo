# Comparo documentation

The `refactor` branch currently contains Steps 1 through 11 of the rebuild: the immutable
domain, shared selector engine, pure evaluation kernel, versioned configuration compiler,
deterministic planner, run-scoped value/secrets boundary, bounded runtime, production HTTP adapter,
sanitized artifact/reporting boundary, migrated local CLI, and component-based terminal UI. The
authoritative current documents are:

- [Core domain foundation](core-domain-foundation.md)
- [Selector language](selector-language.md)
- [Pure evaluation kernel](core-evaluation-kernel.md)
- [Versioned configuration compiler](configuration-compiler.md)
- [Deterministic run planner](run-planner.md)
- [Run-scoped values and secrets](run-scoped-values.md)
- [Offline runtime](offline-runtime.md)
- [Production HTTP adapter](production-http-adapter.md)
- [Sanitized artifacts, persistence, and reporters](artifacts-and-reporters.md)
- [CLI reference](cli.md)
- [Terminal UI](tui.md)
- [TUI results implementation contract](tui-results-implementation-contract.md)
- [TUI rework audit and implementation contract](tui-rework-plan.md)
- [Runnable example projects](../examples/README.md)
- [Architecture](architecture.md)
- [Core refactor roadmap](core-refactor-roadmap.md)
- [Accepted product decisions](decisions/README.md)

Documents explicitly labelled as historical or exploratory describe the quarantined prototype or
future product surfaces. They are retained as design input, not as runnable commands or
compatibility promises. The current tree has a `comparo/v1alpha1` compiler, injected run-resolution
ports, fake and production HTTP execution paths, sanitized artifacts, atomic POSIX local
persistence, pure JSON/JUnit/SARIF/Markdown reporters, and a shared production execution
composition used by `run`, `diff`, `exec`, and the TUI Compare workspace. The public plugin host,
scenarios, and dedicated GitHub Action remain later roadmap work.
