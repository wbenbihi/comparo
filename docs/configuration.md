# Configuration reference

Current lineage: **`comparo/v1alpha1`**, reconciled **2026-10-31**. The unsupported prototype's
`comparo/v1` syntax is retained in the [historical reference](history/configuration-prototype-v1.md).
It is not accepted by the rebuilt compiler. For exact field shapes, generate the current schema:

```console
uv run --frozen comparo schema config
uv run --frozen comparo schema policy
```

The [compiler guide](configuration-compiler.md) defines discovery, linking, composition, expressions
and validation. The generated schema plus compiler checks are authoritative; this guide provides
the current authoring entry points rather than duplicating every field in a hand-maintained table.

## Project layout and resources

A project starts with `comparo.yaml` whose first resource is its single `Project`. Discovery loads
that entry, YAML below `spec.data` (default `.comparo`), `spec.sources`, and repeatable CLI `--source`
paths in deterministic order. Data can be a path or `{root, include, exclude}` mapping. Multiple
YAML documents use `---`. Symlink traversal is rejected; an outside source requires caller authority.

Each resource has `apiVersion`, `kind`, `metadata` and `spec`. Identity includes namespace, kind and
ID. `$use` is context-typed: short IDs work in single-kind reference fields; union fields such as
profile subjects require `Request/health`. The old prototype `$ref` resource syntax is not a migration
alias. YAML anchors, aliases, duplicate keys, custom tags, merge keys and non-string keys are rejected.

| Resource | Current purpose |
| --- | --- |
| Project | Source discovery, defaults and project input namespaces |
| Environment | Base URL, local inputs, transport options, health checks and profile attachments |
| Request | HTTP template, body, Matrix bindings and validation/policy attachments |
| Matrix | Reusable axes or explicit cases |
| ExecutionProfile | Request selection, assertion/comparison topology, trials, inputs and attachments |
| Value | Reusable value expression |
| Schema | JSON Schema payload |
| AssertionProfile / DiffProfile | Per-environment assertions / directed semantic comparisons |
| GateProfile | Verdict policy over normalized facts |
| SafetyProfile / RedactionProfile / ArtifactProfile | Project restrictions, sanitization and capture/persistence requests |
| Scenario | Inspectable workflow declaration; stateful execution remains unsupported |

`ExecutionPolicy` is an operator-owned `comparo.policy/v1alpha1` document, loaded with `--policy`
outside project discovery. A project cannot grant itself execution authority. Omitting `--policy`
uses permissive trusted-local authority; see [trust semantics](cli.md#trust-and-executionpolicy).

## Start from a validated project

The [quickstart](../examples/quickstart/README.md) contains a complete Project, two Environments,
one Request, assertion/diff/gate profiles and two ExecutionProfiles. Validate and plan without HTTP:

```console
uv run --frozen comparo validate --config examples/quickstart/comparo.yaml
uv run --frozen comparo plan --config examples/quickstart/comparo.yaml --format json
uv run --frozen comparo plan --config examples/quickstart/comparo.yaml --profile check-baseline
```

A Request uses the current nested `spec.request` shape:

```yaml
apiVersion: comparo/v1alpha1
kind: Request
metadata:
  id: health
  name: Health check
spec:
  request:
    method: GET
    endpoint: /health
    headers:
      Accept: application/json
```

That endpoint is relative to the selected Environment's `baseUrl`; an explicit absolute `url` is
also supported. Bodies have typed JSON, text, form, multipart and bytes modes. Use the
[showcase](../examples/tui-showcase/README.md) for executable syntax examples of those modes,
ordered fields, authentication, timeouts, redirects, retries, values and response contracts.

## Topology, values and profiles

An assertion run selects `environments`. A directed comparison selects one `baseline` and one or
more `candidates`; these topologies are mutually exclusive. Attached Matrices expand logical cells;
trials repeat occurrences without increasing the unique-cell count. Environment executions and
comparisons are distinct counts. The [planner guide](run-planner.md) explains precedence and admission.

Values resolve at explicit run/cell/trial/environment/execution/attempt scopes. Environment-local
input isolation and the supported `$env`, `$file`, `$from`, `$optional`, `$param`, `$var`, `$secret`
and `$matrix` forms are documented in [scoped values](run-scoped-values.md) and the compiler guide.
Reserved directives are not executable simply because the domain models future capabilities.

Assertions and diffs use the [shared selector language](selector-language.md) and
[evaluation kernel](core-evaluation-kernel.md). Safety and operator authority constrain effects;
mutation approval remains a separate explicit run decision. Validation/plan success does not
imply that every compiled declaration is executable; use the [executable subset](cli.md#current-executable-subset).

## Authoring through the TUI

Explore inspects resources and transfers selected Request/profile identities to Launch with `c`.
Launch can save a manually configured target as a **new** ExecutionProfile after showing exact YAML
and its destination. It validates compilation and discovery and never overwrites an existing file.
Invocation-only case filters, clearing inherited gates and unsupported invocation overlays are
rejected. It does not save mutation approval or resolved credentials. See [profile saving](codebase/tui-launch.md#reusing-a-manual-target).

There is no general-purpose TUI editor for arbitrary resources. Settings is a separate user-owned
preference file and does not modify Project or ExecutionPolicy YAML.
