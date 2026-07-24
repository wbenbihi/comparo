# ADR 0011: HTTP exchange and transport observation

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

Comparo must expose the actual HTTP behavior used to compare environments. Redirects, retries,
protocol negotiation, transport-library defaults, and partial reads cannot be flattened into one
synthetic response without hiding drift or corrupting outcome semantics.

## Decisions

### Observation hierarchy

An EnvironmentExecution contains ordered attempts; each attempt contains ordered exchanges. A
retry creates an attempt and a followed redirect creates another exchange within the attempt. Each
exchange retains its effective request, informational responses, terminal response metadata, body,
trailers, timings, and transport failure. `response.*` is a convenience view of the selected
terminal response; advanced selectors may address attempts and exchanges explicitly.

The domain distinguishes the authored Request resource, a cell-shared LogicalRequest, an
Environment-specific EffectiveRequest, and a transport exchange. Protocol-neutral effective
request data is portable; byte-for-byte request capture is optional adapter diagnostics because
HTTP/2 has no HTTP/1 request-byte representation.

### Transport defaults and isolation

Comparo negotiates HTTP/2 through TLS ALPN with HTTP/1.1 fallback. Redirect following and retries
are disabled by default, TLS and hostname verification are enabled, no proxy or ambient cookie jar
is used, and Comparo explicitly controls content decoding. Cleartext h2 and HTTP/3 are deferred.

Every Environment has an isolated connection pool, DNS cache, TLS state, cookie jar,
authentication state, and attempt history. Connections may be pooled or multiplexed only within
one Environment. Standalone Requests do not persist cookies between executions; a Scenario may
explicitly enable its own per-Environment session.

The transport does not implicitly honor ambient proxy, certificate-bundle, `.netrc`, or cookie
configuration. The operating-system trust store is the explicit default. Proxy, custom trust,
client identity, and unsafe TLS behavior require typed Environment configuration and effective
SafetyProfile/ExecutionPolicy permission.

### Redirects and retries

When redirects are enabled, every hop is retained and re-authorized after URL resolution and DNS.
Cross-origin sensitive credentials are stripped by default and HTTPS downgrade is blocked. `303`
changes to GET, `307` and `308` preserve method and body, and the initial strict behavior for `301`
and `302` preserves the method rather than silently rewriting a mutation. A future explicit
browser-compatible mode may differ.

All retry attempts are observable; hidden library retries are disabled. Mutation retries require
explicit safety permission, an applicable idempotency strategy, and a repeatable or snapshotted
body. Conditions, backoff, jitter, and exhaustion are recorded. An HTTP error status remains a
valid observation even if an explicit policy chooses to retry it.

### Response observation and partial failures

A response retains negotiated protocol, status, optional reason phrase, informational responses,
ordered duplicate-preserving headers and trailers, media metadata, content-encoding chain,
BodyHandle, encoded and decoded byte counts, capture completeness, endpoint and TLS metadata,
timings, and diagnostics. Headers are a case-insensitive-name ordered multimap and are never
implicitly comma-joined. HTTP/1 casing is optional diagnostics; HTTP/2 pseudo-headers are not
ordinary response headers.

Failures are typed by DNS, connect, TLS, request write, response headers, body read, idle timeout,
protocol, content decoding, storage, cancellation, or policy phase. They never become synthetic
statuses. Headers and partial bodies received before failure remain available with exact
completeness, so consumers that do not need the failed component may still evaluate.

### Content encoding and timeouts

When the Request does not specify Accept-Encoding, Comparo explicitly advertises `gzip`. Brotli is
not an implicit capability. Comparo owns streaming decompression, tracks encoded and decoded
budgets independently, and permits policy to cap expansion ratio. Body views consume the decoded
entity; encoding metadata remains observable. Invalid or unsupported encoding is explicit and
never silently falls back.

Timeouts distinguish connect, TLS handshake, response headers, idle, attempt total, and execution
total. Attempt total covers redirects, execution total covers retries and backoff, and run
cancellation is outermost. Streams combine idle timeout with an explicit stop condition. Values
are configurable through ordinary layers and externally capped; infinite behavior must be explicit
and may be denied in restricted execution.

### Typed request construction

Comparo constructs a validated EffectiveRequest rather than delegating ambiguous maps to an HTTP
library. An Environment base URL is absolute HTTP(S), contains no userinfo, query, or fragment, and
may include an API base path. A Request path appends to that base path by Comparo-defined semantics;
structured origin-relative and percent-encoded segment forms are explicit. Full absolute URLs are
explicit, fully re-authorized, and do not inherit cross-origin credentials.

Query and header collections are ordered multimaps with ergonomic map and exact entry-list forms.
Query names and values use RFC 3986 UTF-8 encoding and preserve duplicate order; bare parameters,
empty values, null, and Missing are distinct. Headers preserve duplicates and validate names and
values after resolution. Names are static and values are strings without implicit scalar
conversion. Comparo owns Host/authority, Content-Length, and Transfer-Encoding. Generated headers
are included in the effective observation with provenance.

Cookies use a typed collection and cannot be combined with a raw Cookie header. Scenario cookie
jars are per Environment, and explicit Request cookies win by name. Cookie and authentication
material retain sensitivity.

Request bodies are a discriminated union of JSON, text, bytes, form, and multipart. JSON uses
deterministic Comparo serialization with an optional named canonical standard. Text is initially
UTF-8, bytes are streamed, forms preserve ordered repeated strings, and multipart preserves
ordered typed parts with a cell-shared boundary. The explicit body type may supply a default
Content-Type when absent; an inconsistent authored media type is observable and warned rather
than rewritten.

Large and file-backed byte values are ByteSources, not in-memory byte arrays. External body sources
are snapshotted into secure transient storage before multi-Environment fanout or retry, producing
a repeatable stream, stable digest, completeness, sensitivity, and provenance. One-shot sources
must be materialized before partial execution can occur. Framing is derived only by Comparo.

Authentication is a nested discriminated object, initially bearer, basic, API key, or explicit
none. Environment authentication is inherited only for the same origin; Request configuration may
replace or clear it. Auth-generated headers, query values, and cookies cannot collide with
ordinary request entries. OAuth acquisition, request signing, and community mechanisms use a
versioned, observable provider/signing seam rather than hidden value-resolution HTTP calls.

The request builder rejects header injection, user-controlled framing, conflicting Host or
authentication values, invalid HTTP/2 connection headers, malformed URL escapes, ambiguous cookie
configuration, and a non-repeatable body where replay is required. The transport accepts only a
fully resolved, validated, and authorized EffectiveRequest.

## Consequences

- Transport adapters cannot erase redirects, retries, partial observations, or implicit behavior.
- The comparison engine consumes a stable observation model rather than an HTTP client library.
- Environment-local state cannot cross-contaminate staging and production.
- Assertions can evaluate available status or header facts even when a body later fails.
- Safety is evaluated on every final effective outbound exchange, including redirects and retries.
