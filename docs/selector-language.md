# Comparo selector language (`comparo/v1alpha1`)

Selectors are the single read-only addressing mechanism used by assertions, diffs, captures,
redaction, triage, and future Scenario injection. A consumer may decide what zero matches or an
unavailable fact means, but it must not reinterpret the selector.

## Pure evaluation boundary

The executable boundary is conceptually:

```text
select(prepared_snapshot, compiled_selector, control) -> SelectionEvaluation
```

`prepared_snapshot` contains an immutable HTTP observation and only body, decoded-view, and stream
values already obtained from storage ports. Selection performs no filesystem, network, decoder,
store, environment, clock, or callback access. Store adapters verify byte counts, digests,
fingerprints, sensitivity, taint origins, and complete stream coverage before constructing the
snapshot. A decoded payload fingerprint is therefore an adapter-issued integrity proof checked
against its view handle; the selector kernel does not reread or rehash storage content. Adapters
are part of this trusted integrity boundary.

`control` supplies caller-derived cumulative budgets and one immutable cancellation observation.
Every evaluation returns the updated budgets, including successful evaluations, so a later rule
cannot reuse allowance consumed by an earlier rule. Large incremental stores can later drive the
same semantics through bounded pure `start`/`resume`/`finish` transitions without changing the
selector language.

## Occurrence scope

Occurrence scope is part of the compiled selector and cannot be overridden by the caller:

| Scope | Meaning |
| --- | --- |
| terminal | Terminal attempt, or its terminal exchange for exchange-owned sources |
| exact attempt | One attempt; exchange-owned sources use its terminal exchange |
| exact exchange | One execution-unique exchange |
| all attempts | Every attempt, for the `attempt` source only |
| all exchanges | Every exchange in attempt and redirect order |

A terminal attempt that stopped before creating an exchange never falls back to an earlier retry.
Finalized request facts remain selectable from a pre-send outcome; response facts report that no
terminal response exists. An all-exchange selector becomes non-ready if a required occurrence is
missing its response fact—it never silently drops that occurrence.

## HTTP field paths

Query, header, trailer, informational-header, and cookie collections retain source order and
duplicates. A field path names a field, may name one zero-based occurrence among duplicate names,
and declares its projection:

- `raw-value` returns the exact unjoined value and is the default;
- `items` explicitly requests a supported parsed token-list projection.

Header names use their canonical lowercase, case-insensitive identity. Query names are exact
Unicode names. Cookie names retain HTTP token semantics. Empty text and a bare query parameter are
distinct. Sensitive values remain sealed with their exact taint origins.

## Built-in observation facts

Static facts use closed, leaf-level paths rather than open dictionaries or guessed aggregates.
The initial catalog covers execution and attempt IDs/timings, exchange IDs, response timing
phases, endpoint address/port/connection facts, connect/origin TLS facts, stream timings, and
stream termination/completeness. There is deliberately no synthetic `response.timings.total`;
`response.timings.elapsed` is the measured end-to-end HTTP exchange interval, while DNS, connect,
TLS, request write, server wait, first body byte, and body read remain separately addressable.

An applicable but unmeasured fact is distinct from a missing response, an invalid observed fact,
and a measured zero duration.

## JSON Pointer

[RFC 6901 JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901) is the singular fast path. The empty
string selects the root. Tokens decode `~1` before `~0`; invalid escapes fail compilation. Array
tokens are either `0` or a base-10 index without a leading zero. `-` denotes a nonexistent member
and therefore produces no match when reading.

Every selected JSON node is reported with a canonical JSON Pointer location. Object member names
escape `~` as `~0` and `/` as `~1`.

## JSONPath

Author-facing JSON queries follow [RFC 9535 JSONPath](https://www.rfc-editor.org/rfc/rfc9535).
Comparo compiles text once into an immutable typed AST; evaluators never parse text.

Supported syntax includes:

- root `$` and current filter node `@`;
- child and descendant segments;
- name, index, slice, wildcard, and union selectors;
- filter expressions with existence tests, comparison, negation, conjunction, and disjunction;
- standard `length`, `count`, and `value` functions;
- standard `match` and `search` syntax and typed dependencies. In `comparo/v1alpha1`, evaluation
  returns an explicit unsupported result because a bounded RFC 9485 I-Regexp evaluator is deferred;
  Comparo never substitutes Python or host-language regular expressions.

Scripts, `eval`, host callbacks, interpolated paths, URI-fragment pointers, unknown functions, and
implementation-specific JSONPath extensions are rejected.

Parser limits bound code points, nesting, segments, selectors, expressions, and numeric text.
Integers follow the RFC interoperable range. Compilation errors retain only a stable error code and
character offset; they never retain or render the authored expression.

### Ordering and duplicates

Comparo preserves:

1. input nodelist order;
2. selector order within a union;
3. array order;
4. source member order for object wildcards and descendants;
5. depth-first pre-order for descendant traversal.

Duplicate nodes produced by JSONPath unions are retained and charged as separate matches. JSON
object structural comparison may later ignore member order, but selection never discards the
decoded view's source order.

### Filter semantics

Existence tests use nodelist non-emptiness: a selected `false`, `null`, or empty string still exists.
A missing or plural value where a singular value is required produces RFC `Nothing`, not a guessed
value. Numeric comparison uses exact integer and decimal values without floating-point coercion.

## SSE and JSON record streams

Stream sources are explicit:

- `stream.events` selects a stable outer SSE event object containing text/event/ID metadata;
- `stream.events.data-json` selects only explicitly decoded nested SSE `data` JSON;
- `stream.records` selects NDJSON or JSON-sequence record values;
- `stream.controls` selects stable control projections;
- `stream.timings` selects time-to-first-record and observation-window duration facts;
- `stream.termination` selects closed termination and extent facts.

A nested JSON failure does not invalidate selection of the outer SSE event. It becomes a located
decode or budget issue only for `stream.events.data-json`. Complete records before a malformed
record or incomplete window can be returned only as an explicit partial result, never as ready.
Locations retain view, connection segment, global item order, category order, and nested JSON
Pointer coordinates.

## Result states

The result union keeps these states distinct:

- ready with zero, one, or many ordered matches;
- partial with complete usable items plus exact extent and located typed issues;
- source unavailable;
- an existing typed fact not performed or unavailable;
- invalid observed metadata;
- body or nested-data decode failure;
- incomplete capture, view, or stream window;
- upstream decoding or selection budget exhaustion with exact evidence;
- unsupported implementation capability;
- cancellation.

Authored parse and static type failures happen before evaluation and are not collapsed into runtime
unavailability. Direct-construction invariant failure is a programmer error, not an authored
selection result.

Successful decoded-view provenance includes the exact view ID, selector source, requested
representation, `DecoderSpec` and options, automatic registry selection when used, media metadata,
and completeness. Failure results preserve the same safe decoder/auto/media/source identity plus
the body ID and exact typed failure reason. Storage locators, payloads, and sensitive fingerprints
never enter diagnostics or failure provenance.

## Resource accounting and cancellation

Every caller supplies configured budgets for:

- selection work;
- selected match count;
- selected result bytes.

Evaluators charge before traversal, filter/function work, URL rendering, header tokenization, or
result append. Duplicate matches count independently. Result bytes use a deterministic value
encoding, never `repr`. The first exhaustion returns exact `BudgetExceeded` evidence and no
successful prefix.

A pure batch call observes cancellation only at its immutable call boundary and is bounded by its
work budget. Responsive cancellation over very large storage-backed content uses bounded pure
resume transitions, each supplied with a fresh cancellation observation by the I/O driver.
