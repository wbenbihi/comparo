# Matrix project — multi-matrix expansion

A minimal, **runnable** comparo project whose star request is expanded across **two matrices at
once**. It exists to exercise cartesian expansion, the global matrix picker (`m`), and the
variant strips in the Run screen.

## What it demonstrates

| File | Feature |
| --- | --- |
| `requests/search.yaml` | **two matrices** (`locales` × `tiers`) → 6 cells per run |
| `requests/checkout.yaml` | a **shared matrix** (`tiers`) — toggling a tier globally hits both requests |
| `requests/ping.yaml` | a **single-cell** request (no matrix) for contrast |
| `matrices/locales.yaml` | 3 cases, merged into the query |
| `matrices/tiers.yaml` | 2 cases, merged into the query and shared across requests |

## The expansion

`request.search` references both matrices, so comparo runs the cartesian product:

```
locales (3)  ×  tiers (2)   =   6 cells

  locale=en-US · tier=free      locale=en-US · tier=pro
  locale=fr-FR · tier=free      locale=fr-FR · tier=pro
  locale=ja-JP · tier=free      locale=ja-JP · tier=pro
```

In the Run screen's **Prepare** state the request shows `6/6 will run`. Open the global matrix
picker with `m` and disable, say, the `pro` tier: `search` drops to `3/6` **and** `checkout`
drops to `1/2`, because both share `matrix.tiers`. That is the whole point — matrix values are
set globally, for every request that references them.

## Run it

```console
comparo validate examples/matrix-project
comparo tui examples/matrix-project        # Run tab → Prepare → m to scope the matrices
comparo run examples/matrix-project --env prod
```
