# Checks project — assertions that pass and fail

A **runnable** project built to light up the Run screen's **ASSERT** column and the Report
screen's pass/fail gate. Four requests hit httpbin; two are designed to pass their assertions and
two are designed to fail — one on status, one on schema.

## What it demonstrates

| Request | Endpoint | Expectation | Outcome |
| --- | --- | --- | --- |
| `status-pass` | `GET /status/200` | status `200` | ✓ status matches |
| `status-fail` | `GET /status/200` | status `201` | ✗ `200 ≠ 201` |
| `schema-pass` | `GET /json` | matches `schema.slideshow` | ✓ schema valid |
| `schema-fail` | `GET /json` | matches `schema.slideshow-strict` | ✗ missing `subtitle` |

`schema-fail` is the interesting one: the request is **reachable** and returns **200**, yet its
schema assertion fails because the strict schema requires a field httpbin never sends. That is a
request that is up but wrong — exactly the case the ASSERT column exists to surface.

## Run it

```console
comparo validate examples/checks-project
comparo run examples/checks-project --env prod
comparo tui examples/checks-project    # Run tab → run all → watch the ASSERT column
```

Expect a **partial** run: two requests green, two red. The Report screen's gate reads **FAIL**
because untriaged assertion failures remain.
