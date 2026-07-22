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
| Transport error (one side) | **Slow endpoint** — `/delay/${DELAY}` against a 2s read budget: only the candidate (10s) times out |
| Transport error (both sides) | **Legacy quote** — `/delay/10` against a 1s budget: times out everywhere; the `!` cell in both tabs |
| `$status` drift | **Status probe** — 200 vs 503 |
| Clean cell | **Checkout** and **HTML page** — the green all-held box, collapsed sections |
| Not run (`⊘`) | Deselect **Quote history** at Prepare |
| Advisory `~` (Run tab) | **Price quote** carries `latency <= 5ms · warn` — breaks almost always, never gates |
| HTML outline / binary view (Run tab) | **HTML page** · **PNG image** (magic · sha256 · hex) |
| SSE envelope (Run tab) | Pick the **sse-dev** environment, run **SSE test stream** — id · event · data · retry, capped by `streamMax` |

The traceability loop: on a red **Price quote** cell press `enter` (into the
broken-rule rows) → `enter` again (that rule's record across every request) →
`enter` on a record row (back to a cell) → `esc` unwinds each hop.

## The Run tab, state by state

Run tab (`2`) → `x` (executes against **baseline** by default; `e` at Prepare
switches — against **candidate**, `STATUS=503` also breaks Status probe):

| RUN state | Where |
|---|---|
| `✗ FAIL` cell + red verdict card | **Checkout** — `assert.order` demands `total == 999` against an echoed `240.0`; the card shows expected · got |
| Red anchor in the body (`n`/`p`) | the same cell — `✗ json.order.total` pinned at its site; `sku` carries the green `✓` |
| `~ advisory` PASS cells | **Price quote** ×3 — the 5ms latency SLO breaks, amber everywhere, gate untouched |
| `! ERROR` cell + error card | **Legacy quote** — verbatim timeout, attempts (retry ×2), the kept masked request |
| Matrix variants table | **Price quote** — `✓ PASS / ✗ FAIL / ! ERROR` per case |
| Rules index (`r`) | broken (`total == 999`) on top → advisory (`latency <= 5ms`) → held → **Legacy quote's** rules as `! error · never evaluated` |
| Record table jump | open `total == 999` → `enter` on a record row lands in that cell's detail |
| Worst-first + `o` | the finished table leads with Checkout (✗) and Legacy quote (!) |
| Filter by attribute | `/` then `fail`, `error`, `POST`, `sse`, a case key… — not just names |
| Facets + `y` | `t` cycles all · request · response · headers · raw; `y` copies the masked exchange |
