<task>
Evaluate `content_markdown` in the input JSON through your assigned primary `lens` and optional `secondary_lens`. Identify technical errors, logical gaps, body-code contradictions, or structural defects relative to `topic`.
</task>

<review_lenses>
- `LOGIC_READABILITY`:
  * Broken causal links between paragraphs/sections, logical leaps, or concepts outside `topic.prerequisites` introduced without explanation.
  * Code bugs, convention violations, or `BODY_CODE_CONTRADICTION` (where prose claims do not match code behavior, code uses unexplained variables/values, or constraints in "before" code silently vanish in "after" code).
  * Convoluted sentence structures that obstruct comprehension. (Ignore external spec facts and stylistic tone.)
- `FACT_STRUCTURE` (legacy path only; new drafts route this lens to web-search fact checking):
  * Omission of essential preconditions or critical edge cases required for `topic.core_mechanism` to work.
  * Incompleteness in tables, section structure, or API/specification usage.
- `CRITICAL_PRACTICAL`:
  * Unaddressed real-world failure modes ("only works in toy setups"), missing operational assumptions or trade-offs (`tradeoff`).
  * Exaggerated conclusions, hollow filler, or AI clichés. (Ignore grammar and stylistic tone.)
</review_lenses>

<severity_and_scope_rules>
- Core Promise Check (All Lenses): If the prose and code fail to actually demonstrate what `title` and `topic.core_mechanism` promised, flag it as `MAJOR` or `CRITICAL`.
- Depth Calibration (All Lenses): Use `topic.target_level` and `level_criteria` to calibrate what the reader needs. If the article falls short of the `assessment`/`evidence` standard for `topic.target_level`, report it as `MINOR` naming the unmet criterion. Depth shortfall alone never blocks publication.
- Scope Boundary: `topic.out_of_scope` lists intentionally excluded features. Demands to implement out-of-scope operational features (e.g., distributed multi-node locks, batch reconciliation, zero-downtime migration) in code must be rated `MINOR` at most—only check whether assumptions and boundaries are stated.
- Falsehoods: Any technically incorrect statement or code bug written in the draft is `CRITICAL` regardless of scope.
- Severity Definitions:
  * `CRITICAL` (Blocking): Technical falsehoods, code bugs, body-code contradictions, broken specs.
  * `MAJOR` (Blocking): Logical leaps, missing conditions essential for `topic.core_mechanism`, unfulfilled core promise.
  * `MINOR` (Non-blocking): Readability, minor structural improvements, depth shortfall against `topic.target_level`.
  * `NITPICK` (Non-blocking): Subjective preferences.
- Tone Preservation: Never flag items in `tone_preservation.must_not_flag`. Only flag issues within `tone_preservation.may_flag`.
- If no genuine defects exist, leave `items` empty (`[]`).
</severity_and_scope_rules>

<output_contract>
- Reference only valid `section_id` values present in `content_markdown` (`<!-- sec:id -->`).
- Set each item's `lens` to your `lens` or `secondary_lens`.
- Write `comment`, `suggested_fix`, and `overall_feedback` in Korean.
- Populate `checklist` with at least one entry across `checked_claims`, `checked_code_blocks`, or `unverifiable`.
- Set `verdict` to `"REQUEST_CHANGES"` if any item is `CRITICAL` or `MAJOR`; otherwise `"APPROVE"`.

Output ONLY a single valid JSON object without explanations or markdown code fences:
{"verdict": "APPROVE"|"REQUEST_CHANGES", "items": [{"section_id", "lens", "severity", "comment", "suggested_fix"}], "checklist": {"checked_claims": [], "checked_code_blocks": [], "unverifiable": []}, "overall_feedback": "Korean summary (<=1000 chars)"}
</output_contract>
