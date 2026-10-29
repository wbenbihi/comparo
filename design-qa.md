# Design QA

Acceptance viewport: 160 columns by 52 rows

The earlier acceptance claim was invalid. Fresh visual, interaction, and architecture reviews
rejected the implementation. The corrective work is being reviewed one lifecycle slice at a time
against the approved screenshots in `docs/design/tui-v2/references`.

## Current corrective slice

- Surface: Compare / Plan
- Reference: `docs/design/tui-v2/references/compare-plan.webp`
- Actual: `/tmp/compare-plan-corrective-2.png`
- Paired evidence: `/tmp/compare-plan-corrective-2-pair.png`
- Viewport: 160 by 52 terminal cells, default dark theme, color enabled
- Interaction evidence: Textual Pilot verifies Tab and Shift+Tab traverse major regions; native
  collection navigation remains inside each region; visible actions display effective remapped
  keys; pointer and key-based plan copy use the same semantic command.
- Safety evidence: the Plan projection exposes only literal method/path metadata and stable profile
  identities. Runtime lookup expressions and sensitive values remain unresolved. Copy serializes
  only this secret-free projection.

The remaining Explorer, Target, Run, Analyze, Report, responsive, light-theme, and no-color states
are not accepted yet. The detailed blocker list remains in
`docs/design/tui-v2/design-qa.md`.

final result: blocked
