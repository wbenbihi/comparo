# ADR 0010: Body capture, sanitization, and artifacts

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo should make complete response bodies available by default without retaining every body in
memory or persisting raw secrets and user data into reports. Runtime evaluation requires raw
observations, while CLI, TUI, CI, and saved artifacts require a strict sanitization boundary.

## Decisions

### Full streaming capture

Comparo captures complete decoded HTTP entity bodies by default through a BodyStore abstraction.
Small bodies may remain in memory and larger bodies spool incrementally to secure temporary or
external storage. Crossing an in-memory threshold never truncates content. Wire/compressed and
decoded byte counts and budgets are tracked separately.

BodyHandle records identity, media metadata, digest, completeness, and an internal storage locator.
Capture states distinguish complete, remote-truncated, budget-exceeded, storage-failed, cancelled,
policy-omitted, and explicitly truncated bodies.

When full capture is requested, exhaustion or storage failure produces an explicit partial
execution and incomplete body evaluation. Truncation or omission occurs only when deliberately
configured and remains visible in results and artifacts.

### Raw and sanitized domains

Raw bodies and sensitive live values are transient. Temporary storage uses restrictive
permissions, randomized run paths, cleanup, and no serialized locators. The runtime and pure
evaluators may access live observations. CLI, TUI, CI reporters, and persistent stores consume only
sanitized models.

Sanitization traverses request and response URLs, headers and bodies, assertion and diff evidence,
errors, Scenario captures, generated values, and artifact metadata. Sensitive values and their
known transformed derivatives remain registered with the RunContext sanitizer until it closes.
Terminal control escaping is an additional presentation invariant.

### Redaction and artifact profiles

RedactionProfile contains shared Selectors with conservative `mask` and `remove` actions. Built-in
protection covers standard credential-bearing HTTP fields; application-specific personal data
requires explicit project rules. Hashing is not treated as safe anonymization by default.

The initial action vocabulary is `mask`, `remove`, and `omitSource`. Mask preserves surrounding
structure and substitutes an explicit redaction marker. Remove deletes the selected persisted
member or record. OmitSource removes the complete capture unit, such as a body, header collection,
or evidence value. An explicit allowlist policy may use `keep`, but it cannot override mandatory
credential protection. Concrete locations are established before sanitization so a report may say
where content was removed without retaining that content.

Credential protection is mandatory for every serializable or presented model. It covers
Authorization and Proxy-Authorization, cookies and Set-Cookie, URL user information, known
authentication query parameters, `$secret` and sensitive-provider values, sensitive Scenario
captures, and the actual derived wire representations Comparo creates from them. It applies to
URLs, headers, bodies, errors, evidence, future external-capability outputs, and metadata. A project may add
application-specific rules but cannot weaken the mandatory set. Any future bypass requires a
separate explicitly unsafe external ExecutionPolicy capability; initial implementations do not
offer raw persistence.

Sensitivity propagates through selection, interpolation, concatenation, transformation, patching,
and serialization. Comparo registers concrete URL-encoded, authentication-wrapped, and other wire
forms that it creates. An untrusted or insufficiently specified future external transformation of
sensitive input taints its complete output. Literal matching across otherwise untainted content provides
defense in depth. Short values may cause conservative over-redaction rather than being skipped, and
hashes remain sensitive unless an explicit policy says their disclosure is safe.

ArtifactProfile controls which sanitized request, response, body, and evidence data is persisted
and the explicit behavior on capture limits. The default captures raw content transiently and
persists complete sanitized content. Raw persistence is not a normal project capability and
requires external ExecutionPolicy authority if introduced.

`persist` supports always, on-gate-failure, and never, with complete sanitized persistence as the
default. Redaction and diff selection are independent: ignored comparison content is not
automatically safe to persist, and redacted content remains available to live evaluation before
the sanitization boundary.

Redaction fails closed. A selector that successfully finds no matching sensitive field is
different from a selector or view that could not be evaluated. On decode, truncation, budget, or
selector failure, ArtifactProfile either omits the affected source and records incomplete artifact
coverage or rejects artifact creation. The secure default omits that source. It never persists the
uninspected original. GateProfile may require complete artifact coverage.

### Storage and artifact format

TransientBodyStore and SanitizedArtifactSink are separate ports. Artifact sinks cannot accept raw
RunResult types. Initial adapters are an in-memory test store, secure local spool, and local bundle
sink.

RunArtifact is independently versioned from configuration. The accepted physical format is a
manifest/results bundle plus content-addressed sanitized body blobs, allowing incremental writes,
deduplication, and offline viewing. It records plan/config/policy fingerprints, resource and rule
identities, decoder/external-capability versions, completeness, outcomes, provenance, and sanitized digests.
Raw omitted content does not persist a raw digest.

The local artifact bundle is the primary physical format: a versioned manifest, structured facts,
sanitized content-addressed blobs, and an explicit completion marker. A portable archive is a
future export, not the storage contract. A writer uses a restrictive randomized sibling staging
directory and atomically finalizes the bundle. Interrupted bundles are visibly incomplete and may
be inspected or cleaned up; they never masquerade as completed runs.

The default local destination is the operating system's per-user application-state directory,
keyed by Project identity, rather than the repository. RunInvocation and CI may choose an explicit
destination. Retention deletion is off by default; age, count, or storage-quota cleanup requires an
explicit policy and must not affect transient cleanup. Reporters and stdout projections consume the
same sanitized RunArtifact whether or not a bundle is persisted.

## Consequences

- Full-body comparison does not imply unbounded RAM use.
- A capture prefix cannot masquerade as a complete response.
- Ordinary TUI and artifact views are complete but sanitized.
- Storage adapters can change without entering transport, evaluation, or reporting logic.
- Comparo protects declared/tainted secrets and selected personal data but cannot infer every
  application-specific privacy field.
- Diff exclusion cannot accidentally become a redaction policy.
- An artifact may be useful but explicitly incomplete when a source could not be safely sanitized.

## Implementation-level follow-ups

1. Exact ArtifactProfile and RedactionProfile YAML schemas and built-in credential-name catalog.
2. Exact RunArtifact manifest envelope and interrupted-bundle recovery commands.
3. Exact ArtifactProfile fields for the budget dimensions accepted in ADR 0014.
4. Whether a future trusted-local unsafe live reveal is offered.
5. Encrypted transient and persistent storage adapters.
