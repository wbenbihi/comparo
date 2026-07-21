# Results showcase

Every state of the Diff (and Run) results screens, on two tiny local servers
that differ deliberately. Nothing external, nothing flaky.

## Run it

```console
python examples/showcase/serve.py          # baseline :8091 · candidate :8092
comparo tui --config examples/showcase     # in another terminal
```

Diff tab (`3`) → `x`. The pair `baseline ⇄ candidate` is preconfigured.

## What to look at

| State | Where |
|---|---|
| Body drift (3 cells, one bug) | **Price quote** — `$.quote.taxRate` drifts on every plan; the fields pivot collapses it to one row |
| Header drift | **Price quote** — `x-api-version` in the response-headers well (−/+; `v` flips it side-by-side too) |
| Tolerance absorbed | `$.quote.total` on `plan=basic` differs by 0.004 — inside the ±0.01 band, so the rule shows **passed**, not broken |
| Volatile built-ins | `x-request-id` differs on every call — silenced by the built-in rules (grey, `◌`) |
| User header rule | `$headers.server` — the two servers name themselves differently; the profile ignores it |
| Unused rule | `$.tpyo.field` — matches nothing anywhere; the rules pivot calls it out (`– … typo?`) |
| Stream drift | **Price feed** — event 3 drifts on `$[2].price`; `$[*].ts` silenced on every event |
| Transport error | **Legacy quote** — candidate drops the connection: the error panel (attempts · retry policy · the kept baseline) |
| `$status` drift | **Unstable endpoint** — candidate answers 503 |
| Clean cell | **Checkout** — the green all-held box, collapsed sections |
| Not run (`⊘`) | Deselect **Quote history** at Prepare |
| Advisory `~` (Run tab) | **Price quote** carries a `latency <= 5ms · warn` rule — breaks almost always, never gates |
| HTML outline / binary view (Run tab) | **Status page** (`contains "operational"` highlighted) · **Logo** (magic · sha256 · hex) |

The traceability loop: on a red **Price quote** cell press `enter` (into the broken-rule
rows) → `enter` again (that rule's record across every request) → `enter` on a record row
(back to a cell) → `esc` unwinds each hop.
