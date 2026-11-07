<task>
Resolve the deadlocked review dispute (`item`) between the author and the reviewer based on `dispute_kind`, `author_rejection_reason`, `reviewer_unresolved_note`, `section_before`, `section_after`, article scope (`core_mechanism`, `out_of_scope`, `scope_declaration`), and `search_evidence`.
</task>

<decision_criteria>
- Evaluate strictly within the scope of `item.lens`. Never base the decision on stylistic preference or tone.
- If `dispute_kind == "REJECTED_WITH_REASON"`:
  * Return `"UPHOLD_REVIEWER"` if an actual technical error, factual falsehood, code bug, or broken causal link remains in `section_after`.
  * Return `"UPHOLD_AUTHOR"` if the reviewer's objection is mistaken or the author's current explanation is technically sound for the target reader.
- If `dispute_kind == "OUT_OF_SCOPE"`:
  * Return `"UPHOLD_REVIEWER"` if the missing content is indispensable for `core_mechanism` to work or if `section_after` states something factually wrong.
  * Return `"UPHOLD_AUTHOR"` if the reviewer is demanding extra implementation outside `core_mechanism` and the article declares its assumptions and scope boundaries.
- Write `reasoning` in Korean (max 800 chars).
</decision_criteria>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"outcome": "UPHOLD_REVIEWER"|"UPHOLD_AUTHOR", "reasoning": "Korean rationale (<=800 chars)"}
</output_contract>
