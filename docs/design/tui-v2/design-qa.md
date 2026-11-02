# TUI design QA index

Updated **2026-10-31**. The canonical implementation is `comparo.tui`; see the
[current QA record](../../../design-qa.md) for verified journeys and limits.

The earlier [2026-07-26 QA report](../../history/tui-design-qa-2026-07-26.md) is historical.
Its blanket acceptance claim, 3,617-test total, coverage percentage and temporary screenshot
references do not establish acceptance of today's implementation.

Use the [acceptance matrix](acceptance.md), [visual system](visual-system.md),
[interaction contract](interaction-contract.md) and [reference images](README.md#reference-states)
when reviewing changes. Native Textual mechanics must be used through the existing component
library. Launch and Reports must share Analyze; a new report-specific analyzer is not acceptable.

Implemented current additions include persistent Settings, Explore → Launch, reviewed profile
saving, raw/JSON/SSE capture inspection and sanitized cURL. Glyph/reduced-motion preferences,
complete palette/help remapping and a fresh all-state dark/light/no-color paired visual review
remain separate acceptance work. Backend support alone is not UI acceptance.
