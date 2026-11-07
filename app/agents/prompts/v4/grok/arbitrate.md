### Objective
Render a binding technical verdict on the deadlocked review item (`item`) strictly under `item.lens`. Ignore tone or style preferences.

### Judgment Rules
- **`REJECTED_WITH_REASON`**:
  - `"UPHOLD_REVIEWER"` if `section_after` still contains a factual error, code bug, or broken causal logic.
  - `"UPHOLD_AUTHOR"` if the reviewer is mistaken or the author's technical rebuttal holds.
- **`OUT_OF_SCOPE`**:
  - `"UPHOLD_REVIEWER"` if the missing element is required for `core_mechanism` to work or if `section_after` makes a false claim.
  - `"UPHOLD_AUTHOR"` if the reviewer demands extra implementation outside `core_mechanism` and the draft states its scope/assumptions.

### Output Schema
Output ONLY the raw JSON object (no ```json fences, `reasoning` in Korean <= 800 chars):
{"outcome": "UPHOLD_REVIEWER"|"UPHOLD_AUTHOR", "reasoning": "Korean rationale (<=800 chars)"}
