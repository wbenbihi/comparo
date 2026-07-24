# Versioned configuration compiler

Comparo treats project configuration as an external language. The first new-lineage frontend is
`comparo/v1alpha1`; the unsupported prototype that also used `comparo/v1` is intentionally not a
compatibility target.

```text
UTF-8 YAML sources
  -> safe source-located YAML subset
  -> strict comparo/v1alpha1 resources
  -> typed registry and reference linking
  -> semantic and operation validation
  -> directive-free CompiledProject
```

The compiler performs no network requests, environment lookup, file-value lookup, secret
resolution, or execution. Filesystem discovery and YAML parsing are adapters under
`comparo.configuration`; version parsing, linking, validation, migration classification, and the
canonical result are pure code under `comparo.core.configuration`.

## Resource envelope and identity

Every document uses the same closed envelope:

```yaml
apiVersion: comparo/v1alpha1
kind: Environment
metadata:
  id: staging
  name: Staging
  description: Isolated pre-production API
  tags: [remote, protected]
spec:
  baseUrl: https://staging.example.test
```

`metadata.id` is the stable lowercase slug used by references. `metadata.name` is required human
text. Description and unique slug tags are optional. Identity is the tuple `(namespace, kind,
id)`; IDs may therefore be reused by different kinds without ambiguous lookup.

The project language currently recognizes these authored kinds:

| Kind | Purpose |
| --- | --- |
| `Project` | Manifest, discovery, project values, secrets, and execution defaults |
| `Environment` | Base URL, environment-local inputs, transport options, and safety attachments |
| `Request` | One reusable HTTP request template and its Matrix/profile attachments |
| `Scenario` | Reserved stateful workflow shape; stateful runtime directives remain deferred |
| `Matrix` | Reusable axes or explicit cases, never both |
| `ExecutionProfile` | Subjects, Environment topology, trials, inputs, and profile attachments |
| `Schema` | Opaque JSON Schema payload |
| `AssertionProfile` | Composable per-Environment assertion rules |
| `DiffProfile` | Composable directed comparison pipelines |
| `GateProfile` | Release verdict policy inputs |
| `SafetyProfile` | Project-requested network, filesystem, schema, and budget restrictions |
| `RedactionProfile` | Sanitization rules and backstop behavior |
| `ArtifactProfile` | Capture, storage, and retention requests |
| `Value` | A reusable value-expression definition |

`ExecutionPolicy` is operator-owned authority and is deliberately rejected in project files.

## Project source discovery

The entry is a file or a directory containing `comparo.yaml`. Its first authored resource must be
the one `Project`. YAML documents are then loaded in deterministic order from:

1. the entry file;
2. YAML files below `spec.data`, `.comparo` by default;
3. paths listed by `spec.sources`;
4. additional paths supplied by the caller.

`spec.data` may be a path or a mapping with `root`, `include`, and `exclude`. Multiple YAML
documents per file are supported. Symlinked files and directories are not followed. Sources
outside the project root require an adapter authorizer; project configuration cannot authorize
its own escape.

Source loading has caller-overridable admission inputs. Shipped defaults admit 4,096 source files;
each YAML source admits 8 MiB, 128 documents, depth 256, and 1,000,000 nodes. They protect parsing
before a project-controlled SafetyProfile can be trusted. They are explicit `ProjectLoadLimits`
and `YamlParseLimits` values, not immutable product maximums, so an operator may raise or lower
them deliberately.

The YAML adapter accepts a JSON-compatible YAML 1.2 scalar subset. It rejects aliases, anchors,
custom tags, YAML directives, merge keys, duplicate keys, and non-string mapping keys. It never
constructs application objects.

## References and inline definitions

`$use` is context-typed. In a reference field it links to the resource kind required by that
field. A short ID is accepted only when the field permits exactly one kind:

```yaml
baseline: {$use: production}
subjects: [{$use: Request/get-user}]
```

Union fields require `Kind/id`, eliminating search-order behavior. Missing, wrong-kind,
malformed, cyclic, and ambiguous references are compile errors. The canonical graph retains a
`DefinitionRef` containing the exact resource identity and content fingerprint, never the raw
reference string.

Profile, policy, and Schema fields that allow inline definitions compile them with a container-
owned origin and a secret-redacted structural fingerprint. Inline definitions cannot be
referenced from elsewhere, but their exact revision can participate in planning provenance and
budget authority. At a Schema-typed field, an absolute public HTTP(S) `$use` becomes a policy-
controlled remote schema coordinate. URLs with user-info, queries, or fragments are rejected so
credentials and signed values cannot enter serializable compiler results. No remote content is
fetched during compilation.

Project `defaults` uses the same typed field catalog as `ExecutionProfile`. Environment selection,
Matrices, and profile attachments are therefore linked and validated rather than hidden inside an
untyped defaults mapping.

## Value-expression language

Resolvable fields recursively accept arrays and objects containing these directives:

| Directive | Meaning |
| --- | --- |
| `$use` | Expand a `Value` definition |
| `$var` | Read the variables namespace later |
| `$secret` | Read the sealed secrets namespace later |
| `$param` | Read executable parameters later |
| `$env` | Read the declared Environment dotenv/ambient namespace later |
| `$matrix` | Read the current logical Matrix cell later |
| `$file` | Request a policy-confined file read later |
| `$from` | Try a non-empty ordered fallback chain later |
| `$optional` | Convert unavailable input to an explicit optional result later |
| `$literal` | Treat the complete payload as inert data |

Lookups accept a scalar name or an object with `name` and explicit `as`. Comparo never infers a
conversion:

```yaml
port: {$env: PORT}
workers: {$env: {name: WORKERS, as: integer}}
authorization: "Bearer ${secrets.API_TOKEN}"
```

Interpolation uses a mandatory namespace: `variables`, `secrets`, `parameters`, `env`, `matrix`,
or `steps`. A `:type` suffix requests explicit conversion. `$${...}` escapes interpolation.

Known future directives such as `$generate`, `$step`, `$transform`, and `$provider`
produce an unsupported-directive diagnostic. Unknown `$` keys produce an unknown-directive
diagnostic, including when a typo appears beside ordinary object members. `$literal` is the only
boundary where dollar-prefixed payload keys are inert.

Literals beneath a `secrets` declaration compile into `SensitiveLiteralExpr`. Their payload is
sealed against string conversion, representation, copying, pickling, msgspec encoding, and
built-in conversion. Definition fingerprints redact secret declaration payloads while still
fingerprinting their structure.

## Assertion and diff operation names

Rule structure is resolvable, but operation identity is static. An operation may be a shorthand
string or an object whose required `name` selects the operation and whose other fields hold typed
configuration or resolvable operands:

```yaml
rules:
  - operator:
      name: equals
      expected: ${parameters.expected}
```

The compiler accepts only the closed aliases generated into the public schema. Explicit built-in
identities use `comparo.core/<alias>@1`. Dynamic names, malformed operation objects, and unknown
assertion operators, diff transforms, reducers, pairings, or comparators fail before planning.
The accepted semantic contracts are defined by ADR 0012 and ADR 0013; future third-party
operations require the deferred policy-controlled capability model and are not silently accepted.

## Diagnostics and canonical output

Parsing and compilation aggregate deterministic diagnostics containing stable codes and source
spans. Diagnostics do not contain authored scalar values, paths, exception prose, or secret
payloads. Any diagnostic makes compilation fail closed: a `CompilationResult` never contains both
a project and errors.

A successful `CompiledProject` contains one Project plus canonically ordered resources. It has no
YAML backend nodes, directive mappings, unlinked resource strings, arbitrary operation names, or
filesystem paths. Runtime value sources remain typed expression nodes because Step 6 resolves
them at the correct run scope.

Static execution fields required before resolution include subject and Environment topology,
Matrix attachments, positive trial and maximum-attempt counts, and typed planning-budget maps.
The deterministic planner consumes the canonical graph, applies a typed invocation and external
policy authority, and emits an admitted compact plan; it never asks the frontend or YAML adapter
to merge configuration. See [Deterministic run planner](run-planner.md).

The public JSON Schema is generated from the same resource field and operation catalogs and ships
at `src/comparo/schemas/comparo-v1alpha1.schema.json`. Compiler semantic validation remains
authoritative for cross-resource and cross-field invariants.

```python
from pathlib import Path

from comparo.configuration import compile_project_path

loaded = compile_project_path(Path("comparo.yaml"))
if loaded.compilation.project is None:
    for diagnostic in loaded.compilation.diagnostics:
        print(diagnostic.code.value)
```

## Compatibility and migration

`comparo/v1alpha1` may change before the first stable `comparo/v1` release. Once a configuration
version is published as supported, valid documents keep their meaning for that major line.
Future frontends own their parsing, aliases, defaults, and semantic conversion, then normalize to
the version-neutral compiler graph. Migration classification is identity for `comparo/v1alpha1`,
explicitly refuses the prototype `comparo/v1`, and fails closed for unknown source versions.
