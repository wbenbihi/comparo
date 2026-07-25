# Comparo CLI

The `comparo` executable is the local command adapter over the same preparation, planning,
execution, artifact, and reporting services used by future frontends. Command handlers translate
arguments and render structured outcomes; they do not own domain rules.

Comparo is alpha software. The CLI is usable for the [current executable
subset](#current-executable-subset), but it is not yet a promise of production stability.

The current safe local-file and artifact adapters require POSIX descriptor APIs. Offline schema,
validation, planning, and diagnostics are platform-neutral, but `render`, `run`, `diff`, `exec`,
and local artifact persistence are currently supported only on Linux and macOS; they fail closed
rather than falling back to pathname-based file access on Windows.

## Command map

| Command | Effects | Purpose |
| --- | --- | --- |
| `comparo schema …` | none | Print versioned configuration, policy, artifact, report, or generated CLI schemas. |
| `comparo validate` | local reads | Discover and compile a project. |
| `comparo plan` | local reads | Compile and inspect a deterministic admitted plan. |
| `comparo doctor` | none | Check the sensitive-value containment boundary. |
| `comparo render` | local value reads | Resolve and sanitize request previews without DNS or HTTP. |
| `comparo run` | HTTP | Execute Request assertions in one or more Environments. |
| `comparo diff` | HTTP | Execute a baseline and candidates, then evaluate directed diffs. |
| `comparo exec PROFILE` | HTTP | Execute an `ExecutionProfile`, with optional CLI overrides. |
| `comparo artifact inspect` | local reads | Authenticate a saved bundle and print a body-free summary. |
| `comparo artifact verify` | local reads | Authenticate a bundle and every declared sanitized blob. |
| `comparo artifact blob` | local reads | Write one authenticated sanitized blob as exact bytes. |
| `comparo report` | local reads | Render an authenticated bundle as JSON, JUnit, SARIF, or Markdown. |
| `comparo completion` | none | Generate Bash, Zsh, or Fish completion text. |

Run `comparo COMMAND --help` for the exact installed options. The command graph can also generate
its own Markdown, HTML, or RST reference:

```console
comparo schema cli markdown
comparo schema cli html
comparo schema cli rst
```

## Trust and ExecutionPolicy

The local CLI follows the accepted trust model:

- omitting `--policy` selects the explicit, permissive trusted-local authority;
- supplying `--policy PATH` loads an operator-owned `comparo.policy/v1alpha1`
  `ExecutionPolicy` outside project discovery and uses restricted authority;
- a Project cannot select, replace, or weaken that external policy;
- unsupported or unauthorized capabilities are rejected before DNS or transport;
- redirects and resolved addresses are reauthorized by the HTTP adapter.

Trusted-local is for configuration the operator trusts on their own machine. The current package
does not yet ship a dedicated GitHub Action or CI wrapper that defaults to restricted mode. When
invoking the local CLI in CI, especially against pull-request-controlled configuration, always
provide a policy from a protected path or base revision:

```console
comparo diff \
  --config checkout/comparo.yaml \
  --policy /operator/policies/comparo-ci.yaml \
  --baseline production \
  --candidate staging \
  --format json
```

Do not place that policy inside the untrusted Project data tree and then treat it as operator
authority. Mutation permission and approval are separate: the policy must permit the method, and
requests that require explicit approval also need `--approve-mutations`.

Restricted policies authorize sensitive-value egress separately from ordinary HTTP access. Every
sensitive source carried by the finalized request must match one destination- and method-bound
rule. For example, this `values` fragment permits the logical `chat.token` secret and its ambient
`CHAT_TOKEN` provider value only to one HTTPS API endpoint authority:

```yaml
values:
  ambientEnvironment:
    names: [CHAT_TOKEN]
    allowAny: false
  allowProjectFiles: false
  remoteSchemas:
    sources: []
    allowAny: false
  secretEgress:
    allowAny: false
    rules:
      - sources:
          - source: secret
            names: [chat.token]
          - source: environment
            names: [CHAT_TOKEN]
        allowedSchemes: [https]
        allowedMethods: [POST]
        allowedHosts: [api.example.com]
        allowedPorts: [443]
```

Here `source: environment` covers the isolated `$env` namespace regardless of whether the winning
value came from an inherited process variable, a declared dotenv file, or Environment inline
values. The separate `values.ambientEnvironment` section controls only which parent-process names
may enter that snapshot. Granting the host for ordinary HTTP is not enough to disclose a secret to
it. Unknown sensitive origins and a named source sent to a different otherwise-allowed host are
denied before DNS. The built-in trusted-local policy is explicitly `allowAny` for this capability.

## Project inputs and precedence

Commands default to `--config comparo.yaml`. The option can identify a manifest or a project
directory. `--source/-S PATH` adds an explicitly selected local YAML source and may be repeated;
automatic discovery remains confined to the Project's declared data folder.

Every project-source admission default is caller-controlled through
`--maximum-config-sources` and the `--maximum-config-yaml-*` options. The explicitly selected
operator policy has independent `--maximum-policy-yaml-*` byte, depth, and node limits; it remains
exactly one policy document. These are finite version-owned defaults, not immutable application
maxima, and invalid limit combinations fail before any source is opened.

Planning options follow the same typed precedence pipeline on every frontend:

```text
Project values
  -> selected Environment values
  -> selected ExecutionProfile values
  -> global invocation value bindings
  -> Environment-specific invocation value bindings
  -> external ExecutionPolicy restrictions
```

The policy is authority, not another value-precedence layer: it may narrow or deny the effective
request but cannot supply Project-owned values. Common invocation overrides are:

- `--subject REQUEST`, repeatable;
- `--environment/--env ENVIRONMENT`, repeatable assertion topology;
- `--baseline ENVIRONMENT` plus repeatable `--candidate ENVIRONMENT` comparison topology;
- `--matrix BINDING=MATRIX`, repeatable and independently bound;
- repeatable `--parameters-from`, `--variables-from`, and `--secrets-from` references to reusable
  `Value` resources, optionally scoped as `ENVIRONMENT=VALUE`;
- `--trials`, `--maximum-attempts`, and `--fail-fast/--no-fail-fast`;
- `--budget RESOURCE=LIMIT`, repeatable.

`comparo exec PROFILE` supplies the selected `ExecutionProfile`; `run` and `diff` select their
topology directly. `plan`, `render`, `run`, `diff`, and `exec` all use the same compiler and
planner rather than reconstructing precedence in a command handler.

Invocation value options never accept an inline value or infer an environment lookup. A bare
operand binds a global overlay; `ENVIRONMENT=VALUE` binds the overlay only to that selected
Environment. The same scope may bind all three namespaces, but duplicate bindings for the same
namespace and scope are rejected instead of using last-option-wins behavior:

```console
comparo diff \
  --baseline staging \
  --candidate production \
  --parameters-from request-inputs \
  --variables-from staging=staging-values \
  --variables-from production=production-values \
  --secrets-from staging=staging-credentials \
  --secrets-from production=production-credentials
```

`--secrets-from` accepts a `Value` resource reference, never a secret literal or environment
variable name. Put an explicit directive such as `$env` inside the referenced Value resource; this
keeps the credential out of argv and preserves explicit conversion rules.

## Offline commands

`validate` loads all selected YAML documents, performs structural and semantic compilation, and
prints all safe diagnostics. It does not resolve `$env`, `$secret`, `$file`, or generated values.

`plan` adds profile/CLI precedence, Matrix expansion, external-policy admission, deterministic
ordering, exact resource accounting, and a secret-free plan fingerprint. It still performs no
value resolution, DNS, or HTTP.

`render` resolves request values and produces an all-or-nothing sanitized preview. It may read
explicit project files, declared Environment dotenv files, and caller-provided ambient values, but
it does not resolve hosts or use the network. Routing fields whose safe representation cannot be
proved are rejected rather than redacted into a misleading executable preview.

`doctor` runs deterministic checks against the process-local sensitive-value containment rules.
It does not inspect the Project.

## Execution commands

`run`, `diff`, and `exec` share one production composition:

```text
compile and plan
  -> capability preflight
  -> Environment snapshots and run-scoped values
  -> policy-authorized finalized HTTP requests
  -> bounded transport and storage-backed bodies
  -> assertions and directed diffs
  -> gate
  -> sanitized RunArtifact
  -> optional atomic persistence
```

The CLI prints a body-free summary. Full captured response bodies remain storage-backed during the
run and can be retained in a sanitized artifact bundle with `--persist`. A response does not need
to fit in memory: the caller controls spill thresholds, aggregate transient storage, per-response
capture, decode, stream, artifact, sanitization, and authorization-ledger budgets through the
execution options. `--maximum-authorization-occurrences` bounds the run-local once-only send ledger;
exhaustion fails closed, and an authorization occurrence cannot be reused to send twice.

These numeric defaults are a named version-owned default set, not immutable application maxima.
Every default shown by `comparo run --help`, `diff --help`, or `exec --help` is explicitly
overridable by the caller and then constrained by the effective Project policy and external
ExecutionPolicy.

## Persistence and reports

Persistence is disabled by default. `--persist` changes the execution artifact policy to persist
every completed safe artifact. `--artifact-root` defaults to `.comparo/artifacts`, but the selected
directory must already exist, be authorized by the effective ExecutionPolicy, and satisfy the
POSIX ownership and permission checks. Comparo does not create the root by pathname.

```console
mkdir -p .comparo/artifacts
chmod 700 .comparo/artifacts
comparo diff --baseline production --candidate staging --persist
```

Published bundles are atomic, sanitized, content-addressed, and authenticated when read. Use:

```console
comparo artifact inspect run-RUN_ID
comparo artifact verify run-RUN_ID
comparo artifact blob run-RUN_ID SHA256 > response.bin
comparo report run-RUN_ID --format junit > comparo.junit.xml
comparo report run-RUN_ID --format sarif > comparo.sarif.json
```

`artifact blob` requires redirected stdout so raw bytes are not accidentally mixed with terminal
presentation. Reporters consume only the versioned safe artifact; they never reopen live run
values or secrets.

## Output and exit codes

Most commands support `--format human|json` and `--color auto|always|never`. JSON output uses the
versioned `comparo.cli/v1alpha1` envelope and deterministic serialization. Human output and
diagnostics remove or escape terminal-control and bidirectional-control characters.

| Code | Meaning |
| ---: | --- |
| `0` | Command succeeded; for execution, the gate passed. |
| `1` | Execution completed and the gate failed. |
| `2` | Usage, configuration, planning, policy, or other pre-execution input was invalid. |
| `3` | Execution started but did not produce a complete gateable result. |
| `4` | An unexpected internal CLI boundary failure was contained. |
| `130` | Interrupted or cancelled. |

CI should distinguish a real behavioral failure (`1`) from invalid setup (`2`) and an incomplete
run (`3`).

## Schemas and completion

The installed package is self-describing:

```console
comparo schema config > comparo.schema.json
comparo schema policy > comparo-policy.schema.json
comparo schema artifact > comparo-artifact.schema.json
comparo schema report > comparo-report.schema.json
comparo completion zsh > _comparo
```

Completion generation writes only to stdout and never modifies shell startup files.

## Current executable subset

The `comparo/v1alpha1` compiler and planner intentionally model capabilities beyond the first
production adapter. `run`, `diff`, and `exec` currently execute only this fail-closed subset:

- `Request` subjects; `Scenario` execution is deferred;
- request `method`, `endpoint` or `url`, `headers`, `query`, and an absent/null body;
- Environment `baseUrl`, declared dotenv files, `variables`, and `secrets`;
- equality assertions (`equals`, `notEquals`);
- exact or ignored binary diffs (`exact`, `ignore`);
- the selectors and gate form accepted by the current evaluation preflight;
- ordinary HTTP responses and the implemented streaming/SSE observation model.

The production preflight currently rejects non-null request bodies, response Schema validation,
Environment overlays outside the supported fields, `SafetyProfile`, `RedactionProfile`, and
`ArtifactProfile` binding, unsupported assertion/diff operators, and scenarios. The compiler may
accept those resource shapes so the language can evolve coherently, but execution never silently
ignores them.

WebSocket, gRPC, browser automation, public plugins, effectful analysis such as an LLM judge, and
the TUI are not part of this CLI step. Browser automation is outside Comparo's product scope;
other listed capabilities remain explicit future work.
