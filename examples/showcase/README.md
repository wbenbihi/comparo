# Results showcase

Every state of the **Diff**, **Run**, and **Execution** results screens, produced
deterministically by two local servers — no public service, no randomness, no
network. One process serves a **baseline** on `:8091` and a **candidate** on
`:8092`; they answer identically except where a scenario needs them to differ.

## Run it

Start the servers in one terminal:

```console
python examples/showcase/serve.py
```

Then, from another terminal, open the TUI or run any command headless:

```console
comparo tui  --config examples/showcase             # explore all three tabs
comparo diff --config examples/showcase/comparo.yaml -p baseline-vs-candidate
comparo run  --config examples/showcase/comparo.yaml -e baseline
comparo run  --config examples/showcase/comparo.yaml -e candidate
comparo exec execution.release-gate --config examples/showcase/comparo.yaml
```

`Ctrl-C` stops the servers.

The pair `baseline ⇄ candidate` is preconfigured. Deterministic tallies you'll
see: **diff** → `9 same · 12 drift · 2 error`, gate FAIL; **exec release-gate**
→ `6 cells · 4 drift`, gate FAIL.

## Diff states

| State | Request | What you see |
|---|---|---|
| Value drift (number + string) | **Price quote** (`×3 plans`) | `$.quote.price` and `$.quote.taxRate` drift on every plan — one bug, three cells, one row in the fields pivot |
| Outbound differs | **Config echo** | identical server, but each env injects its own `TAX_RATE`; the OUTBOUND band reads "we sent DIFFERENT requests" — `o` shows the query surface |
| Type drift | **Inventory count** | `count` is `3` vs `"3"` — `type number → string` |
| Shape drift (both directions) | **User profile** | candidate loses `$.user.plan`, gains `$.user.betaFlags` |
| Tolerance **absorbed** | **Rounded total** | `240.0 → 240.004` inside the ±0.01 band → still same |
| Tolerance **broken** | **Service fee** | `12.5 → 14.0` blows through the same band → `(±0.01)` drift |
| Header drift | **API version header** | `$headers.x-quote-version` in the headers well (`v` flips it side-by-side) |
| `$status` drift | **Flaky endpoint** | `200 → 503`, same body — only `$status` drifts |
| Stream drift + length cut | **Price feed** (SSE) | event 3's price drifts AND the trailing event is dropped (`length 5 → 4`) — walk events with `↑↓`, expand the drifted one |
| HTML drift | **Status page** | one paragraph changes — rendered as an outline, not tag soup |
| Binary identical | **Brand logo** | identical PNG — magic · size · sha256 · hex, no drift |
| Binary drift | **Approval stamp** | different PNG bytes — the sha256 differs |
| One-side error | **Legacy quote** | candidate truncates mid-body → `! ERROR`, single-sided evidence kept |
| Both-side error | **Dead endpoint** | both truncate → `! ERROR`, nothing to judge |
| Volatile silenced | *(every response)* | `$.meta.nonce` and `$headers.x-build-id` change per call/side but are silenced (grey `◌`), alongside the built-in `date`/`x-request-id` |
| Unused rule | *(diff profile)* | `$.tpyo.field` matches nothing — the rules pivot flags it unused |
| Not run (`⊘`) | **Parked request** | deselect it at Prepare for the not-run roster |
| Clean cell | **Health check** | identical body, every rule green |

## Run states

Run against **baseline** for the clean/advisory picture, or **candidate** to
watch `$status` and latency rules flip (`e` at Prepare, or `-e candidate`).

| State | Request | What you see |
|---|---|---|
| Clean pass | **Health check** | green verdict box, schema + status held |
| Assertion **FAIL** + red anchor | **Checkout** (POST) | `assert.order` wants `$.order.total == 999` against the echoed `240.0` → expected · got, red anchor pinned at the field |
| Schema fail | **Renewal contract** | `schema.contract` requires `renewsOn`; absent on both sides — 200, clean diff, still wrong |
| Warn advisory **broke** | **SLO probe** | sleeps 300 ms everywhere → `latency <= 150ms` breaks as `~` advisory, gate stays green |
| Warn advisory **held** | *(base profile latency)* | `latency < 5s` always holds — a `~ · held` line |
| Latency regression | **Express lane** | candidate sleeps 300 ms → the ledger shows the Δ; the warn rule breaks on candidate only |
| Error (unreachable) | **Dead endpoint** | `! ERROR`, rules record "never evaluated" |
| Header-target assertion | **Checkout** | `header:x-service-tier == gold` holds |
| Composed provenance | **Checkout** | `assert.order` includes `assert.base` — rows attribute to both |
| Redaction (never-leak) | **Token echo** | the Bearer secret is echoed into `$.tokenEcho` and `x-echoed-token`; every sink shows `••••••` |
| Streamed events explorable | **Price feed** | the SSE chunks are a foldable tree — envelope (id · event · retry) + parsed data per event |
| HTML outline / binary view | **Status page** · **Brand logo** | outline vs magic·sha256·hex |
| Matrix variants | **Price quote** | `✓ PASS / ✗ FAIL / ! ERROR` per plan |

## Execution states

`comparo exec execution.release-gate` (or launch **Release gate** from the
Execution tab) runs the `pricing`-tagged surface against **both** environments
and gates on `asserts(baseline) ∧ diff ∧ asserts(candidate)`.

| State | Where |
|---|---|
| 3-factor gate | the gate band — assertions on each side ∧ the diff between them |
| Per-cell triplet | **Price quote**, **Checkout**, the tolerances — three glyphs (B asserts · diff · C asserts) |
| Drift factor fails | the quote drift and the broken fee band fail the diff factor |
| Assertion factor fails | Checkout's `total == 999` fails on both sides |
| Clean factor | **Rounded total** — absorbed tolerance, all three glyphs green |

## The traceability loop

On a drifted **Price quote** cell in the Diff tab: `enter` (into the broken-rule
rows) → `enter` again (that rule's record across every request) → `enter` on a
record row (back to a cell) → `esc` unwinds each hop. The same loop works in Run
(verdict card → rule record → cell) and, via the shared components, in the
Execution and Report tabs.
