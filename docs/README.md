# Comparo documentation

The `refactor` branch currently contains Steps 1 through 3 of the core rebuild: the immutable
domain, shared selector engine, and pure assertion, diff, and gate evaluation kernel. The
authoritative current documents are:

- [Core domain foundation](core-domain-foundation.md)
- [Selector language](selector-language.md)
- [Pure evaluation kernel](core-evaluation-kernel.md)
- [Architecture](architecture.md)
- [Core refactor roadmap](core-refactor-roadmap.md)
- [Accepted product decisions](decisions/README.md)

Documents explicitly labelled as historical or exploratory describe the quarantined prototype or
future product surfaces. They are retained as design input, not as runnable commands, implemented
configuration schemas, or compatibility promises. The completed core has no project-configuration
frontend, transport runtime, persistence, CLI, TUI, reporter, plugin host, or GitHub Action. Steps
2 and 3 add only pure selection and evaluation boundaries; configuration compilation,
orchestration, storage, reporting, and product interfaces remain later roadmap work.
