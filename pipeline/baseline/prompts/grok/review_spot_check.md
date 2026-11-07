### Objective
Check whether each item in `my_previous_items` is resolved in `current_content` based on the author's edits and `resolutions`.

### Rules
1. **`item_checks`**: Must contain every `item_id` from `my_previous_items` exactly once.
   - `resolved: true` if fixed in text/code, if `REJECTED_WITH_REASON` is technically valid, or if `OUT_OF_SCOPE` is valid (not a bug/falsehood in existing text, not required for `topic.core_mechanism`, and boundary is declared).
   - `resolved: false` otherwise (must include a non-empty Korean `note`).
2. **`new_critical_items`**: Only report new `CRITICAL` bugs/falsehoods introduced during revision. Ignore `MAJOR` or below. Never flag `tone_preservation.must_not_flag`.
3. **`checklist`**: Must have at least one non-empty entry.

### Output Schema
Output ONLY the raw JSON object (no ```json fences):
{"item_checks": [{"item_id", "resolved", "note"}], "new_critical_items": [{"section_id", "lens", "severity": "CRITICAL", "comment", "suggested_fix"}], "checklist": {"checked_claims": [], "checked_code_blocks": [], "unverifiable": []}}
