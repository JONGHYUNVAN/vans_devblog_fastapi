<task>
Verify whether your previous blocking items (`my_previous_items`) on `previous_content` have been resolved in `current_content`, taking into account the author's `resolutions`.
</task>

<verification_rules>
1. Existing Items (`item_checks`)
- Include every item from `my_previous_items` exactly once in `item_checks` using its exact `item_id`.
- Set `resolved: true` if:
  * The author revised `current_content` and fixed the defect.
  * The author selected `REJECTED_WITH_REASON` and provided a technically valid justification.
  * The author selected `OUT_OF_SCOPE`, the item is neither essential for `topic.core_mechanism` to work nor a factual/code error in the existing text, and the article states the boundary assumption.
- Otherwise, set `resolved: false` and explain in `note` (in Korean) why the defect remains unresolved (`note` must not be empty when `resolved` is `false`).

2. Newly Introduced Defects (`new_critical_items`)
- Report ONLY new `CRITICAL` defects (code bugs, factual falsehoods, severe contradictions) introduced by the edits. Ignore `MAJOR` or lower issues.
- Respect `tone_preservation.must_not_flag`.
- Record at least one entry in `checklist`.
</verification_rules>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"item_checks": [{"item_id", "resolved", "note"}], "new_critical_items": [{"section_id", "lens", "severity": "CRITICAL", "comment", "suggested_fix"}], "checklist": {"checked_claims": [], "checked_code_blocks": [], "unverifiable": []}}
</output_contract>
