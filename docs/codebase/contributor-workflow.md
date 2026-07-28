# Contributor workflow

## Start from the capability stage

Before changing code, classify the requested feature:

1. **Domain:** do immutable types represent it without effects?
2. **Configuration:** can `comparo/v1alpha1` express and validate it?
3. **Planning:** are precedence, topology, identity, provenance, and budgets deterministic?
4. **Resolution:** are runtime values produced at the correct scope through injected ports?
5. **Execution:** does the production composition bind and authorize it?
6. **Evaluation:** can selectors/assertions/diffs/gates consume it?
7. **Artifact:** is the outcome sanitized, complete/partial/omitted truthfully, and versioned?
8. **Application:** is there a bounded frontend-safe use-case projection?
9. **Presentation:** can CLI/TUI/CI render the application product without business logic?

Do not start in a frontend when an earlier stage is missing. Do not widen an application DTO by
returning live core objects.

## Common traces

### Trace a project field

Follow this order:

```text
core/configuration/v1alpha1/schema.py
  -> v1alpha1/frontend.py and semantic_validation.py
  -> core/configuration/compiler.py/directives.py
  -> core/configuration/canonical.py
  -> core/planning/binding.py and models.py
  -> application/runtime_capabilities.py
  -> application or runtime lowering code
  -> adapters and artifacts
```

If the field exists in schema/canonical types but is rejected by `runtime_capabilities`, it is not
an executable feature.

### Trace a request

```text
application.preflight.prepare_project_plan
  -> application.planning / core.planning.plan_run
  -> application.environment_binding
  -> core.resolution.RunContext
  -> core.runtime.ConfigurationRequestProvider
  -> adapters.http.PolicyHttpExchangePreparer
  -> adapters.http.HttpcoreTransport
  -> adapters.http snapshot providers
  -> application.evaluation_binding
  -> core.runtime.run_prepared
  -> core.artifacts.ArtifactCapturingDriver
  -> core.artifacts.compile_run_artifact
```

### Trace a result screen

```text
RunArtifact
  -> application.results.project_results
  -> application.result_analysis.project_*
  -> stable ResultReference navigation
  -> presentation widgets
```

For request/response bodies, follow the `ArtifactBodyReference` projected on the owning HTTP
message through `application.artifacts.read_artifact_body_window`. This authenticates the exact
artifact results revision and message coordinates. Generic blob reads and transient body stores
must not be used by presentation code.

### Trace an Explorer row

```text
configuration.loader.compile_project_path
  -> application.project_workspace.inspect_project_workspace
  -> application.project_outline.outline_resource
  -> ProjectResourceSummary / ProjectResourceOutline
```

Use `application.source_inspection.inspect_project_source` for a revision-pinned masked or
explicitly trusted exact source window. Use `inspect_effective_resource` for bounded compiled
structure and `application.render_preview.render_prepared_request_window` for sanitized,
run-scoped resolved request intent. Never collapse these three representations into one “resolved”
label.

## Where to add a feature

| Feature type | Primary owner |
| --- | --- |
| New immutable fact/outcome | `core/domain` |
| New selector source/path semantics | `core/domain/selectors` then `core/selection` |
| New pure assertion operator | assertion domain + `core/assertions` |
| New pure diff transform/reducer/pair/comparator | diff domain + `core/diffs` |
| New project syntax | version frontend, semantic validation, schema generation, canonical conversion |
| New precedence/invocation behavior | planning inputs + `core/planning` + application `PlanningOptions` |
| New runtime value source | resolution port/runtime plus an adapter and policy authority |
| New HTTP behavior | HTTP domain contract, adapter, production binding, artifact capture |
| New artifact field | independently versioned artifact DTO/codec/compiler plus reporter compatibility |
| New frontend journey | `comparo.application` service/projection first, presentation second |

Inheritance is acceptable where it improves a closed structural model, but most behavior is
composed from immutable strategies and narrow ports. Avoid “manager” objects that discover or
reach into unrelated packages.

## Invariants to preserve

### Purity and dependency direction

- core configuration, planning, selection, assertions, diffs, gates, and policy are pure;
- resolution/runtime access effects only through declared ports;
- domain records do not import adapters or presentation frameworks;
- assertions, diffs, and gates remain independently replaceable;
- reporters consume only safe artifact types.

### Determinism

- source order, resource order, Matrix order, plan occurrence order, fact IDs, artifact rows, and
  report output must not depend on task scheduling or dictionary accidents;
- plan fingerprints cover all semantic inputs without opening secrets;
- final runtime results return canonical plan order even when execution completes out of order.

### Safety

- external ExecutionPolicy remains outside project authority;
- resolved destinations and redirects require exact post-resolution authorization;
- secret/env values remain sealed and taint propagates through derived values;
- diagnostics and public exception messages remain value-free;
- live observations never become persistence DTOs directly;
- every artifact capture says complete, partial, or omitted and why;
- all enumeration, parsing, execution, decoding, selection, evidence, artifact, archive, and
  frontend projection operations remain bounded.

### Frontend contracts

- return typed immutable DTOs, not formatting fragments or core aggregates;
- use stable identity/reference objects for navigation;
- preserve artifact-owned verdicts instead of recomputing them;
- expose partial/omitted/unsupported states explicitly;
- never solve application gaps by importing TUI code into core or core internals into widgets.

## Tests and architecture evidence

The repository groups tests by source responsibility:

| Test area | What it proves |
| --- | --- |
| `tests/core/domain` | Direct-construction invariants and impossible-state rejection |
| `tests/core/configuration` / `tests/configuration` | Language, linking, migrations, schemas, source discovery, YAML safety |
| `tests/core/planning` | Precedence, Matrix, topology, admission, identities, fingerprints |
| `tests/core/resolution` / `tests/adapters/values` | Scope, caching, conversion, taint, dotenv/files/provider effects |
| `tests/core/runtime` | Scheduler, fake transport, cancellation, deterministic results |
| `tests/adapters/http` | Wire fidelity, network/TLS, body storage, redirect/retry, timing, ordinary/SSE decoding |
| `tests/core/assertions`, `diffs`, `gates`, `selection` | Pure evaluation semantics and budgets |
| `tests/core/artifacts`, `tests/adapters/artifacts`, `tests/reporters` | Sanitization, schema, capture truth, persistence, authentication, report projections |
| `tests/application` | Frontend-safe composition and use-case boundaries |
| `tests/security` | Secret, authority, HTTP topology, stream, and analysis adversarial properties |
| `tests/properties` / `tests/conformance` | Generated invariants and pinned standards behavior |
| `tests/architecture` | Imports, cohesion, immutability, packaging, and distribution membership |

When a feature crosses layers, add tests at each changed contract rather than only an end-to-end
frontend test.

## Local quality gates

The configured authoritative checks are:

```console
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
uv run --frozen python -m pytest --cov --cov-report=term-missing
uv build
uv run --frozen python -m tests.architecture.distribution_members \
  dist/comparo-0.2.0.tar.gz dist/comparo-0.2.0-py3-none-any.whl
```

Ruff enforces broad correctness/security/docstring rules, mypy runs in strict mode, pytest treats
warnings as errors, statement and branch coverage are configured at 100%, and the distribution
check compares built members to an allowlist. Run smaller relevant suites during an incremental
change, then the complete gates before a release.

## Documentation rules

- This `docs/codebase/` directory describes current working behavior.
- ADRs describe accepted constraints and product intent.
- The roadmap describes implementation order and exit criteria.
- Feature documents such as `configuration-compiler.md` explain a subsystem in depth.
- `docs/design/report-format.md` is a historical prototype and is not the current artifact
  contract.

When implementation changes a capability status, update the pipeline, frontend audit, and module
catalog in the same incremental change. If a change contradicts an accepted ADR, supersede the
ADR rather than silently editing its historical decision.

## Handoff checklist for a replacement TUI

Read the [TUI/backend integration contract](tui-backend-integration.md) before implementing a
workspace. It is the exhaustive callable and information-design reference.

Before implementing a screen, identify:

1. the application service that owns the operation;
2. the exact DTO and stable identity used for navigation;
3. whether the data is authored, compiled, plan-effective, resolved/sanitized, live, or persisted;
4. the caller-owned result/body/list window;
5. unavailable, incomplete, partial, redacted, and error states;
6. which actions require policy or mutation authority;
7. whether the operation is cancelable and how slow-consumer backpressure works;
8. the application test that proves the boundary independently of Textual.

If no suitable service exists, add or request an application contract. Do not use the current TUI
as a behavioral source of truth.
