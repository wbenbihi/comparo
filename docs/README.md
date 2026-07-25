# Comparo documentation

The `refactor` branch currently contains Steps 1 through 9 of the core rebuild: the immutable
domain, shared selector engine, pure evaluation kernel, versioned configuration compiler,
deterministic planner, run-scoped value/secrets boundary, bounded runtime, production HTTP adapter,
and sanitized artifact/reporting boundary. The
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
- [Architecture](architecture.md)
- [Core refactor roadmap](core-refactor-roadmap.md)
- [Accepted product decisions](decisions/README.md)

Documents explicitly labelled as historical or exploratory describe the quarantined prototype or
future product surfaces. They are retained as design input, not as runnable commands or
compatibility promises. The current tree has a `comparo/v1alpha1` compiler, injected run-resolution
ports, fake and production HTTP execution paths, sanitized artifacts, atomic POSIX local
persistence, and pure JSON/JUnit/SARIF/Markdown reporters. It still has no migrated CLI, TUI,
plugin host, or GitHub Action; those product interfaces remain later roadmap work.
