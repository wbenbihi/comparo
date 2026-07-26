# Core and backend codebase guide

This directory is the golden technical reference for Comparo's **current core and backend**. It
describes the working tree as it exists, not only the intended architecture recorded by the
roadmap and ADRs. It deliberately excludes `src/comparo/tui`: a replacement interface should
consume the application contracts documented here rather than copy behavior from the current TUI.

Last code audit: **2026-07-26**, on the `refactor` branch.

## How to read this guide

Comparo exposes capabilities at four different levels. Treating them as interchangeable is the
main source of false assumptions about what a frontend can do.

| Level | Meaning | Example |
| --- | --- | --- |
| Modeled | Immutable domain types can represent the concept | Typed JSON, multipart, retry, SSE, and TLS types exist |
| Compiled | `comparo/v1alpha1` accepts and links the authored syntax | A remote `responseSchema` can compile |
| Executable | The production composition lowers the compiled graph and performs the behavior | Body-free Requests execute today |
| Inspectable | A frontend-neutral application service exposes a safe projection | The plan, archive, and result manifest are inspectable |

A feature is usable by a new TUI only when every required stage is executable **and** an
appropriate application projection exists. Domain support by itself is not a product feature.

## Documents

- [Architecture and boundaries](architecture.md) explains dependency direction, state ownership,
  security boundaries, and the composition roots.
- [Data and execution pipelines](pipelines.md) traces configuration from YAML through compilation,
  planning, resolution, HTTP execution, evaluation, artifacts, and reports.
- [Frontend capability audit](frontend-capability-audit.md) is the handoff contract for a new TUI.
  It marks each expected Explorer, Compare, live-run, and Report capability as supported,
  partial, or missing.
- [Module catalog](module-catalog.md) maps every non-TUI package and source module to its purpose.
- [Contributor workflow](contributor-workflow.md) shows where to start, how to trace a feature,
  which invariants must remain true, and how the repository is verified.

The [accepted ADRs](../decisions/README.md) remain authoritative for product intent. The
[refactor roadmap](../core-refactor-roadmap.md) remains authoritative for sequencing. When either
document claims a broad engine capability, this guide records whether that capability is wired
through the current configuration and production application path.

## Product in one paragraph

Comparo validates HTTP behavior across environments. A project declares reusable Requests,
Environments, matrices, values, assertion profiles, diff profiles, gates, and execution defaults.
A frontend selects an assertions-only or baseline-to-candidates topology. Comparo compiles and
links the project, constructs a deterministic bounded plan, resolves scoped values, executes each
environment occurrence, evaluates assertions and directed binary diffs, reduces the facts through
a gate, sanitizes the run, and optionally persists a versioned report artifact. The canonical
promise is cross-environment validation—not replaying captured traffic.

## Current production-shaped vertical slice

The production composition executes `Request` resources across one or more Environments. It
supports run-scoped method/URL/endpoint/header/query/cookie/auth/body resolution, Environment HTTP
overlays, request policy fields, ordinary and SSE response views, local and authorized remote JSON
Schemas, the typed assertion catalog, semantic diff pipelines, configurable gates, and attached
Safety/Redaction/Artifact profiles. Matrices, case filters, trials, baseline-to-many comparisons,
bounded concurrency, cancellation, artifacts, reporters, and typed invocation overlays all use the
same planner/runtime path.

The intentionally unsupported product areas are stateful `Scenario` execution, WebSocket/gRPC,
browser automation, and community plugin hosting/external judges. They remain modeled extension
seams where useful, but fail before external effects if selected.

The [capability audit](frontend-capability-audit.md) gives the complete frontend consequences.

## Authoritative application entry points

New interactive code should begin with `comparo.application`, not internal core objects or the
current TUI backend.

| Need | Application boundary |
| --- | --- |
| Load Explorer structure | `inspect_project_workspace` |
| Read masked or explicitly exact YAML | `inspect_project_source` |
| Read bounded effective structure | `inspect_effective_resource` |
| Compile, authorize, and plan | `prepare_project_plan` |
| Build invocation/resource/request/profile overrides | `PlanningOptions` / `plan_project` |
| Prepare and start Compare | `CompareService` |
| Inspect a plan before execution | `PlanReviewInspection` and `inspect_plan` |
| Resolve a safe outbound preview | `render_prepared_request_window` / `render_prepared_requests` |
| Observe/cancel an interactive run | `ExecutionSession` |
| Execute without the session facade | `execute_production` |
| Project final results | `project_results` |
| Build Issues/Requests/Rules/Fields/Timeline lenses | `project_issues` / `project_requests` / `project_rules` / `project_fields` / `project_timeline` |
| List persisted runs | `list_report_archive` |
| Authenticate and inspect an artifact | `verify_artifact` / `inspect_artifact` |
| Read a sanitized body by result coordinates | `read_artifact_body_window` |
| Export a secret-safe cURL | `export_artifact_curl` |
| Query paginated report history | `query_report_archive` |
| Plan/apply/restore report retention | `plan_report_retention` / `apply_report_retention` / `restore_report` |
| Present safe human guidance | `present_diagnostic` |
| Export a report | `render_artifact_report` |

These boundaries return immutable renderer-independent DTOs, sanitized values, or closed error
codes. A frontend must not render `CompiledProject`, `PreparedRun`, live observations, provider
exceptions, or `SensitiveValue` instances directly.

## Versions that must not be conflated

| Version | Current value | Owns |
| --- | --- | --- |
| Project language | `comparo/v1alpha1` | Authored resources and directives |
| Planning defaults | `comparo.execution` version `2` | Default admission requests |
| Runtime defaults | `comparo.runtime-defaults` version `1` | Capture-budget provenance |
| Artifact schema | `comparo.artifact/v1alpha1` | Sanitized persisted run contract |

Configuration compatibility and artifact compatibility are independent. A frontend should route
on typed DTOs or explicit version fields and never infer one version from another.
