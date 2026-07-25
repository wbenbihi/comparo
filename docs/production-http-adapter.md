# Production HTTP adapter

Step 8 implements Comparo's real HTTP exchange boundary without moving policy, orchestration, or
evaluation rules into an HTTP-client wrapper. The adapter consumes only a finalized, route-pinned,
authorized request and returns the same `HttpTransportResult` contract used by the programmable
offline transport.

The composition remains deliberately split:

```text
EffectiveRequest
  -> HttpRequestFinalizer
       -> RequestBodyMaterializer
       -> TransientBodyStore
  -> HttpExchangePreparer port (resolution and ExecutionPolicy authority)
  -> HttpExecutionDriver
       -> StandardRedirectResolver
       -> ProductionRetryWaiter
       -> HttpcoreTransport
            -> RoutePinnedNetworkBackend
            -> ExplicitTlsContextProvider
            -> ResponseBodyCapturer
            -> TransientBodyStore
  -> StoredStreamSnapshotProvider
```

No component in this chain reads process environment variables, ambient proxy settings, browser
cookie state, or implicit certificate paths. Resolution and send authorization remain an injected
port because DNS and network authority are operator decisions, not transport conveniences.

## Request finalization and wire fidelity

`HttpRequestFinalizer` applies typed authentication and default media metadata before producing a
`FinalizedWireRequest`. Bearer, Basic, header/query/cookie API keys, explicit cookies, and every
supported body representation preserve sensitivity and provenance. JSON, text, bytes,
URL-encoded, multipart, and file-backed bodies materialize through one bounded body-store port.
Multipart boundaries and proofs belong to the finalized request, so retries and compared
environments do not silently serialize a different request.

`RequestWireRenderer` is intentionally mechanical. It preserves duplicate query parameters and
headers, renders the exact authorized authority and target, computes framing from the materialized
body, and refuses reserved fields that would let authored input contradict the finalized request.
The transport disables library-managed redirects, retries, decompression, cookies, DNS, and
environment proxy discovery.

## Route and environment isolation

Every exchange uses a `RoutePinnedNetworkBackend`. The logical host is retained for the HTTP Host
field, SNI, and certificate verification, while the socket may connect only to the IP and port in
the authorized route. A transport-side attempt to change either endpoint fails before a socket is
opened.

Connection pools are keyed by Environment definition, direct/proxy destination, TLS policy, and
proxy policy. A connection, client identity, proxy credential, or TLS context therefore cannot be
reused across Environment identities merely because two URLs happen to share an origin.

Comparo sends only explicitly finalized request cookies. It does not maintain an ambient cookie
jar in ordinary request execution. Stateful Scenario cookie jars remain a future Scenario-owned
capability and must be isolated by Environment execution when introduced.

## Redirects, retries, and deadlines

Redirects are interpreted by the runtime after the complete redirect response has been observed.
Each hop is rewritten by `StandardRedirectResolver`, then freshly resolved and authorized before
the next exchange. Relative and absolute `Location` fields, 301/302 method mode, 303 rewriting,
307/308 preservation, hop limits, origin changes, HTTPS downgrade, client identity, and sensitive
field stripping are explicit policy outcomes. The transport never follows a redirect itself.

Retries are attempt transitions, not hidden client behavior. Safe methods may retry only for an
authored condition. Mutating methods additionally require the typed idempotency strategy already
validated by the request domain. `ProductionRetryWaiter` records deterministic backoff plus
injected jitter and races every delay against run cancellation without leaving sleeper tasks
behind.

Connect, TLS handshake, response-header, and idle timeouts remain distinct. Attempt-total time
spans redirect exchanges with the same `AttemptId`; execution-total time spans retries with the
same `EnvironmentExecutionId`. When a total deadline interrupts body capture, Comparo retains the
response headers and every safely committed entity byte, marks the capture cancelled, and reports
whether the attempt or execution envelope expired.

## Bounded storage and response truth

Request and response bytes use `TransientBodyStore`. Caller-owned limits control per-body bytes,
run-wide transient bytes, in-memory spill threshold, I/O chunk size, encoded response bytes,
decoded entity bytes, and decompression ratio. These values have no hidden application maximum;
the effective SafetyProfile and runtime budgets choose them.

The store keeps opaque locators and sensitive digests sealed, spills to private temporary files,
validates every descriptor on read, tracks active readers, wipes in-memory buffers on close, and
unlinks spool files. `HttpcoreTransport` owns the shared store lifetime and closes it together with
all connection pools, including cancelled and partially failed cleanup paths.

HTTP statuses are always observations rather than transport failures. Post-header failures retain
the response plus an exact capture state: complete, remote truncation, encoded/entity/expansion
budget exhaustion, idle/read failure, content-decoding failure, storage failure, or cancellation.

## Record streams

`StoredStreamSnapshotProvider` recognizes stream framing only from parsed media metadata:

- `text/event-stream` uses SSE;
- `application/x-ndjson` and `application/ndjson` use NDJSON;
- `application/json-seq` uses JSON text sequences.

It incrementally reads the captured body from storage, independent of HTTP chunk and HTTP/2 frame
boundaries. Caller-owned limits bound record, control, error, and individual framing-item storage.
Every item carries a byte range and logical index. Receipt timing is explicitly unavailable when
the post-capture store cannot truthfully reconstruct it; the adapter never invents a timestamp.

SSE implements BOM handling, LF/CRLF/CR lines, multi-line data joining, default event type,
last-event-ID state, retry hints, comments, unknown fields, and trailing incomplete events.
`SseDataJsonPolicy` is the explicit opt-in for strict nested JSON decoding of all or selected event
types. A nested JSON failure belongs to that event and does not invalidate the outer SSE stream.

NDJSON retains complete lines before malformed records, diagnoses invalid JSON as record errors,
ignores blank lines semantically, and accepts a final unterminated record only after clean EOF.
JSON text sequences require record separators and recover at the next separator. Duplicate JSON
members, non-finite numbers, invalid UTF-8, syntax errors, deeply recursive input, count limits,
framing limits, cancellation, transport failure, and remote completion remain distinguishable in
the resulting closed `StreamObservation`.

Initial production execution intentionally does not reconnect SSE streams. The domain can retain
multiple connection segments, but reconnection policy, `Last-Event-ID` replay, and budgets spanning
reconnects remain deferred until the runtime owns that behavior end to end.

## Verification boundary

The production adapter and programmable fake implement the same narrow `HttpTransport` port and
return the same validated response/failure/timing union. Runtime retry, redirect, ordering,
cancellation, and cleanup tests therefore remain transport-independent. Adapter tests additionally
pin exact wire bytes, route confinement, TLS identity isolation, pool reuse, decompression,
partial-body retention, aggregate deadlines, and stream framing against local protocol servers and
bounded store readers.

The Step 8 gate is the repository gate: Ruff, formatting, strict mypy, import-linter architecture
contracts, and 100 percent statement and branch coverage.
