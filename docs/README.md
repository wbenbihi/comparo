# Comparo documentation

The `refactor` branch currently contains Steps 1 through 6 of the core rebuild: the immutable
domain, shared selector engine, pure evaluation kernel, versioned configuration compiler,
deterministic planner, and run-scoped value/secrets boundary. The authoritative current documents
are:

- [Core domain foundation](core-domain-foundation.md)
- [Selector language](selector-language.md)
- [Pure evaluation kernel](core-evaluation-kernel.md)
- [Versioned configuration compiler](configuration-compiler.md)
- [Deterministic run planner](run-planner.md)
- [Run-scoped values and secrets](run-scoped-values.md)
- [Architecture](architecture.md)
- [Core refactor roadmap](core-refactor-roadmap.md)
- [Accepted product decisions](decisions/README.md)

Documents explicitly labelled as historical or exploratory describe the quarantined prototype or
future product surfaces. They are retained as design input, not as runnable commands or
compatibility promises. The current tree has a `comparo/v1alpha1` compiler and injected
run-resolution ports, but no transport runtime, persistence, CLI, TUI, reporter, plugin host, or
GitHub Action. Runtime orchestration, concrete I/O adapters, storage, reporting, and product
interfaces remain later roadmap work.
