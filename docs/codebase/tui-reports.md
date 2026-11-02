# TUI Reports

Reports follows `docs/design/tui-v2/references/reports.webp`: a searchable, date-grouped archive
beside the selected run's outcome, top issues, provenance, integrity, and actions. It uses the
TUI Menu, ActionBar, FilterInput, Panel, KeyValueList, typography, and status/color primitives.

Open workspace **3**. Existing runs come from the selected project's `.comparo/artifacts` archive
by default; `--artifact-root` selects another archive shared with Launch.
No execution service is invoked when browsing or opening reports. A fresh process can inspect
saved results without a live RunHandle or a running demo server.

The archive displays status, run identity, local time, execution profile, environments, calls,
adverse rule-fact count, duration, and commit where space permits. Missing metadata stays explicit.
Environment names in the listing are not assumed to define a baseline/candidate order. The preview
uses the saved manifest's exact comparison links to show directed topology.

Search matches safe report metadata. Status, profile, environment, date, and tag filters operate
over a bounded authenticated listing (500 physical archive entries by default, configurable with
`--maximum-report-entries`). Date filters use
local calendar days. Pages contain at most 50 reports; date headers are not selectable.
Corrupt or unverifiable entries are counted separately and cannot be opened. Read-limit and
archive failures have an explicit retry state; they do not appear as an empty successful archive.

At wide terminal widths, the archive and preview follow the mockup's two-thirds/one-third split.
Standard widths reduce list columns and wrap the filter bar. Compact terminals give the archive
the full width; `p` opens the full-width preview and Escape returns to the same selection.

| Key | Action |
| --- | --- |
| Up / Down | Select the previous/next saved run; update the preview |
| Enter | Open the selected run in the existing Analyze view |
| `/` | Focus archive search |
| Tab / Shift+Tab | Move through search, filter pickers, archive, and actions |
| `p` | Focus the preview; replace the archive on compact terminals |
| `e` | Choose an export format and destination |
| `c` | Copy the selected report ID |
| `r` | Reload the archive |
| Alt+Left | Return from saved Analyze to the archive |

The filter pickers use the shared searchable Menu interaction: arrows select a row, Enter commits,
and Escape cancels. Clear filters restores all values. Returning from Analyze preserves the
archive's filters, page, selection, and scroll position.

## Shared analysis and integrity

Reports mounts the exact `ResultsView` used by Launch. There is no separate archived-results
analyzer. It accepts the same `ResultProjection`, navigates the same typed references, and uses
the same bounded authenticated body-preview formatter. Alt+1–5 selects an Analyze lens;
`[` / `]` cycles lenses without conflicting with macOS Ctrl+arrow shortcuts. Brackets remain
ordinary characters inside text filters. Escape stays local to Analyze; Alt+Left returns to Reports.
The view shortcuts can be remapped in [Settings](tui-settings.md).

Press **i** for the shared capture inspector: raw byte paging, JSON subtrees, SSE events and reviewed
sanitized request cURL. It retains exact attempt/exchange coordinates and returns to the same Analyze
selection. See [capture controls and limits](tui-launch.md#full-capture-inspection).

Listing and preview authenticate report metadata and manifests without reading detached captures.
The preview says **Manifest verified**; each captured body is authenticated when explicitly read.
Generation checks prevent an older asynchronous preview from replacing a newer selection.
Background completion does not change the active workspace.

Export explicitly selects JSON, Markdown, JUnit XML, or SARIF and a destination. The backend fully
verifies the artifact before rendering the bounded export and opens the destination exclusively;
existing files are not overwritten. Report bodies and filesystem exception messages never enter
archive metadata or arbitrary widget markup.

Regression coverage is in `tests/tui/test_reports.py`. It persists real authenticated test artifacts,
checks shared-analysis body access, filters, pagination, export/non-overwrite, invalid and empty
archives, retries, stale preview results, and 80×30, 120×40, and 160×52 layouts. Set
`COMPARO_SCREENSHOTS=/tmp/comparo-tui-reports` to capture the rendered archive and analysis screens.
