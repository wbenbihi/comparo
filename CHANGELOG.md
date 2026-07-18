# CHANGELOG


## v0.1.0 (2026-07-18)

### Bug Fixes

- **action**: Select the project with --config, not a positional argument
  ([`3c4e02b`](https://github.com/wbenbihi/comparo/commit/3c4e02b6c9815d5f40785d25d0dce2f4becbd2ff))

Under the reworked CLI the first positional is a request id, so the composite action must pass the
  project through --config.

- **adapters**: Harden reporters and version the run archive
  ([`19e9b28`](https://github.com/wbenbihi/comparo/commit/19e9b28e4d9076c3d47855a3313ce2d7550b1455))

- JUnit: strip XML-1.0-invalid control chars from names, failure text, and error messages so a
  server-controlled field can't produce malformed XML. - Markdown: escape pipes and newlines in
  table cells so a poisoned drift path or detail can't shatter the table. - SARIF: give each result
  a physicalLocation.artifactLocation.uri so GitHub code-scanning ingests it instead of silently
  dropping it. - Archive: stamp every saved record with a schema version (legacy files load as v0)
  and add an opt-in retention prune(keep=N).

- **cli**: Evaluate a request's response.assert on the run path
  ([`78bcfe3`](https://github.com/wbenbihi/comparo/commit/78bcfe31b3aff0898d042201b353d33c3d4266c2))

comparo run compiled only a request's response.status / response.schema sugar into rules
  (request_rules), so a request that attached AssertionProfiles via response.assert had them
  silently ignored — while comparo exec honoured the same block. A response.assert that should have
  failed the gate passed it.

Introduce request_response_rules(project, request) in the assertion engine — the single place that
  turns a request's whole response contract (status + schema sugar + response.assert profiles) into
  rules — and use it on the run path. The execution planner now reuses it (and the shared
  profiles_to_rules), dropping its private _profiles_to_rules copy so both paths compile assertions
  identically.

- **cli**: Fail loud on an empty project and an unknown report format
  ([`3ed97fe`](https://github.com/wbenbihi/comparo/commit/3ed97fe7d869dafc9b3c0491f98a3dfd0650fccf))

- validate now exits non-zero with a clear message when a manifest's data dir resolves to zero
  objects, instead of a green '0 object(s) valid'. - diff rejects an unknown --report format up
  front instead of warning and exiting 0 with no artifact written.

- **core**: Close silent-config and gate-correctness holes
  ([`ed28817`](https://github.com/wbenbihi/comparo/commit/ed28817e2ddc940db05c3dcc904ca2ca5f1a5631))

- C1: accept mapping-form request headers ({Name: value}) and interpolate ${VAR} in endpoints, so
  the shape comparo's own AGENTS.md teaches actually sends auth on the wire (was silently dropped;
  validate passed). - H4: compare HTTP status as a synthetic $status field, so a 200->500 with
  identical bodies fails the gate; opt out with {path: $status, mode: ignore}. - H5/H6: resolve_pair
  errors on a lone --baseline/--candidate and on a diffPair missing a side, instead of silently
  diffing the wrong (or self) pair. - H10: guard $val instance cycles as a captured
  InterpolationError, not a RecursionError that aborts the whole run.

Regenerate the committed JSON Schema for the tightened headers type.

- **core**: Make run_checks enforce inline schemas like the assertion engine
  ([`4cd90bc`](https://github.com/wbenbihi/comparo/commit/4cd90bccf899a10c79903aecd51c26d8fd32a7aa))

The TUI Run tab validated a response.schema only when it was a {$ref}, so a request with an inline
  schema showed green in the TUI while the CLI/exec path (which uses the assertions engine) failed
  it — a false green. Resolve inline dict schemas too, and catch unresolvable-$ref errors as a
  failed check rather than a crash, matching assertions.

- **core**: Never crash the whole run on valid-looking input
  ([`fcfc853`](https://github.com/wbenbihi/comparo/commit/fcfc85329bed838bb330592561fd73280b1ea270))

Convert seven crash paths into captured results/diagnostics and give requests a finite deadline: -
  H7: default connect 5s / read 30s when no timeout is declared, and cap the whole non-streaming
  read with a total deadline so a trickling server fails a run instead of hanging CI forever. - M1:
  a wildcard/quoted assertion path ([*], ["k"]) is a clean miss. - M2: an invalid 'matches' regex
  fails the rule, not the run. - M3: a pathologically deep body compares as a leaf (no
  RecursionError). - M4: a YAML-native date in a body loads as an ISO string. - M5: a tz-aware
  saved-report timestamp no longer crashes the Report tab. - M6: an unresolvable secret in a health
  header fails that check only. - M7: an unresolvable schema $ref fails the rule, not the run.

- **openapi**: Wire auth, path params, form bodies, and schema refs on import
  ([`b644a98`](https://github.com/wbenbihi/comparo/commit/b644a98147e865087d475129329f741b28739d1b))

- H11: basic auth declares both API_USERNAME and API_PASSWORD. - M17: apiKey in query/cookie schemes
  are wired into each request. - M16: {param} path templates become ${param} holes backed by env-var
  stubs. - M18: form-only request bodies emit a schema stub with bodyType: form. - H12: emitted
  Schemas inline transitive components under $defs and rewrite #/components/schemas/X pointers, so
  nothing dangles at run time. - M19: an external (multi-file) $ref is skipped with a recorded
  warning instead of silently importing zero requests.

- **security**: Close six regressions the re-audit found
  ([`601dc6f`](https://github.com/wbenbihi/comparo/commit/601dc6f9bbc6dcaf0f04f824053e542388dfa1c4))

R1 (HIGH): redaction.stringMatchBackstop:false made Redactor.for_project a no-op, so the .reports
  archive and CLI report files wrote server-echoed secrets to disk. The backstop is now an
  unconditional floor; the key is accepted but never disables masking. R2:
  run.concurrency/retry.attempts constrained >=1 at load (no more Semaphore(-1) crash). R3: a
  deadline timeout (HttpTimeoutError) is never retried. R4: the diff depth cap marks a too-deep
  subtree uncompared, and a too-deep JSON

body falls back to raw compare. R5: the live TUI header view masks server credential headers. R6:
  docs/configuration.md updated for the wired/strict config.

- **security**: Confine report paths and mask percent-encoded secrets
  ([`5836b03`](https://github.com/wbenbihi/comparo/commit/5836b038a55fc91118f2cdc8e17d0f1fc50ff8f4))

Two verified findings from the re-audit's security finder: - Path traversal: spec.report.dir/output
  accepted an absolute or ../ path, letting a config write (and prune) report files outside the
  project root — unlike the $file secret source, which is confined. Now a report path that escapes
  the project root is a load error. - The encoding-robust Redactor registered JSON-escaped forms but
  not percent-encoded ones, so a secret echoed back in a reflected URL slipped through. Register
  quote / quote(safe='') / quote_plus forms too.

- **security**: Make secret redaction encoding-robust and single-source
  ([`9cb7f21`](https://github.com/wbenbihi/comparo/commit/9cb7f218829109fd72663ae82353996b05c2ba10))

Close three confirmed bypasses of the never-leak guarantee: - H1: register each secret's
  JSON-escaped form so a secret containing a quote/backslash/newline is masked in the json.dumps'd
  detail sinks read. - H2: delete export.py's private, unordered redaction copy and route it through
  the one longest-first Redactor, so an overlapping secret's tail no longer survives to runs/*.json.
  - H3: mask server-issued credential headers (Set-Cookie, Authorization, ...) by name, independent
  of the declared-secret set.

Harden the doctor canary with a special-char secret, an overlapping pair, and an undeclared
  Set-Cookie so the self-check guards all three forever.

- **security**: Shell-quote the curl method and cap the buffered body
  ([`fccfa81`](https://github.com/wbenbihi/comparo/commit/fccfa81064185b967138c4583d3961b9d8cd0e65))

- M35: the HTTP method is interpolated into the exported curl command, so quote it like the
  URL/headers/body — an unusual method can't inject shell syntax. - M36: stream and bound the
  non-streaming response read at 64 MiB instead of buffering .content, so a runaway response can't
  exhaust memory.

- **tests**: Resolve mypy errors surfaced by the full-tree type check
  ([`fb5677c`](https://github.com/wbenbihi/comparo/commit/fb5677cf11ceeb4757d5bbe0ce6309fc1bf0586b))

The pre-commit mypy checks tests too: a ResolvedRequest header list needed the invariant
  list[tuple[str, object]] annotation, and an is-False/is-True assert pair let mypy narrow the
  config to its default and flag the post-toggle assertion as unreachable — a fresh read after the
  toggle breaks the narrowing.

- **tui**: A diff finishing on another tab no longer steals focus
  ([`419c2ee`](https://github.com/wbenbihi/comparo/commit/419c2ee23556eb21f6e6630d80bfd74cbcea81ca))

DiffView._finish unconditionally focused the drift table and swapped the footer even when the user
  had navigated away while the diff ran in the background — the visible tab went dead until they
  clicked. Only grab focus/footer when the Diff tab is actually showing; returning to it re-focuses
  via refresh_screen.

- **tui**: Allow re-running a diff from RESULTS after picking a new pair
  ([`0567f94`](https://github.com/wbenbihi/comparo/commit/0567f94d017ffd926179dd587c1baa3bd9676b28))

The re-run guard treated 'diff-results with _done cleared' (exactly what picking a new
  baseline/candidate leaves behind) as an in-flight run, so pressing x falsely warned 'A diff is
  already running…' and the triage loop was stuck. Guard on the RUNNING panel actually showing
  instead, and correct the sibling test to model the real in-flight state.

- **tui**: Center the nav bar and give the tabs a clear active pill
  ([`d2e3661`](https://github.com/wbenbihi/comparo/commit/d2e3661ed0ac97268ca1497c1ead6635e9631d85))

- **tui**: Keep the matrix picker from hiding requests; prepare shows cases that will run
  ([`4baf571`](https://github.com/wbenbihi/comparo/commit/4baf57199f2b694db6bde16c36b05c656c7c65b6))

- **tui**: Keep the report filter on tab return and clear a stale re-run cell
  ([`067dd41`](https://github.com/wbenbihi/comparo/commit/067dd41dea7e505daac5a023c9f8d13f3674519b))

Two Report/Execution state bugs: - ReportView.apply_filter matched a saved run on its id, envs,
  kind, gate, or execution, but refresh_screen (fired when you re-enter the tab) re-filtered on the
  id ALONE — so a gate or envs filter silently collapsed to nothing on return. Both now share one
  _matches predicate. - ExecutionView.launch (the re-run path) reset the result and record but left
  _cell pointing at a CellOutcome from the previous run, so a stale cell could render against the
  new result. A re-run now clears _cell and _drifted.

- **tui**: Make the Settings tab reachable without shift on an Apple keyboard
  ([`8202c86`](https://github.com/wbenbihi/comparo/commit/8202c86272505bf3edc520d9c6dcdc87c6e67644))

Tab 6 (Settings) bound `minus` as its unshifted alternative — the PC AZERTY character for that key.
  On an Apple French keyboard the 6 key is `§` (section_sign) unshifted, so opening Settings needed
  shift. Bind section_sign too; tabs 1-5 already matched both layouts.

- **tui**: Move the accent border to the focused panel so the active one is clear
  ([`431029b`](https://github.com/wbenbihi/comparo/commit/431029b88c5b48477da009df50bea03c9af3f110))

- **tui**: Pin a run's environment so it cannot be mislabeled on save
  ([`ff4d5da`](https://github.com/wbenbihi/comparo/commit/ff4d5da9a234784d2d64c4e118533c420884713f))

RunView read the app's current default environment for the RUNNING banner, the per-cell detail
  resolution, and the save path — so navigating and changing the default after a run relabeled (and
  mis-saved) the finished run against the wrong environment. Pin the env at launch and use it
  everywhere the run is rendered or persisted.

- **tui**: Show real pass/fail counts in the live running view
  ([`d4bd072`](https://github.com/wbenbihi/comparo/commit/d4bd072202b63f29ac121be25b4f30a91715b927))

_running_body hardcoded '0 ✗', '~410ms/cell', and 'stable'/'candidate' labels and painted every
  finished cell green. Derive the ✓/✗ tallies from the per-cell glyphs the caller already tracks,
  drop the fabricated throughput figure, label the roles honestly (baseline/candidate), and mark a
  drifted finished cell as ✗. A run against a broken candidate no longer shows all-green while it
  fails.

- **tui**: Stop asserting an execution drift is the service's fault
  ([`427ea66`](https://github.com/wbenbihi/comparo/commit/427ea6634c438ee1319a10e3f4c24eaf7f35b986))

The cell view stated 'outbound identical → the drift is the service's' without ever comparing the
  outbound requests — but a per-env variable/secret can make the two sides send different requests,
  so the drift may be the user's own config. Replace the false assertion with an honest prompt to
  confirm the outbound via the Diff tab before blaming the service.

- **tui**: Stop labeling q as close/back/cancel in footers and help
  ([`0396a1e`](https://github.com/wbenbihi/comparo/commit/0396a1e99d6559fbc05399989d8ac71ba8072909))

Every Execution and Report sub-view footer bundled q into an 'esc/⌫/q close' (or 'back'/'cancel
  run') hint, and the graph/curl/replay help said q closes — but q quits the whole app. A user
  trusting the hint lost an unsaved run. Split the real back/close key (esc/⌫) from q, which is
  always labeled 'quit', and add an invariant test that no footer hint can regress the rule.

- **tui**: Stop the tree scrolling sideways and give the top bar mockup spacing
  ([`28a72b1`](https://github.com/wbenbihi/comparo/commit/28a72b16cc25852850b2ffb60e7f5b72d62c0985))

### Chores

- Fix documentation drift and remove dead weight
  ([`f89708d`](https://github.com/wbenbihi/comparo/commit/f89708d9e57d2145471b87f04b0f03fbbfc68445))

- README: fix the quickstart (scaffold has one 'local' env, not 'prod'/a diff pair), drop the
  non-existent 'plugin' claim, and correct the GitHub alert. - Add --config to the root command so
  'comparo --config <manifest>' (which the scaffold and docs advertise) actually opens the TUI
  instead of erroring. - docs: drop the phantom --tags/--save flags and the stale 'streamIdle not
  yet applied' note; remove the deleted plugins dir from the architecture tree. - Remove the empty
  comparo.plugins package (advertised a registry that does not exist), the unused
  diff.profile_rules, and the unused syrupy dev dependency.

- Ignore the runtime report archive (.reports/)
  ([`7c17146`](https://github.com/wbenbihi/comparo/commit/7c17146379af375d215416195c46ddb077da2197))

- Scaffold project tooling, packaging, and CI
  ([`3522214`](https://github.com/wbenbihi/comparo/commit/35222141be8e627f1a17c74c58defc94261240e6))

- **release**: Drop internal design scaffolding and gate PyPI publish
  ([`7fd301a`](https://github.com/wbenbihi/comparo/commit/7fd301abef0e0845e72e9505d063b8eff477ca0f))

- Remove docs/design/ (raw AI-session transcript + mockups) from the tree and gitignore it; it
  discloses an NDA client engagement and local paths. History must still be purged before the repo
  goes public (see CODE_HEALTH_REPORT.md prerequisites). - Gate the release workflow's
  Publish-to-PyPI step on an actual version bump, so a non-release push to main no longer runs (and
  fails) the publish.

- **release**: Keep releases in 0.x and add alpha/beta/rc prerelease tracks
  ([`8866663`](https://github.com/wbenbihi/comparo/commit/886666319a60dd818e481dc00d3d2a3d5244a901))

The first Release run cut 1.0.0 from a 0.0.0 baseline because semantic-release's defaults leap to a
  1.0.0 minimum. Pin the pre-1.0 behaviour and set up the prerelease promotion tracks:

- allow_zero_version + major_on_zero=false keep every release in the 0.x line; even a breaking
  change bumps the minor, never 1.0.0, until we cut a stable release deliberately. - alpha/beta/rc
  branches are prerelease channels (0.x.0-alpha.N -> beta -> rc); main cuts stable 0.x. The Release
  workflow now triggers on all four branches. - commit_parser: angular -> conventional (the 9.15
  rename; angular is deprecated).

### Code Style

- Apply ruff format to userconfig and the tui app
  ([`a50c99b`](https://github.com/wbenbihi/comparo/commit/a50c99bc4afdec21b81c0e1e0824c197542538a5))

Two files committed earlier under --no-verify carried small ruff-format deviations (blank-line
  placement); reformat them so the whole tree is clean.

### Continuous Integration

- **release**: Push via RELEASE_TOKEN, guard the release loop, allow manual dispatch
  ([`bc2a6b9`](https://github.com/wbenbihi/comparo/commit/bc2a6b90bf00151fe8b8cd94ece48e43d9f81a44))

Prepare release automation to work with protected release branches: - checkout uses ${{
  secrets.RELEASE_TOKEN || github.token }} so semantic-release pushes the version commit + tag as
  the repo admin, bypassing branch protection on main/alpha/beta/rc; falls back to the default token
  when no PAT is set. - skip the job when the head commit is semantic-release's own version bump — a
  PAT push re-fires the workflow and would otherwise loop. - add workflow_dispatch for a controlled
  manual release on a chosen branch.

- **release**: Use the official python-semantic-release action
  ([`8ed0cfd`](https://github.com/wbenbihi/comparo/commit/8ed0cfdff9d91f348142f4a74b5e6c5383b94aad))

The hand-rolled 'uv run semantic-release version' step failed at Create Release with a 401 —
  python-semantic-release reads the GitHub API token from GH_TOKEN, which the step never set, so it
  had no credential for the releases API.

Switch to the maintained python-semantic-release/python-semantic-release@v9.21.2 action (matches our
  >=9.15 pin). Its github_token wires RELEASE_TOKEN into both the git push and the GitHub API —
  fixing the 401 — and exposes outputs.released so the PyPI publish gate no longer relies on a
  before/after HEAD heuristic.

### Documentation

- Add example projects for matrices, checks, health, and load errors
  ([`8019703`](https://github.com/wbenbihi/comparo/commit/801970398ebf56500421ef276c70ec01e198d7ed))

Four focused, httpbin-backed projects that each exercise one feature so the screens have real
  fixtures to render:

- matrix-project: a request expanded across two matrices (locales × tiers) → six-cell cartesian
  product, plus a shared matrix to show the global picker scoping every referencing request at once.
  - checks-project: status and JSON-schema assertions wired to pass and fail on purpose, including a
  reachable-but-wrong request, for the ASSERT column. - health-project: three environments whose
  health resolves green, orange, and red (PASS / PARTIAL / FAIL, the last via an unresolvable host).
  - broken-project: one distinct load error per file — invalid YAML, unknown field, missing id,
  duplicate id, and two dangling refs with near-miss hints — for the error screen and `comparo
  validate`.

Each valid project passes `comparo validate`; the broken one exits 1 with all six diagnostics. The
  check-yaml hook skips broken-project since it ships invalid YAML on purpose. Indexed in
  examples/README.md.

- Add runnable httpbin sample project
  ([`ebbd564`](https://github.com/wbenbihi/comparo/commit/ebbd5646f2dea9fe7d019a284df14545b67e3ce3))

- Adopt the comparo.yaml manifest name across examples and docs
  ([`5bb7105`](https://github.com/wbenbihi/comparo/commit/5bb710587b236107015467eaeff3459e6e009d97))

Rename examples/*/project.yaml to comparo.yaml — the manifest name that will not collide with a
  user's own files — and update every doc and example README to the --config invocation form,
  comparo init, and the bare comparo TUI launch.

- Comprehensive documentation — configuration, TUI, CLI, and architecture references
  ([`b193d33`](https://github.com/wbenbihi/comparo/commit/b193d3362a76ff1e6d053a87f44f1733db574edb))

- Correct the resolve sink docstring and the sample project's diff invocation
  ([`a58df9c`](https://github.com/wbenbihi/comparo/commit/a58df9c6f9505f165e0cb2c8d9bb9fec1ab20fbf))

- Execution/report tabs, settings, doctor, and the config model
  ([`ac5b1dc`](https://github.com/wbenbihi/comparo/commit/ac5b1dc30c039c6cae55a110af1e7d9f48de350f))

Rewrite the TUI guide for the six tabs, the self-contained-tab rule, the `q`-always-quits
  convention, and the new Execution, Report, Diff, and Settings behaviors. Document `comparo exec`
  and `comparo doctor` in the CLI reference, and fill the configuration reference with the diff-mode
  vocabulary, the interpolation grammar variants, the Environment fields, Instances, readable
  durations, and the per-environment transport tuning. Update the README kinds table and
  architecture.

Adds the design record under docs/design — the settings and execution/report mockups, and the
  rulebook the build is validated against.

- Readme disclaimer
  ([`01db3bc`](https://github.com/wbenbihi/comparo/commit/01db3bc1b0d6c5b82f7a348647a635f8d90cb653))

- **cli**: Correct the scaffolded AGENTS.md field lists
  ([`f123744`](https://github.com/wbenbihi/comparo/commit/f12374464f1aca6a67d1e81705dd6a4c61a19538))

cookies and auth are Request fields, not Environment; the Environment carries secrets and headers. A
  $val injects a whole Instance value with no sub-path.

- **examples**: Add the postman-echo and canary example projects
  ([`2061451`](https://github.com/wbenbihi/comparo/commit/20614514bfdf7b7712cbd70110746ac10035b91c))

postman-echo-project mirrors the full Postman Echo API (61 objects) as an exhaustive HTTP-coverage
  reference; canary-project is the curated stable-vs-canary diff showcase (34 objects). Both run
  against the live postman-echo host.

- **examples**: Auth, matrix, cookies, and execution-profile showcases
  ([`e31fe35`](https://github.com/wbenbihi/comparo/commit/e31fe35dcb9f994fc34c6f526df41fb555fc67fd))

Add an auth-project (env-level and per-request Basic/Bearer auth with secret refs, a composing
  AssertionProfile, and an assert-only ExecutionProfile). Show matrix values injecting into the
  endpoint path, form and raw request bodies, and a per-request cookie jar in the postman-echo
  project. Add ExecutionProfile and AssertionProfile definitions across the example projects,
  including canary coverage for the redaction invariant.

- **examples**: Correct the /stream note and add a Server-Sent Events project
  ([`b624ce2`](https://github.com/wbenbihi/comparo/commit/b624ce2a4100fed2a6dc1270aa9a6eafb382f4b5))

The postman-echo stream example claimed /stream/N for N>1 is "not valid JSON" and used N=1; that's
  wrong — the parser splits back-to-back objects with raw_decode, so the example now uses /stream/3
  (verified end to end with run and diff).

Adds an sse-project that actually exercises the text/event-stream path: a bundled local server whose
  two feeds differ by one event (a deterministic diff that pinpoints the drift), plus a `live`
  environment pointing at the public sse.dev feed, bounded by streamMax. Frames carry id, event, and
  data; a parser test locks in id/retry/comment handling.

### Features

- Add config model, project loader, and validate command
  ([`662cf87`](https://github.com/wbenbihi/comparo/commit/662cf875a8869fcf635e29566c23f7ca83353873))

- Add HTTP execution engine and run command
  ([`d83bdfa`](https://github.com/wbenbihi/comparo/commit/d83bdfafba91de83176a2cbcaa5bcc50439e56f9))

- Add matrix expansion and per-cell execution
  ([`4814394`](https://github.com/wbenbihi/comparo/commit/481439472ced8adc841d4a332dea6a1271048b6d))

- Add reporters, CI gate, and GitHub Action
  ([`46fbe45`](https://github.com/wbenbihi/comparo/commit/46fbe45a4f01c5d4affcccd7c70d6ed40f9acd82))

- Add resolution, interpolation, and the render command
  ([`6ea894b`](https://github.com/wbenbihi/comparo/commit/6ea894b4e9d0facb13be744d967876d2140b9b74))

- Add the tri-state diff engine and diff command
  ([`5408a99`](https://github.com/wbenbihi/comparo/commit/5408a993d3c707b29989db4893fd6ef664f1fb2d))

- Add the TUI shell and Explorer screen
  ([`9b5432c`](https://github.com/wbenbihi/comparo/commit/9b5432cad741875635069b02d93218a788354bf1))

- **adapters**: User config, opt-in PyPI update check, and the redaction self-check
  ([`c5f515a`](https://github.com/wbenbihi/comparo/commit/c5f515a9deb81699a996f3755042b21c857d93db))

userconfig persists app-level preferences (theme, diff default, the update-check toggle, startup
  prefs) to ~/.config/comparo/config.toml — XDG-respecting, read with the stdlib tomllib and written
  with a tiny emitter, so no TOML-writer dependency is added. A malformed file degrades to defaults.

updates is the one outbound call comparo makes for itself: a best-effort GET of PyPI's public
  version JSON, compared with packaging. It is opt-in, sends no telemetry, and never raises — any
  network or parse error resolves to "no update".

doctor runs the never-leak self-check: it writes a minimal canary project, pushes the canary through
  every sink (display, saved runs/reports, the four reporters, curl, crash report), and returns a
  pass/fail per sink. It never raises.

- **cli**: Add init and help, default to the TUI, and select projects with --config
  ([`0aa5e23`](https://github.com/wbenbihi/comparo/commit/0aa5e23111f0a849961de5837acf688c20ee5e3a))

comparo init scaffolds a comparo.yaml plus a .comparo/ starter (prompts for a name, refuses to
  overwrite existing files). comparo help prints the command reference. A bare comparo opens the TUI
  on ./comparo.yaml. Every command now takes --config (default comparo.yaml, a manifest file or a
  directory); the positional project argument is gone.

- **cli**: Drop an AGENTS.md authoring guide and schema modeline on init
  ([`5e38909`](https://github.com/wbenbihi/comparo/commit/5e38909c173516ac338989f44d5f2e231229e0d8))

`comparo init` now writes an AGENTS.md into the project — a compact authoring guide (the object
  model, the ${...} grammar, diff modes, the $secret rule, and the author -> validate -> fix loop)
  so any coding agent working in the project is instantly competent. The starter environment and
  request carry a `# yaml-language-server: $schema=...` modeline pointing at the shipped JSON
  Schema, so editors autocomplete and validate them out of the box.

- **cli**: Import a project from an OpenAPI 3.x document
  ([`8f3942a`](https://github.com/wbenbihi/comparo/commit/8f3942a08d142f36a3efaf2875984ff5b9caed50))

`comparo import openapi <spec>` scaffolds a project from an existing OpenAPI 3.0/3.1 document:
  servers become Environments (two or more become a diff pair), operations become Requests (method,
  path, query, a body stub, the 2xx status and schema ref), components.schemas become Schemas, and
  securitySchemes become auth stubs. Every credential is a $secret reference sourced from an env-var
  placeholder — never a literal. It is a scaffold: no DiffProfile is generated (which fields are
  volatile is your call), so it prints the next steps. The spec parsing lives in an adapter so core
  stays pure; every emitted object is validated against the models before it is written.

- **cli**: Let comparo exec write report artifacts
  ([`3a1c93a`](https://github.com/wbenbihi/comparo/commit/3a1c93ae07534e69254f2e7c9caf17cc2538c90a))

comparo exec gated CI but could not emit the junit/sarif/json/markdown artifacts that comparo diff
  writes, so a release gate had no machine-readable output for GitHub code-scanning or test
  reporting.

Add build_execution_report(result) in the execution planner: it maps an ExecutionResult to a
  RunReport whose gate matches ExecutionResult.passed — a cell that failed only its assertions (no
  drift) still fails the report, its failed error-severity rules rendered as drift rows tagged
  assert[<env>]. Wire --report/--output onto exec through a shared _emit_reports helper (also used
  by diff, replacing its inline copy) that honours the manifest's report defaults. Advisory warn
  rules never gate the artifact.

- **cli**: Surface OpenAPI-import warnings for skipped external refs
  ([`601fa44`](https://github.com/wbenbihi/comparo/commit/601fa44aad3dfd08bc3e13db1a4894d96505504a))

The importer records a warning when it cannot follow an external $ref; print those on the import so
  the user knows requests were skipped.

- **cli**: The exec and doctor commands
  ([`08ebd12`](https://github.com/wbenbihi/comparo/commit/08ebd12eb769a060f764c06276e7da165fe97775))

`comparo exec <id>` runs an ExecutionProfile headlessly — asserting both environments and diffing
  the pair — and exits 0 only when the gate passes.

`comparo doctor` runs the never-leak self-check, printing a line per sink and exiting non-zero if
  any sink leaked the canary, so CI can guard the redaction invariant. It needs no project — it
  builds its own canary scenario.

- **core**: Add AssertionProfile and ExecutionProfile object kinds
  ([`bbcfc87`](https://github.com/wbenbihi/comparo/commit/bbcfc87405ae625dc7234ecebf975d7ec9087468))

AssertionProfile carries composable response-assertion rules (target / op / value / severity, plus
  an include list); ExecutionProfile is a run plan — a selection, per-matrix
  include/exclude/override, explicit baseline/candidate environments, assert/diff toggles, profile
  overrides, and report config. Both join the object union and must declare metadata.id.

- **core**: Assertion & execution profiles, redaction backstop, and response archive
  ([`8d07ae0`](https://github.com/wbenbihi/comparo/commit/8d07ae0101bfc3a90cdbc9fa62b660718cc52715))

An AssertionProfile evaluates composable assertions (status, body, latency, schema, ...) against a
  single response; an ExecutionProfile runs one declarative pass that asserts BOTH environments and
  diffs the pair behind a single gate.

A whole-value Redactor is added as the never-leak backstop: it masks any declared secret value found
  anywhere in a string that leaves the process — even when a server echoes it back, and even when it
  appears as a JSON key or a field path — so the DISPLAY sink is no longer the only guard. The
  core-no-HTTP contract is tightened to forbid requests/aiohttp/urllib3/httpcore alongside httpx.

Responses are archived as ReportRecord/CellRecord (redacted before write) so a saved run or diff can
  be replayed faithfully from disk. The diff and compare layers carry the extra baseline response
  metadata the replay needs.

- **core**: Bound a streaming read with streamIdle and streamMax
  ([`5cecee7`](https://github.com/wbenbihi/comparo/commit/5cecee7e6c31a9c6efb9ffd7bc198d1d5129c198))

A streaming response is now read under two optional timeouts so an open feed can never hang the
  engine. streamIdle ends the stream gracefully after a quiet gap (the httpx read timeout, caught
  rather than raised) — good for a bursty feed that goes silent. streamMax is a total wall-clock cap
  (via asyncio.timeout) that stops the read regardless of activity — needed for a steady public SSE
  feed that emits on a timer and never idles. Whatever arrived is parsed and diffed. streamIdle was
  previously declared but never enforced; both are now wired through TimeoutBudget.

- **core**: Carry the parsed response bodies on CellDiff
  ([`33dee79`](https://github.com/wbenbihi/comparo/commit/33dee7941fe46ce73a3c87499849713644f43ef4))

A diff front-end can now render a body-level diff from the two trees. The bodies are populated only
  for JSON cells and are kept out of the serialized report.

- **core**: Evaluate an AssertionProfile against a response
  ([`7f37930`](https://github.com/wbenbihi/comparo/commit/7f379307e276760889e5a0638ee59fd2149bcde1))

A new assertion sink alongside the diff: a rule targets status, latency, a header, a content-type,
  the raw body, or a body JSON-path, and applies an op (equals, matches, lt/lte/gt/gte, between,
  oneOf, exists, contains, schema). The error/warn severity decides whether a failure fails the
  gate. Included profiles compose (with a cycle guard), and response.status/response.schema compile
  into implicit rules so the engine is the one place pass/fail is decided.

- **core**: Export a run's results to masked JSON
  ([`4da8156`](https://github.com/wbenbihi/comparo/commit/4da8156b93866e81062cdeb260896a4d9dceec57))

- **core**: Find a diff profile's file without writing to it
  ([`93f5fe8`](https://github.com/wbenbihi/comparo/commit/93f5fe8ff773d8a0b6c25225e2e0fcb969f4db98))

Add profile_path() so a caller can name the file a triage write would touch before committing to the
  write; silence() now reuses it to locate the file.

- **core**: Let a matrix inject into the endpoint path
  ([`1b84e4e`](https://github.com/wbenbihi/comparo/commit/1b84e4e16010a82837753aec125d0028e14d8445))

A matrix whose target is request.path fills ${key} placeholders in the endpoint template, so
  /status/${code} matrixed over codes resolves to /status/200, /status/404, and so on — the gap the
  postman-echo project worked around with a file per code. The path injection is recorded in the
  provenance trail.

- **core**: Load a comparo.yaml manifest, and accept fractional diff tolerances
  ([`b6f4054`](https://github.com/wbenbihi/comparo/commit/b6f4054b5f55e3a45800747d17d1dd3bd39c0c73))

load_project now accepts a manifest file (objects load from spec.data, defaulting to the file's own
  directory) in addition to a project directory. It also unwraps ruamel's ScalarFloat before strict
  conversion, so a DiffProfile with a fractional tolerance such as 0.01 loads instead of being
  rejected by msgspec.

- **core**: Make the manifest config surface strict and validated
  ([`a440e24`](https://github.com/wbenbihi/comparo/commit/a440e245fb84b29c0be8a1c7a7c1effbeefa5840))

- M14: replace the all-Any ProjectSpec interiors with forbid_unknown_fields structs
  (EnvironmentsConfig, DiffPair, RunConfig, RetryConfig, SelectionConfig, ReportConfig,
  RedactionConfig), so a mistyped manifest key is a hard load error and the exported JSON Schema
  constrains it. Migrate every reader (resolve, archive, TUI project/settings views) to typed
  attribute access. - H8: constrain a Matrix target to query/body/path so a typo can't silently
  no-op. - H9: a request's matrix entry must be a {$ref: Matrix}; a bare or wrong-kind ref is a load
  error, not a silently-dropped matrix. - M11: a $val must resolve to an Instance, not silently to
  None on the wire. - M12: on a same-path diff-rule tie the later-loaded rule wins, so an execution
  override can re-check a path the request profile ignored.

Regenerate the committed JSON Schema.

- **core**: Per-request cookies and separate baseline/candidate cookie jars
  ([`91ee6a5`](https://github.com/wbenbihi/comparo/commit/91ee6a5130ae5e2d680a6a9284e30f3df42236f4))

A request can send cookies (name->value, resolved through the sink so a secret cookie is masked in
  display and real for execution). And diff_run / run_execution take an optional candidate_client so
  the baseline and candidate no longer share one cookie jar — the TUI and CLI now pass a second
  httpx client, so a stateful cookie set on one side never bleeds into the other.

- **core**: Probe environment health through the HTTP port
  ([`b689504`](https://github.com/wbenbihi/comparo/commit/b689504174a60a236199d5d4225a25b5c71fb0e6))

- **core**: Render a resolved request as a curl command
  ([`186be64`](https://github.com/wbenbihi/comparo/commit/186be64119e8ddbe69f1bec7ae3212de4bb1dc24))

- **core**: Request body encodings and first-class Basic/Bearer auth
  ([`4ebfdf3`](https://github.com/wbenbihi/comparo/commit/4ebfdf33f227a19dab27e1c602d212453c2a5d37))

A request can set bodyType (json, form, raw), so form-urlencoded and raw bodies are finally
  expressible — the httpx adapter maps them to json/data/content. And auth (basic {username,
  password} or bearer) on a request, or an environment-wide default: the password/token resolves
  through the sink — masked in the display sink, real for execution — and the adapter applies
  httpx.BasicAuth or an Authorization header.

- **core**: Resolve profiles by $ref, inline, or a composing list
  ([`9f249f5`](https://github.com/wbenbihi/comparo/commit/9f249f57302cd6b9d5e780ef74ccc37923fb91eb))

A shared resolver (refs.resolve_specs) lets response.diff, response.assert, and an
  ExecutionProfile's profile overrides each be a $ref, an inline spec written in place, or a list
  mixing both — so a project can live in a single file. The diff now composes the
  request/project/execution diff profiles (rules concatenate, the last default wins) and the planner
  composes assertion profiles the same way.

- **core**: Run an ExecutionProfile — assert both environments and diff them
  ([`fc5053c`](https://github.com/wbenbihi/comparo/commit/fc5053cf721c0a30f8b23e3d68de8cc9d3f9850f))

A planner resolves an ExecutionProfile to a concrete plan (select requests by tag/id, scope each
  matrix via include/exclude/override, resolve the baseline and candidate environments), executes
  every cell against both, runs the request's composed assertions on each, and diffs baseline
  against candidate — folded into one gated ExecutionResult. Adds response.assert, a scoped matrix
  expand, a public compare_cell, and compose_rules.

- **core**: Silence a drift by writing an ignore rule into its DiffProfile
  ([`5ed5add`](https://github.com/wbenbihi/comparo/commit/5ed5addad18606edd866d84b016f307c06c3aaa1))

- **core**: Stream responses for real and diff the event sequence
  ([`f87a7c7`](https://github.com/wbenbihi/comparo/commit/f87a7c7bb9798b32cabc8d804cfd432dc9a292c0))

When response.streaming is set, the httpx adapter now streams the response, capturing the ordered
  records — parsed SSE events, or the JSON objects of a chunked stream — via a new core stream
  parser. The diff compares that event sequence rather than flattening it to bytes, so a streamed
  drift reads as "event 3 changed". Non-streaming requests are unchanged.

- **core**: Validate responses against expected status and JSON schema
  ([`28f5f0d`](https://github.com/wbenbihi/comparo/commit/28f5f0d2be40866933044120f92d3d9eb2f6ee6c))

- **core**: Wire every documented run/report/redaction knob
  ([`cdf539e`](https://github.com/wbenbihi/comparo/commit/cdf539e4634ef912981c6d4cfc0f3708df3ec89b))

Make the manifest's documented config surface real instead of decorative: - run.concurrency now
  bounds execute_all, diff_run, and run_execution (exec becomes bounded-concurrent, preserving
  outcome order) and the TUI loops. - run.retry retries transport failures with
  constant/linear/exponential backoff; resolution errors are never retried. -
  selection.tags/requests is the default request filter for headless run/diff. -
  report.formats/output default the --report/--output flags for diff. -
  redaction.stringMatchBackstop can disable the display/report backstop (the on-disk runs export
  always masks regardless — a saved artifact never leaks). - H20: LoadedProject carries root
  (project dir) and data_dir separately, so the archive lands at <data>/.reports instead of a
  doubly-nested path and $file secrets resolve against the project root.

- **schema**: Export the comparo/v1 JSON Schema
  ([`26b2aed`](https://github.com/wbenbihi/comparo/commit/26b2aeddc26b68746f9c3d1bdcefde555de9782e))

`comparo schema` emits a JSON Schema of the config, generated from the same msgspec object models
  the loader validates against, so it can never drift from the real surface. A copy is shipped at
  schema/comparo-v1.schema.json — point an editor's YAML language server at it for autocomplete and
  inline validation, or hand it to an agent authoring config to then check with `comparo validate`.

- **tui**: '/' text filter for the run tables and prepare tree, shown on the panel
  ([`69233b3`](https://github.com/wbenbihi/comparo/commit/69233b30afc2c756ca3fae1dac1b5ecb63fa3ebd))

- **tui**: Add filter, reference graph, and error-recovery screens to the Explorer
  ([`2dfc664`](https://github.com/wbenbihi/comparo/commit/2dfc6645dab82ecdaa5d6d98ebec4bffa11a90a2))

- **tui**: Add the Execution Screen, launched from the Explorer
  ([`c5468a7`](https://github.com/wbenbihi/comparo/commit/c5468a71c59cb79dc94f2c964f8e238972a8ccbe))

Selecting an ExecutionProfile in the Explorer and pressing Enter runs it — both environments
  asserted and diffed — and opens a full-screen execution report: a per-cell list with assert and
  diff status, a detail panel that drills into the baseline and candidate assertions plus the
  git-style body diff (v flips unified vs side-by-side), and a unified gate. esc returns to the
  Explorer.

- **tui**: Build the Run, Diff, Report, and Settings screens
  ([`5b56963`](https://github.com/wbenbihi/comparo/commit/5b5696320e842c5edd05d81c397ca32478e017ab))

- **tui**: Confirm before silencing a drift so triage never writes silently
  ([`023920c`](https://github.com/wbenbihi/comparo/commit/023920ce32183a428e05e745f942aa4296a88595))

Pressing i on a drifted field now opens a confirmation naming the field and the exact DiffProfile
  file(s) the ignore rule would be written to; the write happens only on confirm. Nothing is written
  when the modal opens or is cancelled.

- **tui**: Curl preview with matrix-case picker, real-secret copy, and toast notifications
  ([`c02222b`](https://github.com/wbenbihi/comparo/commit/c02222b3d3586d5f778689b91cb07f695b12b04a))

- **tui**: Env health, default selection, raw/resolved toggle, instance provenance, and help overlay
  ([`3f39570`](https://github.com/wbenbihi/comparo/commit/3f39570ff50ef6a92da78cb2eb99a35f48e4ea23))

- **tui**: Execution & report tabs, git-style diff, per-cell views, and settings
  ([`f2ec0d9`](https://github.com/wbenbihi/comparo/commit/f2ec0d94b237074b0d4d1e98eda6558459888c9c))

The Execution tab is a self-contained flow (launch, running, results, cell, and in-flow diff) that
  runs an ExecutionProfile without ever redirecting to another tab. The Report tab replays saved
  diffs and runs through the live Diff and Run panels. The git-style body diff is reworked into a
  full-width rounded panel with a banded purple hunk header, matched across unified and
  side-by-side.

Per-cell detail is switchable (Request / Response / Headers / Raw, `t`); the Diff adds an
  outbound-request diff (`o`) that resolves the request against both envs to prove drift is the
  service's; the prepare screens spell out the call arithmetic and preview the equivalent CLI
  command.

A new app-level Settings tab carries nine sections — About, a read-only Project summary, Security &
  Redaction (a live self-check on `t`), Appearance, Keybindings, Updates & Privacy, Plugins, Engine,
  and Behavior — with preferences persisted to the user config. `q` always quits, never back/close;
  `esc` / backspace is back.

- **tui**: Failures-only filter on the run tables, shown on the panel
  ([`de8eaff`](https://github.com/wbenbihi/comparo/commit/de8eaff43fc7dde405c1bc812524117be84281fe))

- **tui**: Full-fidelity Explorer and Diff to the comparo-ink design
  ([`34e6706`](https://github.com/wbenbihi/comparo/commit/34e67063eb2b721a621e25733b29a1b8bb241dee))

- **tui**: Git-style body diff, inline env pickers, and a broken-rules toggle
  ([`e68a923`](https://github.com/wbenbihi/comparo/commit/e68a923748a35e23e785abec964c210eb61000e8))

The Diff screen's compare panel now renders a git-style JSON body diff — drift as -/+ red/green,
  SAME dimmed, a profile-ignored node collapsed to a greyed "skipped" line — with `v` flipping
  unified vs side-by-side. `b`/`c` pick the baseline/candidate environment inline (seeded from the
  project's environments, no diffPairs required) and Run's `e` picks the run env, both via a new
  EnvPickerModal. `r` toggles the drift index between grouped-by-field and broken-rules (which rule
  fired on which field).

- **tui**: Interactive Run screen — select cells, matrix-case picker, live status, and per-cell
  deep-dive
  ([`9e00452`](https://github.com/wbenbihi/comparo/commit/9e004529312f7af4297601addebb0e4b23a1464c))

- **tui**: Make 'm' a global matrix-value picker across all requests (multi-matrix)
  ([`0d91011`](https://github.com/wbenbihi/comparo/commit/0d91011b632e388b9b2e226db6f28ebf3c3c0fc2))

- **tui**: Make Settings a navigable category overview (project, envs, run, diff, appearance,
  plugins, engine)
  ([`b107a8a`](https://github.com/wbenbihi/comparo/commit/b107a8ad8130ee768ac447f9aa2e169dc2668702))

- **tui**: Navigable JSON/HTML detail tree with a maximize key
  ([`400f39a`](https://github.com/wbenbihi/comparo/commit/400f39a3360203a2dfb467bf7a9ef975883e4581))

- **tui**: Parse SSE responses into a navigable event tree in the detail panel
  ([`d5dbabf`](https://github.com/wbenbihi/comparo/commit/d5dbabfb6cb803b6ca72602822245fa857d25c71))

- **tui**: Per-state Run footer and consistent, fully-documented keybinds
  ([`7c0ebcb`](https://github.com/wbenbihi/comparo/commit/7c0ebcb823289e948622241bb6848d014f2c1dff))

- **tui**: Rebuild Diff as the signature — drift grouped by field, tri-state comparison,
  triage-to-profile
  ([`f3953e1`](https://github.com/wbenbihi/comparo/commit/f3953e153106acde592743db996b0a9d7e5a0d86))

- **tui**: Rebuild Report as the CI pillar — verdict gate, stat pills, breakdown, and exporters
  ([`d30dcdc`](https://github.com/wbenbihi/comparo/commit/d30dcdc8d1b05daeb4eea0d60d5b633c9f8f421b))

- **tui**: Rebuild Run as a requests → cells → report table drill-down with checks
  ([`476c911`](https://github.com/wbenbihi/comparo/commit/476c91174872421fe28700b6a4ae56944f00c07d))

- **tui**: Show the project manifest as a root node and make tab keys AZERTY-friendly
  ([`6d524c1`](https://github.com/wbenbihi/comparo/commit/6d524c1ad9a516510e10023f5247b4fc175e6634))

- **tui**: Split Run into Prepare/Running states with dynamic columns, run id, progress, abort, and
  masked save
  ([`0857d8b`](https://github.com/wbenbihi/comparo/commit/0857d8b80830362cbadd0aae39797cc66d94cad4))

### Refactoring

- Use the core SSE parser everywhere, deleting the TUI copy
  ([`35af51e`](https://github.com/wbenbihi/comparo/commit/35af51e2e880e40f3d0ca006ef4362a641376d91))

The TUI carried a second Server-Sent-Events parser that dropped every field except
  event/id/retry/data and diverged from core/streams on whitespace-only separator lines. Promote
  core's parser to a public parse_sse and use it in the detail-tree renderer, so the diff engine and
  the response viewer can never disagree about what a stream contains.

- **core**: Collapse six identical _ref_id copies into one refs.ref_id
  ([`c6cdee0`](https://github.com/wbenbihi/comparo/commit/c6cdee0add78e09632f4ec5cc5f2047ad2eeb9c7))

The `{$ref: id}` extractor was defined byte-for-byte identically in matrix, compare, checks,
  execution, assertions, and the TUI. Add a single public `ref_id` in core/refs.py and import it
  everywhere.

- **core**: Make the Run tab's checks delegate to the assertion engine
  ([`35e46b9`](https://github.com/wbenbihi/comparo/commit/35e46b998222d4fcac5c059ddc0c5c0ab45c0189))

checks.py was a second response-validation engine — it re-implemented status comparison and
  JSON-schema validation that assertions.py already owns, and (like the old run path) it ignored a
  request's response.assert block. So the TUI Run tab could show a request green while the CLI
  failed it.

Gut checks.py into a thin boundary adapter: run_checks now compiles the whole response contract
  through request_response_rules + evaluate_rules and flattens the error-severity results into the
  same reachable/status/schema Check rows, gaining response.assert support. The {name, ok, detail}
  saved-run JSON shape is unchanged (it is write-only — nothing decodes it back), and passed()
  semantics are preserved because only error-severity rules become gating rows; advisory warns ride
  the Execution and Report tabs.

- **tui**: Derive the self-check sink list from doctor._SINKS
  ([`531bdfb`](https://github.com/wbenbihi/comparo/commit/531bdfb04ca6858e7d942eda5b0070510d330be5))

The Settings Security panel hand-maintained a second copy of doctor's nine (name, detail) sink rows.
  Expose doctor.SINK_LABELS and use it, so the two can never drift.

- **tui**: Split the 8k-line app.py into tokens + render + app
  ([`5124d96`](https://github.com/wbenbihi/comparo/commit/5124d9603073ca75e4948be952ebe2f5ce83c68f))

Pure, behavior-preserving decomposition of the God file (8,130 -> 4,571 lines): - tui/tokens.py: the
  63 module-level design/data constants (palette, method/ mode/kind maps, footer-hint tuples, help
  data). - tui/render.py: the 137 pure render/format/lookup helpers + the two helper classes
  (_RunningRow, _HtmlOutline). - tui/app.py: now only the 19 view/modal classes and ComparoApp,
  re-exporting the moved names so every existing import (and test) keeps working.

The dependency direction is one-way (constants <- helpers <- views), verified by AST analysis before
  and after, so there is no import cycle. Full gate green, 313 tests unchanged.
