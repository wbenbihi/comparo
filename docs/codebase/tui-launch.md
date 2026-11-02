# TUI Launch

Launch owns one session draft and the Target → Plan → Run → Analyze lifecycle.
It uses the existing application planner and execution services. It does not compile YAML,
calculate matrix products, or evaluate results. Profile writes go through the application authoring service.

From the repository root:

```console
uv run --frozen comparo tui --config examples/tui-showcase/comparo.yaml
```

For actual HTTP execution, start the loopback demo in another terminal:

```console
uv run --frozen python examples/support/demo_server.py
```

Open workspace `2`. The starting choices are **Run requests**, **Run an execution profile**, and
**Run assertions**. Saved profile selection lives inside Target. Profile mode also offers
**Configure manually**, using explicit Requests and comparison or assertion-only topology.
Manual configuration stays in the session until you explicitly choose **Save as profile**.

Variants exposes actual Matrix case filters and trials. Validation offers inherited, selected,
or cleared assertion/diff/gate profiles. Request response schemas remain active when assertion
profiles are cleared. Safety offers per-call maximum attempts, fail-fast behavior, and mutation
approval. Blank numeric controls preserve inheritance. The scope rail previews exact planner
counts after edits; preview handles are discarded and cannot start execution.
The summary uses a proportional side column with a 32-column minimum when space allows.
Below the layout breakpoint it moves under the form and switches to horizontal counts,
topology, and overrides. Its actions remain visible when the terminal resizes.

Single-choice menus use radio markers; multiple-choice menus use checkboxes. Conditional
selectors appear in labeled, indented `SelectionGroup` children. Profile-inherited Requests
and topology are explicitly read-only displays, not disabled selection menus.

Mutation approval is an explicit run control and is enforced according to the admitted policy.
Omitting `--policy` selects trusted-local authority; `--policy PATH` supplies an external
ExecutionPolicy. The admitted authority is displayed during review.

| Control | Action |
| --- | --- |
| Arrows | Move the menu cursor without changing committed choices |
| Space / Enter / click | Commit a choice or toggle a multi-selection |
| Tab / Shift+Tab | Move between section headers; traverse fields only inside an entered editor |
| ↑ / ↓ on a section header | Select the previous/next section without entering its fields |
| Enter on a section header | Open that editor and close other sections |
| Escape in Target | Return from fields to their header, then to starting choices |
| Alt+1 … Alt+5 | Jump directly into Target, Environments, Variants, Validation, or Safety |
| Left on a section header | Close the section, keeping keyboard focus |
| `/` | Focus the Request filter in an ad-hoc mode |
| `o` | Show/hide run options |
| Ctrl+R | Prepare the current draft |
| Ctrl+X | Execute an admitted review |
| Ctrl+E | Discard the checkpoint and edit the retained draft |
| Ctrl+K | Request cancellation and observe cleanup |

Filtering preserves choices that are temporarily hidden. Changing modes retains editable form
state, but only the active mode's selection enters `PlanningOptions`. A baseline cannot also be
a candidate. Local validation keeps Prepare unavailable until required selections are complete.
Preparation diagnostics preserve the form and expose application-owned guidance rather than
exception text. Matrix filters narrow existing bindings across selected Requests; they do not
attach the selected Matrix to unrelated Requests or relax duplicate-binding validation.

## Component boundary

The workspace composes `LaunchConfiguration`, `LaunchSummary`, `LaunchReview`, `Caption`, and
`ActionBar`. It consumes semantic events and coordinates effects through `TuiBackend`.
It must not import native Textual input, select, checkbox, button, or table widgets.

The screen components reuse `Panel`, `Menu`, `FilterInput`, `KeyValueList`, `Table`, `Badge`,
`Separator`, and typography primitives. Shared library additions fill specific gaps:

- `ChoiceMenu` extends `Menu` with committed single/multiple selections. It reuses cursor,
  scrolling, disabled-item, and pointer behavior; assigning state does not emit a user change.
- `ValueInput` encapsulates a compact labeled editor and emits a semantic change event.
- `DisclosureHeader` owns the title, summary and right-aligned edit affordance. It is one
  keyboard focus stop and emits navigation/edit messages; it uses neither native Collapsible
  nor a native Button for its header. `Disclosure` keeps editor state mounted.
- `ActionBar` generalizes the compact keyed resource-action presentation for workspace commands.

These additions are also visible in Debug → Components. `Menu.format_item` is the shared row
formatting hook, and `FilterInput.value` supports coordinated clearing without exposing its editor.

## Lifecycle

`TuiBackend.prepare_launch` serializes compilation with Explorer inspections and retains the
one-shot preparation handle plus the exact execution controls. The review presents application
cardinalities, environment pairs, matrix-axis counts, validation, policy notices, persistence,
and planning budgets. Destinations include a sanitized preview of up to 64 planned requests;
coverage is stated explicitly. Session overrides and configuration provenance remain inspectable.
The application's `execution_allowed` determines Run availability.

Edit discards admission. Start consumes the retained handle using unchanged controls. Progress
and cancellation use `RunService`; execution continues when the user visits another workspace.
Terminal outcome and persistence status come from the backend, and no intermediate counters are
used to infer a gate verdict. Shutdown uses the existing run/service cleanup path.

## Running and analysis

Run starts with one overview row per Request. Matrix cases are folded under their Request;
Space, Enter/click, or Right expands them and Left collapses back to the summary. Live refreshes
retain expansion, selection, and focus. Summary rows expose completed/active lanes and exact rule
counts; child rows expose case state, HTTP status, validation, and elapsed execution time.
The full screen separates progress and statistics from the request hierarchy, selected execution,
and recent activity. Run groups cells/trials into adjacent baseline and candidate lanes, with terminal
HTTP status, observed elapsed time, assertion/diff facts, evolving totals, selected-call details,
and recent events. HTTP error responses and transport failures remain separate facts.
A terminal run stays on its completed Run summary, including the selected row. Execution is not
artificially slowed. **Analyze results** opens analysis explicitly; **Run summary** returns to
the same completed table without discarding the analysis selection.

Analyze uses application-owned Issues, Requests, Rules, Fields, and Timeline projections. It
supports exact occurrence navigation, rule evidence/provenance, canonical HTTP attempts and
exchanges, bounded authenticated body reads, and baseline/candidate body comparison through
`DiffView`. Diff issues preview captured response bodies automatically when both sides exist.
Absent evidence, omitted records, missing captures, and redactions remain explicit. Related-row
HTTP time is the terminal HTTP exchange measurement, not an invented total execution duration.

The five lenses have dedicated index columns and widths. Requests uses an expandable Request/case
comparison table, per-environment assertion counters, diff counters, top issues, and a case inspector.
Issues leads with the explanation and provenance, followed by occurrences and response evidence.
Rules exposes rule/source and evaluation counts; Fields exposes evidence paths and their outcomes;
Timeline preserves the application's event order with request/environment context.
The group index and occurrence list keep their identities during evidence navigation. Selecting
an occurrence updates its context and evidence, never replaces the list with owners or rules.
HTTP details, Baseline, Candidate, and Compare bodies are explicit actions; Escape returns from
body focus or detail navigation. Requests groups pair environment executions by comparison and
retain unpaired executions. Matrix case labels are readable names, with full binding names in
the persistent selection strip. Baseline/candidate colors, HTTP status, and validation outcomes
remain distinct. Body previews label both environments and start at the first changed line.
On compact terminals, Enter opens the selected group's detail and Escape returns to the index.
The standard-width Requests lens also uses a full-width overview/inspector switch, preserving
readable comparison columns at 120 columns; the wide layout shows both regions together.
The inspector scrolls as one explanation-to-occurrences-to-evidence surface. Its breadcrumb and
actions remain fixed. Open evidence replaces the index with a full-width response view; the back
command restores the previous lens and selection. Both automatic previews and full evidence keep
baseline/candidate labels synchronized with unified or split rendering.
Use `g` for groups, `o` for occurrences, and `e` for evidence; on compact terminals `e` gives
the detail the available height while keeping request/environment context visible.
Use **Alt+1–5** for Issues, Requests, Rules, Fields, and Timeline, or **[ / ]** to
cycle between them from the index or evidence inspector. Brackets remain text while editing a
filter. Switching views clears the previous view's filter and returns focus to the new index.
`/` opens the current view's filter;
Enter returns to the index and Escape clears the filter before leaving it.

On macOS, Alt is **Option (⌥)**. In Apple Terminal, enable **Use Option as Meta key** under
Settings → Profiles → your profile → Keyboard. In iTerm2, set **Left Option Key** to **Esc+**
under Settings → Profiles → Keys → General. Then Option+1–5 selects the corresponding view.

Fields lists only evidence locations retained in the report, not every field in a captured body.
When none are retained, it explains the missing evidence, shows the omitted-record count, and
offers Requests and Rules as alternative inspection routes. The showcase's `demo-artifacts`
profile disables evidence capture, and its custom redaction policy also suppresses inline rule
evidence. Its Fields view therefore shows this explicit empty state; projects retaining rule
evidence, such as `examples/quickstart`, populate the field index normally.

Editing after a completed run releases the terminal service handle. Starting another run resets
prior results and evidence. `comparo tui` and `python -m comparo.tui` launch this interface
with the same command options. [Reports](tui-reports.md) reuses this analysis surface for saved runs.
Scenario execution remains unsupported by the production backend.

### Reusing a manual target

**Save as profile** on Target opens an ID/name editor and an exact YAML/destination preview.
Save becomes available only after Preview. The application validates the new ExecutionProfile
against the project, publishes a new file exclusively, checks normal project discovery, and refreshes
both Launch and Explore. Existing source files are never overwritten. Selections include requests,
environments or baseline/candidates, trials, retries, fail-fast, and assertion/diff/gate profiles.
Inherited selections stay inherited; mutation approval stays session-only. Invocation-only Matrix
case filters and clearing an inherited gate cannot be represented in profile YAML and are rejected
explicitly; they are never silently dropped.

From Explore, select a Request or ExecutionProfile and press **c** (**Use selected resource in
Launch**). A Request uses the active Environment for assertion checks; without one it starts from
project comparison defaults. An ExecutionProfile uses its own topology. This only populates Target:
Prepare and Run remain separate explicit actions. A busy Launch rejects the handoff, and stale
resource revisions are rejected before invalidating an existing plan.

### Full capture inspection

Press **i** on a selected occurrence, or choose **Inspect capture**, to open the shared capture
inspector. Message selection retains baseline/candidate, Request, Environment, attempt and exchange
identity. Raw text pages in 64 KiB windows with **n / p**; JSON paths and SSE events page in groups
of 50. Enter drills into an object/array or a JSON SSE event, **b** returns to the parent, and Escape
restores the unchanged Analyze selection. Structured inspection is bounded to 4 MiB / 50,000 JSON
nodes / 10,000 SSE events; the raw pager remains available for larger captures. Missing, truncated,
redacted and unreadable evidence remains explicit.

**Review request cURL** reconstructs the sanitized stored request for the selected attempt/exchange.
It shows replay limitations before the explicit Copy action; it neither sends HTTP nor substitutes
live credentials. Command previews expire after 60 seconds. Both Launch and Reports use this same
inspector and the existing application body/cURL services.

Regression coverage is in `tests/tui/test_launch_workspace.py` and
`tests/tui/test_run_analysis.py`, including real loopback execution, a 16-call Matrix run, a 22-call mixed Request run, manual
configuration, case filters, profile overrides, reset, cancellation, failed assertions, drift,
authenticated evidence, mode isolation, background focus, and terminal sizes 80×30, 120×40,
and 160×52. Set `COMPARO_SCREENSHOTS=/tmp/comparo-tui-review` while running these tests to export
populated Target, Plan, Run, and Analyze SVG snapshots.

For a populated visual review, select **Run an execution profile → Launch review** in the
showcase project. It combines eight catalog Matrix cases with stable health, release drift, and
an inline response contract (22 calls). The demo server command above serves all four Requests.
