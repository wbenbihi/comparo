# ADR 0004: Selectors and response body views

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Assertions, diff rules, redaction, future Scenario captures, `$select`, triage, and plugin
operators all need to address values in an HTTP observation. The prototype currently mixes
assertion targets such as `status`, `header:content-type`, and `body:$.user.id` with a different
set of synthetic diff paths. The redesigned core needs one typed selection model without
discarding HTTP-specific semantics such as duplicate headers or raw response bodies.

## Accepted decisions

### Rule field and forms

The configuration field is named `select`, replacing the overloaded `target`. Common selectors
have concise forms, while advanced selectors have a structured form. Both compile into the same
typed Selector AST.

```yaml
- select: response.status
  op: equals
  value: 200

- select:
    source: response.body
    view: json
    path: $.users[*].id
  op: matches
  value: '^[0-9]+$'
  quantifier: all
```

### JSON selection and result locations

Author-facing JSON selection follows standardized JSONPath rather than a Comparo-specific path
language. JSONPath filters are required by the first stable `comparo/v1`; implementation may be
staged during `v1alpha1` as long as unsupported syntax fails compilation explicitly.

Every concrete JSON match is identified in outcomes and artifacts by an unambiguous JSON Pointer.
Selectors and concrete locations are separate types.

### Cardinality

A selector returns an ordered collection of zero, one, or several matches plus structured
unavailable or decode-error states. Comparo never silently takes the first match. A selector that
may yield several matches requires an explicit quantifier such as `one`, `all`, `any`, or `none`.

Consumers decide how no-match and unavailable states affect their result. Assertion, diff,
redaction, and capture semantics are not hidden inside the selector engine.

### HTTP-specific sources

Selectors retain typed source behavior. Response headers remain an ordered, case-insensitive-name
multimap and are not flattened into a JSON object or comma-joined. Body selection names an explicit
decoded view such as bytes, text, JSON, SSE events, or chunked JSON values. The original body remains
available through the configured BodyStore regardless of which decoded views are requested.

Query, header, trailer, cookie, informational-header, and derived encoding occurrences select
exact raw field values. Their locations retain source, name or index, and occurrence order; empty
text remains distinct from a bare query parameter; and sensitive text remains sealed without
changing its runtime value kind. Raw fields are not required to satisfy HTTP token grammar.
`HTTP_FIELD_ITEMS` is reserved for an explicitly requested parsed token-list projection and is not
the default result type of any generic field source.

## Accepted body diff default

The default for body comparison is a named `auto` view-selection strategy. `auto` is not
content sniffing or field-driven type coercion. It chooses a decoder deterministically from parsed
HTTP media-type metadata using a versioned registry. JSON media types select JSON, textual media
types select text, SSE selects the SSE event view, recognized streaming JSON media types select a
JSON stream view, and unknown or missing media types select entity bytes.

The selected view, media type, decoder identity/version, and any decoding failure are retained in
the result. A declared representation that fails to decode is an explicit decode error and never
falls back silently to a different view. Content sniffing, if supported later, is a separate
explicit opt-in strategy.

If baseline and candidate select different views, Comparo reports a representation/view drift
rather than coercing one side to the other. A DiffProfile may force a particular view when the user
intentionally wants both sides decoded the same way.

Content-type-driven `auto` is the default body-diff strategy. It performs no content sniffing,
never falls back after a declared representation fails to decode, and treats different selected
views across compared responses as representation drift. Schema assertions and explicit selectors
may independently request JSON or another view.

### Nested SSE data views

For `text/event-stream`, `auto` decodes the outer body into an ordered SSE event view. SSE `data`
is text after the protocol-defined concatenation of multiple `data:` lines and has no declared
per-event media type. Comparo therefore does not infer JSON from data contents.

Selectors and DiffProfiles may explicitly request a nested JSON view for event data globally or
for selected event names. A nested decode failure is reported against the specific event and never
falls back silently to text. Concrete locations retain the event index or stable event identity
and the JSON Pointer within its decoded data.

## Accepted response representation contract

### Body layers and presence

The pipeline distinguishes HTTP framing, content-coded bytes, decoded entity bytes, and semantic
views. Transport chunk and HTTP/2 DATA-frame boundaries never define application records. The
default BodyHandle addresses the decoded entity; encoded content may remain transient for decode
diagnostics but is not ordinarily persisted.

Body presence is one of NoBody, EmptyBody, complete, partial, or unavailable. HEAD, informational,
204, 205, and 304 responses have NoBody semantics; an ordinary zero-byte response is EmptyBody.
Empty bytes and text are valid, empty JSON is a decode error, and empty SSE/record streams contain
zero complete records. NoBody and EmptyBody are distinct comparison facts. Unexpected bytes on a
body-forbidden response are a protocol violation. Successful CONNECT tunnels and 101 upgrades
retain headers but do not enter the ordinary body model.

### Media metadata and auto registry

Content-Type is parsed strictly once. Multiple or malformed values produce explicit media errors;
the bytes view remains addressable but `auto` never falls back because metadata was invalid.
Missing Content-Type selects bytes. The versioned initial auto registry selects JSON for
`application/json` and `application/*+json`, SSE for `text/event-stream`, record streams for
`application/x-ndjson`, `application/ndjson`, and `application/json-seq`, text for other `text/*`,
`application/xml`, and `application/*+xml`, and bytes otherwise.

Projects may map exact media types to installed built-in decoders. Plugin decoders have namespaced
identities and require operator permission if a future plugin frontend is introduced. ADR 0015
defines no public plugin model initially. Decoder registry identity/version, chosen view, and
options are artifact provenance. A forced selector view may decode despite missing or mismatched
media metadata, while retaining that mismatch as a separate fact.

### Strict JSON and text

JSON is strict UTF-8; a UTF-8 BOM is accepted and recorded, a declared charset must identify UTF-8,
invalid text, duplicate members, non-finite numbers, and trailing content are errors. Numbers
retain arbitrary-precision integer/decimal semantics, and source order plus concrete JSON Pointer
locations remain available. Default structural comparison treats object order as insignificant
and array order as significant without discarding source order from the view.

Text uses a versioned charset registry, UTF-8 when unspecified, strict invalid-sequence handling,
and explicit compatible BOM behavior. A BOM does not independently infer UTF-16/32. Explicit
replacement or compatibility modes, if added, are decoder options and part of view identity.

### Content decoding, view storage, and budgets

Content-Encoding is decoded before view selection. Identity, gzip, and deflate are initial
decoders; other algorithms require an advertised capability. Multiple codings reverse application
order, and unsupported, invalid, truncated, trailing, or expansion-limited encodings are explicit
errors. Encoded bytes, entity bytes, and expansion ratio have independent budgets.

DecodedViewStore lazily maps a BodyHandle and versioned DecoderSpec to a DecodedViewHandle. It
single-flights identical work, caches typed failures, permits eviction and re-decoding, propagates
sensitivity, and supports memory, disk-backed token/index, and streaming implementations. Large
JSON is not required to materialize as one language-native object; selectors may consume events or
request an index subject to budget.

Capture bytes, text characters, JSON depth/nodes, view-index storage, and in-memory view budgets
are independent, configurable, and externally capped. Exceeding one view budget does not erase a
successfully captured entity or prevent another applicable view.

Bytes and text may expose a prefix marked partial. A single JSON document requires a complete
entity by default. Record streams retain complete records before failure while the enclosing
stream stays incomplete. View resolution distinguishes Ready with completeness and decoder
metadata, NoBody, DecodeError, Incomplete, BudgetExceeded, Unsupported, and Cancelled; selector
cardinality never collapses these states into zero matches.

## Accepted SSE and JSON-stream contract

A stream produces a storage-backed StreamObservation containing records, controls/diagnostics,
connection segments, captured extent, exact termination reason, and completeness. Completeness is
full after clean server EOF, window after an authored observation stop, or incomplete after
unexpected limits, failures, policy interruption, or cancellation. Every stream must have a finite
stop or externally capped bound. Authored event/record count, duration, idle, or restricted
selector/operator conditions complete a window; safety byte/count/duration limits do not
masquerade as successful stops.

SSE follows the UTF-8 EventSource line and field model, including BOM, line endings, data-line
joining, default event type, ID state, retry hints, comments, and unknown-field diagnostics.
Complete dispatched events, comments, controls, raw field provenance, and trailing incomplete
items remain distinct. Default comparison uses dispatched events and ignores heartbeat comments,
retry controls, and formatting unless selected.

SSE data remains text unless the DecoderSpec explicitly selects strict JSON globally or by event
type. Nested failures belong to a concrete event and do not invalidate other outer events.
Locations preserve connection segment, event index/type/ID, byte range, and nested JSON Pointer.

NDJSON and JSON text sequence are distinct framed decoders. They retain per-record index, byte and
line/framing location, connection segment, decoded value or record error, and completeness.
NDJSON accepts CRLF, diagnoses and ignores blank lines, and may accept a final nonempty
unterminated line at clean EOF with an explicit diagnostic. JSON sequence may recover at the next
record separator. Concatenated JSON values require an explicit future decoder; a streamed JSON
array remains one complete JSON document semantically.

Ordered record/event pairing is the default. DiffProfile may configure keyed pairing by SSE ID or
a scalar selector from event/record JSON, optionally partitioned by event type. Missing and
duplicate keys are explicit; keyed mode never falls back to order. Initial execution captures each
Environment's bounded window independently and compares only closed windows, so scheduler timing
does not define identity. Live TUI output remains provisional.

This is the default for comparing raw record/event views, not a requirement that application-level
semantics preserve transport segmentation. As defined by ADR 0013, a DiffProfile may project,
transform, reconstruct, or reduce each stream independently before pairing derived values. Chunk
count, boundaries, and order are therefore compared only when retained by the effective rule.

SSE reconnection is disabled by default and explicit when used. Every connection remains an
observed transport attempt/exchange, Last-Event-ID and server retry hints are policy-controlled,
window budgets span reconnects, duplicate delivery is preserved, and exhaustion is incomplete.
The domain and artifact model support multiple connection segments even if initial implementation
ships only non-reconnecting mode.

Termination reasons distinguish remote EOF; event-count, duration, idle, and condition windows;
safety count, duration, and byte limits; transport/decode failures; reconnect exhaustion; manual
stop; cancellation; and policy blocking. Manual stop is not green unless the invocation explicitly
declared an accepted manual window. Raw bytes, record indexes, and nested decoded views remain
storage-backed and pass through sanitization before persistence.

## Accepted shared Selector standard

JSON selection implements RFC 9535 JSONPath rather than a library-specific dialect, including
child/name/index/slice/wildcard/descendant/filter syntax and the standard length, count, match,
search, and value functions. Legacy eval, scripts, host callbacks, interpolated paths, and
unregistered functions are rejected. JSON Pointer is an additional statically singular fast path.
Paths and pointer expressions are structural compiler fields.

The typed source catalog covers execution/attempt/exchange state and timings; effective request
method, URL, query, headers, cookies, and body; terminal or explicitly addressed response protocol,
status, informational responses, headers, trailers, media, encodings, body, timing, endpoint, and
TLS facts; stream events/records/controls/termination; and applicable Scenario step data. Common
shorthands and structured forms lower to one Selector AST. Header/trailer lookup returns every
ordered occurrence and never silently chooses or joins values.

Assertions require explicit semantic body views; a bare body selects the BodyHandle. RFC result
duplicates are preserved and counted. Comparo imposes a documented deterministic order where RFC
object traversal is unspecified while preserving array, parent/descendant, and selector ordering
constraints. Internally locations are structured; JSON artifacts canonically render JSON Pointer
and may also retain RFC Normalized Path. HTTP, stream, attempt/exchange, and Scenario locations have
domain-specific typed coordinates.

Response shorthand addresses the selected terminal exchange; structured coordinates can address
specific or all attempts/exchanges. SSE and record streams expose typed JSON-compatible
projections without losing native connection/event/record provenance or nested JSON Pointer.

Every compiled selector reports singularity, incremental/index/materialization needs, descendant
and filter use, duplicate possibility, and function dependencies. Consumers impose stricter
capabilities: stream stops must be incremental, diff keys singular, captures explicitly
cardinalized, and redaction fails closed. Query, traversal, filter, regex, result, and storage
budgets return BudgetExceeded rather than a successful prefix.

A ready runtime result must satisfy that compiled contract exactly. `exactlyOne` has one match,
`zeroOrOne` has at most one, and source absence remains a non-ready outcome. Every match uses the
closed carrier for its declared value kind; body and view handles bind their exact location IDs,
and duplicate locations are accepted only when the compiled selector says they are possible.

## Implementation-level follow-up

The remaining per-kind shorthand inventory will be generated from concrete versioned schemas; the
shared selector semantics are closed.
