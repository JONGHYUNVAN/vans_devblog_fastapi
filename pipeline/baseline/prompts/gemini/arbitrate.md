# Task: Technical Review Dispute Arbitration

## Step 1: Evaluate Evidence & Scope
Examine `item` (strictly under `item.lens`), `dispute_kind`, `author_rejection_reason`, `reviewer_unresolved_note`, `section_before`, `section_after`, `core_mechanism`, `out_of_scope`, `scope_declaration`, and `search_evidence`. Ignore stylistic or tone preferences.

## Step 2: Apply Decision Rule
1. When `dispute_kind == "REJECTED_WITH_REASON"`:
   - Choose `"UPHOLD_REVIEWER"` if a factual error, code bug, or logical defect remains in `section_after`.
   - Choose `"UPHOLD_AUTHOR"` if the author's technical rebuttal is valid.
2. When `dispute_kind == "OUT_OF_SCOPE"`:
   - Choose `"UPHOLD_REVIEWER"` if the omitted mechanism is required for `core_mechanism` to function or if existing text is technically wrong.
   - Choose `"UPHOLD_AUTHOR"` if the demand is for extra implementation outside `core_mechanism` and the article already states its boundary assumptions.

## Output Format
Return ONLY a single raw JSON object (no markdown fences, `reasoning` in Korean <= 800 chars):
{"outcome": "UPHOLD_REVIEWER"|"UPHOLD_AUTHOR", "reasoning": "Korean rationale (<=800 chars)"}
