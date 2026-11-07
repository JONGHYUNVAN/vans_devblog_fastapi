# Task: Spot-Check Revised Draft

## Step 1: Verify Previous Blocking Items (`item_checks`)
Evaluate every item in `my_previous_items` against `current_content` and `resolutions` (include each `item_id` exactly once):
1. Mark `resolved: true` if:
   - The defect was fixed in `current_content`, OR
   - The author's `REJECTED_WITH_REASON` rebuttal is technically sound, OR
   - The author marked `OUT_OF_SCOPE`, the demand is not required for `topic.core_mechanism` to function, the existing text is not factually wrong, and the assumption is stated in the text.
2. Otherwise, mark `resolved: false` and write a non-empty Korean explanation in `note`.

## Step 2: Check for Regressions (`new_critical_items`)
- Flag ONLY newly introduced `CRITICAL` errors (broken code, factual falsehoods, severe contradictions) caused by the revision. Do not add `MAJOR` or lower items.
- Do not flag items in `tone_preservation.must_not_flag`.
- Populate at least one entry in `checklist`.

## Output Format
Return ONLY a single raw JSON object (no markdown fences):
{"item_checks": [{"item_id", "resolved", "note"}], "new_critical_items": [{"section_id", "lens", "severity": "CRITICAL", "comment", "suggested_fix"}], "checklist": {"checked_claims": [], "checked_code_blocks": [], "unverifiable": []}}
