# Health project — green, orange, and red environments

A **runnable** project whose three environments are engineered to produce each of the three
health states. It exists to exercise the environment health dot in the Explorer, the `h` key that
runs the checks, and the `PASS` / `PARTIAL` / `FAIL` aggregate.

## What it demonstrates

| Environment | Health checks | Aggregate | Dot |
| --- | --- | --- | --- |
| `healthy` | `/status/200`, `/status/204` | all pass → `PASS` | 🟢 green |
| `degraded` | `/status/200`, `/status/503` | one fails → `PARTIAL` | 🟠 orange |
| `down` | `/status/200` on an unresolvable host | transport error → `FAIL` | 🔴 red |

A check passes on any status below 400. `degraded` mixes a `200` with a `503`, so it lands on the
in-between `PARTIAL` state. `down` points its base URL at `health-showcase.invalid`, a name that
never resolves, so the probe raises a transport error and nothing passes.

## Run it

```console
comparo validate --config examples/health-project/comparo.yaml
comparo tui --config examples/health-project/comparo.yaml    # Explorer → select an env → h to probe → watch the dot
```

In the TUI, select each environment and press `h`; the dot recolors to match the table above and a
toast summarizes the checks. Press `Enter` on an environment to make it the default that
interpolation resolves against.
