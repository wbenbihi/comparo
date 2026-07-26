# TUI showcase

This is the deliberately dense project for assessing the current terminal interface. Explorer can
browse all current resource kinds except operator-owned `ExecutionPolicy`. The executable catalog
uses local Schema validation and attached SafetyProfile, RedactionProfile, and ArtifactProfile
resources. Only the stateful Scenario example is tagged `future` and remains unbound.

Start the server and TUI from the repository root:

```console
uv run --frozen python examples/support/demo_server.py
uv run --frozen comparo tui --config examples/tui-showcase/comparo.yaml
```

## Suggested journeys

### Explorer

Inspect the resource kinds, metadata, tags, source grouping, dependency graph, and masked/effective
representations. In particular, compare the two Matrix definitions, reusable Values, attached
profiles, active response Schema, and unbound future Scenario. Trusted exact source inspection is
explicitly separate from the default masked source view.

### Compare

Use Execution profile mode:

- `matrix-release` creates 8 logical cells, 16 Environment executions, and 8 comparisons;
- `repeated-matrix-release` doubles those counts with two trials;
- `candidate-smoke` runs assertions without a baseline/candidate comparison.

The `catalog` Request attaches `audiences` and `locales` simultaneously. The planner forms their
Cartesian product, while `$matrix` resolves `region`, `plan`, and `locale` inside a reusable query
Value. Every response is first validated against `Schema/catalog-response`. Only the `eu` + `pro`
audience differs between baseline and candidate, across its two locale cells, so the result
contains both clean and drifted comparisons.

### Report

After running both a passing and failing profile, refresh Report and navigate between the saved
artifacts. This is useful for judging list density, verdict semantics, cross-references, and the
shared result components without executing HTTP again.

The equivalent headless command is expected to exit with code `1` because two cells drift:

```console
uv run --frozen comparo exec matrix-release \
  --config examples/tui-showcase/comparo.yaml
```
