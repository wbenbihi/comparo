# CHANGELOG


## v0.2.0 (2026-07-22)

### Bug Fixes

- **ci**: Pass the version input through env, and scrub .gitignore comments
  ([`2d345c1`](https://github.com/wbenbihi/comparo/commit/2d345c1685cc018896fd83193e38c0ce2bf00ca3))

S-3. The install step interpolated ${{ inputs.version }} straight into the bash `run:` line — a
  crafted version input could inject shell. Pass it through `env:` (VERSION) and reference
  "$VERSION", matching the diff step's pattern.

S-6. Rewrite the .gitignore comments that named a private NDA engagement and the internal
  code-health report into neutral descriptions, so the public repo does not disclose them.

- **cli**: Fail closed when a run expands to zero cells
  ([`12da68c`](https://github.com/wbenbihi/comparo/commit/12da68c914f75b58b6dc68dfc9e043cd8a0a2ff4))

M-1. `comparo run` seeded its gate with ok_all=True and returned it, so a run whose selected
  requests all expanded to zero matrix cells (an empty `matrix.values`) verified nothing yet exited
  0 with a green gate. Guard the empty plan at the command (exit non-zero with a message) and make
  _print_results fail closed on an empty result set, mirroring diff_passed / ExecutionResult.passed.

- **cli**: Report an unresolved variable in render instead of crashing
  ([`0060b9b`](https://github.com/wbenbihi/comparo/commit/0060b9b1f05941ca3b2b941ed16dfd10240fb17c))

M-3. `comparo render` called resolve_request without a guard, so a required ${VAR} left unset raised
  InterpolationError straight out of the command as a traceback. Catch
  InterpolationError/SecretError and surface a clean message with a non-zero exit, like the other
  commands.

- **execute**: Capture a malformed URL as a cell error, not a run abort
  ([`a11c3c5`](https://github.com/wbenbihi/comparo/commit/a11c3c53d21a2c064be839f20a8031a780c812fe))

M-4. build_request runs before the try, so an httpx.InvalidURL from a malformed resolved URL
  propagated out of send() and aborted the whole gather — one bad URL killed every other cell's
  result. Move build_request inside the try and catch httpx.InvalidURL alongside httpx.HTTPError, so
  a bad URL becomes this cell's captured error like any other transport failure.

- **execute**: Drop an unset optional instead of sending "None"
  ([`0fe0ba8`](https://github.com/wbenbihi/comparo/commit/0fe0ba87f4fa08d1a45626f7c9ba7860998de732))

M-2. A header/query/cookie whose value resolved to None — an unset ${VAR?} — was stringified to the
  literal "None" and sent on the wire (and serialized into the report). Omit any None-valued header,
  query param, or cookie both at send time (httpx_client) and in the outbound record
  (report_builder), so an unset optional is simply absent, as intended.

- **execute**: Fail closed on a response body over the size cap
  ([`6ab30d0`](https://github.com/wbenbihi/comparo/commit/6ab30d02f4e76b1b87c58dc8bb453459ae9880ee))

S-1. A body exceeding the 64 MB cap was silently truncated (the read broke and returned the prefix),
  so a diff compared a cut-off body as if it were the whole response — hiding any regression past
  the cap. Raise an HttpError instead, so the cell fails with a clear message rather than a
  misleading same/drift verdict.

- **loader**: Confine spec.data to the project root
  ([`a8c9e52`](https://github.com/wbenbihi/comparo/commit/a8c9e52122968873a96cfcab1a312ef0b250ece7))

S-2. spec.data was resolved and globbed without a containment check, so a `../..` or absolute path
  made comparo scan and parse YAML from anywhere on disk. Refuse a data dir that escapes the project
  root up front, before any glob, so nothing outside the project is ever read.

- **redaction**: Build one redactor per project and fail closed on unreadable secrets
  ([`955541a`](https://github.com/wbenbihi/comparo/commit/955541aed633936c57b205f7e46ffd93116e0696))

H-3. The DISPLAY-sink redactor was rebuilt — re-reading every declared secret file — at ~35 render
  sites per frame. Build it once as a cached_property on ComparoApp and route every site through
  _app_redact, which now backs onto it.

Also close the silent mask-shrink: environment_secret_values swallowed every SecretError, so a
  declared $file that became unreadable at redaction time (after its value may already have been
  echoed into a response) was quietly dropped from the mask. Split the taxonomy —
  SecretUnavailableError marks a benign absence (an unset $env, an exhausted `from` chain) that is
  safe to skip, while an unreadable or root-escaping $file now propagates and fails the render
  loudly.

- **redaction**: Cap redact_tree recursion depth
  ([`ac60be1`](https://github.com/wbenbihi/comparo/commit/ac60be1b74ad7c06148b6d6664df247b1e150b03))

M-5. A hostile server can send a pathologically deep body; redact_tree recursed without bound and
  could overflow the stack. Cap the depth (200, matching the diff engine) and, past it,
  stringify-and-redact the remaining subtree as one opaque leaf — so a deep payload can neither
  crash the process nor slip through unmasked.

- **tui**: Diff inspect fidelity — selectable rows, outlined wells, gate-tinted summary
  ([`38e77fe`](https://github.com/wbenbihi/comparo/commit/38e77fe9226d0923dd991a7d2887ad5e0a621a05))

The inspect panel gains its selectable inner rows: the broken rules on a cell, the per-cell record
  of a rule, and a field's occurrences are now a real table you tab/enter into — enter on the index
  drills into the rows, enter on a row jumps to its home pivot with the return crumb, esc steps
  focus back out before it ever pops the jump. The rows wear the mockup's bordered-box look (red for
  broken, green for a passed rule's record).

Both wells get the rounded-outline chrome back — the response-headers well now matches the body well
  (hunk band, banded -/+ rows, muted background, rounded border) and v flips BOTH between unified
  and side-by-side. The outbound band explains itself ('we sent DIFFERENT requests — some drift may
  be ours' / 'same request sent to both sides — any response drift is the service's') instead of a
  bare press-o hint. The summary band's border now tints with the gate: green PASS, red FAIL, amber
  ERROR.

- **tui**: Drive the live run counter from real per-cell verdicts
  ([`22ce143`](https://github.com/wbenbihi/comparo/commit/22ce143546f0dd32969d4fe3745d857cf4312831))

H-1. The running view maintained a per-cell glyph array but only ever wrote "○/◐/●"
  (pending/in-flight/done), while _running_body tallied "✓/✗" — so the live counter stuck at "0 ✓ 0
  ✗", and the recently-finished log keyed its ✓/✗ off the drift string alone, painting an
  errored-but-undrifted cell green.

Carry the real verdict to the view: ExecutionProgress gains `ok` (from CellOutcome.ok — no error,
  assertions hold, no drift) and the diff view computes `drifted or error`. Both now write "✓/✗"
  into the glyph array, so the tally counts real passes and failures, and _RunningRow.failed drives
  the log colour so an error or a failed assertion reads red, not only a drift.

The counter test now drives real ExecutionProgress ticks end-to-end through ExecutionView instead of
  asserting over synthetic glyphs.

- **tui**: Fix execution enter/d/esc navigation and clarify the diff
  ([`08615e9`](https://github.com/wbenbihi/comparo/commit/08615e9280fedb7dfa1102a6ff54fddbf11f60f0))

The in-flow diff's back target was inferred from a stale `self._cell`, so drilling into a cell,
  backing out to results, then pressing `d` and `esc` wrongly jumped into the old cell detail
  instead of returning to the gate.

- Track the sub-view `d` was opened from (`_diff_origin`) and return there exactly on `esc`. - Clear
  the cell selection when returning to the results overview, so no stale selection can mislead the
  back-stack. - Spell out the diff screen's scope and where `esc` lands in its subtitle.

- **tui**: Render the headers well through the one diff component
  ([`494485c`](https://github.com/wbenbihi/comparo/commit/494485cea3169a0f680f15472fdb309470403a63))

The headers well hand-rolled its own diff: unified was close, but the side-by-side styled only the
  text (no full-width bands — the washed-out look), and neither had the pane separator. Header
  drifts now become the shared diff-line shape and render through _diff_unified /
  _diff_side_by_side, so both wells carry the same banded muted backgrounds, the ⋯ skip grammar, the
  env-named pane headers, and the hairline separator. A regression test pins the shared component by
  checking the actual band colors in ANSI output for both layouts.

- **tui**: Scope the run-report detail to the selected request
  ([`7b19525`](https://github.com/wbenbihi/comparo/commit/7b195255a42b88945eb6e015e04ddc7b206c75e5))

The Run report's DETAIL panel leaked every request's data into the selected request's view, so
  navigating the request list up/down never changed what mattered:

- The "checks" section rendered the record-wide assertion roll-up (record.baseline_assertions) —
  every request's checks at once, e.g. hundreds of "status equals 200". Project per-cell baseline
  assertions onto ReplayCell and scope the checks to the selected request's cell. - The request
  object showed only method + path; project the outbound request's headers and body so the "request"
  subtree is actually filled. - The HTTP method ignored the shared method palette (hardcoded green);
  render it as a coloured badge via _method_badge / _METHOD, matching the rest of the UI.

- **tui**: The event-sequence diff follows the unified/side-by-side toggle
  ([`a0ce45e`](https://github.com/wbenbihi/comparo/commit/a0ce45e2ce1126c07902f3ce209e674a95461ea0))

The stream (SSE) per-event data diff always rendered unified — v flipped the JSON/headers wells but
  left the events stacked. Thread unified + the env names through _stream_body_view ->
  _event_expanded so the opened event's data diff draws two named panes in side-by-side, exactly
  like the body well; the non-JSON data case now routes through the same renderer too, so it honors
  the toggle as well. Live and replay callers both pass the flag.

- **tui**: Tighten the Run/Diff summary bar spacing and honor the method palette
  ([`bd1a9d4`](https://github.com/wbenbihi/comparo/commit/bd1a9d4e2d709e5821f190cf37551faac97520ba))

Two spacing bugs pushed the summary bar and left a gap: - the Diff CSS selector bundled
  #diff-mode/#diff-results/#diff-running into the padding:1 0 0 0 that was meant only for
  #diff-prepare, so the results pane inherited TWO extra top rows — the summary bar sat at y=6
  instead of y=4. Scope the centering+padding to #diff-prepare only (mirroring Run) and give the
  panes height:1fr. - both summary bars carried margin-bottom:1, a dead row between the bar and the
  columns. Drop it — the gap below is now 0.

And the RUN requests table hardcoded _ACCENT for every HTTP method instead of the shared _METHOD
  palette (GET green · POST blue · PUT amber · DELETE red) that Diff and Explorer already use —
  fixed in both the wide and compact rows.

### Chores

- Dead-code removal and small robustness fixes
  ([`4993eb8`](https://github.com/wbenbihi/comparo/commit/4993eb8a399278e58e999105aa2d8b835423a641))

- Remove the never-called redaction.identity (callers pass str). - Drop the unreachable
  unknown-format branch in _write_reports (_emit_reports already rejects unknown formats). - Load
  .yml object files, not only .yaml. - `schema -o` creates the output's parent directory instead of
  crashing. - save_user_config writes atomically (temp file + rename) so a crash mid-write can't
  leave a corrupt config on disk.

- Sync uv.lock with the 0.1.0 version bump
  ([`9665639`](https://github.com/wbenbihi/comparo/commit/9665639880ce8afd5c1b1817ba25004561ec99b0))

- **schema**: Regenerate the shipped config schema for report.retention
  ([`b97bdf9`](https://github.com/wbenbihi/comparo/commit/b97bdf92a32e8c8e3e4885ffdbc6a393dbe8b47f))

### Documentation

- Correct the Environment cookie note and the action pin
  ([`a036e11`](https://github.com/wbenbihi/comparo/commit/a036e11eff501a371c47cc22436e7bf3fb091e9d))

- Cookies are a Request-level field, not an Environment one — drop them from the Environment row in
  the README object table. - Pin the GitHub Action example to @main with a note to switch to a
  released tag, since no v1 tag exists yet (pre-alpha).

- Replace the code health report with the post-remediation re-audit
  ([`cc4fe31`](https://github.com/wbenbihi/comparo/commit/cc4fe311fcc4dc47ad0100d15db69005e117a562))

The remediation branch was independently re-verified finding by finding (wire-level repros, Textual
  Pilot). Overall grade moves C -> B+; the report now carries the prior-findings ledger, the four
  remaining A-grade blockers, and a phased roadmap.

- **examples**: Add the results showcase — every state on two local servers
  ([`05a7b91`](https://github.com/wbenbihi/comparo/commit/05a7b914e5b5490134a432c78f3b8fd57f542300))

examples/showcase runs two deliberately-different servers (serve.py, stdlib only) and a project that
  exercises every state of the results screens: the flagship taxRate drift across three matrix
  plans, a header drift, a tolerance-absorbed total, volatile built-ins, a user header rule, a
  deliberately unused rule, a drifting event stream, an immediate transport error (a truncated
  response, not a slow timeout), a $status drift, clean cells, the ⊘ roster, an always-breaking warn
  advisory for the Run tab, and HTML/binary bodies for the outline and hex renderers. The README
  maps each state to where to look.

- **examples**: Exhaustive local-server showcase for every result state
  ([`b00e02e`](https://github.com/wbenbihi/comparo/commit/b00e02ee20e52cd7fba117b41ac72f097c90f8ad))

Rebuild the showcase on a deterministic two-server serve.py (baseline :8091, candidate :8092) so
  every Diff, Run, and Execution state is reproducible offline — public services could not produce
  errors, $status flips, binary drift, or the redaction echo on demand.

- serve.py: 21 routes, one per scenario; deterministic bodies (the only volatile fields exist to be
  silenced), a truncate-and-hangup trick for transport errors, an SSE feed exercising the full
  envelope with a drifted and a dropped event, and /whoami echoing the Bearer secret for the
  never-leak demo - 21 requests covering: value/type/shape/header/$status/stream/HTML/binary drift,
  tolerance absorbed vs broken, outbound-differs, one- and both-side transport errors, clean,
  not-run; and for Run: assertion FAIL with a body anchor, schema pass/fail, warn advisory
  broke/held, latency regression, header-target and composed-profile provenance, redaction -
  assert.base + assert.order (include composition), schema.health/.contract, the diff profile's
  silences (nonce, x-build-id) + tolerance bands + one unused rule, matrix.plans, and
  execution.release-gate (assert both + diff) - README maps every state to the request that shows
  it, three tables

Verified live: diff 9 same · 12 drift · 2 error (gate FAIL), exec 6 cells · 4 drift (gate FAIL), and
  the echoed secret is masked everywhere — the raw value never reaches a report or export, only the
  mask does.

- **examples**: Showcase runs on public services — no local server
  ([`3af3406`](https://github.com/wbenbihi/comparo/commit/3af3406845ac78db4d9b05ce045f9aa810b36310))

Both environments point at httpbin.org; each injects its own variables (TAX_RATE, API_VERSION,
  STATUS, DELAY), so every drift is one we sent — the outbound band's story told with real
  infrastructure. sse.dev rides as a third environment for the SSE renderer on the Run tab. serve.py
  is gone; nothing to start, nothing to forget.

- **tui**: Note the call ledger and real-mode replay in the Report tab
  ([`092cec9`](https://github.com/wbenbihi/comparo/commit/092cec9fadcde6a0ed784b5b6139e081187a1058))

### Features

- **archive**: Bound the saved-report archive with spec.report.retention
  ([`20fa2e7`](https://github.com/wbenbihi/comparo/commit/20fa2e7364c5e62625a041b308850d8565b10e05))

The .reports archive grew without limit. Add a `retention` knob to ReportConfig and pass it as
  `keep` to save_record on every TUI save path, so the archive is pruned to the newest N records on
  write (unlimited when unset). The prune mechanism already existed; this wires it to config.

- **cli**: Distinct exit codes for gate failure vs usage error
  ([`aaec5ac`](https://github.com/wbenbihi/comparo/commit/aaec5acb2d8dded2d01c7523779106902bc179f3))

CI could not tell a real regression from a broken setup — both exited 1. Split them: exit 1 is now a
  *gate failure* (the command ran but a diff drifted / an assertion failed / a cell errored), and
  exit 2 is a *usage / config error* (the project won't load, an env/profile/request is unknown, an
  argument won't resolve, an unknown report format, or an empty plan). Docs and tests updated.

- **core**: Capture structured diff data and both executions per cell
  ([`2037113`](https://github.com/wbenbihi/comparo/commit/20371134ed513fd60fe623afa12da761e74aaaf8))

Groundwork for the v1 report, which must replay each cell in full. Three additive enrichments, all
  redacted at report-build time (they hold live secrets in memory):

- FieldDiff gains baseline/candidate (the raw before/after values) and rule (the governing
  DiffRule.path), populated at every construction site in diff.py and compare.py (_status_field
  included). - AssertionResult gains expected/actual, threaded through the shared result closure so
  every op records the declared expectation and observed value. - Execution gains resolved (the
  exact request sent, None on resolve failure); CellDiff and CellOutcome each gain
  baseline/candidate Executions, so both sides' request+response are reachable at build time even
  with no diff run.

- **core**: Capture wire metadata and move the outbound diff into core
  ([`e4a6bce`](https://github.com/wbenbihi/comparo/commit/e4a6bcee82a99c8d6cfa38f41e2e5c81853b8e71))

HttpResponse records the HTTP version and reason phrase (a raw exchange can now render its true
  status line), and Execution records how many transport attempts were made and under which retry
  policy — the error panel's 'gave up after 3 (exponential x3)' line becomes data, not guess.

The outbound request diff moves from the TUI into core/outbound.py, typed against a structural
  protocol so the same implementation serves the live pair (ResolvedRequests) and report replay
  (serialized outbound records). Bodies now diff structurally, redact-first, through the same tree
  walker as response bodies — an injected secret masks to the same glyph on both sides and never
  reads as drift.

- **core**: Lock gate precedence and share the verdict vocabulary
  ([`193a768`](https://github.com/wbenbihi/comparo/commit/193a76882a0984ef6d88fdbadd0c84b69cf2bec3))

Add comparo.core.outcomes — the single vocabulary both rule systems speak: CheckOutcome
  (held/broke/silenced/absent/error), Provenance with one display grammar, CheckTally, and Verdict +
  combine() implementing the locked precedence: FAIL whenever any rule broke anywhere; ERROR only
  when errors are the only failure; judging nothing fails closed.

Reorder every gate to that precedence (diff_gate was ERROR-first) and add run_gate/execution_gate
  beside it. Assertions auto-failed against a response-less side were never judged, so they now
  count toward the cell's error, not the gate's broken rules. Also fixes the latent empty-run PASS:
  all three kinds fail closed on an empty selection, and the report-format doc no longer contradicts
  the code.

- **core**: Make the report record fully replayable — inventories, whole bodies, one verdict
  vocabulary
  ([`54668c1`](https://github.com/wbenbihi/comparo/commit/54668c19901459babe694c5422fd6b7e68da010a))

The one pre-alpha schema edit. The record now carries everything the three Results indexes need,
  with no repetition: rule inventories stored once at record level (diff rules with their parameters
  and provenance, assertion rules with severity/label/owner, each with per-CELL outcome tallies) and
  referenced by id from every field and assertion row; SAME fields serialized path-only (values
  recovered by lookup — re-diffing redacted bodies is unsound, stored verdicts are authoritative);
  the summary split into field counts and cell-verdict counts, never mixed.

Cell verdicts collapse to pass|fail|error (+not_run roster, +advisory for green cells with a broken
  warn — never on failed cells). Sides carry attempts and the retry policy; responses carry the HTTP
  version and reason phrase and exactly one body representation: parsed JSON, events,
  redacted-then-truncated text, or a binary digest. Both the hex head AND the sha256 drop when any
  text view of the whole body trips the redactor — no hex side channel, no digest oracle
  (review-caught: the head check originally truncated first and missed non-ASCII secrets). The
  outbound layer serializes per cell via core outbound_diffs, which now masks credential headers by
  NAME (review-caught blocker: undeclared Authorization/Cookie values wrote to disk in clear); the
  provenance trail rides on every outbound request.

Tallies count one unit each (review-caught: assertion inventories counted sides as cells, phantom
  candidate sides inflated notAsserted); replay repeats stored labels verbatim and gives unjudged
  rows their own state; reporters read the cell-level error. The doctor canary now drives the
  outbound layer with a differing undeclared credential, the bodyText/binary/trail/reason/cell-error
  channels, and checks the marker in hex so a head regression cannot hide. docs/report-format.md
  rewritten to match.

- **core**: Parse the full SSE envelope per the processing model
  ([`5af2f0a`](https://github.com/wbenbihi/comparo/commit/5af2f0aaab7ce39f1c0aeb16db5befa9c8d90ef7))

parse_sse now follows the SSE spec precisely: any line ending, a leading BOM stripped, colon-less
  lines as empty-valued fields, exactly one leading space stripped, later non-data fields
  overwriting earlier ones, ids containing NUL ignored, and only a truly empty line dispatching. One
  deliberate divergence, now documented: a trailing unterminated event is kept, so a stream cut by
  the idle/total timeout still diffs whatever arrived.

This is THE stream parse — the Run tab's renderer and the diff's per-event comparison consume the
  same output (stated in the module and the architecture doc), so the tabs can never disagree about
  what an event contains.

- **core**: Retire the lossy Check adapter and evaluate assertions once
  ([`cd9c611`](https://github.com/wbenbihi/comparo/commit/cd9c611640a0a1421e25353c7fbbdf1a231cfedc))

The Run tab now renders full AssertionResults from the single evaluation made when each cell ran —
  the screen, the saved run file, and the archived report consume the same objects (action_save's
  re-evaluation is gone and pinned by test). Warn-severity rules reach the screen for the first
  time, as amber ~ advisories that never fail the run. checks.py is deleted; its invariants (H19
  inline schema, M-a response.assert parity) move to the assertion-pipeline tests.

Composition returns SourcedAssertions: every rule carries an AssertRef — target/op/severity/label
  plus provenance (owning profile id, or the request for inline sugar) with a stable within-block
  index (inline blocks index continuously so identities never collide). Evaluation stamps the ref
  onto every result, mirroring the diff engine's RuleRef. The run path now dedupes
  layered/diamond-include rules exactly like the execution planner, first occurrence keeping its
  provenance.

A dead cell is never judged: no response means no rule ran — the tree shows the synthesized
  reachable row alone (always LAST, per the run-results spec) instead of painting every rule as
  broken, and the run export carries the transport error, not fake failures. The saved-run JSON
  serializes results at full fidelity (label/target/op/severity/ expected/actual, all redacted; the
  doctor canary proves label masking). Docs: architecture table and graph, tui.md Run copy.

- **core**: Thread rule identity through the diff and compare the whole exchange
  ([`3adfe50`](https://github.com/wbenbihi/comparo/commit/3adfe5058af9f79254d2c94ee8456d4bd3484655))

Every FieldDiff now carries a RuleRef — the declared path, mode, and provenance (profile / inline /
  default / synthetic) of the rule that governed it — threaded from resolve_sources through
  composition and the walker, so rule ↔ field ↔ cell traceability lives in the data.

Response headers join the comparison as the $headers namespace: partitioned by literal prefix like
  $status (immune to body-field collisions), names case-folded (rules match in any casing),
  duplicates joined per RFC 9110 with set-cookie kept as a list, credential values masked before the
  diff ever sees them, and built-in overridable ignores for clock, counter, framing, and
  correlation-id headers so identical environments stay green out of the box.

Cells gain a rule-outcome ledger: every effective rule — including the $status synthetic, shadowed
  overrides, and both catch-alls — grades broke / silenced / held / absent per cell, with error
  cells inconclusive. unused_rules() folds by written identity across compositions to name typo'd
  paths without false positives. $status now honours the same later-loaded-wins tie-break as every
  other rule.

The redactor registers case-folded secret forms (a secret reflected as a header name reaches sinks
  lowercased) and the doctor's leak check is now case-insensitive, with its scenario renamed off the
  marker word and a folded-canary field proving the new namespace masks. The live compare panel
  renders envelope drifts as a before/after card instead of an empty body well until the Results
  rework lands.

- **loader**: Reject a $val instance cycle at validate time
  ([`2183a2b`](https://github.com/wbenbihi/comparo/commit/2183a2b5ae43142c4ca1c62d434b31386bb895c8))

validate loads but never resolves, so a $val cycle (A → B → A) reported a false green and only blew
  up on the first run. Detect the cycle statically over the Instance $val graph and surface it as a
  load diagnostic naming the chain. Also pins, with a test, that $literal shields a $ref-shaped
  payload (returned verbatim, never resolved or interpolated).

- **report**: Add the versioned report record, schema, and schema --report
  ([`94b1032`](https://github.com/wbenbihi/comparo/commit/94b10323809e7c6f1345e13adf58e69f35040c04))

The single JSON artifact comparo writes for every run, diff, and execution — one kind-discriminated
  record carrying the resolved outbound request AND the response per side, structured field diffs,
  and per-side assertions, so a saved report replays in full offline. schemaVersion is a stored
  constant 1 (the first published format — pre-alpha, no migration story); unknown fields are
  tolerated on read so an additive field never breaks an older reader.

Adds core/report_record.py (the msgspec structs), report_schema() + `comparo schema --report` to
  emit the record's JSON Schema, and docs/report-format.md.

- **report**: Build v1 records from run/diff/execution results
  ([`de8f879`](https://github.com/wbenbihi/comparo/commit/de8f8797adc40e78d2df37fa5845e299ec507e69))

report_builder.record_from_{diff,execution,run} — the one result→record pipeline. It reads the
  in-memory objects the engine now keeps (each cell's two Executions: the exact request sent and the
  full response received, plus the structured diff and assertions) and projects them into the report
  record.

Redaction happens here, unconditionally: url, headers, query, body, cookies, JSON paths, names,
  selection, and error text are all masked, credential-bearing headers by name, and an auth value is
  always the mask glyph. An empty run fails closed.

- **tui**: Add a baseline-vs-candidate call ledger to the diff replay
  ([`f01dce6`](https://github.com/wbenbihi/comparo/commit/f01dce699060a131a4a1bea85525fcbd410c5aff))

4C. The saved-diff replay now shows a CALL LEDGER above the body diff — status, latency, and size
  for both sides with the Δ — using the two sides' response metrics the v1 record already carries
  (the view-model now projects the candidate side's status/latency/size too). It renders only for a
  two-sided cell; a run has no candidate side, so the ledger is omitted.

- **tui**: Add the diff field-drill card (enter)
  ([`1a8f2e6`](https://github.com/wbenbihi/comparo/commit/1a8f2e61f2b6a1056dc1dbfad33c78896f75cb53))

The Diff tab had no field-drill state — enter on a drifted field did nothing. Add the mockup's
  d-drill: enter opens a focused card in a new #diff-drill sub-view that tells the whole story of
  one drift on one screen —

- state and the mode that made it a drift, with prose (exact / shape / tolerance); - how many cells
  it drifts on, with their matrix variants, and the silencing rule (or "none"); - a
  baseline→candidate table with value AND type; - the EXACT ignore-rule YAML that i would write, so
  silencing is never a hidden act.

esc returns to the results index; i silences from the card too.

- **tui**: Add the single-implementation components layer
  ([`6eb23d2`](https://github.com/wbenbihi/comparo/commit/6eb23d21272db61b6627f169cbc188a77c80f943))

tui/components.py is the layer the duplication audit demanded: the summary strip (one slot, two
  costumes), the verdict pill and the locked glyph grammar (✓ ✗ ! ~ ⊘ ◐ ·), the progress-bar
  primitive, the segmented pill (render's copy now delegates), the filter row, the verdict box with
  its N-of-M auditable header and two-line broken rows, the rule-record chrome (spec table + stat
  chips), the error panel, the index-pane frame, and the cross-view NavStack — each exactly once,
  consuming plain view-models both live objects and a saved ReportRecord can build. Provenance
  display speaks one grammar, with the TUI's single divergence: synthetics read 'built-in' so a user
  never hunts their profiles for a rule comparo made up.

The hard rule is stated in the module and the architecture doc: a view never defines a private
  renderer for a concept that lives here. Dependency order: tokens ← components ← render ← app.

- **tui**: Diff a streamed response as an event sequence, not a blob
  ([`6001a93`](https://github.com/wbenbihi/comparo/commit/6001a9304374d2c35ee99bbee3a678f626956ed4))

When a diff cell's response is streamed (chunked/SSE, so the executions carry per-side events), the
  live compare panel rendered the event array as one git-style body blob — the exact anti-pattern
  the mockup rejects.

Detect a streamed cell in _show_field via the executions' response events and route it to a new
  _stream_body_view: a numbered per-event ✓/✗ strip ("✓1 · ✓2 · ✗3 — 1 of 3 events drift") over the
  aligned per-event table, so the eye lands on exactly which event diverged. The call ledger and
  outbound layers still sit above it.

- **tui**: Fold execution results into the gate-ledger layout
  ([`f22a920`](https://github.com/wbenbihi/comparo/commit/f22a920473bfd921ac23a089ce349668095a6c0a))

Rebuild the Execution results screen to the mockup's top-down order and delete the duplication that
  buried the verdict:

- Lead with a gate-verdict banner (✗ GATE FAIL · exit 1 · which of the three dimensions are red),
  its border echoing pass/fail. - Render the gate composition directly beneath it as three
  side-by-side dimension panels (baseline ∧ candidate assertions ∧ diff), each a tally + PASS/FAIL,
  rolled up to one verdict — instead of a stacked AND table buried at the bottom. - Delete the two
  standalone assertion-list panels (#exec-assert-base/cand and _render_results_assertions);
  per-assertion detail already lives in the cell-detail screen. This removes the assert tally that
  was being rendered four times on one screen. - Extend the per-cell row to the full triplet: cell ·
  baseline · candidate · diff · verdict-with-reason (FAIL (assert + diff)), with a header row. -
  Drop the redundant _exec_gate_body prose narrative that restated the ledger, and the now-unused
  _exec_assert_rows import.

- **tui**: Fold the outbound diff into a two-layer compare panel
  ([`8f1d173`](https://github.com/wbenbihi/comparo/commit/8f1d1734651453db9235fe82d3ae7290181a52d2))

Rework the single-slot compare panel into two layers: a persistent OUTBOUND header above the
  response-body diff, collapsed to a one-line summary by default and expanded to the full request
  diff with `o`. The header reuses the resolved requests already captured on the executed cell, so
  there is no re-resolve — no live-secret exposure and no interpolation cost on cursor moves — and
  every value stays redacted.

Drop the old `o`-as-separate-mode overlay that replaced the field diff, along with the now-dead
  `_current_cell_diff` helper and the cursor-move reset; the expand/collapse state now persists
  across rows.

- **tui**: Give the outbound layer a source attribution and a verdict
  ([`e4d5827`](https://github.com/wbenbihi/comparo/commit/e4d58271f8ad61cde9e52f8f68d2651ce5c0eb8a))

The outbound layer was a flat − baseline / + candidate dump with a hedged "may be explained by what
  you send". Rework it to answer the triage question decisively:

- Tag every differing field with the config surface it came from (env · base url, env · query var,
  env · auth, env · injected body value), via a new _outbound_diffs source element. - State the
  verdict plainly: the outbound differs, so some response drift is ours — fix is config, not the
  service (vs. "identical → the drift is the service's").

Values stay on their own full-width lines so a long URL never wraps mid -token in the narrow compare
  panel; the source is tagged inline per field.

- **tui**: Make streamed events explorable in both tabs
  ([`a50dd26`](https://github.com/wbenbihi/comparo/commit/a50dd268a7fd7d3d9b0464b9516904c296cbafbc))

- RUN: a streamed response's chunks now mount in the evidence tree as foldable per-event branches
  (envelope + parsed data) — previously the tree rendered the empty body bytes and showed nothing to
  explore - DIFF: the stream tail carries a hint naming the interaction (enter on an event row opens
  it in place); an end-to-end pilot test drives the real key sequence — index enter → card focus →
  event row enter → that event's data diff in the tail — in both tabs

- **tui**: Make the execution in-flow diff show the real compare panel
  ([`9f2e958`](https://github.com/wbenbihi/comparo/commit/9f2e9584543c8b717053d9c3d368b5200eeaaa35))

The `d` diff was a bare stack of body diffs — redundant with the cell detail and unable to answer
  "is this drift ours or the service's". Give each drifted cell the same three layers as the Diff
  tab's compare panel:

- a CALL LEDGER (status/latency/size + Δ), so a latency/size regression shows even when the bodies
  match; - the OUTBOUND request diff with its source column and verdict, so the drift is traced to
  our env config or the service; - the response body diff (as before), under a banner explaining the
  view.

Refactor _outbound_diff_view / _outbound_header to take env names instead of Environment objects so
  both the Diff tab and the Execution in-flow diff reuse them; move the "press o to collapse" hint
  into the toggle wrapper.

- **tui**: Make the execution launch panel a spec sheet
  ([`ecc3823`](https://github.com/wbenbihi/comparo/commit/ecc38238370c8b50ac1ac04b74e2e31a570dd34f))

The Setup panel showed a mode pill and a plan preview but never explained, in the profile's own
  terms, what the run actually checks. Rework it into the mockup's read-only spec sheet:

- "asserts" — on both environments, the status/schema sugar per request plus any composed assertion
  profiles (named). - "diffs" — the pair and the diff profiles it composes (named). - selection math
  extended to cells × envs = calls. - the gate formula up front (baseline asserts ∧ candidate
  asserts ∧ diff), so the verdict's composition is legible before you press enter.

- **tui**: Match the results screens to the mockups — cards, tables, pills
  ([`b0dab31`](https://github.com/wbenbihi/comparo/commit/b0dab31a02ede7006121d1c0ebd446899123c021))

The mockups in docs/design are the acceptance spec; this round closes the gaps the user called out
  across both results screens, plus the review findings against it.

DIFF - o actually toggles the outbound layer (the binding existed, the action did not) and the band
  is documented: did we even send the same request? - the verdict phrase lives INSIDE the red/green
  card (the inspect table's border title); a clean cell gets an auditable green card listing every
  held and silenced rule - rule records and field occurrences are headered tables with REQUEST and
  VARIANT as separate columns, no tone border, a visible scrollbar - the call ledger wears the
  mockup's table chrome with env-named headers - a streamed cell lists every event as a selectable
  row: enter expands it in place with its SSE envelope and a real per-event data diff; the ✓/✗ marks
  come from the ENGINE's judged fields, never raw equality (a silenced-only difference stays ✓),
  replay reads the saved drift paths - the side-by-side well gains a hairline pane separator, banded
  so the recessed background stays contiguous

RUN - the detail is restructured to the mockup stack: call line, then the verdict card as a
  FOCUSABLE table (tab into it; enter on a rule opens its record in the rules pivot; N-of-M phrase
  on the card border), then the evidence tree; dead cells get the error card (verbatim error,
  attempts, retry, the kept masked request) — no fake N-of-M claim - the requests table matches
  state 2 (verdict, request + method + payload type + matrix marker, cells strip, assertions rollup,
  P50, ⊘ rows for deselected requests) and swaps to the compact nested index when drilled - variants
  show ✓ PASS / ✗ FAIL / ! ERROR; the rules record is a real DataTable (request · variant · outcome
  · expected · got) - / filters on any attribute — method, payload, status, case, state words (fail,
  error, warn, pass) — on both tabs

SHARED - one bordered filter-row component showing the ACTIVE query; joined-then -quieted seg pill
  (dim segments, hairline separators, accent active); pill-shaped stat chips; record_table = the
  mockup's hairline table chrome; DataTable backgrounds normalized to the panels

Review rounds (13 agents total) confirmed and fixed: two secret leaks (the SSE event NAME is server
  data — now redacted in both sinks), the record table clipping rows invisibly, stream verdicts
  re-judged at display time, facet chrome divergence (card only on all, raw bare), the headerless
  rules pivot, event-0 treated as unfocused, live cells cropped by stale column widths, cellrow keys
  made opaque against ::-bearing ids, and the focused-table repopulate echo discarding the drilled
  cell.

The showcase example runs against public services again and now covers every RUN state too:
  assert.order breaks deterministically on Checkout, Legacy quote times out everywhere, the README
  maps each state.

- **tui**: Name the ignore rule that skipped a field in the drift drill
  ([`615db40`](https://github.com/wbenbihi/comparo/commit/615db407e5f472fd5119af10f3b5b75106819baf))

4C. A skipped field's sub-row read a generic "volatile"; now that FieldDiff carries the governing
  DiffRule.path, it names the exact rule ("ignored by $.ts"), so a green cell says out loud which
  profile rule chose not to check the field.

- **tui**: Payload renderers — anchored evidence tree, real HTML outline, honest binary view
  ([`b32b1df`](https://github.com/wbenbihi/comparo/commit/b32b1dffaf120bdb67efc09631129a4a43ff4852))

The evidence tree now pins verdicts INTO the body: a held rule shows a green ✓ at its field, a
  broken rule a red ✗ naming the rule at the exact site it points to, and a missing required field
  renders as a red synthetic node where it should have been — never a detached message. The
  broken-anchor registry in render order backs n/p hopping.

The HTML renderer becomes an outline, not tag soup: title, headings with their levels, landmark
  sections, table shapes, quoted text content, and a contains-assertion's needle highlighted at its
  site — scripts and styles elided with a count. Binary bodies render honest bytes: content-type ·
  size · magic · the load-bearing sha256 line · a hex head with offset and ascii columns, buildable
  live and from a saved record, withheld together under the same whole-body dual-view fail-closed
  rule the record uses (now shared from redaction.py). Undecodable bodies route here instead of
  mojibake lines.

The raw facet prints the true status line (HTTP/1.1 200 OK) from the captured wire metadata; the SSE
  facet shows the full envelope — the spec-default *message* for unnamed events, 'no id', and retry
  labeled as the reconnect hint — and the per-event verdict strip reads the shared glyph map.

- **tui**: Polish the diff prepare checklist
  ([`7842915`](https://github.com/wbenbihi/comparo/commit/7842915f59b76993083c0854a16ecc21acee6b8f))

Two mockup touches on the d-prepare screen: mark a streaming request in the checklist, and replace
  the "up to 4 in parallel" aside with the "0 writes — nothing is written until you run" reassurance
  the mockup leads with.

- **tui**: Rebuild the Diff Results screen around the three indexes
  ([`c0762a6`](https://github.com/wbenbihi/comparo/commit/c0762a645678eeadfb4a515174efb3215e14df36))

One result set, three pivots, cycled with r: requests (cells with the locked glyphs — ✓ clean, ✗
  rule broke, ! error, ⊘ not run), rules (every effective rule's record: broken red on top, then
  passed, ignored, and unused — a typo'd path finally has a face), and fields (one row per broken
  path, however many cells it hit). The old two-mode drift index, the skip-group machinery, and the
  full-screen field-drill state are deleted — the fields pivot's triage card replaces the drill.

The inspect panel reads in triage order: call ledger → the verdict box naming which rules broke (the
  auditable N-of-M header, evidence lines) → the response-headers well → the body (git well for
  JSON, the event sequence for streams, the error panel with attempts and retry policy for dead
  cells; clean sections collapse to one-line stubs). enter jumps across pivots — cell ↔ its broken
  rule, field → its cell — with a 'from X — esc returns' crumb on the NavStack; esc pops the jump
  before it ever means back-to-Prepare. / filters the active index, f collapses it to broken rows,
  n/p hop between red rows.

The summary bar speaks the locked gate precedence (FAIL when any rule broke; ERROR only when errors
  are the only failure) through the shared verdict pill. Footers and the help screen match per
  pivot; tui.md rewritten to the new contract.

- **tui**: Rebuild the RUN results screen on the shared components
  ([`907753d`](https://github.com/wbenbihi/comparo/commit/907753d130f273ce9765f7175c2994ca924e6228))

The last workstream of the unified RUN+DIFF rework: RunView now composes the components layer end to
  end.

- summary strip wears the two costumes: bar + cell tally while running, the run_gate verdict pill
  with a gate-tinted border when finished - requests table snaps worst-first (fail, error, advisory,
  pass) when the run completes; o flips back to plan order - r pivots the left column to the
  assertion-rules index: one row per written rule (AssertRef fold) with a per-cell glyph strip,
  provenance, and a broke/enforced count; enter opens the rule record (spec rows, stat chips, every
  cell it belongs to) and a record row jumps to that cell's detail - the detail's checks section
  speaks the verdict-box grammar via the shared check_row_lines (N-of-M header, two-line broken
  rows, reachable last); a dead cell replaces the box with the error panel — no fake N-of-M claim -
  a JSON response body mounts the anchored evidence tree; n/p hop between the broken anchors
  (forcing the tree line rebuild so a first reveal lands) - y copies the raw exchange through the
  redactor with credential headers masked by name — the clipboard is a sink like any other - cell
  glyphs unified on the one grammar: unreachable is ! (error), advisory is ~, ✗ stays reserved for
  rules that actually broke — tables, strips, and the rules index all render through cell_glyph -
  dead cells join every rule record as '! never evaluated' instead of vanishing; inline rules name
  their owning request in provenance - spurious table-rebuild highlight echoes are gated on table
  focus, so a finished-run resort or a cross-pivot jump can no longer clobber the open detail

- **tui**: Render a streamed response as a numbered event sequence
  ([`aac5f05`](https://github.com/wbenbihi/comparo/commit/aac5f0597ca1e2a35f59223756e0abdcafb85b26))

4C. A saved streamed diff now replays as a numbered event sequence — each event aligned baseline vs
  candidate with a per-event ✓ (same) / ✗ (drifted) and a "—" where one side ran short — so the eye
  lands on exactly which event in the stream diverged. The record already carries response.events;
  the view-model now projects them per side.

- **tui**: Render the live run as a per-plan, per-side table
  ([`590a090`](https://github.com/wbenbihi/comparo/commit/590a090fdd75c790cf853c68a43249e3bc7bb2fd))

Replace the flat progress strip (a bar + one in-flight line + a last-6 log + an anonymous glyph row)
  with a persistent table over the whole plan, matching the d-running / e-running mockups:

- Every planned cell is a row, seeded "queued" up front, that flips ○ queued → ◐ in flight → ✓/✗
  finished and fills its two side columns (status · latency) as it lands. - For an execution each
  side also carries its live assert tally (4/0), and a STATE column names the failing dimension
  (assert ✗ / diff ✗ / assert + diff); the Diff tab shows same/drift. - Core: ExecutionProgress
  gains a queued seed phase (started=False) and per-side finish data (candidate_status, per-side
  assert pass/fail); run_execution emits a queued tick per plan cell before the run. - Both view
  drivers now keep a list[_RunningRow] keyed by plan index instead of separate glyph/current/recent
  state.

- **tui**: Reorient the diff "rules" index to the silencing rules
  ([`63c4e17`](https://github.com/wbenbihi/comparo/commit/63c4e170c51f08eee51a9d8c7e83013ae1c45d51))

Toggling the drift index to "broken rules" re-listed the drifted fields (the same data as "grouped
  by field", just different columns). Rebuild it around what the view is actually for: the
  DiffProfile rules that silenced something.

- _populate_rules now lists one row per fired ignore/tolerance rule, with how many fields and
  requests it silenced, keyed rule::<rule>. - Selecting a rule opens _show_rule → _rule_detail: the
  rule's mode, why it exists, and every field path it hid with its source request — so a skip is
  auditable, never a silent pass. - Rules mode selects the first rule on open; _render_row routes
  rule:: rows.

- **tui**: Show every execution cell as a triplet row
  ([`1985d86`](https://github.com/wbenbihi/comparo/commit/1985d8688d9f2768147b21766c47daf125f3c35c))

4C. The execution results table listed only the drifted cells; it now shows every cell as a triplet
  row — verdict glyph · cell · both sides' assertion tallies · diff — so the whole run reads at a
  glance and a passing cell is as visible as a failing one. Enter still drills into the highlighted
  cell's detail (now indexed over all outcomes).

- **tui**: Show the execution gate as an AND-composition ledger
  ([`18d3f1d`](https://github.com/wbenbihi/comparo/commit/18d3f1de5003395a078832a384725c4bfb790ae7))

4C. The gate panel now leads with a gate-composition ledger — one aligned table listing each factor
  (baseline assertions, candidate assertions, diff) with its verdict and the ∧ rollup to the gate —
  so it reads at a glance which factor blocks the run. An assertion-only failure (no drift) is no
  longer hidden behind a single gate glyph.

- **tui**: Wire the call ledger into the live compare and cell views
  ([`e9547ea`](https://github.com/wbenbihi/comparo/commit/e9547eac1d6ba92fdd9536fc15abcbc31d61905f))

The call ledger (baseline vs candidate status/latency/size + Δ) rendered only in the saved-report
  replay path; the live Diff compare panel and the Execution cell-detail screen never showed it, so
  a latency/size regression was invisible until a report was reopened.

- Extract the ledger renderer (_ledger_table) and add _executions_ledger, which reads the metrics
  straight off the two executions carried on a live cell; _call_ledger (replay) and
  _live_call_ledger now share it. - Diff compare panel is now three layers — call ledger → outbound
  → body (mockup d-results); the ledger sits above the response diff. - Execution cell detail leads
  its left stack with a CALL LEDGER panel (hidden when there is no candidate side). - Flag a slower
  candidate's latency Δ in warn colour.

### Refactoring

- **archive**: Persist and replay the v1 report record
  ([`a134a2f`](https://github.com/wbenbihi/comparo/commit/a134a2f9d2056923beaa8a9b9041253df943fc7e))

The archive now stores the whole msgspec ReportRecord (request + response per side, structured field
  diffs, per-side assertions) — save/load/list/prune encode/decode it directly, keyed by
  metadata.id. The old flat SavedRecord and its record_from_* builders are gone; the TUI save paths
  build the record through report_builder (passing the real Environment/ExecutionProfile/Execution
  objects).

The Report tab reads the record through a new view-model (tui/replay.project): it flattens the
  nested record into the per-record / per-request / per-cell shape the replay helpers want, and —
  crucially — carries the real FieldDiffRecords so the body diff renders the true profile mode
  instead of fabricating "exact" (M-6).

doctor's "saved reports" sink now writes the real v1 record to disk (the old and v1 sinks are one),
  so it is 9/9 again. Tests reworked to the new builder + record.

- **compare**: Drop CellDiff's now-dead response-metadata fields
  ([`1aeb798`](https://github.com/wbenbihi/comparo/commit/1aeb798348d24ddd3746ab0765873765fe65f6f5))

With the archive retired, nothing reads CellDiff.status / latency_ms / size_bytes / response_headers
  off a live cell — the saved-replay detail tree reads them from the record's ResponseRecord via the
  view-model instead. Remove the four fields and the locals that fed them; the live Diff view still
  uses baseline_body / candidate_body and the threaded Executions.

- **execution**: Extract the plan builder from run_execution
  ([`5cc353f`](https://github.com/wbenbihi/comparo/commit/5cc353fc57eefc2fdb920bca27ccee4c662f076c))

M-11. Lift the request→cell plan expansion out of run_execution into a focused _build_plan helper,
  so the orchestrator reads as setup → build plan → run cells → assemble result. The per-cell
  coroutine stays a closure (it legitimately captures the clients, limits, and progress callback).
  No behaviour change.

- **loader**: Decompose the 144-line _check_profiles
  ([`693845f`](https://github.com/wbenbihi/comparo/commit/693845fabd641b1e915164d8a8755a7fcd5f8ed4))

M-11. _check_profiles held five nested closures plus the dispatch loop. Lift the five validators
  (_check_spec / _check_includes / _check_inline_assertions / _check_matrix_refs / _check_val_kinds)
  to focused module-level functions, leaving _check_profiles as the ~40-line dispatch. Behaviour is
  unchanged (loader/refs tests green).

- **redaction**: Unify the two body redactors into redact_tree
  ([`f8de436`](https://github.com/wbenbihi/comparo/commit/f8de436fe105fc8d917de8321d9be3ba070603dc))

archive._redact_body and export._redact_value were byte-identical recursive tree redactors (mask
  object keys and string values alike). Move the single implementation to redaction.redact_tree —
  the one home for request/response body, events, and the upcoming FieldDiff / AssertionResult value
  redaction — and point both call sites at it. No behaviour change (M-9).

- **report**: Make the CI reporters project from the v1 record
  ([`45bcb2a`](https://github.com/wbenbihi/comparo/commit/45bcb2ae74c8034890b523be8ea15a999909c96f))

The reporters (JUnit, SARIF, JSON, Markdown) now consume the one ReportRecord the archive stores, so
  a CI report can never disagree with a replayed one (M-8). A finding is a drifted field or a failed
  error-severity assertion; the JSON reporter emits the whole record. `comparo diff`/`exec --report`
  build the record via report_builder and derive record_from_execution's env refs from the outcomes.

Deletes the now-dead intermediate model: report.RunReport / CellReport / DriftEntry / build_report,
  execution.build_execution_report, and the app's write-only last_report. report.py keeps only the
  diff-gate arithmetic. The doctor reporter sinks render from the record too; still 10/10.

- **tui**: Decouple the crash handler from Textual privates
  ([`8d8d634`](https://github.com/wbenbihi/comparo/commit/8d8d634e8236a68eb407dd0afdd5c9a031c262ec))

M-10. The crash handler set self._return_code / self._exception / self._exception_event by hand —
  Textual internals that could be renamed on a version bump. Replace them with the public
  App.exit(return_code=1, message=…), which does the same bookkeeping and shows the redacted crash
  report as the exit message. Not re-raising is deliberate: we surface the redacted report, never
  the raw traceback.

- **tui**: Delete the orphaned _drift_change helper
  ([`71c6747`](https://github.com/wbenbihi/comparo/commit/71c67475719dadacaa44f2bcb43f71ff89baf776))

The per-cell triplet swap (7f89fde) replaced the drift-table "change" column with _exec_triplet,
  leaving _drift_change with no callers in src/. Remove it and its export; the drift-path redaction
  it guarded is still covered by _cell_verdict and _diff_body_view in the same test.

- **tui**: Fold this session's verdict-card dups back into components
  ([`efe82f6`](https://github.com/wbenbihi/comparo/commit/efe82f63c4bcadb934db1d3a0351b62d8ca3466c))

Post-audit cleanup of one-implementation regressions introduced while building the RUN verdict card,
  so Execution/Report compose the real components instead of inheriting a fork:

- add check_row_cells(row) -> (rule, source, evidence) to components, built on the shared
  _ROW_MARKS, and consume it from RunView._populate_checks — the DataTable costume no longer defines
  a private glyph map or restates the warn-suffix rules (the audit found the broke-label styling had
  already forked between check_row_lines and _populate_checks) - retire _run_value by giving the
  canonical _sv a width parameter; the rule record's expected/got cells now use the one
  redact-then-clip formatter

458 tests; the check-row grammar is pinned by a new component test.

- **tui**: One summary bar for RUN and DIFF
  ([`b3f8897`](https://github.com/wbenbihi/comparo/commit/b3f88970fc7c6dd63cc62b23cd6e00686af115d2))

The two summary bars had diverged — Diff had a background, the SUMMARY title and worded counts with
  an env selector on the right; Run had icons, the run id, and no framing. Rationalize them onto one
  component.

- new components.summary_bar(segments, env, ident, gate, detail, save_key): split details as icon ·
  count · word (✓ 4 same), a │ separator, the id, the gate verdict pill, an extra detail, the save
  hint, and the env aligned right — the exact structure the user specified - both tabs now build it
  and frame it identically: #run-progress becomes a .panel with the SUMMARY border title and the
  gate-tinted border, matching #diff-progress (which was already that shape) - Run keeps its ~ warns
  segment and 'run <id>'; Diff gains the ◌ ignore (silenced-field) count and keeps 'baseline ⇄
  candidate'; the redundant Diff drift-count detail is dropped so the save hint fits -
  summary_strip_running/finished are no longer used by any view (summary_bar is THE live summary);
  left in place for the Report/Execution rebuild

- **tui**: Replace star-imports with explicit imports (re-arm F405)
  ([`8362702`](https://github.com/wbenbihi/comparo/commit/83627026ed18ac4af94c30d2f29ede929cb8a181))

app.py re-exported comparo.tui.render and comparo.tui.tokens via `import *` under a file-wide `#
  ruff: noqa: F405`, which disarmed undefined-name analysis over the TUI. Replace them with explicit
  imports of the 74 render + 43 tokens names app.py actually uses, redirect the tests that pulled
  those helpers from app.py to import them from render/tokens where they live, and drop the pragma.
  No behaviour change.

- **tui**: Reuse the core request selector instead of a verbatim copy
  ([`7bd090c`](https://github.com/wbenbihi/comparo/commit/7bd090c43091f2bd1e22d2173e6cb8294fb18881))

M-7. render.py carried _exec_selected_requests, a byte-for-byte copy of execution._select (a
  profile's select tags/ids → requests). Promote the core one to a public select_requests and call
  it from render, deleting the duplicate so the selection logic lives in exactly one place.

### Testing

- Align openapi/resolve tests with distinct exit codes and load-time cycle check
  ([`5c160f7`](https://github.com/wbenbihi/comparo/commit/5c160f78c7e5705aee9a18901bd93cf01f27d758))

Follow-ups to the exit-code split and the load-time $val-cycle check: the openapi import errors are
  usage errors (exit 2), and the $val-cycle test now builds the cyclic project past the loader
  (which rejects it up front) to still exercise the resolver's own fail-closed protection.

- **doctor**: Gate the v1 report record with a never-leak canary sink
  ([`6718b75`](https://github.com/wbenbihi/comparo/commit/6718b75ccd3b9c47b685ed2d67a956d1f6a10ebb))

Adds a saved-reports-v1 sink to the doctor self-check that pushes the canary through the whole new
  surface — the outbound request (url, query, a credential header, body, cookies, the always-masked
  auth block), the response body and event stream, the structured FieldDiff, and both sides'
  AssertionResult expected/actual — then serializes the diff and run records and asserts the canary
  is absent. This is the gate the report builder's redaction must clear. Now 10/10 sinks.

- **render**: Add a dedicated test for the render helpers
  ([`1b9effc`](https://github.com/wbenbihi/comparo/commit/1b9effce2eec4a5299a64578a9acc5b96e610a4d))

render.py had no dedicated test despite being the largest module. Cover the pure helpers
  (_fmt_bytes, _relative_age, _pad_cells, _req_short, _run_label, _assert_tally /
  _assert_count_text), the reading-pane and saved-diff body well, and — as a direct M-6 regression
  guard — _field_from_record, which reconstructs a live FieldDiff from the saved record's real
  state/mode rather than a fabricated "exact".

- **render**: Pin truecolor so the band-color assertions hold in CI
  ([`0c144c7`](https://github.com/wbenbihi/comparo/commit/0c144c744ce402336ae59c6a4166ad4d5c24cb7c))

The event-sequence and headers-well diff tests assert the muted DEL/ADD band backgrounds (`48;2;…`
  truecolor sequences). Their Console set force_terminal=True but left the color system
  auto-detected, so it resolved to truecolor locally (COLORTERM=truecolor) yet downgraded to
  256-color on CI — where `48;2;43;22;28` never appears and both tests failed. Force
  color_system="truecolor" so the assertion is deterministic regardless of the runner's terminal.
  Verified by running the full suite with COLORTERM unset and TERM=xterm-256color.

- **tui**: Pin the outbound band's self-explanatory copy
  ([`36554fc`](https://github.com/wbenbihi/comparo/commit/36554fc4efec2e1d82ac0f10f93ae6ba80b7e9c5))


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
