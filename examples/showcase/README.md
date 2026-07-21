# Results showcase

Every state of the Diff (and Run) results screens, entirely on **public
services** — httpbin.org for the pair, sse.dev for the SSE stream. Nothing to
start, nothing local.

The trick: both environments point at the *same* httpbin, but each injects its
own variables (`TAX_RATE`, `API_VERSION`, `STATUS`, `DELAY`). httpbin echoes
what it receives, so every drift you see was **sent by us** — which is exactly
the story the OUTBOUND band tells (`⚠ we sent DIFFERENT requests`).

## Run it

```console
comparo tui --config examples/showcase     # Diff tab (3) → x
```

The pair `baseline ⇄ candidate` is preconfigured. Deselect **SSE test stream**
at Prepare when diffing (it belongs to the `sse-dev` environment).

## What to look at

| State | Where |
|---|---|
| Body drift (3 cells, one bug) | **Price quote** — `$.args.taxRate` drifts on every plan; the fields pivot collapses it to one row |
| The outbound story | Any drifted cell — the band reads `⚠ we sent DIFFERENT requests`; `o` shows which config surface (query · env var) |
| Header drift | **Version header** — `/response-headers` turns the query into response headers, so `x-api-version` drifts in the headers well (`v` flips it side-by-side too) |
| Tolerance rule (passed) | `$.json.order.total` on **Checkout** — the band holds; the rules pivot shows it green |
| Volatile built-ins | `date` and friends — silenced by the built-in rules (grey `◌`) |
| User rules (grey) | `$.headers` / `$.origin` / `$.url` — httpbin's echo noise, deliberately ignored |
| Unused rule | `$.tpyo.field` — matches nothing anywhere; the rules pivot calls it out (`– … typo?`) |
| Stream drift | **Price feed** — `/stream/5`'s five events each echo the injected taxRate: per-event drift in the sequence view |
| Transport error | **Slow endpoint** — `/delay/10` against a 2s read budget: the candidate times out; the error panel shows attempts and the deadline |
| `$status` drift | **Status probe** — 200 vs 503 |
| Clean cell | **Checkout** and **HTML page** — the green all-held box, collapsed sections |
| Not run (`⊘`) | Deselect **Quote history** at Prepare |
| Advisory `~` (Run tab) | **Price quote** carries `latency <= 5ms · warn` — breaks almost always, never gates |
| HTML outline / binary view (Run tab) | **HTML page** · **PNG image** (magic · sha256 · hex) |
| SSE envelope (Run tab) | Pick the **sse-dev** environment, run **SSE test stream** — id · event · data · retry, capped by `streamMax` |

The traceability loop: on a red **Price quote** cell press `enter` (into the
broken-rule rows) → `enter` again (that rule's record across every request) →
`enter` on a record row (back to a cell) → `esc` unwinds each hop.
