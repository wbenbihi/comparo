# Sanitized artifacts, persistence, and reporters

Step 9 establishes the boundary between Comparo's live execution graph and anything that may be
saved or presented. A `RunResult` is an internal, live object: it may still refer to transient
bodies and sensitive values. A `RunArtifact` is an independently versioned, immutable, bounded,
and sanitized record. Persistent stores and reporters accept only the latter.

```text
PreparedRun + RunResult
  + final run-scoped sanitization snapshot
  + live body reader
  -> ArtifactCapturingDriver
  -> compile_run_artifact
  -> comparo.artifact/v1alpha1
       -> sanitized blob source
       -> atomic local bundle
       -> JSON / JUnit / SARIF / Markdown
```

## Ownership and lifecycle

`ArtifactCapturingDriver` decorates the runtime `ExecutionDriver`. It retains live execution
outcomes only until scheduler cleanup, takes the final sanitizer snapshot, streams bodies through
the sanitizer while their transient store is still open, and then closes the underlying driver.
The compiler consumes only the resulting safe capture batch plus the terminal `PreparedRun` and
`RunResult`. It always releases and wipes the sanitizer boundary, including rejection paths.

This lifecycle is deliberate: moving artifact construction after transport, `RunContext`, or
transient-body cleanup would make the final secret registry and complete bodies unavailable.

## Artifact contract

`comparo.artifact/v1alpha1` is distinct from the configuration API. Its strict codec rejects
unknown fields, malformed variants, unsupported versions, and inputs above caller-owned limits.
The checked-in JSON Schema is generated from the exact artifact types, pins the supported API
version, and deliberately identifies itself as structural validation. Cross-record rules such as
summary conservation, comparison lineage, occurrence uniqueness, definition closure, and blob
closure remain mandatory runtime validation performed by the strict decoder.

The manifest retains:

- tool, run, invocation, configuration, sanitizer, and credential-catalog versions;
- Project, ExecutionPolicy, ArtifactProfile, and RedactionProfile provenance where one effective
  profile applies, together with configuration and plan fingerprints;
- terminal completion and gate outcomes without collapsing absence into success;
- every execution, attempt, redirect exchange, comparison, assertion fact, and diff fact in
  deterministic order;
- exact gate, summary, and resource-budget counts;
- explicit partial and omitted coverage;
- content descriptors for sanitized blobs, with no path or transient storage locator.

Artifact values use a closed tagged JSON vocabulary. Artifact DTOs do not reuse runtime or
configuration structures, accept arbitrary mappings, or contain `SensitiveValue`, body handles,
exceptions, or adapter types. A top-level artifact is valid only when its summaries exactly
conserve retained outcomes, all comparison sides and coverage owners refer to retained executions,
stable resource identities are unique, every embedded definition is declared, occurrence
identifiers are unique in their scope, and its declared blobs exactly match all body references,
including terminal pre-send requests.

## Sanitization

The run-scoped boundary registers every sealed sensitive value and the concrete values observed on
the HTTP graph before it is sealed. It masks exact UTF-8, URL-encoded, form-encoded, JSON-string,
and Base64 representations, including matches split across arbitrary stream chunks. Mandatory
credential fields are masked as complete values: authorization headers, cookies, `Set-Cookie`,
and the built-in credential query-name catalog cannot be weakened by project configuration.

Application-specific redaction rules are fail-closed in this initial artifact implementation. A
custom rule causes its selected source to be omitted conservatively if fine-grained application
cannot be established safely; required request method/URL or response status/protocol rules omit
the complete request or response record. Every such source-wide degradation is recorded as an
artifact coverage issue. Evaluation always occurs on the live observation before persistence
redaction, and diff exclusions never imply that content is safe to persist.

Body source truth and artifact persistence truth remain separate. The artifact records whether a
source was complete, remotely truncated, budget-exhausted, storage-failed, cancelled,
decode-failed, policy-omitted, or explicitly truncated, independently from whether sanitized
content was retained completely, partially, or omitted. Omitted content has no raw digest.

## Bounded blob staging

The in-memory blob target is intended for composition and tests. It enforces caller-supplied blob
count, per-blob byte, and aggregate byte limits while streaming. Identical sanitized bytes are
deduplicated by their sanitized SHA-256 digest. The transient-body adapter exposes only an async
byte stream and never its locator.

`PosixSanitizedBlobSpool` is the production staging target for Linux and macOS. It keeps sanitized
body bytes in a newly created private directory beneath a caller-selected private root or the
platform's sticky temporary directory rather than accumulating the aggregate body budget in RAM.
Writers use restrictive, exclusive, descriptor-relative files; commit verifies the descriptor
returned to the compiler, abort removes incomplete content, and close rejects new work, waits for
admitted work, then best-effort wipes the private spool. The spool is still run-scoped temporary
storage, not a published artifact bundle.

## Local bundle store

`LocalArtifactBundleSink` is a POSIX local-filesystem adapter for Linux and macOS. The caller must
provide an existing absolute, user-owned root that is not group- or world-writable. The adapter
pins that root with a directory descriptor and performs descendant operations descriptor-relative
with no-follow and exclusive-create flags.

A completed bundle has this physical shape:

```text
<root>/<artifact-id>/                 0700
  results.json                       0600
  manifest.json                      0600
  COMPLETE                           0600
  blobs/                             0700
    <sanitized-sha256>               0600
```

The writer creates a randomized sibling staging directory, verifies every streamed blob's size
and digest, synchronizes files and directories, writes `COMPLETE` last, and publishes with a
platform no-replace atomic rename. It never overwrites an existing bundle. A root synchronization
failure after publication is reported as `commit-uncertain`, not as a clean failure. Cancellation
and ordinary failures clean the known staging transaction best-effort; a process interruption can
leave a visibly named staging directory but never a completed final bundle.

The reader reopens all nodes without following symlinks or accepting hard-linked files, enforces
ownership, type, permission, and size constraints, authenticates the completion marker against the
manifest and the results digest, checks the bundle/artifact version binding, decodes the strict
artifact version, reconstructs blob names only from validated digests, and verifies every blob in
fixed-size chunks without retaining whole bodies. `open_local_artifact_bundle` also exposes a
lifetime-bound `SanitizedBlobSource` for offline body consumers. It accepts only descriptors
declared by that artifact and performs a complete digest pass before yielding any bytes.
The first adapter intentionally rejects unsupported platforms rather than falling back to unsafe
pathname checks. Network-filesystem durability and Windows persistence are not claimed.

## Reporters

All built-in reporters are deterministic, pure projections of `RunArtifact` with an exact atomic
publication ceiling:

- JSON uses `comparo.report/v1alpha1` as a report envelope around the complete artifact. Its strict
  decoder, checked-in schema, and golden compatibility fixture are independent from the artifact
  API version.
- JUnit represents gate dimensions, so CI pass/fail semantics remain faithful to the gate rather
  than reinterpreting individual rule facts.
- SARIF 2.1.0 emits adverse assertion/diff facts and incomplete artifact coverage. Rule identities
  include their definition identity, analyzer success is distinct from a failed Comparo gate, and
  automation/finding fingerprints exclude per-run occurrence identifiers.
- Markdown uses fixed sections for overview, gate, coverage, executions, comparisons, findings,
  and budgets, without embedding detached blob bodies.

Presentation helpers remove terminal controls and escape their target syntax. Reporter errors and
storage errors carry only closed, value-free codes; caught parser, filesystem, and rendering error
text is never copied into a reportable outcome.

The report ceiling prevents partial publication; it is not an independent allocator limit because
each reporter constructs one complete output before the final byte-count check. Reporter memory is
instead bounded by the already-admitted artifact manifest, and reporters never load detached blob
content. A future streaming reporter API would require a different atomic-output contract.

Import contracts enforce the boundary in both directions: artifact DTOs cannot import live
runtime, resolution, or adapter modules, and reporters cannot import the runtime, resolution,
configuration, planning, selection, evaluation, transport, or storage adapters.

## Deferred integration

Step 10 will bind effective `ArtifactProfile` and `RedactionProfile` configuration to these typed
policies, apply `persist` conditions, select destinations, and expose artifact/report commands.
Retention policies, interrupted-staging discovery and cleanup commands, archives, encryption at
rest, Windows storage, and historical artifact conversion remain explicit future capabilities;
none is silently approximated by this module.
