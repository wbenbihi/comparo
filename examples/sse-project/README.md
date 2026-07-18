# SSE example — diffing Server-Sent Events

comparo reads a `text/event-stream` response as an **ordered list of events** and
diffs the sequence event-by-event ("event 3 changed"), rather than as one opaque
body. This example shows that end to end.

There are two ways to try it: against a **public** SSE endpoint (zero setup), or
against a **bundled local** server (a clean, deterministic diff).

## Against a public endpoint (no setup)

The `live` environment points at [`sse.dev/test`](https://sse.dev/test), a real,
public SSE feed. It never closes, so the environment sets `timeout.streamMax: 3s` —
a total cap that reads events for three seconds, then stops and uses them:

```console
$ comparo run request.feed --env live
run · Live (sse.dev)
  ✓ request.feed                               200
```

## Against the bundled local server (deterministic diff)

For a clean pass/fail diff, this project ships a tiny local SSE server whose two
feeds differ by exactly one event. In one terminal, start it:

```console
$ python examples/sse-project/serve.py
comparo SSE example — two local event streams (Ctrl-C to stop):
  canary  http://127.0.0.1:8421  (4 SSE events, then closes)
  stable  http://127.0.0.1:8420  (4 SSE events, then closes)
```

It stands up two feeds — `stable` on :8420 and `canary` on :8421 — that are
identical except for one event. Then, in another terminal, diff them:

```console
$ comparo diff --config examples/sse-project/comparo.yaml --baseline stable --candidate canary
diff · Stable ⇄ Canary
  ✗ request.feed                                 drift
      $[2].data  "…value: bravo…" → "…value: BRAVO-DRIFTED…"

summary: 0 same · 1 drift · 0 error · 0 fields skipped
gate: FAIL
```

comparo pinpoints the one event that drifted (`$[2]` — the third event in the
sequence).

## How it's wired

- `requests/feed.yaml` sets `response.streaming: true`. That's the whole switch:
  the engine streams the response, parses `text/event-stream` into events, and the
  diff compares the sequences.
- The two environments point at the two local ports.

## Bounding a never-closing stream

The bundled local server is **finite** — it sends its events and closes, so comparo
reads it to completion with no timeout needed. A real SSE endpoint usually stays
open forever, so `spec.timeout` (on the request or the environment) offers two
bounds — set whichever fits the feed:

```yaml
spec:
  timeout:
    streamIdle: 5s   # end the stream after 5s with NO new event (a quiet feed)
    streamMax: 3s    # a TOTAL cap — end after 3s no matter how busy (a steady feed)
```

- **`streamIdle`** ends the stream on a gap — good for a feed that bursts and then
  goes quiet.
- **`streamMax`** ends it after a fixed wall-clock budget — needed for a *steady*
  feed like `sse.dev` that emits on a timer and never idles. The `live` environment
  here uses it.
